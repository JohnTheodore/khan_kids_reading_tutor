"""State-aware, secret-safe startup for Khan Academy Kids."""

from __future__ import annotations

import getpass
import json
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .adb import AndroidDevice, AutomationError
from .constants import KHAN_KIDS_ACTIVITY, KHAN_KIDS_PACKAGE

PinProvider = Callable[[], str]
SecretsProvider = Callable[[], "LocalSecrets"]
DEFAULT_SECRETS_PATH = Path(".secrets.json")


@dataclass(frozen=True, slots=True)
class LocalSecrets:
    android_pin: str = field(repr=False)
    khan_parent_password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class LaunchResult:
    unlocked: bool
    launched: bool


def ensure_khan_kids_open(
    device: AndroidDevice, *, pin_provider: PinProvider | None = None
) -> LaunchResult:
    """Wake, unlock once if necessary, and verify Khan Kids is foreground."""
    device.wake()
    unlocked = False
    if device.is_locked():
        if pin_provider is None:
            raise AutomationError("Tablet is PIN-locked; a PIN provider is required")
        device.unlock_with_pin(pin_provider())
        if device.is_locked():
            raise AutomationError("Tablet remained locked after one PIN attempt; refusing to retry")
        unlocked = True

    launched = device.foreground_package() != KHAN_KIDS_PACKAGE
    if launched:
        device.start_activity(KHAN_KIDS_ACTIVITY)
    if device.foreground_package() != KHAN_KIDS_PACKAGE:
        raise AutomationError("Khan Kids did not become the foreground app")
    return LaunchResult(unlocked=unlocked, launched=launched)


def local_secrets_provider(secrets_file: Path | None) -> SecretsProvider:
    """Return one lazy, cached credential reader for a complete run."""
    selected = secrets_file or DEFAULT_SECRETS_PATH
    cached: LocalSecrets | None = None

    def provide() -> LocalSecrets:
        nonlocal cached
        if cached is not None:
            return cached
        if selected.exists() or secrets_file is not None:
            cached = read_local_secrets(selected)
            return cached
        if not sys.stdin.isatty():
            raise AutomationError(
                "Credentials are required; create .secrets.json or use --secrets-file"
            )
        cached = LocalSecrets(
            android_pin=getpass.getpass("Android PIN: "),
            khan_parent_password=getpass.getpass("Khan parent password: "),
        )
        return cached

    return provide


def pin_provider(secrets_file: Path | None) -> PinProvider:
    """Compatibility helper returning only the lazy Android PIN."""
    provide = local_secrets_provider(secrets_file)
    return lambda: provide().android_pin


def read_local_secrets(path: Path = DEFAULT_SECRETS_PATH) -> LocalSecrets:
    """Read the expected values from an owner-private local JSON file."""
    if path.is_symlink():
        raise AutomationError(f"Secrets file must not be a symbolic link: {path}")
    try:
        metadata = path.stat()
    except FileNotFoundError as error:
        raise AutomationError(f"Secrets file does not exist: {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise AutomationError(f"Secrets file is not a regular file: {path}")
    if metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise AutomationError(f"Secrets file permissions must be 0600 or stricter: {path}")
    try:
        payload = json.loads(path.read_text())
        android_pin = payload["android_pin"]
        parent_password = payload["khan_parent_password"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise AutomationError(f"Secrets file has an invalid schema: {path}") from error
    if not isinstance(android_pin, str) or not isinstance(parent_password, str):
        raise AutomationError(f"Secrets file values must be strings: {path}")
    if not android_pin or not parent_password:
        raise AutomationError(f"Secrets file values must not be empty: {path}")
    return LocalSecrets(android_pin=android_pin, khan_parent_password=parent_password)
