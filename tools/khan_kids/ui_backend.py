"""Optional persistent hierarchy transport backed by uiautomator2."""

from __future__ import annotations

from typing import Protocol


class UiBackendError(RuntimeError):
    """Raised when the optional persistent UI backend is unavailable."""


class UiHierarchyBackend(Protocol):
    name: str

    def dump_hierarchy(self) -> bytes: ...


class UiAutomator2Backend:
    name = "uiautomator2"

    def __init__(self, serial: str) -> None:
        try:
            import uiautomator2 as u2
        except ImportError as error:
            raise UiBackendError("uiautomator2 is not installed; run `uv sync`") from error
        try:
            self._device = u2.connect(serial)
            self._device.jsonrpc.setConfigurator(
                {"waitForIdleTimeout": 100, "waitForSelectorTimeout": 0}
            )
        except Exception as error:
            raise UiBackendError(
                f"uiautomator2 could not connect: {type(error).__name__}"
            ) from error

    def dump_hierarchy(self) -> bytes:
        try:
            hierarchy = self._device.dump_hierarchy(compressed=False, pretty=False)
        except Exception as error:
            raise UiBackendError(
                f"uiautomator2 hierarchy failed: {type(error).__name__}"
            ) from error
        return hierarchy.encode()


def create_ui_backend(serial: str, mode: str) -> UiHierarchyBackend | None:
    if mode == "legacy-adb":
        return None
    if mode != "uiautomator2":
        raise ValueError(f"unknown UI backend: {mode}")
    return UiAutomator2Backend(serial)
