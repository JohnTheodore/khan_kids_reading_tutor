"""Narrow image checks used before changing graphical React Native controls."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from shutil import which

from .adb import AutomationError, run_command
from .ui import Rect


class CheckboxState(StrEnum):
    CHECKED = "checked"
    UNCHECKED = "unchecked"


@dataclass(frozen=True, slots=True)
class CheckboxReading:
    state: CheckboxState
    dark_fraction: float
    center: tuple[int, int]


def checkbox_center(student_label: Rect) -> tuple[int, int]:
    """Return the checkbox center relative to a roster label in assignment dialogs."""
    return (student_label.left - 190, (student_label.top + student_label.bottom) // 2)


def read_checkbox(
    screenshot: Path,
    student_label: Rect,
    *,
    crop_size: int = 50,
    checked_dark_fraction: float = 0.12,
) -> CheckboxReading:
    """Classify the white checkbox interior; checked interiors contain a dark-blue tick."""
    x, y = checkbox_center(student_label)
    offset = crop_size // 2
    geometry = f"{crop_size}x{crop_size}+{x - offset}+{y - offset}"
    raw = (
        run_command(
            (
                _imagemagick_command(),
                str(screenshot),
                "-crop",
                geometry,
                "+repage",
                "-colorspace",
                "Gray",
                "-threshold",
                "70%",
                "-format",
                "%[fx:1-mean]",
                "info:",
            ),
            timeout=20,
            capture=True,
        )
        .decode()
        .strip()
    )
    try:
        fraction = float(raw)
    except ValueError as error:
        raise AutomationError(
            f"ImageMagick returned an invalid checkbox reading: {raw!r}"
        ) from error
    if 0.05 <= fraction < 0.20:
        raise AutomationError(f"Ambiguous checkbox image at {(x, y)}: dark fraction {fraction:.3f}")
    state = CheckboxState.CHECKED if fraction >= checked_dark_fraction else CheckboxState.UNCHECKED
    return CheckboxReading(state=state, dark_fraction=fraction, center=(x, y))


def _imagemagick_command() -> str:
    command = which("magick") or which("convert")
    if command is None:
        raise AutomationError("ImageMagick is required for checkbox validation")
    return command
