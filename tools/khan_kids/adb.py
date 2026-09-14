"""Reliable Android Debug Bridge operations shared by every crawler."""

from __future__ import annotations

import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .timing import TimingRecorder
from .ui import Rect
from .ui_backend import UiBackendError, UiHierarchyBackend, create_ui_backend


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
    def __init__(
        self,
        serial: str,
        *,
        settle_seconds: float = 1.0,
        timing: TimingRecorder | None = None,
    ) -> None:
        self.serial = serial
        self.prefix = ("adb", "-s", serial)
        self.settle_seconds = settle_seconds
        self.timing = timing or TimingRecorder()
        self.ui_backend: UiHierarchyBackend | None = None

    @property
    def ui_backend_name(self) -> str:
        return self.ui_backend.name if self.ui_backend else "legacy-adb"

    def enable_ui_backend(self, mode: str) -> None:
        with self.timing.span("startup.ui_backend"):
            try:
                self.ui_backend = create_ui_backend(self.serial, mode)
            except UiBackendError as error:
                raise AutomationError(str(error)) from error

    def command(self, *args: str, timeout: int = 60, capture: bool = False) -> bytes:
        with self.timing.span(_command_metric(args)):
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

    def enter_alphanumeric_secret(self, secret: str) -> None:
        """Enter a secret one key at a time so it never appears as one process argument."""
        if not secret or not secret.isascii() or not secret.isalnum():
            raise AutomationError("Secret contains unsupported input characters")
        for character in secret:
            keycode = f"KEYCODE_{character.upper()}"
            if character.isupper():
                self.command(
                    "shell",
                    "input",
                    "keycombination",
                    "KEYCODE_SHIFT_LEFT",
                    keycode,
                )
            else:
                self.command("shell", "input", "keyevent", keycode)

    def foreground_package(self) -> str | None:
        state = self.command("shell", "dumpsys", "window", capture=True).decode(errors="replace")
        for line in state.splitlines():
            if "mCurrentFocus=" not in line or "/" not in line:
                continue
            component = line.rsplit(maxsplit=1)[-1].rstrip("}")
            return component.split("/", maxsplit=1)[0]
        return None

    def start_activity(self, component: str) -> None:
        # Ask ActivityManager to wait for the launch transition itself.  The
        # caller still verifies foreground focus because ``am start -W`` can
        # complete before WindowManager publishes its new current focus.
        self.command("shell", "am", "start", "-W", "-n", component)
        time.sleep(self.settle_seconds)

    def force_stop(self, package: str) -> None:
        """Stop one explicitly named application package."""
        if not package or any(character.isspace() for character in package):
            raise ValueError("package must be a non-empty Android package name")
        self.command("shell", "am", "force-stop", package)

    @contextmanager
    def awake_session(self) -> Iterator[None]:
        """Keep the screen awake in landscape, then restore all prior settings."""
        timeout = self._setting("system", "screen_off_timeout")
        stay_on = self._setting("global", "stay_on_while_plugged_in")
        restore_rotation = self._rotation_restore_command()
        with ExitStack() as restore:
            restore.callback(self.command, *restore_rotation)
            restore.callback(self._set_setting, "global", "stay_on_while_plugged_in", stay_on)
            restore.callback(self._set_setting, "system", "screen_off_timeout", timeout)
            self.keep_awake()
            # WindowManager applies the lock mode and angle in one operation. Separate
            # settings writes briefly lock to a stale portrait fallback before the
            # landscape angle arrives.
            self.command("shell", "wm", "user-rotation", "lock", "3")
            self.command("shell", "wm", "set-ignore-orientation-request", "false")
            time.sleep(self.settle_seconds)
            yield

    def _rotation_restore_command(self) -> tuple[str, ...]:
        state = self.command("shell", "wm", "user-rotation", capture=True).decode().strip()
        fields = state.split()
        if fields == ["free"]:
            return ("shell", "wm", "user-rotation", "free")
        if len(fields) == 2 and fields[0] == "lock" and fields[1] in {"0", "1", "2", "3"}:
            return ("shell", "wm", "user-rotation", "lock", fields[1])
        raise AutomationError(f"Unexpected Android user-rotation state: {state!r}")

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
    ) -> ET.Element:
        root = self.hierarchy()
        prior_signature = self._window_signature(root)
        for _ in range(gestures):
            self.swipe(x, start_y, x, end_y, duration_ms)
            root = self.hierarchy()
            current_signature = self._window_signature(root)
            if current_signature == prior_signature:
                break
            prior_signature = current_signature
        return root

    def _window_signature(
        self, root: ET.Element | None = None, *, attempts: int = 3
    ) -> tuple[tuple[str, ...], ...]:
        """Return stable, visible UI state for detecting a scroll boundary."""
        if root is None:
            root = self._hierarchy_root(attempts=attempts)
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

    def dump(self, destination: Path, *, attempts: int = 3) -> ET.Element:
        root, raw = self._hierarchy_root(attempts=attempts, include_raw=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        return root

    def hierarchy(self, *, attempts: int = 3) -> ET.Element:
        """Return the live hierarchy without persisting potentially sensitive UI text."""
        return self._hierarchy_root(attempts=attempts)

    def _hierarchy_root(
        self, *, attempts: int, include_raw: bool = False
    ) -> ET.Element | tuple[ET.Element, bytes]:
        remote = "/sdcard/khan-kids-window.xml"
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with self.timing.span("ui.hierarchy"):
                    if self.ui_backend is None:
                        self.command("shell", "uiautomator", "dump", remote, timeout=60)
                        raw = self.command("exec-out", "cat", remote, timeout=20, capture=True)
                    else:
                        raw = self.ui_backend.dump_hierarchy()
                    root = ET.fromstring(raw)
                return (root, raw) if include_raw else root
            except (ET.ParseError, UiBackendError, AutomationError) as error:
                last_error = error
                time.sleep(0.25 * (attempt + 1))
        raise AutomationError(
            f"Could not obtain a valid UI hierarchy after {attempts} attempts"
        ) from last_error

    def screenshot(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            self.command("exec-out", "screencap", "-p", timeout=20, capture=True)
        )


def _command_metric(args: Sequence[str]) -> str:
    """Classify commands without retaining coordinates, text, or credentials."""
    if args[:3] == ("shell", "input", "tap"):
        return "adb.tap"
    if args[:3] == ("shell", "input", "swipe"):
        return "adb.swipe"
    if args[:3] == ("shell", "input", "keyevent"):
        return "adb.keyevent"
    if args[:3] == ("shell", "uiautomator", "dump"):
        return "adb.hierarchy_dump"
    if args[:2] == ("exec-out", "cat"):
        return "adb.hierarchy_read"
    if args[:2] == ("exec-out", "screencap"):
        return "adb.screenshot"
    if args[:2] == ("shell", "settings"):
        return "adb.settings"
    return "adb.other"
