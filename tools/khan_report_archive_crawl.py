#!/usr/bin/env python3
"""Crawl Khan Kids Class Report: All Progress for every ELA grade.

Only the grade filter, disclosure triangles, and blank score-table space are
touched. Student progress is not modified and is deliberately omitted from the
manifest; raw screenshots/XML remain available as capture evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path


GRADES = (
    ("preschool-age-2", 580),
    ("preschool-age-3", 688),
    ("preschool-age-4", 796),
    ("kindergarten", 905),
    ("1st-grade", 1013),
    ("2nd-grade", 1112),
)
VARIANTS = {"Main", "Practice 1", "Practice 2", "Basic"}


def run(args: list[str], *, timeout: int = 60, capture: bool = False) -> bytes:
    result = subprocess.run(
        args,
        check=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return result.stdout if capture else b""


class Device:
    def __init__(self, serial: str) -> None:
        self.prefix = ["adb", "-s", serial]

    def tap(self, x: int, y: int) -> None:
        run(self.prefix + ["shell", "input", "tap", str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 850) -> None:
        run(
            self.prefix
            + ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms)]
        )

    def dump(self, path: Path) -> ET.Element:
        remote = "/sdcard/khan-report-window.xml"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                run(self.prefix + ["shell", "uiautomator", "dump", remote], timeout=60)
                run(self.prefix + ["pull", remote, str(path)], timeout=20)
                return ET.parse(path).getroot()
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ET.ParseError) as error:
                last_error = error
                time.sleep(2 + 2 * attempt)
        raise RuntimeError("Could not obtain report UI hierarchy") from last_error

    def screenshot(self, path: Path) -> None:
        path.write_bytes(run(self.prefix + ["exec-out", "screencap", "-p"], timeout=20, capture=True))


def box(node: ET.Element) -> tuple[int, int, int, int] | None:
    values = [int(value) for value in re.findall(r"\d+", node.attrib.get("bounds", ""))]
    return tuple(values) if len(values) == 4 else None  # type: ignore[return-value]


def report_rows(root: ET.Element) -> list[dict[str, object]]:
    rows = []
    for node in root.iter("node"):
        text = node.attrib.get("text", "").strip()
        bounds = box(node)
        if not text or bounds is None:
            continue
        x1, y1, x2, y2 = bounds
        if 385 <= y1 < 1600 and x2 <= 570 and x1 >= 90:
            rows.append({"text": text, "bounds": list(bounds), "x": x1, "y": y1})
    # TextViews are unique here, but this keeps the manifest stable if an
    # accessibility wrapper echoes a label in a future app version.
    unique = {(row["text"], tuple(row["bounds"])): row for row in rows}
    return sorted(unique.values(), key=lambda row: (row["y"], row["x"]))


def report_label(root: ET.Element) -> str:
    candidates = []
    for node in root.iter("node"):
        text = node.attrib.get("text", "").strip()
        bounds = box(node)
        if bounds and text.endswith(": ELA") and bounds[0] < 600:
            candidates.append(text)
    if not candidates:
        raise RuntimeError("Expected an ELA All Progress report")
    return candidates[0]


def has_disclosure(image: Path, row: dict[str, object]) -> bool:
    """Return whether the small region left of a row contains an arrow icon."""
    x1, y1, _, y2 = (int(value) for value in row["bounds"])  # type: ignore[union-attr]
    crop_x = x1 - 40
    crop_y = y1 + 5
    crop_h = max(12, y2 - y1 - 10)
    pixels = run(
        ["magick", str(image), "-crop", f"35x{crop_h}+{crop_x}+{crop_y}", "txt:-"],
        timeout=15,
        capture=True,
    ).decode("utf-8", errors="replace")
    dark_pixels = 0
    for match in re.finditer(
        r"srgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", pixels
    ):
        red, green, blue = (int(value) for value in match.groups())
        if red < 180 and green < 190 and blue < 205:
            dark_pixels += 1
    return dark_pixels >= 8


def collapsed_rows(rows: list[dict[str, object]], image: Path) -> list[dict[str, object]]:
    result = []
    for index, row in enumerate(rows):
        x = int(row["x"])
        y = int(row["y"])
        # Domain rows and activity-title rows have disclosure triangles.
        if not (120 <= x <= 145 or 185 <= x <= 215) or y > 1430:
            continue
        if not has_disclosure(image, row):
            continue
        following = next((candidate for candidate in rows[index + 1 :] if int(candidate["y"]) > y), None)
        if following is None:
            continue
        # A greater indentation immediately below proves this row is expanded.
        if int(following["x"]) > x:
            continue
        result.append(row)
    return result


def expand_visible(device: Device, scratch: Path) -> tuple[ET.Element, list[dict[str, object]]]:
    ignored: set[tuple[str, int]] = set()
    for expansion_round in range(12):
        root = device.dump(scratch / "expansion.xml")
        report_label(root)
        rows = report_rows(root)
        expansion_image = scratch / "expansion.png"
        device.screenshot(expansion_image)
        targets = [
            row
            for row in collapsed_rows(rows, expansion_image)
            if (str(row["text"]), int(row["y"])) not in ignored
        ]
        if not targets:
            return root, rows
        before = tuple((row["text"], tuple(row["bounds"])) for row in rows)
        for row in sorted(targets, key=lambda item: int(item["y"]), reverse=True):
            x = int(row["x"]) - 27
            y1, y2 = int(row["bounds"][1]), int(row["bounds"][3])  # type: ignore[index]
            device.tap(x, (y1 + y2) // 2)
            time.sleep(0.12)
        time.sleep(1)
        after_root = device.dump(scratch / "expansion-check.xml")
        after_rows = report_rows(after_root)
        after = tuple((row["text"], tuple(row["bounds"])) for row in after_rows)
        if after == before:
            ignored.update((str(row["text"]), int(row["y"])) for row in targets)
        else:
            ignored.clear()
    raise RuntimeError("Visible rows did not settle after expansion")


def select_grade(device: Device, grade_y: int, scratch: Path) -> str:
    current = device.dump(scratch / "before-grade-modal.xml")
    subject_labels = []
    for node in current.iter("node"):
        text = node.attrib.get("text", "").strip()
        bounds = box(node)
        if text.endswith(": ELA") and bounds and bounds[1] < 385:
            subject_labels.append(bounds)
    if len(subject_labels) != 1:
        raise RuntimeError(f"Expected one subject-filter label, found {len(subject_labels)}")
    x1, y1, x2, y2 = subject_labels[0]
    device.tap(x2 + 38, (y1 + y2) // 2)  # Pencil immediately after the label.
    time.sleep(1)
    modal = device.dump(scratch / "grade-modal.xml")
    modal_text = {node.attrib.get("text", "").strip() for node in modal.iter("node")}
    if "Select Grade & Subject" not in modal_text:
        raise RuntimeError("Grade/subject modal did not open")
    device.tap(730, grade_y)
    device.tap(1280, 1275)  # Done.
    time.sleep(4)
    report = device.dump(scratch / "selected-grade.xml")
    return report_label(report)


def crawl_grade(device: Device, output: Path, slug: str, label: str, scratch: Path) -> dict[str, object]:
    destination = output / slug
    destination.mkdir(parents=True, exist_ok=True)
    pages = []
    prior_signature = None
    for page_number in range(200):
        root, rows = expand_visible(device, scratch)
        label = report_label(root)
        xml_path = destination / f"page-{page_number:03d}.xml"
        png_path = destination / f"page-{page_number:03d}.png"
        # Obtain the settled hierarchy at the final destination and capture its image.
        root = device.dump(xml_path)
        rows = report_rows(root)
        device.screenshot(png_path)
        signature = tuple((row["text"], tuple(row["bounds"])) for row in rows)
        if signature == prior_signature:
            xml_path.unlink(missing_ok=True)
            png_path.unlink(missing_ok=True)
            break
        pages.append({"page": page_number, "rows": rows})
        (destination / "manifest.json").write_text(
            json.dumps({"grade": label, "pages": pages}, indent=2) + "\n"
        )
        prior_signature = signature
        # Swipe inside the blank student-score column, never on a report row.
        device.swipe(850, 1380, 850, 680)
        time.sleep(1)
    else:
        raise RuntimeError(f"Did not reach the bottom for {label}")
    return {"slug": slug, "grade": label, "page_count": len(pages)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/raw-reports/ela"))
    parser.add_argument("--grade", choices=[grade[0] for grade in GRADES], action="append")
    args = parser.parse_args()

    device = Device(args.serial)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    scratch = output / ".scratch"
    scratch.mkdir(exist_ok=True)
    selected = set(args.grade or [grade[0] for grade in GRADES])
    summaries = []
    for slug, y in GRADES:
        if slug not in selected:
            continue
        label = select_grade(device, y, scratch)
        summary = crawl_grade(device, output, slug, label, scratch)
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
    (output / "crawl-summary.json").write_text(json.dumps(summaries, indent=2) + "\n")


if __name__ == "__main__":
    main()
