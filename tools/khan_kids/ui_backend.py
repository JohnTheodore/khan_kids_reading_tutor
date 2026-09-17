"""Optional persistent hierarchy transport backed by uiautomator2."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Protocol


class UiBackendError(RuntimeError):
    """Raised when the optional persistent UI backend is unavailable."""


class UiHierarchyBackend(Protocol):
    name: str

    def dump_hierarchy(self) -> bytes: ...


class UiAutomator2Backend:
    name = "uiautomator2"

    def __init__(self, serial: str, *, compressed: bool = False) -> None:
        try:
            import uiautomator2 as u2
        except ImportError as error:
            raise UiBackendError("uiautomator2 is not installed; run `uv sync`") from error
        try:
            self._device = u2.connect(serial)
            self._device.jsonrpc.setConfigurator(
                {"waitForIdleTimeout": 100, "waitForSelectorTimeout": 0}
            )
            self._compressed: bool | None = None if compressed else False
        except Exception as error:
            raise UiBackendError(
                f"uiautomator2 could not connect: {type(error).__name__}"
            ) from error

    def dump_hierarchy(self) -> bytes:
        try:
            if self._compressed is None:
                compressed = self._device.dump_hierarchy(compressed=True, pretty=False)
                full = self._device.dump_hierarchy(compressed=False, pretty=False)
                self._compressed = _control_signature(compressed) == _control_signature(full)
                hierarchy = compressed if self._compressed else full
            else:
                hierarchy = self._device.dump_hierarchy(compressed=self._compressed, pretty=False)
        except Exception as error:
            raise UiBackendError(
                f"uiautomator2 hierarchy failed: {type(error).__name__}"
            ) from error
        return hierarchy.encode()


def _control_signature(xml: str) -> tuple[tuple[tuple[str, str], ...], ...]:
    """Compare text and leaf/control nodes, including unlabeled tap targets."""
    root = ET.fromstring(xml)
    return tuple(
        tuple(sorted(node.attrib.items()))
        for node in root.iter("node")
        if not list(node)
        or node.get("text")
        or node.get("content-desc")
        or node.get("clickable") == "true"
        or node.get("checkable") == "true"
    )


def create_ui_backend(serial: str, mode: str) -> UiHierarchyBackend | None:
    if mode == "legacy-adb":
        return None
    if mode != "uiautomator2":
        raise ValueError(f"unknown UI backend: {mode}")
    return UiAutomator2Backend(serial)
