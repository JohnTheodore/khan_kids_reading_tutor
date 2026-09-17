#!/usr/bin/env python3
"""Read-only crawler for Khan Kids teacher lesson-library screens.

The crawler deliberately interacts only with top navigation, the grade selector,
and a blank area at the far right for scrolling. It never taps lesson cards.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from khan_kids.adb import AndroidDevice, prepare_capture_workspace
from khan_kids.constants import GRADE_NAMES
from khan_kids.ui import visible_nodes

TAB_POINTS = {"Letters": (1100, 270), "Reading": (1280, 270)}
GRADE_SELECTOR_POINT = (2050, 438)
SCROLL_X = 2320  # Deliberately outside the five lesson-card columns.


def lesson_signature(root: ET.Element) -> tuple[tuple[str, str], ...]:
    # Ignore the fixed header. Comparing content text and bounds detects the end.
    return tuple(
        (item.text, item.rect.as_bounds()) for item in visible_nodes(root) if item.rect.top >= 385
    )


def select_tab(device: AndroidDevice, tab: str) -> None:
    device.tap(*TAB_POINTS[tab])
    time.sleep(3)


def select_grade(device: AndroidDevice, grade: str, scratch: Path) -> None:
    device.scroll_to_top(SCROLL_X)
    device.tap(*GRADE_SELECTOR_POINT)
    time.sleep(1)
    menu_root = device.dump(scratch / "grade-menu.xml")
    matches = [
        item
        for item in visible_nodes(menu_root)
        if item.text == grade and item.rect.center[1] >= 480
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one menu option for {grade!r}, found {len(matches)}")
    device.tap_rect(matches[0].rect)
    time.sleep(3)


def crawl_grade(
    device: AndroidDevice, tab: str, grade: str, output: Path, *, resume: bool = False
) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", grade.lower()).strip("-")
    grade_dir = output / tab.lower() / slug
    grade_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = grade_dir / "manifest.json"
    pages: list[dict] = []
    prior_signature: tuple[tuple[str, str], ...] | None = None
    if resume and manifest_path.exists():
        pages = json.loads(manifest_path.read_text())["pages"]
        if pages:
            prior_root = ET.parse(grade_dir / f"page-{len(pages) - 1:03d}.xml").getroot()
            prior_signature = lesson_signature(prior_root)

    for page_number in range(len(pages), 80):
        xml_path = grade_dir / f"page-{page_number:03d}.xml"
        png_path = grade_dir / f"page-{page_number:03d}.png"
        root = device.dump(xml_path)
        text = visible_nodes(root)
        if tab not in {item.text for item in text}:
            # Tab labels are graphical in some states; verify using curriculum labels too.
            curriculum_markers = (
                "Reading",
                "Letter",
                "Lowercase",
                "Uppercase",
                "Alphabet",
                "Phonics",
            )
            has_named_marker = any(
                any(marker in item.text for marker in curriculum_markers) for item in text
            )
            has_curriculum_standard = any(item.text.startswith("CCSS.ELA.RF.") for item in text)
            if not (has_named_marker or has_curriculum_standard):
                raise RuntimeError(f"Unexpected screen while crawling {tab} / {grade}")
        if grade != "All" and grade not in {item.text for item in text}:
            raise RuntimeError(f"Grade selector no longer shows {grade!r}")

        signature = lesson_signature(root)
        if signature == prior_signature:
            # This duplicate is useful for proving that the bottom was reached, but it
            # is omitted from the manifest and removed from the retained raw pages.
            xml_path.unlink(missing_ok=True)
            png_path.unlink(missing_ok=True)
            break

        pages.append(
            {
                "page": page_number,
                "text": [{"text": item.text, "bounds": item.rect.as_bounds()} for item in text],
            }
        )
        prior_signature = signature
        manifest_path.write_text(
            json.dumps({"tab": tab, "grade": grade, "pages": pages}, indent=2) + "\n"
        )
        # A slow, short gesture prevents fling acceleration and keeps at least
        # one full row of overlap between captures.
        device.swipe(SCROLL_X, 1350, SCROLL_X, 800, 900)
        time.sleep(1)
    else:
        raise RuntimeError(f"Did not reach bottom within 50 pages: {tab} / {grade}")

    return {"tab": tab, "grade": grade, "page_count": len(pages)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--tab", choices=tuple(TAB_POINTS), required=True)
    parser.add_argument("--output", type=Path, default=Path("data/raw"))
    parser.add_argument("--grade", choices=GRADE_NAMES, action="append")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    device, output, scratch = prepare_capture_workspace(args.serial, args.output)
    if not args.resume:
        select_tab(device, args.tab)
    if args.tab == "Letters":
        if args.grade:
            parser.error("The Letters tab has no grade selector")
        requested_grades = ("All",)
        if not args.resume:
            device.scroll_to_top(SCROLL_X)
    else:
        requested_grades = tuple(args.grade) if args.grade else GRADE_NAMES

    summaries = []
    for grade in requested_grades:
        if grade != "All" and not args.resume:
            select_grade(device, grade, scratch)
        summary = crawl_grade(device, args.tab, grade, output, resume=args.resume)
        summaries.append(summary)
        print(json.dumps(summary), flush=True)

    (output / f"{args.tab.lower()}-crawl-summary.json").write_text(
        json.dumps(summaries, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
