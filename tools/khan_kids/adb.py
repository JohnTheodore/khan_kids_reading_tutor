"""Reliable Android Debug Bridge operations shared by every crawler."""

from __future__ import annotations

import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .ui import Rect


class AutomationError(RuntimeError):
    """Raised when the live app is not in the exact state automation expects."""


def run_command(args: Sequence[str], *, timeout: int = 60, capture: bool = False) -> bytes:
    try:
        result = subprocess.run(
            list(args),
            check=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise AutomationError(f"Command is unavailable: {args[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise AutomationError(f"Command timed out after {timeout}s: {args[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise AutomationError(f"Command failed: {' '.join(args[:3])}{suffix}") from error
    return result.stdout if capture else b""


def prepare_capture_workspace(serial: str, output: Path) -> tuple[AndroidDevice, Path, Path]:
    """Validate a device and create the standard output and scratch directories."""
    device = AndroidDevice(serial)
    device.assert_connected()
    destination = output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    scratch = destination / ".scratch"
    scratch.mkdir(exist_ok=True)
    return device, destination, scratch


class AndroidDevice:
    def __init__(self, serial: str, *, settle_seconds: float = 1.0) -> None:
        self.serial = serial
        self.prefix = ("adb", "-s", serial)
        self.settle_seconds = settle_seconds

    def command(self, *args: str, timeout: int = 60, capture: bool = False) -> bytes:
        return run_command((*self.prefix, *args), timeout=timeout, capture=capture)

    def assert_connected(self) -> None:
        state = self.command("get-state", timeout=10, capture=True).decode().strip()
        if state != "device":
            raise AutomationError(f"ADB device {self.serial!r} is not ready: {state!r}")

    def tap(self, x: int, y: int, *, settle: float = 0.0) -> None:
        self.command("shell", "input", "tap", str(x), str(y))
        if settle:
            time.sleep(settle)

    def tap_rect(self, rect: Rect, *, settle: float = 0.0) -> None:
        x, y = rect.center
        self.tap(x, y, settle=settle)

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, ms: int = 850, *, settle: float = 0.0
    ) -> None:
        self.command(
            "shell",
            "input",
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(ms),
        )
        if settle:
            time.sleep(settle)

    def keep_awake(self) -> None:
        self.wake()
        self.command("shell", "svc", "power", "stayon", "true")
        self._set_setting("system", "screen_off_timeout", "2147483647")

    def wake(self) -> None:
        self.command("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        time.sleep(self.settle_seconds)

    def is_locked(self) -> bool:
        state = self.command("shell", "dumpsys", "window", capture=True).decode(errors="replace")
        return any(
            marker in state
            for marker in (
                "mDreamingLockscreen=true",
                "mShowingLockscreen=true",
                "isStatusBarKeyguard=true",
            )
        )

    def unlock_with_pin(self, pin: str) -> None:
        """Make one PIN attempt without placing the complete PIN in an argv value."""
        if not 4 <= len(pin) <= 16 or not pin.isascii() or not pin.isdigit():
            raise AutomationError("Android PIN must contain 4 to 16 ASCII digits")
        self.swipe(1280, 1350, 1280, 400, 300, settle=self.settle_seconds)
        for digit in pin:
            self.command("shell", "input", "keyevent", f"KEYCODE_{digit}")
        self.command("shell", "input", "keyevent", "KEYCODE_ENTER")
        time.sleep(self.settle_seconds)

    def foreground_package(self) -> str | None:
        state = self.command("shell", "dumpsys", "window", capture=True).decode(errors="replace")
        for line in state.splitlines():
            if "mCurrentFocus=" not in line or "/" not in line:
                continue
            component = line.rsplit(maxsplit=1)[-1].rstrip("}")
            return component.split("/", maxsplit=1)[0]
        return None

    def start_activity(self, component: str) -> None:
        self.command("shell", "am", "start", "-n", component)
        time.sleep(self.settle_seconds)

    @contextmanager
    def awake_session(self) -> Iterator[None]:
        """Keep the screen awake in landscape, then restore all prior settings."""
        timeout = self._setting("system", "screen_off_timeout")
        stay_on = self._setting("global", "stay_on_while_plugged_in")
        accelerometer = self._setting("system", "accelerometer_rotation")
        rotation = self._setting("system", "user_rotation")
        with ExitStack() as restore:
            restore.callback(self._set_setting, "system", "accelerometer_rotation", accelerometer)
            restore.callback(self._set_setting, "system", "user_rotation", rotation)
            restore.callback(self._set_setting, "global", "stay_on_while_plugged_in", stay_on)
            restore.callback(self._set_setting, "system", "screen_off_timeout", timeout)
            self.keep_awake()
            self.command("shell", "wm", "set-ignore-orientation-request", "false")
            self._set_setting("system", "accelerometer_rotation", "0")
            self._set_setting("system", "user_rotation", "3")
            time.sleep(self.settle_seconds)
            yield

    def _setting(self, namespace: str, key: str) -> str:
        value = (
            self.command("shell", "settings", "get", namespace, key, capture=True).decode().strip()
        )
        if not value or value == "null":
            raise AutomationError(f"Android setting {namespace}/{key} is unavailable")
        return value

    def _set_setting(self, namespace: str, key: str, value: str) -> None:
        self.command("shell", "settings", "put", namespace, key, value)

    def scroll_to_top(
        self,
        x: int,
        *,
        gestures: int = 30,
        start_y: int = 520,
        end_y: int = 1450,
        duration_ms: int = 250,
    ) -> None:
        prior_signature = self._window_signature()
        for _ in range(gestures):
            self.swipe(x, start_y, x, end_y, duration_ms)
            time.sleep(0.15)
            current_signature = self._window_signature()
            if current_signature == prior_signature:
                break
            prior_signature = current_signature
        time.sleep(self.settle_seconds)

    def _window_signature(self, *, attempts: int = 3) -> tuple[tuple[str, ...], ...]:
        """Return stable, visible UI state for detecting a scroll boundary."""
        remote = "/sdcard/khan-kids-scroll-probe.xml"
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                self.command("shell", "uiautomator", "dump", remote, timeout=60)
                raw = self.command("exec-out", "cat", remote, timeout=20, capture=True)
                root = ET.fromstring(raw)
                return tuple(
                    (
                        node.attrib.get("class", ""),
                        node.attrib.get("text", ""),
                        node.attrib.get("content-desc", ""),
                        node.attrib.get("bounds", ""),
                        node.attrib.get("checked", ""),
                        node.attrib.get("selected", ""),
                    )
                    for node in root.iter("node")
                    if node.attrib.get("visible-to-user", "true") == "true"
                )
            except (
                subprocess.TimeoutExpired,
                subprocess.CalledProcessError,
                ET.ParseError,
            ) as error:
                last_error = error
                time.sleep(2 + 2 * attempt)
        raise AutomationError(
            f"Could not inspect the UI scroll position after {attempts} attempts"
        ) from last_error

    def dump(self, destination: Path, *, attempts: int = 3) -> ET.Element:
        destination.parent.mkdir(parents=True, exist_ok=True)
        remote = "/sdcard/khan-kids-window.xml"
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                self.command("shell", "uiautomator", "dump", remote, timeout=60)
                self.command("pull", remote, str(destination), timeout=20)
                return ET.parse(destination).getroot()
            except (
                subprocess.TimeoutExpired,
                subprocess.CalledProcessError,
                ET.ParseError,
            ) as error:
                last_error = error
                time.sleep(2 + 2 * attempt)
        raise AutomationError(
            f"Could not obtain a valid UI hierarchy after {attempts} attempts"
        ) from last_error

    def screenshot(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            self.command("exec-out", "screencap", "-p", timeout=20, capture=True)
        )
