"""Reliable Android Debug Bridge operations shared by every crawler."""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .student_identity import anonymize_text, load_aliases, require_aliases, validate_identity_view
from .timing import TimingRecorder
from .ui import Rect
from .ui_backend import UiBackendError, UiHierarchyBackend, create_ui_backend


class AutomationError(RuntimeError):
    """Raised when the live app is not in the exact state automation expects."""


class HomeHandoffError(AutomationError):
    """Raised when Android does not verify a stable foreground outside the app."""

    def __init__(self, message: str, *, reason: str = "home_unverified") -> None:
        super().__init__(message)
        self.reason = reason


LOCK_TASK_NONE = "none"
LOCK_TASK_PINNED = "pinned"
LOCK_TASK_LOCKED = "locked"
LOCK_TASK_UNKNOWN = "unknown"

# Persistent Android power settings must always permit automatic sleep.  Older
# releases temporarily used an effectively infinite timeout and relied on
# teardown to restore it; a killed process could therefore drain the tablet.
AUTO_SLEEP_TIMEOUT_MS = "120000"


def parse_lock_task_mode(output: str) -> str:
    """Normalize Android's screen-pinning/lock-task dumpsys state."""
    match = re.search(r"mLockTaskModeState=(?:LOCK_TASK_MODE_)?(NONE|PINNED|LOCKED)\b", output)
    if not match:
        return LOCK_TASK_UNKNOWN
    return match.group(1).casefold()


def lock_task_problem(mode: str) -> str | None:
    if mode == LOCK_TASK_NONE:
        return None
    if mode == LOCK_TASK_PINNED:
        return (
            "Khan Kids is screen-pinned. Swipe up and hold to unpin it, "
            "then check the tablet connection again."
        )
    if mode == LOCK_TASK_LOCKED:
        return "The tablet is in managed lock-task mode. Exit kiosk mode before syncing."
    return (
        "Android app-pinning state could not be verified. "
        "Leave app pinning off, then check the tablet connection again."
    )


def run_command(
    args: Sequence[str],
    *,
    timeout: int = 60,
    capture: bool = False,
    input_data: bytes | None = None,
) -> bytes:
    try:
        result = subprocess.run(
            list(args),
            check=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            input=input_data,
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
        cancellation_check: Callable[[], None] | None = None,
    ) -> None:
        self.serial = serial
        self.prefix = ("adb", "-s", serial)
        self.settle_seconds = settle_seconds
        self.timing = timing or TimingRecorder()
        self.ui_backend: UiHierarchyBackend | None = None
        self.student_aliases = load_aliases()
        self._cancellation_check = cancellation_check
        self._ignore_cancellation = 0

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
        self._check_cancelled()
        with self.timing.span(_command_metric(args)):
            result = run_command((*self.prefix, *args), timeout=timeout, capture=capture)
        self._check_cancelled()
        return result

    def _check_cancelled(self) -> None:
        if self._cancellation_check is not None and not self._ignore_cancellation:
            self._cancellation_check()

    @contextmanager
    def cleanup_mode(self) -> Iterator[None]:
        """Temporarily allow restoration commands after cancellation."""
        self._ignore_cancellation += 1
        try:
            yield
        finally:
            self._ignore_cancellation -= 1

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

    def ensure_auto_sleep(self) -> None:
        """Apply a bounded timeout and disable Android's persistent stay-awake mode."""
        errors = []
        for namespace, key, value in (
            ("system", "screen_off_timeout", AUTO_SLEEP_TIMEOUT_MS),
            ("global", "stay_on_while_plugged_in", "0"),
        ):
            try:
                self._set_setting(namespace, key, value)
            except AutomationError as error:
                errors.append(str(error))
        if errors:
            raise AutomationError("Could not restore automatic tablet sleep: " + "; ".join(errors))

    def wake(self) -> None:
        state = self.command("shell", "dumpsys", "power", capture=True).decode(errors="replace")
        if "mWakefulness=Awake" in state:
            return
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
        commands = []
        for character in secret:
            keycode = f"KEYCODE_{character.upper()}"
            if character.isupper():
                commands.append(f"input keycombination KEYCODE_SHIFT_LEFT {keycode}")
            else:
                commands.append(f"input keyevent {keycode}")
        # Stdin avoids both per-key ADB connections and secret-bearing argv.
        # Fail immediately on a failed key injection; never retry partial input.
        script = "set -e\n" + "\n".join(commands) + "\nexit\n"
        with self.timing.span("adb.secret_entry"):
            try:
                run_command((*self.prefix, "shell"), input_data=script.encode())
            except AutomationError:
                raise AutomationError("Secret key entry failed; refusing to retry") from None

    def foreground_package(self) -> str | None:
        state = self.command("shell", "dumpsys", "window", capture=True).decode(errors="replace")
        for line in state.splitlines():
            if "mCurrentFocus=" not in line or "/" not in line:
                continue
            component = line.rsplit(maxsplit=1)[-1].rstrip("}")
            return component.split("/", maxsplit=1)[0]
        return None

    def lock_task_mode(self) -> str:
        state = self.command(
            "shell", "dumpsys", "activity", "activities", timeout=8, capture=True
        ).decode(errors="replace")
        return parse_lock_task_mode(state)

    def return_to_android_home(self, app_package: str, *, timeout: float = 5.0) -> str:
        """Leave one foreground app via Android Home and verify a stable handoff."""
        if not app_package or any(character.isspace() for character in app_package):
            raise ValueError("app_package must be a non-empty package name")
        mode = self.lock_task_mode()
        problem = lock_task_problem(mode)
        if problem:
            raise HomeHandoffError(problem, reason=f"lock_task_{mode}")
        self.command("shell", "input", "keyevent", "KEYCODE_HOME")
        deadline = time.monotonic() + timeout
        previous = None
        stable_reads = 0
        while time.monotonic() < deadline:
            foreground = self.foreground_package()
            if foreground and foreground != app_package:
                stable_reads = stable_reads + 1 if foreground == previous else 1
                if stable_reads >= 2:
                    return foreground
            else:
                stable_reads = 0
            previous = foreground
            time.sleep(min(0.2, self.settle_seconds))
        mode = self.lock_task_mode()
        problem = lock_task_problem(mode)
        if problem:
            raise HomeHandoffError(problem, reason=f"lock_task_{mode}")
        raise HomeHandoffError(f"Android Home did not leave {app_package}")

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
        """Wake for interactive work while retaining a bounded automatic timeout."""
        self.ensure_auto_sleep()
        restore_rotation = self._rotation_restore_command()
        restore = ExitStack()
        # ExitStack runs callbacks in reverse order and continues after one
        # fails, so automatic sleep is still restored if rotation cleanup fails.
        restore.callback(self.ensure_auto_sleep)
        restore.callback(self.command, *restore_rotation)
        try:
            self.wake()
            # WindowManager applies the lock mode and angle in one operation. Separate
            # settings writes briefly lock to a stale portrait fallback before the
            # landscape angle arrives.
            self.command("shell", "wm", "user-rotation", "lock", "3")
            self.command("shell", "wm", "set-ignore-orientation-request", "false")
            if restore_rotation != ("shell", "wm", "user-rotation", "lock", "3"):
                time.sleep(self.settle_seconds)
            yield
        finally:
            with self.cleanup_mode():
                restore.close()

    @contextmanager
    def app_session(
        self,
        app_package: str,
        *,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> Iterator[None]:
        """Restore Android state and always hand the foreground back to Home."""
        active_error: BaseException | None = None
        with self.awake_session():
            try:
                yield
            except BaseException as error:
                active_error = error
                if on_error is not None:
                    try:
                        on_error(error)
                    except Exception as diagnostic_error:
                        error.add_note(
                            "Private diagnostic capture also failed: "
                            f"{type(diagnostic_error).__name__}"
                        )
                raise
            finally:
                try:
                    with self.cleanup_mode(), self.timing.span("teardown.android_home"):
                        self.return_to_android_home(app_package)
                except Exception as home_error:
                    if active_error is None:
                        raise
                    active_error.add_note(f"Android Home cleanup also failed: {home_error}")

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
        root: ET.Element | None = None,
    ) -> ET.Element:
        root = root if root is not None else self.hierarchy()
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
        require_aliases(self.student_aliases)
        remote = "/sdcard/khan-kids-window.xml"
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                self._check_cancelled()
                with self.timing.span("ui.hierarchy"):
                    if self.ui_backend is None:
                        self.command("shell", "uiautomator", "dump", remote, timeout=60)
                        raw = self.command("exec-out", "cat", remote, timeout=20, capture=True)
                    else:
                        raw = self.ui_backend.dump_hierarchy()
                    self._check_cancelled()
                    root = ET.fromstring(raw)
                    if self.student_aliases:
                        for node in root.iter():
                            for key, value in node.attrib.items():
                                node.set(key, anonymize_text(value, self.student_aliases))
                            for field in ("text", "tail"):
                                value = getattr(node, field)
                                if value:
                                    setattr(
                                        node, field, anonymize_text(value, self.student_aliases)
                                    )
                        validate_identity_view(root, self.student_aliases)
                        raw = ET.tostring(root, encoding="utf-8")
                return (root, raw) if include_raw else root
            except (ET.ParseError, UiBackendError, AutomationError) as error:
                last_error = error
                time.sleep(0.25 * (attempt + 1))
        raise AutomationError(
            f"Could not obtain a valid UI hierarchy after {attempts} attempts"
        ) from last_error

    def screenshot(self, destination: Path) -> None:
        private = Path(__file__).resolve().parents[2] / "private"
        if not destination.resolve().is_relative_to(private.resolve()):
            raise ValueError("Raw screenshots must stay under the repository private directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch(mode=0o600, exist_ok=True)
        destination.chmod(0o600)
        destination.write_bytes(
            self.command("exec-out", "screencap", "-p", timeout=20, capture=True)
        )

    @contextmanager
    def private_screenshot(self) -> Iterator[Path]:
        """Capture for one image check, cleaning up even when capture or analysis fails."""
        private = Path(__file__).resolve().parents[2] / "private"
        private.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="screenshot-", dir=private) as temporary:
            path = Path(temporary) / "screen.png"
            self.screenshot(path)
            yield path


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
