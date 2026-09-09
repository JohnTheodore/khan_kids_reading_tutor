"""Reliable Android Debug Bridge operations shared by every crawler."""

from __future__ import annotations

import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from .ui import Rect


class AutomationError(RuntimeError):
    """Raised when the live app is not in the exact state automation expects."""


def run_command(args: Sequence[str], *, timeout: int = 60, capture: bool = False) -> bytes:
    result = subprocess.run(
        list(args),
        check=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
        timeout=timeout,
    )
    return result.stdout if capture else b""


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
        self.command("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        self.command("shell", "svc", "power", "stayon", "true")
        self.command("shell", "settings", "put", "system", "screen_off_timeout", "2147483647")

    @contextmanager
    def awake_session(self) -> Iterator[None]:
        """Keep the screen awake temporarily and restore both prior settings."""
        timeout = self._setting("system", "screen_off_timeout")
        stay_on = self._setting("global", "stay_on_while_plugged_in")
        self.keep_awake()
        try:
            yield
        finally:
            try:
                self.command(
                    "shell",
                    "settings",
                    "put",
                    "system",
                    "screen_off_timeout",
                    timeout,
                )
            finally:
                self.command(
                    "shell",
                    "settings",
                    "put",
                    "global",
                    "stay_on_while_plugged_in",
                    stay_on,
                )

    def _setting(self, namespace: str, key: str) -> str:
        value = (
            self.command("shell", "settings", "get", namespace, key, capture=True).decode().strip()
        )
        if not value or value == "null":
            raise AutomationError(f"Android setting {namespace}/{key} is unavailable")
        return value

    def scroll_to_top(
        self,
        x: int,
        *,
        gestures: int = 30,
        start_y: int = 520,
        end_y: int = 1450,
        duration_ms: int = 250,
    ) -> None:
        for _ in range(gestures):
            self.swipe(x, start_y, x, end_y, duration_ms)
            time.sleep(0.05)
        time.sleep(self.settle_seconds)

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
