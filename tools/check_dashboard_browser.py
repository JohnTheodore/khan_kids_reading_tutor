#!/usr/bin/env python3
"""Browser regressions against a synthetic dashboard: never accesses a tablet.

Run with Playwright installed. KHAN_BROWSER_EXECUTABLE optionally selects an
existing Chromium; otherwise use `python -m playwright install chromium`.
Axe is downloaded from npm with its registry integrity hash verified, used only
in the test browser, and never included in the app or its production traffic.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from dashboard import DashboardServer
from khan_kids.sync_report import build_dashboard_report
from playwright.sync_api import expect, sync_playwright


def axe_source() -> str:
    with urlopen("https://registry.npmjs.org/axe-core/4.10.3", timeout=30) as response:
        distribution = json.load(response)["dist"]
    with urlopen(distribution["tarball"], timeout=30) as response:
        archive = response.read()
    algorithm, expected = distribution["integrity"].split("-", 1)
    actual = base64.b64encode(hashlib.new(algorithm, archive).digest()).decode()
    if actual != expected:
        raise ValueError("Axe package integrity verification failed")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        source = package.extractfile("package/axe.min.js")
        assert source is not None
        return source.read().decode()


def example_report(student: str = "Student A", status: str = "applied") -> dict:
    lessons = [{"title": f"Reading lesson {index}", "variant": "Main"} for index in range(10)]
    return build_dashboard_report(
        {
            "student": student,
            "status": status,
            "generated_at": "2026-09-17T12:00:00-04:00",
            "performance": {"wall_seconds": 42.5},
            "desired_assignments": lessons,
            "verified_assignments": lessons,
            "observed_assignments": lessons,
            "score_evidence": [
                {
                    **lesson,
                    "scores": [94] if index == 0 else [83] if index == 1 else [],
                    "status": "provisional" if index == 0 else "not_mastered",
                }
                for index, lesson in enumerate(lessons)
            ]
            + [{"title": "Completed lesson", "variant": "Main", "scores": [100]}],
            "new_attempts": [{**lessons[0], "score": 94, "attempt_date": "2026-09-17"}],
            "actions": [
                {
                    "kind": "remove",
                    "title": "Completed lesson",
                    "variant": "Main",
                    "reason": "mastered: latest attempt is 100%; promote to Completed lesson — Practice 1",
                },
                {
                    "kind": "add",
                    "title": "Completed lesson",
                    "variant": "Practice 1",
                    "reason": "next difficulty",
                },
            ],
        }
    )


def example_journey(student: str = "Student A") -> dict:
    """Explicitly synthetic coverage data for UI states, never real records."""
    activity = {
        "variant": "Main",
        "grade": "Kindergarten",
        "assignment_status": "unassigned",
        "manual_assignment": False,
        "automatic_assignment_paused": False,
        "state": "mastered",
        "scores": [94, 94],
        "reason": "two consecutive attempts are at least 90%",
        "first_mastery_date": "2026-09-17",
        "first_mastery_confidence": "exact",
        "first_mastery_score": 94,
        "attempts": [{"date": "2026-09-17", "date_confidence": "exact", "score": 94}] * 2,
    }
    lesson = {
        "title": "Short Vowel Sound a",
        "state": "mastered",
        "weekly_gain": True,
        "contexts": [{"grade": "Kindergarten", "skill": "Short Vowel Sounds"}],
        "activities": [activity],
    }
    return {
        "student": student,
        "available": True,
        "archived": False,
        "current_focus": "Short vowels & CVC",
        "mastered_families": 1,
        "total_families": 2,
        "weekly_mastered": 1,
        "weekly_attempts": 2,
        "weekly_start": "2026-09-11",
        "as_of": "2026-09-17",
        "captured_on": "2026-09-17T12:00:00-04:00",
        "reading_level": "Not assessed",
        "forecast": {
            "available": False,
            "reason": "App scores alone cannot predict second-grade reading.",
        },
        "coverage_note": "At least one non-Basic variant has mastery evidence. This is catalog coverage, not a reading-level percentage.",
        "unmapped_attempts": 0,
        "assessment_checks": ["Decode unfamiliar words without hints."],
        "warnings": [],
        "recommendations": [{"title": "Reading lesson 0", "variant": "Main"}],
        "milestones": [
            {
                "id": "cvc",
                "title": "Short vowels & CVC",
                "description": "Read short-vowel words",
                "state": "practicing",
                "mastered": 1,
                "total": 2,
                "weekly_gain": 1,
                "lessons": [
                    lesson,
                    {
                        **lesson,
                        "title": "Short Vowel Sound i",
                        "state": "not_assessed",
                        "weekly_gain": False,
                        "activities": [
                            {**activity, "state": "not_assessed", "scores": [], "attempts": []}
                        ],
                    },
                ],
            }
        ],
    }


def example_phased_journey(student: str = "Student A") -> dict:
    """Six core phases plus supporting skills, with a shared topic placement."""
    reader = example_journey(student)
    template = reader["milestones"][0]
    phase_specs = [
        ("letters", "Know letters", "Recognize letters and their sounds", "letters"),
        ("sounds", "Hear sounds", "Blend and separate spoken sounds", "sounds"),
        ("words", "Read first words", "Read short-vowel words", "cvc"),
        ("patterns", "Build word patterns", "Explore common spelling patterns", "patterns"),
        ("text", "Read connected text", "Read sentences accurately and smoothly", "text"),
        ("meaning", "Understand reading", "Retell and discuss what you read", "meaning"),
        ("supporting", "Supporting skills", "Useful skills alongside the core path", "supporting"),
    ]
    reader["milestones"] = []
    reader["phases"] = []
    for phase_id, title, description, milestone_id in phase_specs:
        milestone = copy.deepcopy(template)
        milestone.update(id=milestone_id, title=title, description=description)
        reader["milestones"].append(milestone)
        reader["phases"].append(
            {
                "id": phase_id,
                "title": title,
                "description": description,
                "milestone_ids": [milestone_id],
                "state": "practicing",
                "mastered": 1,
                "total": 2,
                "weekly_gain": 1,
                "supporting": phase_id == "supporting",
            }
        )
    duplicate = copy.deepcopy(template)
    duplicate.update(id="word-sounds", title="Word sounds")
    duplicate["lessons"][1]["title"] = "Short Vowel Sound u"
    reader["milestones"].append(duplicate)
    reader["phases"][2].update(milestone_ids=["cvc", "word-sounds", "cvc"], total=3)
    reader["current_milestone_id"] = "cvc"
    return reader


class DashboardBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.playwright = sync_playwright().start()
        options = {"headless": True, "args": ["--no-sandbox"]}
        if executable := os.environ.get("KHAN_BROWSER_EXECUTABLE"):
            options["executable_path"] = executable
        cls.browser = cls.playwright.chromium.launch(**options)
        cls.axe = axe_source()
        root = Path(__file__).resolve().parents[1]
        cls.job = Mock(root=root, serial=None, workflow=root / "khan-mastery-sync")
        cls.server = DashboardServer(0, cls.job, token="T" * 43)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
        cls.setup_patch = patch("dashboard.setup_status")
        cls.setup_mock = cls.setup_patch.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.setup_patch.stop()
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self) -> None:
        self.setup_mock.return_value = {
            "ready": True,
            "students": ["Student A", "Student B"],
            "default_student": "Student A",
            "display_names": {"Student A": "Your reader", "Student B": "Another reader"},
            "checks": [{"label": "Synthetic local installation", "ok": True}],
        }
        self.reports = {student: example_report(student) for student in ("Student A", "Student B")}
        self.state = {
            "state": "idle",
            "output": "",
            "student": None,
            "report": None,
            "elapsed_seconds": None,
        }
        self.job.reset_mock()
        self.job.snapshot.side_effect = lambda: copy.deepcopy(self.state)
        self.job.latest_report.side_effect = lambda student: self.reports.get(student)
        self.job.start.side_effect = self.start_job
        self.job.check_connection.return_value = {
            "ready": True,
            "transport": "synthetic USB",
            "checks": [
                {"id": "adb", "label": "Tablet connected", "ok": True},
                {"id": "unlocked", "label": "Tablet unlocked", "ok": True},
                {"id": "screen_pinning", "label": "Android app pinning is off", "ok": True},
                {"id": "internet", "label": "Tablet Internet validated", "ok": True},
                {"id": "app", "label": "Khan Kids available", "ok": True},
            ],
        }
        self.job.request_stop.return_value = True
        self.journeys = [example_journey(student) for student in ("Student A", "Student B")]
        self.job.journeys.side_effect = lambda students: {"readers": copy.deepcopy(self.journeys)}
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.context.set_default_timeout(5000)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self) -> None:
        self.context.close()
        self.assertEqual(self.errors, [], "Unexpected JavaScript exception")

    def start_job(self, student: str, assignment: dict | None = None) -> bool:
        if assignment and "assignment_requests" in self.state:
            busy = self.state["state"] == "running"
            self.state["assignment_requests"].append(
                {
                    "id": str(len(self.state["assignment_requests"])),
                    "student": student,
                    "assignment": assignment,
                    "state": "queued" if busy else "running",
                    "error": None,
                }
            )
            self.state["teacher_session"] = "active"
            if busy:
                return True
        self.state.update(
            state="running",
            student=student,
            report=None,
            elapsed_seconds=2,
            output=(
                "Starting phase.save_parent_assignment\n"
                if assignment and "assignment_requests" in self.state
                else "Starting phase.review_assignments\n"
            )
            + "Synthetic diagnostic log\n",
            assignment=assignment,
        )
        return True

    def open(self, *, token: bool = True) -> None:
        # A hash-only navigation does not reset native disclosure state.
        self.page.goto("about:blank")
        self.page.goto(self.server.origin + ("/#" + self.server.token if token else "/"))
        if token:
            expect(self.page.locator("#result-content")).to_be_visible()
            expect(self.page.locator("#result-meta")).not_to_contain_text("Loading")

    def assert_no_overflow(self) -> None:
        sizes = self.page.evaluate(
            "({width:innerWidth,scroll:document.documentElement.scrollWidth})"
        )
        self.assertLessEqual(sizes["scroll"], sizes["width"])

    def test_assign_exact_variant_waits_for_native_verification(self):
        self.open()
        self.page.locator(".milestone > summary").click()
        self.page.locator(".mastered-lessons > summary").click()
        self.page.locator(".mastered-lessons .journey-lesson > summary").click()
        assign = self.page.locator(".variant-assignment").get_by_role(
            "button", name="Assign Short Vowel Sound a — Main", exact=True
        )
        assign.click()
        expect(self.page.locator("#state")).to_contain_text("Your reader's lesson")
        self.job.start.assert_called_once_with(
            "Student A",
            assignment={
                "grade": "Kindergarten",
                "title": "Short Vowel Sound a",
                "variant": "Main",
                "action": "assign",
            },
        )
        expect(assign).to_be_disabled()
        expect(assign).to_have_text("Assigning…")
        expect(assign).to_have_attribute("aria-busy", "true")
        expect(self.page.locator(".variant-assignment .assignment-feedback")).to_contain_text(
            "Reviewing lesson scores"
        )
        expect(self.page.locator("#sync")).to_be_disabled()
        # No optimistic claim that Android saved the assignment.
        expect(self.page.locator(".variant-assignment")).to_contain_text("Not assigned")
        report = copy.deepcopy(self.reports["Student A"])
        report["assigned"][0].update(title="Short Vowel Sound a", variant="Main")
        report["timestamp"] = "2026-09-17T14:00:00-04:00"
        self.reports["Student A"] = report
        self.journeys[0]["milestones"][0]["lessons"][0]["activities"][0].update(
            assignment_status="assigned", manual_assignment=True
        )
        self.state.update(
            state="succeeded", output="Finished phase.fixed_point_verify\n", report=report
        )
        self.page.evaluate("poll()")
        expect(
            self.page.get_by_role(
                "button", name="Unassign Short Vowel Sound a — Main", exact=True
            ).first
        ).to_be_enabled()
        expect(self.page.locator("#queue")).to_contain_text("Mastered")
        expect(self.page.locator(".milestone")).to_have_attribute("open", "")
        expect(self.page.locator(".mastered-lessons .journey-lesson")).to_have_attribute("open", "")
        expect(self.page.locator(".variant-assignment")).to_contain_text("Assigned · manual")
        expect(self.page.locator(".variant-assignment .assignment-feedback")).to_have_text(
            "Assigned · verified on tablet."
        )

    def test_assignment_feedback_is_immediate_before_server_acknowledges(self):
        self.open()
        pending = []
        self.page.route("**/api/assignment", lambda route: pending.append(route))
        button = self.page.get_by_role(
            "button", name="Assign Short Vowel Sound a — Main", exact=True
        ).first
        button.click()
        expect(button).to_have_text("Queuing…")
        expect(button).to_have_attribute("aria-busy", "true")
        expect(self.page.locator(".recent-lesson .assignment-feedback")).to_contain_text(
            "Sending request"
        )
        self.assertEqual(len(pending), 1)
        pending[0].continue_()
        expect(self.page.locator("#state")).to_contain_text("Your reader's lesson")
        self.page.reload()
        expect(
            self.page.get_by_role(
                "button", name="Assign Short Vowel Sound a — Main", exact=True
            ).first
        ).to_have_text("Assigning…")

    def test_unassign_refreshes_drilldown_without_reload_or_observed_running(self):
        self.reports["Student A"]["assigned"][0].update(title="Short Vowel Sound a", variant="Main")
        self.open()
        self.page.locator(".milestone > summary").click()
        self.page.locator(".mastered-lessons > summary").click()
        self.page.locator(".mastered-lessons .journey-lesson > summary").click()
        button = self.page.locator(".variant-assignment button")
        button.click()
        expect(button).to_have_text("Unassigning…")
        expect(self.page.locator("#state")).to_contain_text("Your reader's lesson")
        report = copy.deepcopy(self.reports["Student A"])
        report["assigned"] = []
        report["queue_count"] = 0
        self.reports["Student A"] = report
        self.journeys[0]["milestones"][0]["lessons"][0]["activities"][0].update(
            assignment_status="unassigned", automatic_assignment_paused=True
        )
        self.state.update(state="succeeded", report=report)
        # A different status consumer or a very fast job can skip a running->done edge.
        self.page.evaluate("running = false; status()")
        expect(self.page.locator(".variant-assignment button")).to_have_text("Assign")
        expect(self.page.locator(".variant-assignment button")).to_be_enabled()
        expect(self.page.locator(".variant-assignment")).to_contain_text(
            "Not assigned · auto paused"
        )
        expect(self.page.locator(".variant-assignment .assignment-feedback")).to_have_text(
            "Unassigned · verified on tablet."
        )
        expect(self.page.locator(".mastered-lessons .journey-lesson")).to_have_attribute("open", "")

    def test_unassign_is_exact_and_archived_controls_are_read_only(self):
        self.reports["Student A"]["assigned"][0].update(title="Short Vowel Sound a", variant="Main")
        self.open()
        self.page.locator(".queue-details").filter(has=self.page.locator("#queue")).locator(
            "summary"
        ).click()
        self.page.locator("#queue").get_by_role(
            "button", name="Unassign Short Vowel Sound a — Main", exact=True
        ).click()
        expect(self.page.locator("#state")).to_contain_text("Your reader's lesson")
        self.job.start.assert_called_once_with(
            "Student A",
            assignment={
                "grade": "Kindergarten",
                "title": "Short Vowel Sound a",
                "variant": "Main",
                "action": "unassign",
            },
        )
        expect(self.page.locator("#student")).to_be_disabled()
        self.state.update(state="idle", output="", student=None, report=None)
        self.setup_mock.return_value["archived_students"] = ["Student A"]
        self.journeys[0]["archived"] = True
        self.open()
        self.page.locator(".milestone > summary").click()
        self.page.locator(".mastered-lessons > summary").click()
        self.page.locator(".mastered-lessons .journey-lesson > summary").click()
        expect(self.page.locator("button[data-assignment]")).to_have_count(0)
        expect(self.page.locator(".variant-assignment")).to_contain_text("Read-only archive")

    def test_parent_requests_can_queue_and_first_result_refreshes_during_next_edit(self):
        self.state["assignment_requests"] = []
        self.open()
        self.page.locator(".milestone > summary").click()
        self.page.locator(".unrecorded-lessons > summary").click()
        self.page.locator(".unrecorded-lessons .journey-lesson > summary").click()
        first = self.page.get_by_role(
            "button", name="Assign Short Vowel Sound a — Main", exact=True
        ).first
        second = self.page.get_by_role(
            "button", name="Assign Short Vowel Sound i — Main", exact=True
        ).first
        first.click()
        expect(first).to_have_text("Assigning…")
        expect(second).to_be_enabled()
        second.click()
        expect(second).to_have_text("Queued")
        expect(self.page.locator(".unrecorded-lessons .assignment-feedback")).to_contain_text(
            "next"
        )
        self.assertEqual(self.job.start.call_count, 2)
        expect(first).to_be_disabled()
        expect(second).to_be_disabled()
        # First result must update even if the worker has already started the second.
        report = copy.deepcopy(self.reports["Student A"])
        report["assigned"][0].update(title="Short Vowel Sound a", variant="Main")
        report["timestamp"] = "2026-09-17T14:00:00-04:00"
        self.reports["Student A"] = report
        self.state["assignment_requests"][0]["state"] = "succeeded"
        self.state["assignment_requests"][1]["state"] = "running"
        self.state.update(
            assignment=self.state["assignment_requests"][1]["assignment"], report=None
        )
        self.page.evaluate("poll()")
        expect(
            self.page.get_by_role(
                "button", name="Unassign Short Vowel Sound a — Main", exact=True
            ).first
        ).to_be_enabled()
        expect(second).to_have_text("Assigning…")
        expect(self.page.locator(".unrecorded-lessons .journey-lesson")).to_have_attribute(
            "open", ""
        )
        self.page.reload()
        expect(
            self.page.get_by_role(
                "button", name="Unassign Short Vowel Sound a — Main", exact=True
            ).first
        ).to_be_enabled()
        self.page.locator("#assignment-requests-summary").click()
        expect(self.page.locator("#assignment-requests-list")).to_contain_text(
            "Short Vowel Sound i — Main"
        )

    def test_warm_teacher_session_allows_assign_but_not_overlapping_sync(self):
        self.state.update(teacher_session="warm", assignment_requests=[], teacher_idle_seconds=59)
        self.open()
        expect(self.page.locator("#parent-session")).to_contain_text("60 seconds")
        expect(self.page.locator("#sync")).to_be_disabled()
        expect(
            self.page.get_by_role(
                "button", name="Assign Short Vowel Sound a — Main", exact=True
            ).first
        ).to_be_enabled()
        self.state.update(teacher_session="closed", teacher_idle_seconds=None)
        self.page.evaluate("poll()")
        expect(self.page.locator("#parent-session")).to_be_hidden()
        expect(self.page.locator("#sync")).to_be_enabled()

    def test_interrupted_request_queue_shows_failed_and_not_applied_without_replay(self):
        self.state.update(
            assignment_requests=[
                {
                    "id": "synthetic-error",
                    "student": "Student A",
                    "assignment": {
                        "title": "Short Vowel Sound a",
                        "variant": "Main",
                        "grade": "Kindergarten",
                        "action": "assign",
                    },
                    "state": "blocked",
                    "error": "Not applied: an earlier tablet operation failed.",
                }
            ]
        )
        self.open()
        expect(self.page.locator(".recent-lesson .assignment-feedback")).to_contain_text(
            "Not applied"
        )
        expect(
            self.page.get_by_role(
                "button", name="Assign Short Vowel Sound a — Main", exact=True
            ).first
        ).to_be_enabled()
        self.job.start.assert_not_called()

    def test_assignment_http_error_retains_unverified_state_without_retry(self):
        self.page.route(
            "**/api/assignment",
            lambda route: route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({"error": "A sync is already running"}),
            ),
        )
        self.open()
        self.page.locator(".milestone > summary").click()
        self.page.locator(".mastered-lessons > summary").click()
        self.page.locator(".mastered-lessons .journey-lesson > summary").click()
        self.page.locator(".variant-assignment").get_by_role(
            "button", name="Assign Short Vowel Sound a — Main", exact=True
        ).click()
        expect(self.page.locator("#error-message")).to_contain_text("already running")
        expect(self.page.locator(".variant-assignment .assignment-feedback")).to_contain_text(
            "already running"
        )
        expect(self.page.locator(".variant-assignment button")).to_have_text("Assign")
        expect(self.page.locator(".variant-assignment")).to_contain_text("Not assigned")
        self.job.start.assert_not_called()

    def test_responsive_themes_and_accessibility(self) -> None:
        for theme in ("light", "dark"):
            for width in (1280, 768, 375, 320):
                with self.subTest(theme=theme, width=width):
                    self.page.emulate_media(color_scheme=theme)
                    self.page.set_viewport_size({"width": width, "height": 900})
                    self.open()
                    expect(self.page.locator("#journey-content")).to_be_visible()
                    self.page.locator(".milestone > summary").click()
                    self.page.locator(".mastered-lessons > summary").click()
                    self.page.locator(".mastered-lessons .journey-lesson > summary").first.click()
                    self.page.locator(".lesson-history > summary").click()
                    self.page.locator(".unrecorded-lessons > summary").click()
                    self.assert_no_overflow()
                    self.page.locator("#sync-details > summary").click()
                    self.page.locator(".queue-details:not(#sync-details) > summary").click()
                    self.page.locator(".reading-checks > summary").click()
                    self.page.locator("#setup-link").click()
                    self.page.locator("#diagnostics summary").click()
                    # CDP evaluation runs audit code without changing the app CSP.
                    self.page.evaluate(self.axe)
                    result = self.page.evaluate(
                        "async()=>await axe.run(document,{runOnly:{type:'tag',"
                        "values:['wcag2a','wcag2aa','wcag21aa']}})"
                    )
                    self.assertEqual(
                        [
                            {"id": item["id"], "nodes": [node["target"] for node in item["nodes"]]}
                            for item in result["violations"]
                        ],
                        [],
                    )
                    targets = self.page.locator(
                        "nav a, button:visible, select:visible, summary:visible"
                    ).evaluate_all("els=>els.map(e=>e.getBoundingClientRect().height)")
                    self.assertTrue(all(height >= 44 for height in targets))
                    if width in (1280, 375) and os.environ.get("KHAN_BROWSER_SCREENSHOTS"):
                        directory = Path(__file__).resolve().parents[1] / "private"
                        directory.mkdir(mode=0o700, exist_ok=True)
                        self.page.screenshot(
                            path=str(directory / f"impeccable-after-{theme}-{width}.png"),
                            full_page=True,
                        )

    def test_enlarged_text_and_long_unicode_content(self) -> None:
        long_name = "Reader " + "é漢字🙂" * 30
        self.setup_mock.return_value["display_names"]["Student A"] = long_name
        report = self.reports["Student A"]
        report["recommendations"][0]["title"] = "Phonics" * 40
        report["assigned"][0]["scores"] = [94] * 100
        self.page.set_viewport_size({"width": 320, "height": 900})
        self.open()
        expect(self.page.locator("#journey-content")).to_be_visible()
        normal = self.page.locator("h1").evaluate("e=>parseFloat(getComputedStyle(e).fontSize)")
        self.page.locator("html").evaluate("e=>e.style.fontSize='200%'")
        enlarged = self.page.locator("h1").evaluate("e=>parseFloat(getComputedStyle(e).fontSize)")
        self.assertEqual(enlarged, normal * 2)
        self.page.locator(".queue-details:not(#sync-details) > summary").click()
        self.assert_no_overflow()

    def test_journey_evidence_and_weekly_gains_do_not_claim_reading_level(self) -> None:
        self.open()
        expect(self.page.locator("#journey-weekly")).to_contain_text("1 topic mastered")
        expect(self.page.locator("#reading-level")).to_have_text("Reading level: Not assessed")
        expect(self.page.locator("#journey-forecast")).to_contain_text("cannot predict")
        self.page.locator(".milestone > summary").click()
        expect(self.page.locator(".unrecorded-lessons > summary")).to_contain_text(
            "No recorded scores · 1"
        )
        self.page.locator(".unrecorded-lessons > summary").click()
        expect(self.page.locator(".unrecorded-lessons")).to_contain_text("Short Vowel Sound i")
        self.page.locator(".mastered-lessons > summary").click()
        self.page.locator(".mastered-lessons .journey-lesson > summary").click()
        expect(self.page.locator(".score-evidence")).to_contain_text("94%")
        expect(self.page.locator(".score-evidence")).to_contain_text("2026")
        expect(self.page.locator(".journey-lesson")).to_have_count(2)

    def test_archived_reader_is_viewable_but_not_syncable(self) -> None:
        self.setup_mock.return_value["archived_students"] = ["Student B"]
        self.journeys[1]["archived"] = True
        self.journeys[1]["recommendations"] = []
        self.open()
        self.page.locator("#student").select_option("Student B")
        expect(self.page.locator("#student")).to_have_value("Student B")
        expect(self.page.locator("#journey-focus")).to_have_text("Archived reading history")
        expect(self.page.locator("#sync")).to_be_disabled()
        self.job.start.assert_not_called()

    def test_phases_group_milestones_and_use_deduplicated_topic_counts(self) -> None:
        self.journeys[0] = example_phased_journey()
        self.open()
        expect(self.page.locator(".journey-phase")).to_have_count(7)
        expect(self.page.locator('.journey-phase:not([data-phase="supporting"])')).to_have_count(6)
        current = self.page.locator('.journey-phase[data-phase="words"]')
        expect(current).to_have_attribute("open", "")
        expect(current.locator(":scope > summary .current-focus-label")).to_have_text(
            "Current focus"
        )
        expect(self.page.locator(".journey-phase[open]")).to_have_count(1)
        expect(current.locator(".milestone")).to_have_count(2)
        expect(current.locator(".phase-counts")).to_contain_text("1 of 3 topics mastered")
        meter = current.locator(".phase-counts meter")
        expect(meter).to_have_attribute("value", "1")
        expect(meter).to_have_attribute("max", "3")
        expect(current.locator(".phase-counts button")).to_have_count(0)
        expect(self.page.locator('.journey-phase[data-phase="supporting"]')).to_contain_text(
            "Supporting skills"
        )
        expect(self.page.locator(".milestone[open]")).to_have_count(0)
        expect(self.page.locator(".recent-lesson")).to_have_count(1)
        self.job.start.assert_not_called()

    def test_supporting_skills_and_unscored_exposure_do_not_claim_mastery(self) -> None:
        reader = example_phased_journey()
        reader["unscored_exposures"] = 4
        supporting = reader["phases"][-1]
        supporting.update(state="not_assessed", mastered=0, weekly_gain=0)
        milestone = next(m for m in reader["milestones"] if m["id"] == "supporting")
        milestone.update(state="not_assessed", mastered=0, weekly_gain=0)
        for lesson in milestone["lessons"]:
            lesson.update(state="not_assessed", weekly_gain=False)
            for activity in lesson["activities"]:
                activity.update(
                    state="not_assessed", scores=[], attempts=[], first_mastery_date=None
                )
        self.journeys[0] = reader
        self.open()
        self.page.locator(".reading-checks > summary").click()
        expect(self.page.locator("#journey-exposure")).to_be_visible()
        expect(self.page.locator("#journey-exposure")).to_contain_text("4 unscored")
        expect(self.page.locator("#journey-exposure")).to_contain_text("not demonstrated mastery")
        phase = self.page.locator('.journey-phase[data-phase="supporting"]')
        expect(phase).to_have_class("journey-phase supporting-phase")
        expect(phase.locator(".phase-counts meter")).to_have_attribute("value", "0")
        expect(phase.locator(".phase-counts")).to_contain_text("Not assessed")
        phase.locator(":scope > summary").click()
        phase.locator(".milestone > summary").click()
        expect(phase.locator(".unrecorded-lessons > summary")).to_contain_text(
            "No recorded scores · 2"
        )
        expect(phase.locator(".mastered-lessons")).to_have_count(0)
        self.job.start.assert_not_called()

    def test_current_practice_link_opens_phase_and_focuses_milestone_with_keyboard(self) -> None:
        self.journeys[0] = example_phased_journey()
        for width in (1280, 375):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.open()
                phase = self.page.locator('.journey-phase[data-phase="words"]')
                phase.locator(":scope > summary").click()
                expect(phase).not_to_have_attribute("open", "")
                link = self.page.locator("#journey-practice-link")
                link.focus()
                self.page.keyboard.press("Enter")
                expect(phase).to_have_attribute("open", "")
                milestone = phase.locator('.milestone[data-milestone="cvc"]')
                expect(milestone).to_have_attribute("open", "")
                expect(milestone.locator(":scope > summary")).to_be_focused()
                expect(milestone.locator(".milestone-title .muted")).to_be_visible()
                self.assert_no_overflow()
        self.job.start.assert_not_called()

    def test_current_practice_focus_survives_completed_sync_background_refresh(self) -> None:
        self.journeys[0] = example_phased_journey()
        for width in (1280, 375):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.state.update(state="idle", student=None, report=None)
                self.journeys[0]["weekly_attempts"] = 2
                self.open()
                self.page.locator("#journey-practice-link").click()
                phase = self.page.locator('.journey-phase[data-phase="words"]')
                milestone = phase.locator('.milestone[data-milestone="cvc"]')
                heading = milestone.locator(":scope > summary")
                expect(heading).to_be_focused()
                old_heading = heading.element_handle()
                self.journeys[0]["weekly_attempts"] = 3
                report = copy.deepcopy(self.reports["Student A"])
                report["timestamp"] = "2026-09-17T14:00:00-04:00"
                self.state.update(
                    state="succeeded",
                    student="Student A",
                    report=report,
                    output="Finished phase.fixed_point_verify\n",
                )
                self.page.evaluate("poll()")
                expect(self.page.locator("#journey-weekly")).to_contain_text("3 scored attempts")
                self.assertFalse(old_heading.evaluate("e => e.isConnected"))
                expect(phase).to_have_attribute("open", "")
                expect(milestone).to_have_attribute("open", "")
                expect(heading).to_be_focused()
                self.assert_no_overflow()
        self.job.start.assert_not_called()

    def test_only_remaining_letter_is_visible_without_searching_completed_lessons(self) -> None:
        milestone = self.journeys[0]["milestones"][0]
        template = milestone["lessons"][0]
        lessons = []
        for letter in "abcdefghijklmnopqrstuvwxyz":
            lesson = copy.deepcopy(template)
            lesson["title"] = "Lowercase " + letter
            if letter == "l":
                lesson["state"] = "practicing"
                lesson["activities"] = [
                    {
                        "variant": "Main",
                        "state": "practicing",
                        "scores": [],
                        "attempts": [],
                        "reason": "Saved report score; lesson date unknown.",
                        "archived_scores": [{"score": 88, "captured_on": "2026-09-08"}],
                    }
                ]
            lessons.append(lesson)
        milestone.update(title="Lowercase letters", lessons=lessons, total=26, mastered=25)
        self.page.set_viewport_size({"width": 375, "height": 900})
        self.open()
        self.page.locator(".milestone > summary").click()
        expect(self.page.locator(".remaining-lessons .journey-lesson")).to_have_count(1)
        expect(self.page.locator(".remaining-lessons")).to_contain_text("Lowercase l")
        expect(self.page.locator(".remaining-lessons")).to_contain_text("88%")
        expect(self.page.locator(".remaining-lessons")).to_contain_text("lesson date unknown")
        expect(self.page.locator(".mastered-lessons")).to_have_count(0)
        expect(self.page.locator(".unrecorded-lessons")).to_have_count(0)
        self.assert_no_overflow()
        self.page.get_by_role(
            "button", name="Lowercase a: Mastery evidence — Main. Show scores", exact=True
        ).click()
        expect(
            self.page.locator(".selected-letter-evidence .journey-lesson > summary")
        ).to_be_focused()
        expect(self.page.locator(".selected-letter-evidence .journey-lesson")).to_have_count(1)
        expect(self.page.locator(".journey-lesson")).to_have_count(2)
        self.page.get_by_role(
            "button", name="Lowercase l: Practicing. Show scores", exact=True
        ).click()
        expect(self.page.locator(".selected-letter-evidence")).to_contain_text("Lowercase l")
        expect(self.page.locator(".remaining-lessons .journey-lesson")).to_have_count(0)
        expect(self.page.locator(".journey-lesson")).to_have_count(1)
        self.journeys[0]["weekly_attempts"] += 1
        self.page.evaluate("loadJourneys()")
        expect(self.page.locator("#journey-weekly")).to_contain_text("3 scored attempts")
        expect(self.page.locator(".selected-letter-evidence .journey-lesson")).to_have_count(1)
        expect(self.page.locator(".selected-letter-evidence")).to_contain_text("Lowercase l")
        expect(self.page.locator('.letter-cell[aria-current="true"]')).to_have_text("l")
        expect(self.page.locator(".journey-lesson")).to_have_count(1)
        self.job.start.assert_not_called()

    def test_sounds_separate_practice_from_missing_scores(self) -> None:
        milestone = self.journeys[0]["milestones"][0]
        template = milestone["lessons"][0]
        practice = copy.deepcopy(template)
        practice.update(title="Beginning Sound c", state="practicing")
        practice["activities"][0].update(state="practicing", scores=[70])
        unknown = copy.deepcopy(milestone["lessons"][1])
        unknown["title"] = "Beginning Sound f"
        milestone.update(title="Letters & sounds", lessons=[template, practice, unknown], total=3)
        self.open()
        self.page.locator(".milestone > summary").click()
        expect(self.page.locator(".remaining-lessons")).to_contain_text("Beginning Sound c")
        expect(self.page.locator(".remaining-lessons")).not_to_contain_text("Beginning Sound f")
        expect(self.page.locator(".unrecorded-lessons > summary")).to_contain_text(
            "No recorded scores · 1"
        )
        expect(self.page.locator(".mastered-lessons > summary")).to_contain_text(
            "Mastery recorded · 1"
        )
        expect(self.page.locator(".remaining-lessons .variant-scores")).to_contain_text("70%")
        self.job.start.assert_not_called()

    def test_letter_navigation_targets_heading_and_respects_reduced_motion(self) -> None:
        milestone = self.journeys[0]["milestones"][0]
        template = milestone["lessons"][0]
        milestone.update(
            title="Lowercase letters",
            total=26,
            mastered=26,
            lessons=[
                {**copy.deepcopy(template), "title": "Lowercase " + letter}
                for letter in "abcdefghijklmnopqrstuvwxyz"
            ],
        )
        for width, reduced in ((1280, "no-preference"), (375, "reduce")):
            with self.subTest(width=width, motion=reduced):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.emulate_media(reduced_motion=reduced)
                self.open()
                self.page.locator(".milestone > summary").click()
                self.page.evaluate("""() => {
                    window.selectionScrolls = [];
                    const original = Element.prototype.scrollIntoView;
                    Element.prototype.scrollIntoView = function(options) {
                        if (this.matches('.lesson-summary')) window.selectionScrolls.push(options);
                        original.call(this, options);
                    };
                }""")
                selected = self.page.get_by_role(
                    "button", name="Lowercase z: Mastery evidence — Main. Show scores", exact=True
                )
                selected.focus()
                self.page.evaluate("window.scrollTo(0, 0)")
                self.page.keyboard.press("Enter")
                heading = self.page.locator(".journey-lesson.is-selected > summary")
                expect(heading).to_be_focused()
                expect(heading).to_contain_text("Lowercase z")
                expect(selected).to_have_attribute("aria-current", "true")
                self.page.wait_for_function(
                    "() => {const r=document.querySelector('.journey-lesson.is-selected > summary').getBoundingClientRect();return r.top>=0 && r.bottom<innerHeight/2}"
                )
                calls = self.page.evaluate("window.selectionScrolls")
                self.assertEqual(calls[-1]["block"], "start")
                self.assertEqual(
                    calls[-1]["behavior"], "instant" if reduced == "reduce" else "smooth"
                )
                if reduced == "reduce":
                    self.assertEqual(
                        heading.evaluate("e => getComputedStyle(e).transitionDuration"), "0s"
                    )
                self.page.get_by_role(
                    "button", name="Lowercase a: Mastery evidence — Main. Show scores", exact=True
                ).click()
                expect(self.page.locator(".journey-lesson.is-selected")).to_have_count(1)
                expect(self.page.locator(".journey-lesson.is-selected > summary")).to_contain_text(
                    "Lowercase a"
                )
                expect(self.page.locator('.letter-cell[aria-current="true"]')).to_have_count(1)
                expect(
                    self.page.locator(".selected-letter-evidence .journey-lesson")
                ).to_have_count(1)
                expect(self.page.locator(".journey-lesson")).to_have_count(1)
                expect(self.page.locator(".mastered-lessons, .unrecorded-lessons")).to_have_count(0)
                self.assert_no_overflow()
        self.job.start.assert_not_called()

    def test_recent_mastery_names_every_variant_and_deduplicates_placements(self) -> None:
        milestone = self.journeys[0]["milestones"][0]
        lesson = milestone["lessons"][0]
        activity = lesson["activities"][0]
        lesson["activities"] = [
            {**activity, "variant": variant}
            for variant in ("Basic", "Main", "Practice 1", "Practice 2")
        ]
        # The topic was already mastered; new variants must still appear.
        lesson["weekly_gain"] = False
        self.journeys[0]["milestones"].append(copy.deepcopy(milestone))
        self.open()
        expect(self.page.locator(".recent-lesson")).to_have_count(4)
        for variant in ("Basic", "Main", "Practice 1", "Practice 2"):
            expect(self.page.locator("#recent-mastery-list")).to_contain_text(
                "Short Vowel Sound a — " + variant
            )
        self.page.locator(".milestone > summary").first.click()
        self.page.locator(".mastered-lessons > summary").click()
        expect(self.page.locator(".mastered-variants")).to_have_text(
            "Mastered: Basic, Main, Practice 1, Practice 2"
        )
        self.page.locator(".mastered-lessons .journey-lesson > summary").click()
        expect(self.page.locator(".variant-status.mastered")).to_have_count(4)

    def test_recent_mastery_excludes_unknown_dates_and_can_show_all(self) -> None:
        milestone = self.journeys[0]["milestones"][0]
        template = milestone["lessons"][0]
        lessons = []
        for index in range(8):
            lesson = copy.deepcopy(template)
            lesson["title"] = "Synthetic reading lesson " + str(index)
            if index >= 6:
                lesson["activities"][0]["first_mastery_confidence"] = "inferred"
            lessons.append(lesson)
        milestone["lessons"] = lessons
        self.open()
        expect(self.page.locator(".recent-lesson")).to_have_count(5)
        self.page.get_by_role("button", name="Show all 6 recently mastered activities").click()
        expect(self.page.locator(".recent-lesson")).to_have_count(6)

    def test_recent_mastery_shows_qualifying_score_not_later_attempt(self) -> None:
        activity = self.journeys[0]["milestones"][0]["lessons"][0]["activities"][0]
        activity["scores"] = [94, 94, 70]
        self.open()
        expect(self.page.locator(".recent-lesson")).to_contain_text("Mastered with 94%")
        expect(self.page.locator(".recent-lesson")).not_to_contain_text("70%")

    def test_selected_reader_progress_is_primary_and_meters_use_evidence(self) -> None:
        self.open()
        expect(self.page.locator(".reader-overview")).to_have_count(0)
        expect(self.page.locator("#recent-mastery-list")).to_contain_text("Short Vowel Sound a")
        meter = self.page.locator(".milestone .mastery-meter")
        expect(meter).to_have_attribute("value", "1")
        expect(meter).to_have_attribute("max", "2")
        self.assertTrue(
            self.page.evaluate(
                "document.getElementById('results').compareDocumentPosition(document.getElementById('journey')) & Node.DOCUMENT_POSITION_FOLLOWING"
            )
        )

    def test_sync_indicator_tracks_completed_stages_and_finishes(self) -> None:
        self.open()
        expect(self.page.locator("#sync-indicator")).to_be_hidden()
        self.page.locator("#sync").click()
        expect(self.page.locator("#sync-indicator")).to_be_visible()
        expect(self.page.locator("#sync-indicator")).to_have_attribute("aria-valuenow", "0.2")
        expect(self.page.locator("#elapsed")).to_contain_text("elapsed")
        self.state.update(state="succeeded", student="Student A", report=self.reports["Student A"])
        self.page.reload()
        expect(self.page.locator("#state")).to_contain_text("complete")
        expect(self.page.locator("#sync-indicator")).to_be_visible()
        expect(self.page.locator("#sync-indicator")).to_have_attribute("aria-valuenow", "1")
        expect(self.page.locator("#sync")).to_have_attribute("aria-busy", "false")
        expect(self.page.locator("#sync-stages")).to_be_hidden()
        self.assertNotIn("sync-active", self.page.locator("#activity").get_attribute("class"))

    def test_offline_preflight_preserves_progress_and_requires_recheck(self) -> None:
        self.job.check_connection.return_value = {
            "ready": False,
            "checks": [
                {"id": "adb", "label": "Tablet connected", "ok": True},
                {"id": "internet", "label": "Tablet Internet validated", "ok": False},
            ],
            "error": "Tablet has no validated Internet connection.",
        }
        self.open()
        expect(self.page.locator("#recent-mastery-list")).to_contain_text("Short Vowel Sound a")
        self.page.locator("#sync").click()
        expect(self.page.locator("#error-message")).to_contain_text("no validated Internet")
        expect(self.page.locator("#connection-checks")).to_contain_text(
            "Needs attention: Tablet Internet validated"
        )
        expect(self.page.locator("#check-connection")).to_be_visible()
        expect(self.page.locator("#sync")).to_be_disabled()
        expect(self.page.locator("#recent-mastery-list")).to_contain_text("Short Vowel Sound a")
        self.job.start.assert_not_called()
        self.job.check_connection.return_value = {
            "ready": True,
            "transport": "synthetic USB",
            "checks": [
                {"id": "adb", "label": "Tablet connected", "ok": True},
                {"id": "unlocked", "label": "Tablet unlocked", "ok": True},
                {"id": "internet", "label": "Tablet Internet validated", "ok": True},
                {"id": "app", "label": "Khan Kids available", "ok": True},
            ],
        }
        self.page.locator("#check-connection").click()
        expect(self.page.locator("#state")).to_contain_text("ready to sync")
        expect(self.page.locator("#sync")).to_be_enabled()
        self.job.start.assert_not_called()

    def test_running_sync_has_prominent_cooperative_stop_state(self) -> None:
        self.state.update(
            state="running",
            student="Student A",
            output="Starting phase.review_assignments\n",
            report=None,
            recovery_state="none",
        )
        self.open()
        expect(self.page.locator("#stop-sync")).to_be_visible()
        self.page.locator("#stop-sync").click()
        self.job.request_stop.assert_called_once_with()
        self.state.update(state="stopping", recovery_state="stopping_safely")
        self.page.evaluate("poll()")
        expect(self.page.locator("#state")).to_contain_text("Stopping safely")
        expect(self.page.locator("#phase")).to_contain_text("No new actions")
        expect(self.page.locator("#stop-sync")).to_be_disabled()

    def test_partial_failure_is_explicit_and_never_replayed(self) -> None:
        report = copy.deepcopy(self.reports["Student A"])
        report.update(status="interrupted", error="A lesson could not be found")
        self.state.update(
            state="failed",
            student="Student A",
            report=report,
            recovery_state="review_required",
            run_id="synthetic-partial-run",
        )
        self.open()
        expect(self.page.locator("#state")).to_contain_text("partial update")
        expect(self.page.locator("#phase")).to_contain_text("Nothing will be replayed")
        expect(self.page.locator("#sync")).to_be_disabled()
        expect(self.page.locator("#check-connection")).to_be_visible()
        self.job.start.assert_not_called()

    def test_sync_working_panel_is_prominent_accessible_and_honest(self):
        for width, theme, motion in (
            (1280, "light", "no-preference"),
            (375, "dark", "reduce"),
            (320, "light", "no-preference"),
        ):
            with self.subTest(width=width, theme=theme, motion=motion):
                self.state.update(
                    state="idle", student=None, report=None, output="", assignment=None
                )
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.emulate_media(color_scheme=theme, reduced_motion=motion)
                self.open()
                self.page.locator("#sync").click()
                expect(self.page.locator("#sync")).to_have_attribute("aria-busy", "true")
                expect(self.page.locator(".activity-symbol")).to_be_visible()
                expect(self.page.locator("#sync-stages")).to_be_visible()
                expect(self.page.locator("#sync-stages [aria-current=step]")).to_contain_text(
                    "Read scores"
                )
                expect(self.page.locator("#sync-explanation")).to_contain_text("real steps")
                self.assertEqual(
                    self.page.locator(".activity-symbol").evaluate(
                        "e=>getComputedStyle(e).animationName"
                    ),
                    "none" if motion == "reduce" else "sync-working",
                )
                self.assertEqual(
                    self.page.locator("#sync-indicator").evaluate(
                        "e=>e.getBoundingClientRect().height"
                    ),
                    10,
                )
                self.assert_no_overflow()
                self.page.evaluate(self.axe)
                result = self.page.evaluate(
                    "async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})"
                )
                self.assertEqual([item["id"] for item in result["violations"]], [])
                if os.environ.get("KHAN_BROWSER_SCREENSHOTS"):
                    self.page.screenshot(
                        path=str(
                            Path(__file__).resolve().parents[1]
                            / f"private/sync-working-{theme}-{width}.png"
                        )
                    )
                self.state.update(
                    output="Finished phase.review_assignments\nStarting phase.plan_queue\n"
                )
                self.page.evaluate("poll()")
                expect(self.page.locator("#sync-stages [aria-current=step]")).to_contain_text(
                    "Update lessons"
                )
                expect(self.page.locator("#sync-stages li").first).to_contain_text("Done")
                self.state.update(
                    output="Finished phase.review_assignments\nFinished phase.plan_queue\nStarting phase.fixed_point_verify\n"
                )
                self.page.evaluate("poll()")
                expect(self.page.locator("#sync-stages [aria-current=step]")).to_contain_text(
                    "Verify & finish"
                )
                expect(self.page.locator("#sync-indicator")).to_have_attribute(
                    "aria-valuenow", "0.6"
                )
                self.assertEqual(
                    self.page.locator("#sync-fill").evaluate(
                        "e=>getComputedStyle(e).animationName"
                    ),
                    "none",
                )
                self.state.update(state="failed")
                self.page.evaluate("poll()")
                expect(self.page.locator("#sync-stages")).to_be_hidden()
                expect(self.page.locator("#sync-indicator")).to_have_attribute(
                    "aria-valuenow", "0.6"
                )
                expect(self.page.locator("#sync")).to_have_attribute("aria-busy", "false")

    def test_manual_edit_does_not_show_mastery_sync_steps(self):
        self.state.update(
            state="running",
            student="Student A",
            assignment={
                "grade": "Kindergarten",
                "title": "Short Vowel Sound a",
                "variant": "Main",
                "action": "assign",
            },
            output="Starting phase.save_parent_assignment\n",
        )
        self.open()
        expect(self.page.locator(".activity-symbol")).to_be_visible()
        expect(self.page.locator("#sync-stages")).to_be_hidden()
        expect(self.page.locator("#sync-indicator")).to_have_attribute(
            "aria-label", "Assignment update stage completion"
        )

    def test_activity_motion_pauses_offscreen_and_stops_on_connection_loss(self):
        self.open()
        self.page.locator("#sync").click()
        symbol = self.page.locator(".activity-symbol")
        self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        self.page.wait_for_function(
            "() => getComputedStyle(document.querySelector('.activity-symbol')).animationPlayState === 'paused'"
        )
        self.page.evaluate("window.scrollTo(0, 0)")
        self.page.wait_for_function(
            "() => getComputedStyle(document.querySelector('.activity-symbol')).animationPlayState === 'running'"
        )
        self.page.route("**/api/status", lambda route: route.abort())
        self.page.evaluate("poll()")
        expect(self.page.locator("#state")).to_contain_text("unavailable")
        expect(symbol).to_be_hidden()
        expect(self.page.locator("#sync-stages")).to_be_hidden()
        expect(self.page.locator("#sync")).to_have_attribute("aria-busy", "false")

    def test_sync_gives_immediate_feedback_before_server_acknowledges(self) -> None:
        self.open()
        pending = []
        self.page.route("**/api/sync", lambda route: pending.append(route))
        self.page.locator("#sync").click()
        expect(self.page.locator("#state")).to_contain_text("Starting")
        expect(self.page.locator("#sync-indicator")).to_be_visible()
        expect(self.page.locator("#sync")).to_be_disabled()
        expect(self.page.locator("#sync")).to_have_attribute("aria-busy", "true")
        expect(self.page.locator(".activity-symbol")).to_be_visible()
        expect(self.page.locator("#sync-stages")).to_be_visible()
        self.assertEqual(len(pending), 1)
        pending[0].continue_()
        expect(self.page.locator("#state")).to_contain_text("Syncing")

    def test_mobile_metadata_reflows_and_queue_scrolls_with_keyboard(self) -> None:
        self.page.set_viewport_size({"width": 375, "height": 900})
        self.open()
        expect(self.page.locator("#journey-content")).to_be_visible()
        title = self.page.locator(".milestone-title").bounding_box()
        counts = self.page.locator(".milestone-counts").bounding_box()
        expect(self.page.locator(".milestone-title .muted")).to_be_visible()
        expect(self.page.locator(".milestone-title .muted")).to_have_text("Read short-vowel words")
        self.assertIsNotNone(title)
        self.assertIsNotNone(counts)
        self.assertGreaterEqual(counts["y"], title["y"] + title["height"])
        self.page.locator(".queue-details:not(#sync-details) > summary").click()
        region = self.page.get_by_role("region", name="Assigned lesson scores")
        self.assertTrue(region.evaluate("e=>e.scrollWidth>e.clientWidth"))
        region.focus()
        self.page.keyboard.press("ArrowRight")
        self.page.wait_for_timeout(150)
        self.assertGreater(region.evaluate("e=>e.scrollLeft"), 0)
        self.assert_no_overflow()

    def test_missing_and_invalid_analytics_do_not_hide_sync_results(self) -> None:
        self.journeys[0].update(available=False, warnings=["No scored history configured."])
        self.open()
        expect(self.page.locator("#journey-status")).to_contain_text("No scored history")
        expect(self.page.locator("#result-content")).to_be_visible()
        self.job.journeys.side_effect = None
        self.job.journeys.return_value = {
            "readers": [],
            "error": "Private reading profiles could not be validated.",
        }
        self.page.locator("#setup-link").click()
        self.page.locator("#refresh").click()
        expect(self.page.locator("#journey-status")).to_contain_text("could not be validated")

    def test_keyboard_skip_focus_and_sync(self) -> None:
        self.open()
        self.page.keyboard.press("Tab")
        expect(self.page.locator(".skip-link")).to_be_focused()
        self.page.keyboard.press("Enter")
        self.page.locator("#sync").focus()
        self.assertEqual(
            self.page.locator("#sync").evaluate("e=>getComputedStyle(e).outlineStyle"), "solid"
        )
        self.page.keyboard.press("Enter")
        expect(self.page.locator("#state")).to_contain_text("Syncing Your reader")
        expect(self.page.locator("#phase")).to_contain_text("Reviewing lesson scores")
        expect(self.page.locator("#sync")).to_be_disabled()
        self.job.start.assert_called_once_with("Student A")
        expect(self.page.locator("#result-content")).to_be_visible()

    def test_synthesized_touch_and_reduced_motion(self) -> None:
        touch = self.browser.new_context(
            viewport={"width": 375, "height": 900},
            has_touch=True,
            reduced_motion="reduce",
        )
        try:
            page = touch.new_page()
            page.goto(self.server.origin + "/#" + self.server.token)
            expect(page.locator("#sync")).to_be_enabled()
            page.locator("#sync").tap()
            expect(page.locator("#state")).to_contain_text("Syncing")
            self.assertEqual(
                page.locator("#sync .icon").evaluate("e=>getComputedStyle(e).animationName"), "none"
            )
            self.job.start.assert_called_once()
        finally:
            touch.close()

    def test_reader_selection_survives_reload_and_anchors_keep_authentication(self) -> None:
        self.open()
        self.page.locator("#student").select_option("Student B")
        expect(self.page.locator("#results-title")).to_contain_text("Another reader")
        self.page.locator("#setup-link").click()
        self.page.reload()
        expect(self.page.locator("#student")).to_have_value("Student B")
        expect(self.page.locator("#sync")).to_be_enabled()
        expect(self.page.locator("#error")).to_be_hidden()
        self.assertEqual(
            self.page.evaluate("sessionStorage.getItem('tutor-token')"), self.server.token
        )

    def test_reader_selection_survives_local_server_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "private/dashboard-token.local"
            first = DashboardServer(0, self.job, token_path=token_path)
            port = first.server_port
            first_thread = threading.Thread(target=first.serve_forever)
            first_thread.start()
            second = None
            second_thread = None
            try:
                self.page.goto(first.origin + "/#" + first.token)
                self.page.locator("#student").select_option("Student B")
                expect(self.page.locator("#student")).to_have_value("Student B")
                first.shutdown()
                first.server_close()
                first_thread.join()

                second = DashboardServer(port, self.job, token_path=token_path)
                second_thread = threading.Thread(target=second.serve_forever)
                second_thread.start()
                self.page.reload()
                expect(self.page.locator("#student")).to_have_value("Student B")
                expect(self.page.locator("#sync")).to_be_enabled()
                expect(self.page.locator("#error")).to_be_hidden()
            finally:
                if first_thread.is_alive():
                    first.shutdown()
                    first.server_close()
                    first_thread.join()
                if second is not None:
                    second.shutdown()
                    second.server_close()
                if second_thread is not None:
                    second_thread.join()

    def test_late_reader_response_cannot_replace_current_reader(self) -> None:
        self.open()
        pending = []

        def delay(route):
            student = parse_qs(urlsplit(route.request.url).query)["student"][0]
            if student == "Student B":
                pending.append(route)
            else:
                route.continue_()

        self.page.route("**/api/latest?*", delay)
        self.page.locator("#student").select_option("Student B")
        expect(self.page.locator("#result-meta")).to_contain_text("Loading")
        self.page.locator("#student").select_option("Student A")
        expect(self.page.locator("#result-content")).to_be_visible()
        self.assertEqual(len(pending), 1)
        pending[0].fulfill(json={"report": self.reports["Student B"]})
        expect(self.page.locator("#results-title")).to_contain_text("Your reader")
        expect(self.page.locator("#result-meta")).not_to_contain_text("Loading")

    def test_other_reader_completion_is_not_misattributed(self) -> None:
        self.state.update(state="failed", student="Student B", report=None)
        self.open()
        expect(self.page.locator("#state")).to_contain_text("Ready for your next")
        expect(self.page.locator("#error")).to_be_hidden()

    def test_proposed_changes_never_look_applied(self) -> None:
        report = example_report(status="review_required")
        self.reports["Student A"] = report
        self.state.update(state="succeeded", student="Student A", report=report)
        self.open()
        expect(self.page.locator("#state")).to_contain_text("No changes applied")
        expect(self.page.locator("#changes")).to_contain_text("Will be unchecked")
        expect(self.page.locator("#queue-count")).to_contain_text("Not verified")
        expect(self.page.locator(".lesson-card")).to_have_count(0)

    def test_teardown_failure_is_preserved_after_refresh(self) -> None:
        self.reports["Student A"]["teardown"] = {"status": "failed"}
        self.open()
        expect(self.page.locator("#error")).to_contain_text("leaving Teacher view failed")
        self.page.locator("#setup-link").click()
        self.page.locator("#refresh").click()
        expect(self.page.locator("#error")).to_contain_text("leaving Teacher view failed")
        expect(self.page.locator("#setup")).to_have_attribute("open", "")

    def test_screen_pinning_preserves_result_and_offers_cleanup_without_resync(self) -> None:
        report = self.reports["Student A"]
        report["teardown"] = {
            "status": "failed",
            "kind": "lock_task_pinned",
            "result_saved": True,
            "error": "Khan Kids is screen-pinned.",
        }
        self.state.update(
            state="failed",
            student="Student A",
            report=report,
            recovery_state="cleanup_required",
        )

        def finish_cleanup():
            report["teardown"]["status"] = "recovered"
            self.state.update(state="succeeded", report=report, recovery_state="none")
            return {"ready": True, "cleanup_recovered": True, "checks": []}

        self.job.finish_cleanup.side_effect = finish_cleanup
        self.open()
        expect(self.page.locator("#state")).to_contain_text("Ready for a fresh sync")
        expect(self.page.locator("#error")).to_contain_text("verified queue are saved")
        expect(self.page.locator("#finish-cleanup")).to_be_visible()
        expect(self.page.locator("#sync")).to_be_enabled()

        self.page.locator("#finish-cleanup").click()
        expect(self.page.locator("#state")).to_contain_text("Check-in complete")
        expect(self.page.locator("#error")).to_be_hidden()
        self.job.finish_cleanup.assert_called_once_with()
        self.job.start.assert_not_called()

    def test_connection_failure_has_direct_recovery_without_resubmission(self) -> None:
        self.page.route("**/api/status", lambda route: route.abort())
        self.open()
        expect(self.page.locator("#error-message")).to_contain_text("Can't reach")
        expect(self.page.locator("#sync")).to_be_disabled()
        self.page.unroute("**/api/status")
        self.page.locator("#retry").click()
        expect(self.page.locator("#sync")).to_be_enabled()
        expect(self.page.locator("#error")).to_be_hidden()
        self.job.start.assert_not_called()

    def test_bad_api_json_and_missing_auth_are_actionable(self) -> None:
        self.page.route("**/api/setup", lambda route: route.fulfill(body="not json"))
        self.page.goto(self.server.origin + "/#" + self.server.token)
        expect(self.page.locator("#error-message")).to_contain_text("unreadable response")
        self.page.unroute("**/api/setup")
        self.page.evaluate("sessionStorage.clear()")
        self.page.goto(self.server.origin)
        expect(self.page.locator("#error-message")).to_contain_text("access expired")
        expect(self.page.locator("#auth-recovery")).to_be_visible()
        expect(self.page.locator("#student")).to_be_disabled()
        expect(self.page.locator("#student")).to_contain_text("Reopen dashboard")
        expect(self.page.locator("#retry")).to_be_hidden()
        expect(self.page.locator("#sync")).to_be_disabled()
        self.job.start.assert_not_called()

    def test_first_run_setup_is_expanded_and_sync_is_disabled(self) -> None:
        self.setup_mock.return_value.update(ready=False, students=[], display_names={})
        self.page.goto(self.server.origin + "/#" + self.server.token)
        expect(self.page.locator("#setup")).to_have_attribute("open", "")
        expect(self.page.locator("#sync")).to_be_disabled()
        expect(self.page.locator("#empty-title")).to_contain_text("Finish setup")

    def test_read_only_startup_and_diagnostics_are_lazy(self) -> None:
        requests = []
        self.page.on("request", lambda request: requests.append(request.url))
        self.state["output"] = "Synthetic diagnostics " * 1000
        self.open()
        self.job.start.assert_not_called()
        self.assertFalse(any("diagnostics=1" in url for url in requests))
        expect(self.page.locator("#setup")).not_to_have_attribute("open", "")
        self.page.locator("#setup-link").click()
        self.page.locator("#diagnostics summary").click()
        expect(self.page.locator("#progress")).to_contain_text("Synthetic diagnostics")

    def test_no_javascript_fallback(self) -> None:
        context = self.browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(self.server.origin)
            self.assertIn(
                "./khan-mastery-sync", page.locator("noscript").evaluate("e=>e.textContent")
            )
            self.job.start.assert_not_called()
        finally:
            context.close()

    def test_deferred_family_shows_reason_and_return_date(self) -> None:
        self.reports["Student A"]["quarantines"] = [
            {
                "title": "Deferred phonics family",
                "reason": "Needs a break",
                "eligible_date": "2026-10-01",
            }
        ]
        self.open()
        expect(self.page.locator("#quarantines")).to_contain_text("Needs a break")
        expect(self.page.locator("#quarantines")).to_contain_text("2026-10-01")
        expect(self.page.locator("#quarantines")).not_to_contain_text("undefined")

    def test_incomplete_worker_result_cannot_claim_completion(self) -> None:
        self.state.update(state="succeeded", student="Student A", report={"student": "Student A"})
        self.open()
        expect(self.page.locator("#error-message")).to_contain_text("incomplete result")
        expect(self.page.locator("#state")).not_to_contain_text("Check-in complete")
        expect(self.page.locator("#sync")).to_be_disabled()

    def test_lost_sync_response_does_not_resubmit_a_running_job(self) -> None:
        self.open()

        def lose_response(route):
            self.start_job("Student A")
            route.abort()

        self.page.route("**/api/sync", lose_response)
        self.page.locator("#sync").click()
        expect(self.page.locator("#error-message")).to_contain_text("may still be running")
        expect(self.page.locator("#sync")).to_be_disabled()
        self.page.locator("#retry").click()
        expect(self.page.locator("#state")).to_contain_text("Syncing")
        expect(self.page.locator("#sync")).to_be_disabled()
        self.job.start.assert_not_called()

    def test_request_timeout_has_recovery_and_preserves_saved_results(self) -> None:
        self.page.clock.install()
        pending = []
        self.page.route("**/api/status", lambda route: pending.append(route))
        self.open()
        self.page.clock.fast_forward(10001)
        expect(self.page.locator("#error-message")).to_contain_text("too long to respond")
        expect(self.page.locator("#result-content")).to_be_visible()
        expect(self.page.locator("#sync")).to_be_disabled()
        for route in pending:
            route.abort()

    def test_idle_polling_is_reduced_and_auth_failure_stops_polling(self) -> None:
        self.page.clock.install()
        requests = []
        self.page.on("request", lambda request: requests.append(request.url))
        self.open()
        expect(self.page.locator("#sync")).to_be_enabled()
        count = len([url for url in requests if "/api/status" in url])
        self.page.clock.fast_forward(9000)
        self.assertEqual(len([url for url in requests if "/api/status" in url]), count)
        self.page.evaluate("sessionStorage.clear()")
        self.page.goto(self.server.origin)
        expect(self.page.locator("#error-message")).to_contain_text("access expired")
        count = len(requests)
        self.page.clock.fast_forward(30000)
        self.assertEqual(len(requests), count)


if __name__ == "__main__":
    unittest.main()
