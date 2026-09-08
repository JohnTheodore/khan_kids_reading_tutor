#!/usr/bin/env python3
"""Read-only crawler for Khan Kids teacher lesson-library screens.

The crawler deliberately interacts only with top navigation, the grade selector,
and a blank area at the far right for scrolling. It never taps lesson cards.
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
    "Preschool (Age 2)",
    "Preschool (Age 3)",
    "Preschool (Age 4)",
    "Kindergarten",
    "1st Grade",
    "2nd Grade",
)

TAB_POINTS = {"Letters": (1100, 270), "Reading": (1280, 270)}
GRADE_SELECTOR_POINT = (2050, 438)
SCROLL_X = 2320  # Deliberately outside the five lesson-card columns.


def run(*args: str, capture: bool = False, timeout: int = 30) -> str:
    result = subprocess.run(
        args,
        check=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
    )
    return result.stdout.decode("utf-8", errors="replace") if capture else ""


class Device:
    def __init__(self, serial: str) -> None:
        self.serial = serial

    def adb(self, *args: str, capture: bool = False, timeout: int = 30) -> str:
        return run("adb", "-s", self.serial, *args, capture=capture, timeout=timeout)

    def tap(self, x: int, y: int) -> None:
        self.adb("shell", "input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 450) -> None:
        self.adb(
            "shell",
            "input",
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(ms),
        )

    def dump(self, destination: Path) -> ET.Element:
        remote = "/sdcard/khan-catalog-window.xml"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                self.adb("shell", "uiautomator", "dump", remote, timeout=60)
                self.adb("pull", remote, str(destination), timeout=20)
                return ET.parse(destination).getroot()
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ET.ParseError) as error:
                last_error = error
                time.sleep(2 + attempt * 2)
        raise RuntimeError("Could not obtain a valid UI hierarchy after 3 attempts") from last_error

    def screenshot(self, destination: Path) -> None:
        result = subprocess.run(
            ["adb", "-s", self.serial, "exec-out", "screencap", "-p"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        destination.write_bytes(result.stdout)


def nodes(root: ET.Element) -> list[dict[str, str]]:
    return [dict(node.attrib) for node in root.iter("node")]


def visible_text(root: ET.Element) -> list[dict[str, str]]:
    return [
        {"text": node.attrib["text"].strip(), "bounds": node.attrib.get("bounds", "")}
        for node in root.iter("node")
        if node.attrib.get("text", "").strip()
    ]


def center(bounds: str) -> tuple[int, int]:
    values = [int(value) for value in re.findall(r"\d+", bounds)]
    if len(values) != 4:
        raise ValueError(f"Invalid bounds: {bounds!r}")
    x1, y1, x2, y2 = values
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def lesson_signature(root: ET.Element) -> tuple[tuple[str, str], ...]:
    # Ignore the fixed header. Comparing content text and bounds detects the end.
    return tuple(
        (item["text"], item["bounds"])
        for item in visible_text(root)
        if (numbers := re.findall(r"\d+", item["bounds"])) and int(numbers[1]) >= 385
    )


def select_tab(device: Device, tab: str) -> None:
    device.tap(*TAB_POINTS[tab])
    time.sleep(3)


def scroll_to_top(device: Device) -> None:
    # Give the app a moment between gestures. Back-to-back gestures can be
    # coalesced while the React Native list is still settling.
    for _ in range(30):
        device.swipe(SCROLL_X, 520, SCROLL_X, 1450, 250)
        time.sleep(0.08)
    time.sleep(1)


def select_grade(device: Device, grade: str, scratch: Path) -> None:
    scroll_to_top(device)
    device.tap(*GRADE_SELECTOR_POINT)
    time.sleep(1)
    menu_root = device.dump(scratch / "grade-menu.xml")
    matches = [
        node
        for node in menu_root.iter("node")
        if node.attrib.get("text", "").strip() == grade
        and center(node.attrib.get("bounds", ""))[1] >= 480
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one menu option for {grade!r}, found {len(matches)}")
    device.tap(*center(matches[0].attrib["bounds"]))
    time.sleep(3)


def crawl_grade(
    device: Device, tab: str, grade: str, output: Path, *, resume: bool = False
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
        text = visible_text(root)
        if tab not in {item["text"] for item in text}:
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
                any(marker in item["text"] for marker in curriculum_markers)
                for item in text
            )
            has_curriculum_standard = any(
                item["text"].startswith("CCSS.ELA.RF.") for item in text
            )
            if not (has_named_marker or has_curriculum_standard):
                raise RuntimeError(f"Unexpected screen while crawling {tab} / {grade}")
        if grade != "All" and grade not in {item["text"] for item in text}:
            raise RuntimeError(f"Grade selector no longer shows {grade!r}")

        device.screenshot(png_path)
        signature = lesson_signature(root)
        if signature == prior_signature:
            # This duplicate is useful for proving that the bottom was reached, but it
            # is omitted from the manifest and removed from the retained raw pages.
            xml_path.unlink(missing_ok=True)
            png_path.unlink(missing_ok=True)
            break

        pages.append({"page": page_number, "text": text})
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
    parser.add_argument("--grade", choices=GRADES, action="append")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    device = Device(args.serial)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    scratch = output / ".scratch"
    scratch.mkdir(exist_ok=True)
    if not args.resume:
        select_tab(device, args.tab)
    if args.tab == "Letters":
        if args.grade:
            parser.error("The Letters tab has no grade selector")
        requested_grades = ("All",)
        if not args.resume:
            scroll_to_top(device)
    else:
        requested_grades = tuple(args.grade) if args.grade else GRADES

    summaries = []
    for grade in requested_grades:
        if grade != "All":
            if not args.resume:
                select_grade(device, grade, scratch)
        summary = crawl_grade(device, args.tab, grade, output, resume=args.resume)
        summaries.append(summary)
        print(json.dumps(summary), flush=True)

    (output / f"{args.tab.lower()}-crawl-summary.json").write_text(
        json.dumps(summaries, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
