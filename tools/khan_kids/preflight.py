"""Read-only tablet health checks shared by the dashboard and sync engine."""

from __future__ import annotations

import re

from .adb import LOCK_TASK_NONE, AndroidDevice, AutomationError, lock_task_problem
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
    access = tablet_access_health(device, require_unlocked=require_unlocked)
    if not access["ready"]:
        raise AutomationError(str(access["error"]))
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
    access = tablet_access_health(device)
    checks = list(access["checks"])
    if not access["ready"]:
        return access
    mode = str(access["lock_task_mode"])
    try:
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
        else "Unlock the tablet, turn off app pinning, and restore Internet before retrying.",
        "lock_task_mode": mode,
        "screen_pinning_recovered": bool(access.get("screen_pinning_recovered")),
    }


def tablet_access_health(
    device: AndroidDevice, *, require_unlocked: bool = True
) -> dict[str, object]:
    """Check device access and safely repair ordinary Android screen pinning."""
    checks: list[dict[str, object]] = []
    try:
        device.assert_connected()
        checks.append({"id": "adb", "label": "Tablet connected", "ok": True})
        unlocked = not device.is_locked()
        checks.append({"id": "unlocked", "label": "Tablet unlocked", "ok": unlocked})
        if require_unlocked and not unlocked:
            return {
                "ready": False,
                "checks": checks,
                "error": "Tablet is locked. Unlock it, then check the connection again.",
            }
        screen_pinning_recovered = device.ensure_screen_unpinned()
        mode = LOCK_TASK_NONE
        pinning_off = mode == LOCK_TASK_NONE
        checks.append(
            {
                "id": "screen_pinning",
                "label": "Android app pinning is off",
                "ok": pinning_off,
            }
        )
        return {
            "ready": pinning_off,
            "checks": checks,
            "error": None if pinning_off else lock_task_problem(mode),
            "lock_task_mode": mode,
            "screen_pinning_recovered": screen_pinning_recovered,
        }
    except AutomationError as error:
        if not checks:
            checks.append({"id": "adb", "label": "Tablet connected", "ok": False})
        return {"ready": False, "checks": checks, "error": str(error)}
