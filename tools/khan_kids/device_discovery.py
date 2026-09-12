"""Resolve one configured Android tablet without persisting its Wi-Fi endpoint."""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class DeviceDiscoveryError(RuntimeError):
    """Raised when the configured tablet cannot be resolved unambiguously."""


@dataclass(frozen=True)
class DeviceConfig:
    student: str
    hardware_serial: str
    model: str

    @classmethod
    def load(cls, path: Path) -> DeviceConfig:
        try:
            payload = json.loads(path.read_text())
        except FileNotFoundError as error:
            raise DeviceDiscoveryError(
                f"Device configuration is missing: {path}. Supply --serial or create it."
            ) from error
        except (OSError, json.JSONDecodeError) as error:
            raise DeviceDiscoveryError(
                f"Cannot read device configuration {path}: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise DeviceDiscoveryError(f"Device configuration must be a JSON object: {path}")
        values: dict[str, str] = {}
        for field in ("student", "hardware_serial", "model"):
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                raise DeviceDiscoveryError(f"Device configuration field {field!r} is required")
            values[field] = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{4,128}", values["hardware_serial"]):
            raise DeviceDiscoveryError("Configured hardware_serial contains unsupported characters")
        return cls(**values)


def parse_adb_devices(output: str) -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    for line in output.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2:
            devices.append((fields[0], fields[1]))
    return devices


def is_network_endpoint(endpoint: str) -> bool:
    host, separator, port_text = endpoint.rpartition(":")
    if not separator or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def parse_browse_instances(output: str, hardware_serial: str) -> list[str]:
    marker = "_adb-tls-connect._tcp."
    prefix = f"adb-{hardware_serial}-"
    instances: list[str] = []
    for line in output.splitlines():
        if marker not in line:
            continue
        instance = line.split(marker, maxsplit=1)[1].strip()
        if instance.startswith(prefix) and instance not in instances:
            instances.append(instance)
    return instances


def parse_service_lookup(output: str) -> tuple[str, int]:
    match = re.search(r"can be reached at\s+(\S+):(\d+)\s", output)
    if not match:
        raise DeviceDiscoveryError("macOS mDNS did not resolve the tablet service endpoint")
    port = int(match.group(2))
    if not 1 <= port <= 65535:
        raise DeviceDiscoveryError("macOS mDNS returned an invalid tablet service port")
    return match.group(1).rstrip("."), port


def parse_host_ipv4(output: str) -> str:
    addresses = re.findall(r"^ip_address:\s*(\d+\.\d+\.\d+\.\d+)\s*$", output, re.MULTILINE)
    if len(addresses) != 1:
        raise DeviceDiscoveryError(
            f"macOS resolved {len(addresses)} IPv4 addresses for the tablet; expected exactly one"
        )
    octets = addresses[0].split(".")
    if any(int(octet) > 255 for octet in octets):
        raise DeviceDiscoveryError("macOS returned an invalid IPv4 address for the tablet")
    return addresses[0]


def _run(command: list[str], *, timeout: float = 10, allowed_codes: tuple[int, ...] = (0,)) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise DeviceDiscoveryError(f"Required command is unavailable: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise DeviceDiscoveryError(f"Command timed out: {command[0]}") from error
    if result.returncode not in allowed_codes:
        detail = result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise DeviceDiscoveryError(f"Command failed: {' '.join(command[:3])}{suffix}")
    return result.stdout


def ensure_current_adb_server() -> None:
    version = _run(["adb", "version"])
    match = re.search(r"^Version\s+(\d+)\.", version, re.MULTILINE)
    if not match or int(match.group(1)) < 37:
        raise DeviceDiscoveryError("ADB 37.0.0 or newer is required for Android 17 Wi-Fi")
    status = _run(["adb", "server-status"], allowed_codes=(0, 1))
    server_match = re.search(r'^version:\s*"(\d+)\.', status, re.MULTILINE)
    if server_match and int(server_match.group(1)) >= 37:
        return
    _run(["adb", "kill-server"], allowed_codes=(0, 1))
    _run(["adb", "start-server"])
    status = _run(["adb", "server-status"])
    if "mdns_backend: LIBADBMDNS" not in status or "mdns_enabled: true" not in status:
        raise DeviceDiscoveryError(
            "ADB restarted without the required LIBADBMDNS discovery support"
        )


def _device_property(endpoint: str, property_name: str) -> str:
    return _run(
        ["adb", "-s", endpoint, "shell", "getprop", property_name],
        timeout=8,
    ).strip()


def _verify_device(endpoint: str, config: DeviceConfig) -> bool:
    try:
        state = _run(["adb", "-s", endpoint, "get-state"], timeout=8).strip()
        serial = _device_property(endpoint, "ro.serialno")
        model = _device_property(endpoint, "ro.product.model")
    except DeviceDiscoveryError:
        return False
    return state == "device" and serial == config.hardware_serial and model == config.model


def _already_connected(config: DeviceConfig) -> str | None:
    output = _run(["adb", "devices"])
    matches = [
        endpoint
        for endpoint, state in parse_adb_devices(output)
        if state == "device" and _verify_device(endpoint, config)
    ]
    if len(matches) > 1:
        raise DeviceDiscoveryError("Multiple online ADB transports match the configured tablet")
    return matches[0] if matches else None


def _remove_offline_network_transports() -> None:
    output = _run(["adb", "devices"])
    for endpoint, state in parse_adb_devices(output):
        if state == "offline" and is_network_endpoint(endpoint):
            _run(["adb", "disconnect", endpoint], allowed_codes=(0, 1))


def _timed_dns_sd(*arguments: str, seconds: int = 5) -> str:
    # Keep the timer and dns-sd child on macOS. Killing OrbStack's Linux-side
    # ``mac`` proxy can orphan the host process and leave the caller blocked.
    script = (
        'seconds="$1"; shift; dns-sd "$@" & discovery_pid=$!; '
        'sleep "$seconds"; kill "$discovery_pid" 2>/dev/null; '
        'wait "$discovery_pid" 2>/dev/null || true'
    )
    return _run(
        ["mac", "sh", "-c", script, "sh", str(seconds), *arguments],
        timeout=seconds + 4,
    )


def _discover_endpoint(config: DeviceConfig) -> str:
    browse = _timed_dns_sd("-B", "_adb-tls-connect._tcp", "local.")
    instances = parse_browse_instances(browse, config.hardware_serial)
    if len(instances) != 1:
        raise DeviceDiscoveryError(
            f"macOS discovered {len(instances)} wireless ADB services for the configured tablet; "
            "expected exactly one"
        )
    lookup = _timed_dns_sd("-L", instances[0], "_adb-tls-connect._tcp", "local.", seconds=4)
    try:
        hostname, port = parse_service_lookup(lookup)
    except DeviceDiscoveryError as error:
        raise DeviceDiscoveryError(
            "macOS sees the tablet service but Android is not publishing its TLS endpoint; "
            "after a tablet reboot, unlock it once and retry"
        ) from error
    host_record = _run(["mac", "dscacheutil", "-q", "host", "-a", "name", hostname])
    address = parse_host_ipv4(host_record)
    return f"{address}:{port}"


def resolve_device(config: DeviceConfig) -> str:
    ensure_current_adb_server()
    _remove_offline_network_transports()
    connected = _already_connected(config)
    if connected:
        return connected
    endpoint = _discover_endpoint(config)
    # Android can reuse its TLS endpoint across a reboot while the host still
    # retains the pre-reboot transport as ``offline``.  ``adb connect`` then
    # reports "already connected" without replacing it, so evict that exact
    # endpoint before establishing and verifying the fresh transport.
    _run(["adb", "disconnect", endpoint], allowed_codes=(0, 1))
    _run(["adb", "connect", endpoint], timeout=15)
    if not _verify_device(endpoint, config):
        _run(["adb", "disconnect", endpoint], allowed_codes=(0, 1))
        raise DeviceDiscoveryError("Discovered ADB endpoint did not match the configured tablet")
    return endpoint
