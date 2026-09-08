#!/usr/bin/env python3
"""Build a deduplicated Letters catalog from captured Khan Kids UI hierarchies."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


VARIANTS = ("Main", "Practice 1", "Practice 2", "Basic")
STANDARD_DESCRIPTIONS = {
    "CCSS.ELA.RF.K.1.D": "Recognize and name all uppercase and lowercase letters.",
    "CCSS.ELA.RF.K.2.D": (
        "Isolate and pronounce the initial, medial-vowel, and final sounds in "
        "three-phoneme consonant-vowel-consonant words."
    ),
    "CCSS.ELA.RF.K.3.A": (
        "Produce the primary or most frequent sound for each consonant."
    ),
    "CCSS.ELA.RF.K.3.B": (
        "Associate common spellings with the five major short-vowel sounds."
    ),
}


def bounds(node: ET.Element) -> tuple[int, int, int, int] | None:
    values = [int(value) for value in re.findall(r"\d+", node.attrib.get("bounds", ""))]
    return tuple(values) if len(values) == 4 else None  # type: ignore[return-value]


def classify(title: str) -> str:
    if title.startswith("Lowercase "):
        return "Lowercase Letters"
    if title.startswith("Uppercase "):
        return "Uppercase Letters"
    if title.startswith("Beginning Sound "):
        return "Beginning Letter Sounds"
    if title.startswith("Ending Sound "):
        return "Ending Letter Sounds"
    if title.startswith("Short Vowel Sound "):
        return "Short Vowel Sounds"
    if title.startswith("Words with ") or title == "Other Words":
        return "CVC Words"
    return "Other"


def target(title: str, cvc_subsection: str | None) -> str:
    if title.startswith("Lowercase "):
        return f"Recognize and name lowercase {title.removeprefix('Lowercase ')}."
    if title.startswith("Uppercase "):
        return f"Recognize and name uppercase {title.removeprefix('Uppercase ')}."
    if title.startswith("Beginning Sound "):
        letter = title.removeprefix("Beginning Sound ")
        return f"Identify the beginning sound associated with {letter}."
    if title.startswith("Ending Sound "):
        letter = title.removeprefix("Ending Sound ")
        return f"Identify {letter} as the ending sound in spoken words."
    if title.startswith("Short Vowel Sound "):
        vowel = title.removeprefix("Short Vowel Sound ")
        return f"Identify the short-{vowel} vowel sound in spoken words."
    focus = title.removeprefix("Words with ").replace(" & ", ", ")
    if title == "Other Words":
        focus = "other consonants"
    position = {
        "CVC Words - Beginning Sounds": "beginning",
        "CVC Words - Ending Sounds": "ending",
        "CVC Words - Middle Sounds": "middle-vowel",
    }.get(cvc_subsection or "", "target")
    return f"Identify the {position} sound in CVC words featuring {focus}."


def extract(xml_paths: list[Path]) -> tuple[list[tuple[str, str]], dict[str, set[str]], dict[str, str]]:
    cards: list[tuple[str, str]] = []
    standards: dict[str, set[str]] = defaultdict(set)
    cvc_subsections: dict[str, str] = {}
    active_cvc_subsection: str | None = None

    for path in xml_paths:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            # A live crawl may have created the destination just before adb
            # finishes pulling it. Ignore that one in-progress page.
            continue
        visible = [
            (node.attrib.get("text", "").strip(), bounds(node)) for node in root.iter("node")
        ]
        headings = [
            (text, box)
            for text, box in visible
            if text.startswith("CVC Words - ") and box is not None
        ]
        if headings:
            active_cvc_subsection = sorted(headings, key=lambda item: item[1][1])[0][0]

        standard_nodes = [
            (text.rstrip(" +"), box)
            for text, box in visible
            if text.startswith("CCSS.ELA.") and box is not None
        ]
        page_cards: list[tuple[int, int, str, str, tuple[int, int, int, int]]] = []
        for node in root.iter("node"):
            descendant_text = [
                child.attrib.get("text", "").strip()
                for child in node.iter("node")
                if child.attrib.get("text", "").strip()
            ]
            variants = [text for text in descendant_text if text in VARIANTS]
            titles = [
                text
                for text in descendant_text
                if text not in VARIANTS and not text.startswith("CCSS.ELA.")
            ]
            box = bounds(node)
            if (
                len(variants) == 1
                and len(titles) == 1
                and box is not None
                and box[1] >= 385
                and box[2] - box[0] < 350
            ):
                page_cards.append((box[1], box[0], titles[0], variants[0], box))

        for _, _, title, variant, box in sorted(page_cards):
            identity = (title, variant)
            if identity not in cards:
                cards.append(identity)
            if classify(title) == "CVC Words" and active_cvc_subsection:
                cvc_subsections.setdefault(title, active_cvc_subsection)
            center_x = (box[0] + box[2]) / 2
            for standard, standard_box in standard_nodes:
                standard_x = (standard_box[0] + standard_box[2]) / 2
                if abs(standard_x - center_x) < 35 and 0 <= standard_box[1] - box[3] <= 100:
                    standards[title].add(standard)

    return cards, standards, cvc_subsections


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--json", type=Path, default=Path("data/letters-lessons.json"))
    parser.add_argument("--markdown", type=Path, default=Path("letters-lessons.md"))
    args = parser.parse_args()

    xml_paths = sorted(args.source.glob("page-*.xml"))
    cards, standards, cvc_subsections = extract(xml_paths)
    titles = list(dict.fromkeys(title for title, _ in cards))
    records = []
    for title in titles:
        variants = [variant for variant in VARIANTS if (title, variant) in cards]
        records.append(
            {
                "section": classify(title),
                "subsection": cvc_subsections.get(title),
                "title": title,
                "variants": variants,
                "standards": sorted(standards.get(title, set())),
                "pedagogical_target_inferred": target(title, cvc_subsections.get(title)),
            }
        )

    payload = {
        "source": "Khan Academy Kids teacher-mode Letters tab",
        "captured_on": "2026-09-08",
        "android_app_version": "9.0.1 (versionCode 123)",
        "capture_pages": len(xml_paths),
        "unique_titles": len(records),
        "assignable_lesson_cards": sum(len(record["variants"]) for record in records),
        "notes": [
            "The app displays titles, variants, and standards, but no prose lesson descriptions in the library.",
            "Pedagogical targets are concise inferences from the displayed title and standard, not official app descriptions.",
            "A plus sign beside a standard in the UI indicates more standards; only the visible standard was captured.",
        ],
        "standard_descriptions": STANDARD_DESCRIPTIONS,
        "lessons": records,
        "cards": [
            {
                "section": record["section"],
                "subsection": record["subsection"],
                "title": record["title"],
                "variant": variant,
                "standards": record["standards"],
                "pedagogical_target_inferred": record["pedagogical_target_inferred"],
            }
            for record in records
            for variant in record["variants"]
        ],
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2) + "\n")

    lines = [
        "# Khan Academy Kids — Letters lesson catalog",
        "",
        f"Captured on **2026-09-08** from **{len(xml_paths)} overlapping screens** in the "
        "teacher-mode Letters tab of Android app **9.0.1 (versionCode 123)**.",
        f"The catalog contains **{len(records)} unique lesson titles** and "
        f"**{payload['assignable_lesson_cards']} assignable lesson cards** when variants are counted.",
        "",
        "The library itself exposes lesson titles, activity variants, and standards, but no prose descriptions. "
        "The ‘Target’ column is therefore an explicitly inferred pedagogical description based on the visible title "
        "and standard. A `+` in the app means additional standards exist, although only the visible code is recorded.",
        "",
        "## Standards shown",
        "",
    ]
    for code, description in STANDARD_DESCRIPTIONS.items():
        lines.append(f"- `{code}` — {description}")

    current_section = None
    current_subsection = None
    for record in records:
        if record["section"] != current_section:
            current_section = record["section"]
            current_subsection = None
            lines.extend(["", f"## {current_section}", ""])
        if record["subsection"] and record["subsection"] != current_subsection:
            current_subsection = record["subsection"]
            lines.extend([f"### {current_subsection}", ""])
        variants = ", ".join(record["variants"])
        standard = ", ".join(f"`{code}`" for code in record["standards"]) or "Not visible"
        lesson_heading = "####" if record["subsection"] else "###"
        lines.extend(
            [
                f"{lesson_heading} {record['title']}",
                "",
                f"- Variants: {variants}",
                f"- Standard shown: {standard}",
                f"- Target (inferred): {record['pedagogical_target_inferred']}",
                "",
            ]
        )
    args.markdown.write_text("\n".join(lines).rstrip() + "\n")
    print(json.dumps({key: payload[key] for key in ("capture_pages", "unique_titles", "assignable_lesson_cards")}))


if __name__ == "__main__":
    main()
