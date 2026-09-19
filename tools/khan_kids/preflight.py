"""Read-only tablet health checks shared by the dashboard and sync engine."""

from __future__ import annotations

import re

from .adb import AndroidDevice, AutomationError
from .constants import KHAN_KIDS_PACKAGE


def connectivity_is_validated(output: str) -> bool:
    """Return whether Android reports Internet on its active default network."""
    active = re.search(r"Active default network:\s*(\d+)", output, re.IGNORECASE)
    if active:
        network = active.group(1)
        blocks = re.split(r"(?=NetworkAgentInfo\{)", output)
        matching = [block for block in blocks if f"network{{{network}}}" in block]
        if not matching:
            return False
        output = "\n".join(matching)
    capabilities = output.upper()
    return "INTERNET" in capabilities and "VALIDATED" in capabilities


def assert_tablet_preflight(device: AndroidDevice, *, require_unlocked: bool = True) -> None:
    """Fail before Khan Kids navigation when the tablet cannot support a sync."""
    device.assert_connected()
    if require_unlocked and device.is_locked():
        raise AutomationError("Tablet is locked. Unlock it, then check the connection again.")
    connectivity = device.command(
        "shell", "dumpsys", "connectivity", timeout=8, capture=True
    ).decode(errors="replace")
    if not connectivity_is_validated(connectivity):
        raise AutomationError(
            "Tablet has no validated Internet connection. Reconnect Wi-Fi, then check again."
        )
    package = device.command(
        "shell", "pm", "path", KHAN_KIDS_PACKAGE, timeout=8, capture=True
    ).decode(errors="replace")
    if not package.strip().startswith("package:"):
        raise AutomationError("Khan Kids is not available on the connected tablet.")


def tablet_health(device: AndroidDevice) -> dict[str, object]:
    """Return a browser-safe health result without opening or changing the app."""
    checks: list[dict[str, object]] = []
    try:
        device.assert_connected()
        checks.append({"id": "adb", "label": "Tablet connected", "ok": True})
    except AutomationError as error:
        return {
            "ready": False,
            "checks": [{"id": "adb", "label": "Tablet connected", "ok": False}],
            "error": str(error),
        }
    try:
        unlocked = not device.is_locked()
        checks.append({"id": "unlocked", "label": "Tablet unlocked", "ok": unlocked})
        connectivity = device.command(
            "shell", "dumpsys", "connectivity", timeout=8, capture=True
        ).decode(errors="replace")
        online = connectivity_is_validated(connectivity)
        checks.append({"id": "internet", "label": "Tablet Internet validated", "ok": online})
        package = device.command(
            "shell", "pm", "path", KHAN_KIDS_PACKAGE, timeout=8, capture=True
        ).decode(errors="replace")
        installed = package.strip().startswith("package:")
        checks.append({"id": "app", "label": "Khan Kids available", "ok": installed})
    except AutomationError as error:
        return {"ready": False, "checks": checks, "error": str(error)}
    ready = all(bool(check["ok"]) for check in checks)
    return {
        "ready": ready,
        "checks": checks,
        "error": None
        if ready
        else "Unlock the tablet and restore its Internet connection before retrying.",
    }
