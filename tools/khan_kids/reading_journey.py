"""Read-only reading coverage analytics; app scores are not reading-level tests.

One catalog title is one lesson family, regardless of practice variants or grade
placements. A family has mastery evidence when a non-Basic scored variant has
met the existing engine policy at some point. This is a coverage proxy, not a
claim that every underlying skill is mastered outside the app.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from .catalog import CatalogIndex
from .manual_assignments import ManualAssignments, policy_path
from .mastery import evaluate_mastery
from .records import read_attempt_records, read_mastered_action_keys
from .student_identity import public_student

ARCHIVE_HISTORY_FIELDS = (
    "student",
    "subject",
    "lesson_title",
    "activity_variant",
    "normalized_date",
    "date_resolution",
    "score_percent",
    "occurrence",
)
# Exact report headings, maintained in one place. Catalog titles carry their
# original context into drill-down; no title-keyword guessing or duplicate rules.
MILESTONES = (
    ("lowercase", "Lowercase letters", "Recognize a–z", ("Lowercase Letters",)),
    ("uppercase", "Uppercase letters", "Recognize A–Z", ("Uppercase Letters",)),
    (
        "sounds",
        "Letters & sounds",
        "Connect letters to their sounds",
        (
            "Beginning Letter Sounds",
            "Ending Letter Sounds",
            "Phonics: Letters & Sounds",
            "Phonics: Extra Letter & Sounds Practice",
        ),
    ),
    (
        "blending",
        "Hear, blend & segment",
        "Work with sounds in spoken words",
        (
            "Phonological Awareness: Rhyming",
            "Phonological Awareness: Syllables",
            "Phonological Awareness: Onset & Rime",
            "Phonological Awareness: Three-Phoneme Words",
            "Phonological Awareness: Add Phonemes",
        ),
    ),
    (
        "cvc",
        "Short vowels & CVC",
        "Read words like cat, sit and dog",
        (
            "Short Vowel Sounds",
            "CVC Words - Beginning Sounds",
            "CVC Words - Ending Sounds",
            "CVC Words - Middle Sounds",
            "Beginning, Middle & Ending Sounds",
        ),
    ),
    ("blends", "Consonant blends", "Blend sounds in words like stop", ("Consonant Blends",)),
    ("digraphs", "Consonant digraphs", "Read pairs like sh, ch and th", ("Consonant Digraphs",)),
    (
        "vowels",
        "Long vowels & vowel teams",
        "Explore silent-e, vowel teams and diphthongs",
        ("Short & Long Vowels", "Final -e & Long Vowel Teams", "Vowel Sounds"),
    ),
    (
        "r-vowels",
        "R-controlled vowels",
        "Read ar, or, er, ir and ur",
        ("r-Controlled Vowels (ar, or, er, ir, ur)", "r-Controlled Vowels"),
    ),
    (
        "longer",
        "Longer words & word parts",
        "Use syllables, endings, prefixes and suffixes",
        ("1-Syllable & 2-Syllable Words", "Inflectional Endings -ing & -ed", "Prefixes & Suffixes"),
    ),
    ("sight", "High-frequency words", "Recognize common and irregular words", ("Sight Words",)),
    (
        "fluency",
        "Connected reading",
        "Practice reading text accurately and smoothly",
        ("Reading Fluency",),
    ),
    (
        "meaning",
        "Understand what you read",
        "Retell, answer questions and use text evidence",
        (
            "Key Ideas & Details",
            "Integrating Text & Illustrations",
            "Words & Structure",
            "Dialogue & Perspective",
            "Define Unknown Words",
        ),
    ),
)


@lru_cache(maxsize=4)
def catalog(root: Path, archive_stamp: int, letters_stamp: int) -> tuple[dict, ...]:
    """Deduplicate placements and collect every activity's original contexts."""
    del archive_stamp, letters_stamp  # Cache identity includes both file revisions.
    archive = json.loads((root / "data/reading-ela-archive.json").read_text())
    headings = {heading: item[0] for item in MILESTONES for heading in item[3]}
    families: dict[str, dict] = {}
    for grade in archive["grades"]:
        for lesson in grade["lesson_placements"]:
            milestone = headings.get(lesson["skill_group"])
            if not milestone:
                continue
            family = families.setdefault(
                lesson["title"],
                {
                    "title": lesson["title"],
                    "milestones": set(),
                    "contexts": set(),
                    "variants": set(),
                    "saved_results": defaultdict(set),
                },
            )
            family["milestones"].add(milestone)
            family["contexts"].add((grade["grade"], lesson["skill_group"]))
            family["variants"].update(a["variant"] for a in lesson["activities"])
            for activity in lesson["activities"]:
                for student, result in activity.get("results", {}).items():
                    score = result.get("percent")
                    if (
                        result.get("status") == "scored"
                        and type(score) is int
                        and 0 <= score <= 100
                    ):
                        family["saved_results"][public_student(student), activity["variant"]].add(
                            score
                        )
    # The letters catalog supplements the archive, not a second denominator.
    for lesson in json.loads((root / "data/letters-lessons.json").read_text())["lessons"]:
        if lesson["title"] in families:
            families[lesson["title"]]["variants"].update(lesson["variants"])
    return tuple(
        {
            **f,
            "milestones": sorted(f["milestones"]),
            "contexts": [{"grade": g, "skill": s} for g, s in sorted(f["contexts"])],
            "variants": sorted(f["variants"]),
            "saved_results": dict(f["saved_results"]),
            "snapshot_date": archive.get("captured_on"),
        }
        for f in sorted(families.values(), key=lambda x: x["title"])
    )


def _archive_attempts(path: Path, student: str) -> list[dict]:
    """Adapt an explicit local history export, preserving date uncertainty.

    Exports repeat histories in multiple report placements. Their occurrence
    numbers distinguish identical attempts; deduplication removes placements,
    never repeated occurrences. Dialogs list newest first.
    """
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = set(ARCHIVE_HISTORY_FIELDS)
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Unsupported history export")
        rows = list(reader)
    unique = {}
    identities = {}
    for order, row in enumerate(rows):
        if row["subject"] != "ela":
            continue
        name = row["student"]
        if name not in identities:
            identities[name] = public_student(name)
        if identities[name] != student:
            continue
        if not row["score_percent"]:
            # Viewed/listened/book exposure is not a scored reading attempt.
            continue
        score = int(row["score_percent"])
        if not 0 <= score <= 100:
            raise ValueError("Attempt score outside 0–100")
        day = date.fromisoformat(row["normalized_date"]) if row["normalized_date"] else None
        confidence = "inferred" if "year_not_displayed" in row["date_resolution"] else "exact"
        if day is None:
            confidence = "unknown"
        key = (row["lesson_title"], row["activity_variant"], day, score, row["occurrence"])
        unique.setdefault(
            key,
            {
                "title": row["lesson_title"],
                "variant": row["activity_variant"],
                "date": day.isoformat() if day else None,
                "score": score,
                "date_confidence": confidence,
                "order": -order,
            },
        )
    return sorted(unique.values(), key=lambda a: (a["date"] or "", a["order"]))


def _native_attempts(path: Path, student: str) -> list[dict]:
    return [
        {
            "title": r["lesson_title"],
            "variant": r["activity_variant"],
            "date": r["attempt_date"],
            "score": int(r["score_percent"]),
            "date_confidence": "exact",
            "order": n,
        }
        for n, r in enumerate(read_attempt_records(path, student))
    ]


def _evidence(attempts: list[dict], durable: bool, snapshots: list[dict] | None = None) -> dict:
    """Monotonic evidence using the engine's policy, not a parallel grader."""
    scores = []
    chronological = []
    first = None
    qualified = False
    for attempt in attempts:
        scores.append(attempt["score"])
        # Unknown chronological placement cannot establish a consecutive pair.
        if not attempt["date"]:
            chronological.clear()
            decision = evaluate_mastery([attempt["score"]])
        else:
            chronological.append(attempt["score"])
            decision = evaluate_mastery(chronological)
        if decision.should_advance and not qualified:
            qualified, first = True, attempt
    decision = evaluate_mastery(chronological or scores[-1:])
    snapshots = snapshots or []
    snapshot_mastery = [s for s in snapshots if evaluate_mastery([s["score"]]).should_advance]
    if snapshot_mastery and (
        not first
        or not first["date"]
        or any(s["captured_on"] <= first["date"] for s in snapshot_mastery)
    ):
        first = None  # Capture date is not a lesson date; do not invent weekly growth.
    qualified = qualified or bool(snapshot_mastery)
    return {
        "state": "mastered"
        if qualified or durable
        else "practicing"
        if scores or snapshots
        else "not_assessed",
        "scores": scores,
        "archived_scores": snapshots,
        "attempts": [{k: v for k, v in a.items() if k != "order"} for a in attempts],
        "reason": "Mastery was previously verified by the sync engine."
        if durable and not first
        else "Mastery evidence: 100% once or two consecutive scores ≥90%."
        if qualified
        else "Saved All Progress score; attempt dates and consecutive history are unavailable."
        if snapshots and not scores
        else decision.reason,
        "first_mastery_date": first["date"] if first else None,
        "first_mastery_score": first["score"] if first else None,
        "first_mastery_confidence": first["date_confidence"] if first else None,
    }


def build_journey(
    root: Path,
    student: str,
    *,
    profile: dict | None = None,
    today: date | None = None,
    report: dict | None = None,
) -> dict:
    """Explain recorded coverage without writing records or touching Android."""
    today = today or date.today()
    profile = profile or {}
    slug = student.casefold().replace(" ", "-")
    path = Path(profile.get("attempts", root / f"student-records/{slug}-lesson-attempts.csv"))
    if not path.is_absolute():
        path = root / path
    archived = bool(profile.get("archived"))
    result = {
        "student": student,
        "archived": archived,
        "as_of": today.isoformat(),
        "available": False,
        "milestones": [],
        "weekly_attempts": 0,
        "weekly_mastered": 0,
        "mastered_families": 0,
        "total_families": 0,
        "last_lesson_date": None,
        "forecast": {
            "available": False,
            "reason": "App scores alone cannot predict second-grade reading. A complete route and reading checks are not yet available.",
        },
        "reading_level": "Not assessed",
        "recommendations": [],
        "warnings": [],
    }
    try:
        attempts = (
            (_archive_attempts if profile.get("format") == "archive" else _native_attempts)(
                path, student
            )
            if path.is_file()
            else []
        )
        durable = (
            set()
            if archived
            else read_mastered_action_keys(
                root / f"student-records/{slug}-assignment-actions.csv", student
            )
        )
        archive_path, letters_path = (
            root / "data/reading-ela-archive.json",
            root / "data/letters-lessons.json",
        )
        families = catalog(root, archive_path.stat().st_mtime_ns, letters_path.stat().st_mtime_ns)
    except (OSError, ValueError, KeyError, TypeError):
        result["warnings"].append(
            "Reading records could not be validated. Check the local profile configuration; no progress was inferred."
        )
        return result
    use_snapshots = not profile and not archived

    def snapshots_for(family: dict, variant: str) -> list[dict]:
        captured = family["snapshot_date"]
        if not use_snapshots or not captured or captured > today.isoformat():
            return []
        return [
            {"score": score, "captured_on": captured}
            for score in sorted(family["saved_results"].get((student, variant), []))
        ]

    if not path.is_file() and not any(snapshots_for(f, v) for f in families for v in f["variants"]):
        result["warnings"].append(
            "No scored history is configured for this reader. Missing records do not mean no learning."
        )
        return result
    grouped = defaultdict(list)
    if any(a["date"] and a["date"] > today.isoformat() for a in attempts):
        result["warnings"].append(
            "Future-dated records were excluded; check their date resolution before relying on them."
        )
        attempts = [a for a in attempts if not a["date"] or a["date"] <= today.isoformat()]
    known_keys = {(f["title"], variant) for f in families for variant in f["variants"]}
    result["unmapped_attempts"] = sum(
        (a["title"], a["variant"]) not in known_keys for a in attempts
    )
    attempts = [a for a in attempts if (a["title"], a["variant"]) in known_keys]
    for attempt in attempts:
        grouped[attempt["title"], attempt["variant"]].append(attempt)
    start = today - timedelta(days=6)
    result["available"] = True
    result["weekly_start"] = start.isoformat()
    result["weekly_attempts"] = sum(
        a["date_confidence"] == "exact"
        and a["date"] is not None
        and start.isoformat() <= a["date"] <= today.isoformat()
        for a in attempts
    )
    dated = [a["date"] for a in attempts if a["date"]]
    result["last_lesson_date"] = max(dated, default=None)
    result["last_lesson_inferred"] = any(
        a["date"] == result["last_lesson_date"] and a["date_confidence"] != "exact"
        for a in attempts
    )
    result["captured_on"] = profile.get("captured_on") or (report or {}).get("timestamp")
    assigned_keys = {(a.get("title"), a.get("variant")) for a in (report or {}).get("assigned", [])}
    queue_known = not archived and (report or {}).get("queue_count") is not None
    result["assignment_verified_at"] = (report or {}).get("timestamp") if queue_known else None
    placements = CatalogIndex(root / "data/reading-ela-archive.json")
    try:
        policy = ManualAssignments.load(policy_path(root, student), student, placements)
    except (OSError, ValueError, RuntimeError):
        policy = ManualAssignments(student)
        result["warnings"].append(
            "Manual assignment preferences could not be read. Sync must validate them before changing the queue."
        )
    pins = {a.key: a for a in policy.assigned}

    def assignment_info(title: str, variant: str) -> dict:
        key = (title, variant)
        matches = [e for e in placements.entries if e.title == title and variant in e.variants]
        return {
            "grade": pins[key].grade if key in pins else matches[0].grade if matches else None,
            "assignment_status": "unknown"
            if not queue_known
            else "assigned"
            if key in assigned_keys
            else "unassigned",
            "manual_assignment": key in pins,
            "automatic_assignment_paused": key in policy.excluded_keys,
        }

    if any(a["date_confidence"] != "exact" for a in attempts):
        result["warnings"].append(
            "Some history dates have inferred years or no date. They are labeled in the evidence and excluded from exact weekly gains."
        )
    indexed = []
    for family in families:
        activities = [
            {
                "variant": variant,
                **assignment_info(family["title"], variant),
                **_evidence(
                    grouped[family["title"], variant],
                    (family["title"], variant) in durable,
                    snapshots_for(family, variant),
                ),
            }
            for variant in family["variants"]
        ]
        qualifying = [a for a in activities if a["variant"] != "Basic" and a["state"] == "mastered"]
        state = (
            "mastered"
            if qualifying
            else "practicing"
            if any(a["scores"] or a["archived_scores"] for a in activities)
            else "not_assessed"
        )
        first_dates = [a["first_mastery_date"] for a in qualifying if a["first_mastery_date"]]
        first_date = min(first_dates, default=None)
        weekly = bool(
            first_date
            and start.isoformat() <= first_date <= today.isoformat()
            and all(a["first_mastery_confidence"] == "exact" for a in qualifying)
            and all(a["first_mastery_date"] for a in qualifying)
        )
        indexed.append(
            {
                **{k: v for k, v in family.items() if k not in {"saved_results", "snapshot_date"}},
                "activities": activities,
                "state": state,
                "weekly_gain": weekly,
            }
        )
    result["mastered_families"] = sum(f["state"] == "mastered" for f in indexed)
    result["total_families"] = len(indexed)
    result["weekly_mastered"] = sum(f["weekly_gain"] for f in indexed)
    active = None
    for key, title, description, _headings in MILESTONES:
        items = [f for f in indexed if key in f["milestones"]]
        mastered = sum(f["state"] == "mastered" for f in items)
        practicing = sum(f["state"] == "practicing" for f in items)
        state = (
            "mastered"
            if items and mastered == len(items)
            else "practicing"
            if practicing or mastered
            else "not_assessed"
        )
        if active is None and practicing:
            active = title
        result["milestones"].append(
            {
                "id": key,
                "title": title,
                "description": description,
                "state": state,
                "mastered": mastered,
                "total": len(items),
                "weekly_gain": sum(f["weekly_gain"] for f in items),
                "lessons": items,
            }
        )
    recommendations = [] if archived else (report or {}).get("recommendations", [])
    if recommendations:
        candidate = next(
            (f for f in indexed if f["title"] == recommendations[0].get("title")), None
        )
        if candidate:
            active = next(
                m["title"] for m in result["milestones"] if m["id"] in candidate["milestones"]
            )
        next_titles = {item.get("title") for item in recommendations}
        for milestone in result["milestones"]:
            if milestone["state"] == "not_assessed" and any(
                f["title"] in next_titles for f in milestone["lessons"]
            ):
                milestone["state"] = "next"
    result["current_focus"] = active or "No active scored practice recorded"
    result["recommendations"] = [] if archived else (report or {}).get("recommendations", [])
    result["coverage_note"] = (
        "Lesson-family coverage: at least one non-Basic variant has mastery evidence. Repeated grades and practice variants are not additional skills. This does not certify independent reading."
    )
    result["assessment_checks"] = [
        "Decode unfamiliar words without hints.",
        "Read an unfamiliar grade-level passage accurately, smoothly and with expression.",
        "Retell the passage and answer questions about its meaning.",
    ]
    return result


def family_journeys(
    root: Path, students: list[str], *, profiles: dict | None = None, reports: dict | None = None
) -> dict:
    profiles, reports = profiles or {}, reports or {}
    return {
        "readers": [
            build_journey(root, student, profile=profiles.get(student), report=reports.get(student))
            for student in students
        ]
    }
