"use strict";
const fragment = location.hash.slice(1);
function storage(key, value) {
  try {
    if (value !== undefined) sessionStorage.setItem(key, value);
    return sessionStorage.getItem(key) || "";
  } catch {
    return value || "";
  }
}
const launchToken = /^[A-Za-z0-9_-]{43}$/.test(fragment) ? fragment : "";
const token = launchToken
  ? storage("tutor-token", launchToken)
  : storage("tutor-token");
if (launchToken) history.replaceState(null, "", "/");
const element = (id) => document.getElementById(id);
// Put daily practice before the longer journey map; preserve the native report
// as its only recommendation source, with one instance of each component.
element("journey").before(element("results"));
element("daily-practice").prepend(element("next-practice"));
let ready = false,
  running = false,
  setupLoaded = false,
  lastReport = "";
let submitted = false;
let assignmentFeedback = null;
const assignmentFeedbacks = new Map();
let teacherSession = "closed",
  completedRequestsSignature = "";
let manualRunning = false;
let workflowManual = false,
  currentPhase = "",
  currentRecoveryState = "none";
let activityOnscreen = true;
let completedJourneyKey = "";
let assignmentControlSequence = 0;
let displayedReport = null;
let progressFraction = 0;
let progressVisible = false;
let connected = false,
  authFailed = false,
  latestSequence = 0,
  statusSequence = 0;
let retrying = false,
  checkingConnection = false,
  finishingCleanup = false,
  needsConnectionCheck = false,
  stopping = false,
  failedOperationKey = "",
  checkedFailureKey = "",
  timer;
let journeys = [],
  archivedStudents = [],
  journeySequence = 0,
  journeySignature = "";
let reportProblem = "",
  jobProblem = "",
  tabletProblem = "",
  connectionProblem = "";
function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
function icon(name, className = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon " + className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#icon-" + name);
  svg.append(use);
  return svg;
}
async function api(path, options = {}) {
  let response;
  const controller = new AbortController();
  const { timeoutMs = 10000, ...fetchOptions } = options;
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    response = await fetch(path, {
      ...fetchOptions,
      signal: controller.signal,
      headers: { "X-Tutor-Token": token, ...fetchOptions.headers },
      cache: "no-store",
    });
    if (response.status === 403) {
      authFailed = true;
      showAuthFailure();
      throw new Error(
        "Dashboard access expired. Reopen the private dashboard to load your readers.",
      );
    }
    let body;
    try {
      body = await response.json();
      if (!body || typeof body !== "object" || Array.isArray(body))
        throw new Error();
    } catch {
      throw new Error(
        "The local app returned an unreadable response. Restart khan-dashboard after any active sync finishes, then open its new launch link.",
      );
    }
    if (!response.ok) {
      const failure = new Error(
        body.error ||
          "The local app couldn't complete this request. Reconnect or open troubleshooting.",
      );
      failure.payload = body;
      throw failure;
    }
    return body;
  } catch (e) {
    if (response) throw e;
    throw new Error(
      controller.signal.aborted
        ? "The local app took too long to respond. A sync may still be running; reconnect to check before trying again."
        : "Can't reach the local app. Keep khan-dashboard running, then reconnect. A sync may still be running.",
    );
  } finally {
    clearTimeout(timeout);
  }
}
function updateButton() {
  document
    .querySelectorAll(".assignment-controls")
    .forEach(paintAssignmentFeedback);
  document.querySelectorAll("button[data-assignment]").forEach((button) => {
    button.disabled =
      !ready ||
      !connected ||
      authFailed ||
      assignmentPending(
        button.closest(".assignment-controls").dataset.assignmentKey,
      ) ||
      retrying ||
      archivedStudents.includes(element("student").value);
  });
  element("sync").disabled =
    !ready ||
    !connected ||
    authFailed ||
    running ||
    submitted ||
    teacherSession !== "closed" ||
    [...assignmentFeedbacks.values()].some((f) =>
      ["sending", "queued", "working"].includes(f.state),
    ) ||
      retrying ||
      checkingConnection ||
      finishingCleanup ||
    needsConnectionCheck ||
    !element("student").value;
  element("finish-cleanup").disabled = finishingCleanup;
  if (archivedStudents.includes(element("student").value))
    element("sync").disabled = true;
  setText(
    "sync-help",
    archivedStudents.includes(element("student").value)
      ? "Archived profile: explore preserved reading history. Live syncing is disabled; no account is switched automatically."
      : teacherSession === "warm"
        ? "Teacher view is ready for more manual edits. Mastery sync becomes available after automatic logout."
        : "Reviews scores and maintains ten assignments, plus protected manual extras. Keep the tablet unlocked while changes are verified.",
  );
  element("student").disabled =
    authFailed || !setupLoaded || running || submitted || retrying || checkingConnection;
  element("sync-indicator").hidden = !(running || submitted || progressVisible);
  const fraction = submitted ? 0 : progressFraction;
  element("sync-indicator").setAttribute("aria-valuenow", fraction);
  element("sync-indicator").setAttribute(
    "aria-label",
    workflowManual
      ? "Assignment update stage completion"
      : "Mastery sync stage completion",
  );
  element("sync-fill").style.transform = `scaleX(${fraction})`;
  element("sync-indicator").setAttribute(
    "aria-valuetext",
    submitted
      ? "Starting mastery sync"
      : !running && progressVisible
        ? progressFraction === 1
          ? "Check-in complete"
          : "Check-in stopped before completion"
        : element("phase").textContent || "Mastery sync is running",
  );
  const working = connected && (running || submitted);
  element("activity").classList.toggle("sync-active", working);
  element("sync").setAttribute("aria-busy", working && !workflowManual);
  element("sync-explanation").hidden = !working;
  element("sync-stages").hidden = !working || workflowManual;
  element("stop-sync").hidden = !running || manualRunning;
  element("stop-sync").disabled = stopping;
  setText("stop-sync", stopping ? "Stopping safely…" : "Stop safely");
  const stage = /fixed_point|teardown/.test(currentPhase)
    ? 2
    : /plan_queue|add_and_verify|remove_and_verify/.test(currentPhase)
      ? 1
      : 0;
  element("sync-stages")
    .querySelectorAll("li")
    .forEach((step, index) => {
      step.dataset.state =
        index < stage ? "done" : index === stage ? "current" : "waiting";
      if (index === stage) step.setAttribute("aria-current", "step");
      else step.removeAttribute("aria-current");
      const text =
        index < stage ? "Done" : index === stage ? "In progress" : "Waiting";
      if (step.querySelector(".stage-status").textContent !== text)
        step.querySelector(".stage-status").textContent = text;
    });
  document.body.classList.toggle("busy", running || submitted);
  const label =
    (running && !manualRunning) || submitted
      ? "Syncing… "
      : currentRecoveryState === "review_required" || currentRecoveryState === "safe_to_retry"
        ? "Retry sync "
        : "Sync progress ";
  if (element("sync").dataset.label !== label) {
    element("sync").replaceChildren(
      document.createTextNode(label),
      icon("sync"),
    );
    element("sync").dataset.label = label;
  }
}
function setText(id, text) {
  if (element(id).textContent !== text) element(id).textContent = text;
}
function showProblems() {
  const message = connectionProblem || tabletProblem || reportProblem || jobProblem;
  element("error").hidden = !message;
  setText("error-message", message);
  element("retry").hidden = !connectionProblem || authFailed;
  element("check-connection").hidden =
    authFailed || (!needsConnectionCheck && !tabletProblem);
  element("finish-cleanup").hidden =
    authFailed || currentRecoveryState !== "cleanup_required";
  element("auth-recovery").hidden = !authFailed;
  element("error-setup").hidden = authFailed;
}
function showAuthFailure() {
  const option = node("option", "", "Reopen dashboard to load readers");
  option.value = "";
  element("student").replaceChildren(option);
  element("student").value = "";
  element("student").disabled = true;
  setText("state", "Dashboard access expired");
  setText("phase", "Your reader records are still safely stored on this computer.");
}
function error(message) {
  connectionProblem = message;
  showProblems();
}
function readerName(student = element("student").value) {
  return (
    [...element("student").options].find((option) => option.value === student)
      ?.textContent || "your reader"
  );
}
function activityInfo(title, variant) {
  const reader = journeys.find((r) => r.student === element("student").value);
  const lesson = reader?.milestones
    ?.flatMap((m) => m.lessons)
    .find((l) => l.title === title);
  return lesson?.activities.find((a) => a.variant === variant);
}
function masteryLabel(activity) {
  return activity?.state === "mastered"
    ? "Mastered"
    : activity?.state === "practicing"
      ? "Not yet mastered"
      : "No mastery recorded";
}
function assignmentKey(student, title, variant) {
  return JSON.stringify([student, title, variant]);
}
function assignmentPending(key) {
  return ["sending", "queued", "working"].includes(
    assignmentFeedbacks.get(key)?.state,
  );
}
function rememberAssignmentFeedback(feedback) {
  assignmentFeedback = feedback;
  assignmentFeedbacks.set(feedback.key, feedback);
}
function paintAssignmentFeedback(controls) {
  const button = controls.querySelector("button[data-assignment]");
  if (!button) return;
  const feedback = controls.querySelector(".assignment-feedback");
  const current = assignmentFeedbacks.get(controls.dataset.assignmentKey);
  const busy = assignmentPending(controls.dataset.assignmentKey);
  button.textContent = busy
    ? current.state === "sending"
      ? "Queuing…"
      : current.state === "queued"
        ? "Queued"
        : current.action === "assign"
          ? "Assigning…"
          : "Unassigning…"
    : button.dataset.label;
  button.setAttribute("aria-busy", String(busy));
  feedback.hidden = !current;
  feedback.dataset.state = current?.state || "";
  if (feedback.textContent !== (current?.message || ""))
    feedback.textContent = current?.message || "";
}
function assignmentControls(title, variant, showMastery = false) {
  const activity = activityInfo(title, variant);
  const controls = node("span", "assignment-controls");
  controls.dataset.title = title;
  controls.dataset.variant = variant;
  controls.dataset.showMastery = String(showMastery);
  controls.title =
    "Assignment state is from the last verified queue. Use Sync progress to refresh changes made directly on the tablet.";
  if (showMastery)
    controls.append(
      node(
        "span",
        "assignment-mastery " + (activity?.state || ""),
        masteryLabel(activity),
      ),
    );
  if (archivedStudents.includes(element("student").value)) {
    controls.append(node("span", "assignment-state", "Read-only archive"));
    return controls;
  }
  const queueKnown = displayedReport?.queue_count != null;
  const assigned = queueKnown
    ? displayedReport.assigned.some(
        (a) => a.title === title && a.variant === variant,
      )
    : activity?.assignment_status === "assigned";
  const known =
    queueKnown ||
    activity?.assignment_status === "assigned" ||
    activity?.assignment_status === "unassigned";
  controls.append(
    node(
      "span",
      "assignment-state",
      !known
        ? "Assignment not verified"
        : assigned
          ? "Assigned" + (activity?.manual_assignment ? " · manual" : "")
          : "Not assigned" +
            (activity?.automatic_assignment_paused ? " · auto paused" : ""),
    ),
  );
  if (!activity?.grade) return controls;
  const button = node(
    "button",
    "secondary assignment-button",
    assigned ? "Unassign" : "Assign",
  );
  button.type = "button";
  button.dataset.assignment = "true";
  button.dataset.label = assigned ? "Unassign" : "Assign";
  controls.dataset.assignmentKey = assignmentKey(
    element("student").value,
    title,
    variant,
  );
  button.setAttribute(
    "aria-label",
    `${assigned ? "Unassign" : "Assign"} ${title} — ${variant}`,
  );
  button.title = assigned
    ? "Remove this variant and pause automatic reassignment. Assign it again to resume."
    : "Add this exact variant now. Manual extras can take the queue above ten.";
  button.disabled =
    !ready ||
    !connected ||
    authFailed ||
    assignmentPending(controls.dataset.assignmentKey) ||
    retrying;
  button.addEventListener("click", () =>
    startWorkflow({
      grade: activity.grade,
      title,
      variant,
      action: assigned ? "unassign" : "assign",
    }),
  );
  const feedback = node("span", "assignment-feedback");
  feedback.id = "assignment-feedback-" + ++assignmentControlSequence;
  button.setAttribute("aria-describedby", feedback.id);
  feedback.hidden = true;
  controls.append(button, feedback);
  paintAssignmentFeedback(controls);
  return controls;
}
async function startWorkflow(assignment = null) {
  if (
    (!assignment && (submitted || running || teacherSession !== "closed")) ||
    (assignment &&
      assignmentPending(
        assignmentKey(
          element("student").value,
          assignment.title,
          assignment.variant,
        ),
      )) ||
    !ready ||
    !connected ||
    authFailed ||
    archivedStudents.includes(element("student").value)
  )
    return;
  if (!assignment) {
    submitted = true;
    workflowManual = false;
    currentPhase = "";
    progressFraction = 0;
  }
  const feedback = assignment
    ? {
        ...assignment,
        key: assignmentKey(
          element("student").value,
          assignment.title,
          assignment.variant,
        ),
        accepted: false,
        state: "sending",
        message: "Sending request · waiting for tablet verification…",
      }
    : null;
  if (feedback) rememberAssignmentFeedback(feedback);
  connectionProblem = jobProblem = "";
  showProblems();
  setText(
    "state",
    assignment ? "Queuing this lesson update…" : "Starting your check-in…",
  );
  setText(
    "phase",
    assignment
      ? `${assignment.action === "assign" ? "Assigning" : "Unassigning"} ${assignment.title} — ${assignment.variant}. Waiting for tablet verification.`
      : "Connecting to your tablet and opening Teacher view…",
  );
  updateButton();
  try {
    if (!assignment) await checkTabletConnection({ startingSync: true });
    await api(assignment ? "/api/assignment" : "/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        student: element("student").value,
        ...(assignment || {}),
      }),
    });
    if (feedback) {
      feedback.accepted = true;
      feedback.state = "queued";
      feedback.message = "Queued · waiting for the tablet…";
    }
    await status();
    if (!assignment) submitted = false;
    updateButton();
    schedulePoll();
  } catch (e) {
    if (!assignment) submitted = false;
    if (!e.payload?.checks) connected = false;
    if (feedback) {
      feedback.state = "error";
      feedback.message = e.message;
    }
    updateButton();
    if (e.payload?.checks) {
      tabletProblem = e.message;
      showProblems();
    } else error(e.message);
  }
}

function renderConnectionChecks(checks = []) {
  const list = element("connection-checks");
  list.replaceChildren(
    ...checks.map((check) =>
      node("li", check.ok ? "ready" : "missing", `${check.ok ? "Ready" : "Needs attention"}: ${check.label}`),
    ),
  );
  list.hidden = checks.length === 0;
}

async function checkTabletConnection({ startingSync = false } = {}) {
  if (checkingConnection) return false;
  checkingConnection = true;
  tabletProblem = "";
  setText("state", "Checking the tablet…");
  setText("phase", "Verifying ADB, unlock state, Internet, and Khan Kids before navigation.");
  updateButton();
  try {
    const result = await api("/api/connection-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      timeoutMs: 30000,
    });
    renderConnectionChecks(result.checks);
    needsConnectionCheck = false;
    checkedFailureKey = failedOperationKey;
    jobProblem = "";
    setText("state", startingSync ? "Tablet ready. Starting check-in…" : "Tablet ready to sync.");
    setText("phase", `${result.transport || "ADB"} is healthy and Internet is available.`);
    showProblems();
    return true;
  } catch (e) {
    renderConnectionChecks(e.payload?.checks || []);
    needsConnectionCheck = true;
    tabletProblem = e.message;
    showProblems();
    throw e;
  } finally {
    checkingConnection = false;
    updateButton();
  }
}
async function setup() {
  const data = await api("/api/setup");
  if (!Array.isArray(data.students) || !Array.isArray(data.checks))
    throw new Error(
      "The local setup response is incomplete. Restart khan-dashboard after any active sync finishes.",
    );
  const chosen = element("student").value || storage("tutor-student");
  archivedStudents = data.archived_students || [];
  element("checks").replaceChildren(
    ...data.checks.map((check) =>
      node(
        "li",
        check.ok ? "" : "missing",
        (check.ok ? "Ready: " : "Missing: ") + check.label,
      ),
    ),
  );
  element("student").replaceChildren(
    ...data.students.map((student) => {
      const option = node(
        "option",
        "",
        data.display_names?.[student] || student,
      );
      option.value = student;
      return option;
    }),
  );
  element("student").value = data.students.includes(chosen)
    ? chosen
    : data.students.includes(data.default_student)
      ? data.default_student
      : data.students[0] || "";
  ready = data.ready;
  storage("tutor-student", element("student").value);
  element("setup-summary").textContent = ready
    ? "Setup complete · Connection & setup"
    : "Finish setup to start syncing";
  if (!setupLoaded || !ready) element("setup").open = !ready;
  setupLoaded = true;
  updateButton();
}
const scores = (items) =>
  items?.length
    ? items.map((score) => score + "%").join(" → ")
    : "No scores recorded";
function lessonRow(item) {
  const row = node("li");
  row.append(
    node(
      "p",
      "lesson-name",
      item.title + (item.variant ? " — " + item.variant : ""),
    ),
  );
  if (item.score !== undefined)
    row.append(
      node("p", "lesson-detail", item.score + "% · " + item.attempt_date),
    );
  else if (Array.isArray(item.scores)) {
    const source =
      item.score_source &&
      item.score_source !== item.title + " — " + item.variant
        ? "Evidence from " + item.score_source + ": "
        : "Scores: ";
    row.append(node("p", "lesson-detail", source + scores(item.scores)));
  }
  if (item.reason) row.append(node("p", "lesson-detail", item.reason));
  if (item.eligible_date)
    row.append(
      node("p", "lesson-detail", "Eligible again: " + item.eligible_date),
    );
  return row;
}
function validReport(report, student) {
  return (
    report &&
    report.student === student &&
    typeof report.outcome === "string" &&
    [
      "new_scores",
      "mastered",
      "unchecked",
      "added",
      "recommendations",
      "assigned",
    ].every(
      (key) =>
        Array.isArray(report[key]) &&
        report[key].every(
          (item) =>
            item &&
            typeof item.title === "string" &&
            typeof item.variant === "string" &&
            (item.scores == null || Array.isArray(item.scores)),
        ),
    )
  );
}
function renderReport(report) {
  displayedReport = report;
  const signature = JSON.stringify(report);
  const archived = archivedStudents.includes(element("student").value);
  setText(
    "empty-title",
    archived
      ? "Archived history, not a live queue"
      : "Ready for the first check-in",
  );
  element("empty").querySelector("p").textContent = archived
    ? "Explore this reader's preserved scores in the reading journey above. This profile is read-only; no account is switched automatically."
    : "Sync after your reader finishes a session. New scores and the best lessons to try next will appear here.";
  setText("results-title", "Latest check-in · " + readerName());
  const date = new Date(report?.timestamp);
  setText(
    "result-meta",
    !report
      ? archived
        ? "Read-only profile · live assignments not verified"
        : "Run a sync after a lesson session."
      : Number.isNaN(date.valueOf())
        ? "Last recorded sync"
        : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) +
          (running ? " · Sync in progress" : ""),
  );
  if (signature === lastReport) return;
  lastReport = signature;
  reportProblem = "";
  element("empty").hidden = !!report;
  element("result-content").hidden = !report;
  if (!report) {
    showProblems();
    return;
  }
  element("outcome").textContent = report.outcome;
  const stats = [
    [report.new_scores.length, "New scores"],
    [report.mastered.length, "Activities mastered"],
    [report.queue_count ?? "—", "Verified assignments"],
    [
      report.duration == null ? "—" : Math.round(report.duration) + "s",
      "Sync time",
    ],
  ];
  element("stats").replaceChildren(
    ...stats.map(([value, label]) => {
      const card = node("div", "stat");
      card.append(node("dt", "", label), node("dd", "", value));
      return card;
    }),
  );
  const proposed = report.status === "review_required";
  const groups = [
    ["New scores", report.new_scores, "score"],
    ["Mastery found", report.mastered, "check"],
    [
      proposed ? "Will be unchecked" : "Assignments unchecked",
      report.unchecked,
      "remove",
    ],
    [proposed ? "Will be added" : "Assignments added", report.added, "add"],
  ];
  element("change-note").textContent = proposed
    ? "Proposed only · no changes applied"
    : "Since the preceding sync";
  element("changes").replaceChildren(
    ...groups.map(([title, items, symbol]) => {
      const group = node("section", "change-group");
      const heading = node("h3");
      heading.append(
        icon(symbol, "change-" + symbol),
        document.createTextNode(title),
      );
      group.append(heading);
      const list = node("ul");
      list.replaceChildren(
        ...(items.length
          ? items.map(lessonRow)
          : [node("li", "lesson-detail", "None.")]),
      );
      group.append(list);
      return group;
    }),
  );
  element("quarantines").hidden = !report.quarantines?.length;
  element("quarantine-list").replaceChildren(
    ...(report.quarantines || []).map(lessonRow),
  );
  element("recommendations").replaceChildren(
    ...(report.recommendations.length
      ? report.recommendations.map((item, index) => {
          const card = node("article", "lesson-card"),
            top = node("div", "card-top");
          top.append(
            node("span", "rank", index === 0 ? "First choice" : "Then try"),
            node(
              "span",
              "score-badge",
              item.latest_score == null
                ? "Ready to try"
                : "Latest: " + item.latest_score + "%",
            ),
          );
          card.append(
            top,
            node("h3", "", item.title),
            node("p", "variant", item.variant),
            node("p", "lesson-detail", item.reason),
            node("p", "goal", item.mastery_goal),
            assignmentControls(item.title, item.variant, true),
          );
          return card;
        })
      : [
          node(
            "p",
            "muted",
            "Next-lesson suggestions require a successfully verified queue.",
          ),
        ]),
  );
  setText(
    "queue-count",
    report.queue_count == null
      ? "Not verified"
      : report.queue_count +
          " assigned" +
          (report.queue_count > 10 ? " · automatic top-ups paused" : ""),
  );
  setText(
    "recommendation-note",
    proposed
      ? "No assignments changed yet"
      : "From the last verified assignment queue",
  );
  element("queue").replaceChildren(
    ...report.assigned.map((item) => {
      const row = node("tr");
      const name = node("th");
      name.scope = "row";
      name.append(
        node("div", "lesson-name", item.title),
        node("div", "lesson-detail", item.variant),
      );
      row.append(
        name,
        node("td", "", scores(item.scores)),
        node(
          "td",
          "",
          {
            MASTERED: "Mastered · review optional",
            PROVISIONAL: "Close to mastery",
            HOLD: "Keep practicing",
            "NOT ATTEMPTED": "Ready to try",
          }[item.state] || item.state,
        ),
      );
      const action = node("td");
      action.append(assignmentControls(item.title, item.variant, true));
      row.append(action);
      return row;
    }),
  );
  reportProblem = report.error || "";
  if (report.teardown?.status === "failed")
    reportProblem =
      report.teardown.kind === "lock_task_pinned" && report.teardown.result_saved
        ? "Progress and the verified queue are saved. Android app pinning blocked cleanup; unpin the tablet, then finish cleanup."
        : (report.queue_count == null
            ? "The final queue wasn't verified, and leaving Teacher view failed."
            : "The queue was verified, but leaving Teacher view failed.") +
          " Inspect the tablet before retrying.";
  showProblems();
}
async function latest() {
  const sequence = ++latestSequence;
  const student = element("student").value;
  element("results").setAttribute("aria-busy", "true");
  setText("result-meta", "Loading the last check-in…");
  if (!element("student").value) {
    renderReport(null);
    setText("empty-title", "Finish setup to add your reader");
    element("results").setAttribute("aria-busy", "false");
    return;
  }
  try {
    const data = await api(
      "/api/latest?student=" + encodeURIComponent(student),
    );
    if (sequence !== latestSequence || student !== element("student").value)
      return;
    if (data.report && !validReport(data.report, student))
      throw new Error(
        "The saved check-in is incomplete or belongs to another reader. Open troubleshooting to inspect the local records.",
      );
    renderReport(data.report);
  } catch (e) {
    if (sequence === latestSequence && student === element("student").value)
      throw e;
  } finally {
    if (sequence === latestSequence)
      element("results").setAttribute("aria-busy", "false");
  }
}
function friendlyPhase(last = "") {
  if (/verify_parent_assignment/.test(last))
    return "Verifying this assignment on the tablet.";
  if (/save_parent_assignment|inspect_parent_assignment/.test(last))
    return "Opening this lesson and saving your assignment change.";
  if (/teardown/.test(last))
    return "Returning the tablet to the profile picker.";
  if (/fixed_point/.test(last))
    return "Checking that the final queue is stable.";
  if (/add_and_verify|remove_and_verify/.test(last))
    return "Updating and verifying reading assignments.";
  if (/review_assignments/.test(last))
    return "Reviewing lesson scores and mastery.";
  if (/plan_queue/.test(last)) return "Choosing the next reading lessons.";
  return "Connecting to the tablet and opening Teacher view.";
}
function renderAssignmentRequests(data) {
  teacherSession = data.teacher_session || "closed";
  setText(
    "parent-session",
    teacherSession === "warm"
      ? "Teacher view is ready for another assignment. It logs out 60 seconds after the last update."
      : "",
  );
  element("parent-session").hidden = teacherSession !== "warm";
  if (!Array.isArray(data.assignment_requests)) return false;
  const known = new Set(
    data.assignment_requests.map((r) =>
      assignmentKey(r.student, r.assignment.title, r.assignment.variant),
    ),
  );
  for (const feedback of assignmentFeedbacks.values())
    if (
      feedback.accepted &&
      assignmentPending(feedback.key) &&
      !known.has(feedback.key)
    ) {
      feedback.state = "error";
      feedback.message =
        "No current request found. Check the tablet before trying this update again.";
    }
  let position = 0;
  for (const request of data.assignment_requests) {
    const assignment = request.assignment;
    const key = assignmentKey(
      request.student,
      assignment.title,
      assignment.variant,
    );
    const local = assignmentFeedbacks.get(key);
    if (request.state === "queued") position++;
    if (local?.state === "sending" && !local.accepted) continue;
    const state = {
      running: "working",
      queued: "queued",
      succeeded: "success",
      failed: "error",
      blocked: "error",
    }[request.state];
    const message =
      request.state === "queued"
        ? `Queued · ${position === 1 ? "next" : "position " + position} after the current tablet operation.`
        : request.state === "running"
          ? friendlyPhase(data.phase)
          : request.state === "succeeded"
            ? (assignment.action === "assign" ? "Assigned" : "Unassigned") +
              " · verified on tablet."
            : request.error || "Not applied. Check the tablet before retrying.";
    const feedback = { ...assignment, key, accepted: true, state, message };
    assignmentFeedbacks.set(key, feedback);
    if (
      data.student === request.student &&
      data.assignment?.title === assignment.title &&
      data.assignment?.variant === assignment.variant
    )
      assignmentFeedback = feedback;
  }
  const requests = data.assignment_requests.filter(
    (r) => r.student === element("student").value,
  );
  const pending = requests.filter((r) =>
    ["queued", "running"].includes(r.state),
  );
  element("assignment-requests").hidden = requests.length === 0;
  setText(
    "assignment-requests-summary",
    pending.length
      ? `Assignment requests · ${pending.length} in progress or queued`
      : "Assignment requests · latest updates",
  );
  element("assignment-requests-list").replaceChildren(
    ...(pending.length ? pending : requests.slice(-5)).map((request) => {
      const row = node("li");
      row.append(
        node(
          "span",
          "lesson-name",
          `${request.assignment.title} — ${request.assignment.variant}`,
        ),
        node(
          "span",
          "muted",
          `${request.assignment.action === "assign" ? "Assign" : "Unassign"} · ${{ queued: "Queued", running: "Updating on tablet", succeeded: "Verified", failed: "Failed", blocked: "Not applied" }[request.state]}`,
        ),
      );
      return row;
    }),
  );
  const signature = JSON.stringify(
    requests
      .filter((r) => !["queued", "running"].includes(r.state))
      .map((r) => [r.id, r.state]),
  );
  const changed = signature !== completedRequestsSignature;
  completedRequestsSignature = signature;
  return changed;
}
async function status() {
  const sequence = ++statusSequence;
  const diagnostics = element("setup").open && element("diagnostics").open;
  const data = await api("/api/status" + (diagnostics ? "?diagnostics=1" : ""));
  if (sequence !== statusSequence) return;
  if (
    data.report &&
    data.student === element("student").value &&
    !validReport(data.report, data.student)
  )
    throw new Error(
      "The sync returned an incomplete result. Open troubleshooting; no verified outcome can be shown.",
    );
  connected = true;
  running = ["running", "stopping", "recovering"].includes(data.state);
  stopping = data.state === "stopping";
  currentRecoveryState = data.recovery_state || "none";
  manualRunning = running && !!data.assignment;
  if (running || data.student === element("student").value) {
    workflowManual = !!data.assignment;
    currentPhase = data.phase || "";
  }
  if (submitted && !running && data.state === "idle") {
    updateButton();
    return;
  }
  const selected = data.student === element("student").value;
  const requestsChanged = renderAssignmentRequests(data);
  if (
    !Array.isArray(data.assignment_requests) &&
    selected &&
    data.assignment &&
    !(submitted && assignmentFeedback && !assignmentFeedback.accepted)
  ) {
    const key = assignmentKey(
      data.student,
      data.assignment.title,
      data.assignment.variant,
    );
    if (running || assignmentFeedback?.key === key) {
      rememberAssignmentFeedback({
        ...data.assignment,
        key,
        accepted: true,
        state: running ? "working" : "error",
        message: running
          ? friendlyPhase(data.phase)
          : data.report?.error ||
            "The update could not be verified. Reconnect to check before retrying.",
      });
      if (
        data.state === "succeeded" &&
        data.report?.queue_count != null &&
        ["applied", "no_op"].includes(data.report.status)
      ) {
        const assigned = data.report.assigned.some(
          (a) =>
            a.title === data.assignment.title &&
            a.variant === data.assignment.variant,
        );
        if (assigned === (data.assignment.action === "assign")) {
          assignmentFeedback.state = "success";
          assignmentFeedback.message = assigned
            ? "Assigned · verified on tablet."
            : "Unassigned · verified on tablet.";
        }
      }
    }
  }
  progressVisible =
    running || (selected && ["succeeded", "failed"].includes(data.state));
  progressFraction = Number.isFinite(data.progress_fraction)
    ? Math.max(0, Math.min(1, data.progress_fraction))
    : 0;
  const state = running || selected ? data.state : "idle";
  document.body.dataset.state = state;
  setText(
    "state",
    {
      idle: ready
        ? archivedStudents.includes(element("student").value)
          ? "Archived profile · history is read-only."
          : "Ready for your next check-in."
        : "Complete setup below to get started.",
      running: data.assignment
        ? "Updating " + readerName(data.student) + "'s lesson…"
        : "Syncing " + readerName(data.student) + "'s progress…",
      stopping: "Stopping safely at the next Android boundary…",
      recovering: "A sync is still running after the dashboard restarted.",
      succeeded:
        data.report?.status === "review_required"
          ? "Review ready. No changes applied."
          : "Check-in complete.",
      failed:
        data.recovery_state === "cleanup_required"
          ? "Previous check-in saved. Ready for a fresh sync."
          : data.recovery_state === "review_required"
          ? "Stopped after a partial update. Review required."
          : data.recovery_state === "safe_to_retry"
            ? "Stopped before any assignment changes."
            : "Check-in stopped. Your attention is needed.",
    }[state] || "Checking the local app…",
  );
  setText(
    "phase",
    running
      ? (data.assignment
          ? `${data.assignment.action === "assign" ? "Assigning" : "Unassigning"} ${data.assignment.title} — ${data.assignment.variant} · `
          : "") +
        (data.state === "stopping"
          ? "No new actions will start. If a change began, the live queue will be reconciled."
          : data.state === "recovering"
            ? "The existing operation is being monitored and will not be replayed."
            : friendlyPhase(data.phase))
      : selected && data.assignment && data.state === "succeeded"
        ? "Only your requested variant was checked; no mastery sync was run."
        : data.state === "failed" && data.recovery_state === "cleanup_required"
          ? "The earlier Home-screen cleanup did not finish. A fresh sync will recheck the tablet before doing anything."
        : data.state === "failed" && data.recovery_state === "review_required"
          ? "Verified changes remain saved. Nothing will be replayed automatically."
          : data.state === "failed" && data.recovery_state === "safe_to_retry"
            ? "No assignment change began. Check the tablet connection before retrying."
        : "Your tablet is checked during each sync.",
  );
  if (selected && data.assignment && assignmentFeedback?.state === "success")
    setText(
      "state",
      `${data.assignment.title} — ${data.assignment.variant}: ${assignmentFeedback.message}`,
    );
  setText(
    "elapsed",
    running && data.elapsed_seconds != null
      ? Math.floor(data.elapsed_seconds) + "s elapsed"
      : "",
  );
  if (diagnostics)
    setText(
      "progress",
      data.output || "No diagnostic output for this check-in.",
    );
  if (data.report && selected) {
    renderReport(data.report);
  }
  jobProblem =
    data.state === "failed" && selected
      ? data.recovery_state === "cleanup_required"
        ? "The previous result is saved. Start a fresh sync now, or unpin Khan Kids and finish the old Home-screen cleanup."
        : data.report?.error ||
        (data.assignment
          ? "The assignment session stopped. Any verified changes remain saved; check the tablet before retrying."
          : "The sync stopped before it could finish normally. Leave unexpected screens visible and open troubleshooting for details.")
      : "";
  if (data.state === "failed" && selected) {
    failedOperationKey = JSON.stringify([
      data.run_id || "unknown-run",
      data.student,
      data.report?.timestamp || "unknown-time",
    ]);
    // A saved teardown-only failure must not trap tomorrow's session. A fresh
    // sync performs its own connection/app-pinning preflight before navigation.
    needsConnectionCheck =
      data.recovery_state !== "cleanup_required" &&
      checkedFailureKey !== failedOperationKey;
    if (!needsConnectionCheck) jobProblem = "";
  }
  if (running)
    setText(
      "result-meta",
      manualRunning
        ? "Assignment update in progress · last verified queue shown below"
        : "Sync in progress · previous check-in shown below",
    );
  else if (data.state === "failed" && selected && !data.report)
    setText("result-meta", "Sync stopped · previous check-in shown below");
  showProblems();
  updateButton();
  if (
    requestsChanged ||
    (selected &&
      ["succeeded", "failed"].includes(data.state) &&
      !(submitted && assignmentFeedback && !assignmentFeedback.accepted))
  ) {
    const key = JSON.stringify([
      data.student,
      data.state,
      data.assignment,
      data.report,
    ]);
    if (requestsChanged || key !== completedJourneyKey) {
      completedJourneyKey = key;
      if (requestsChanged) await latest();
      // Queue evidence is already verified; don't depend on a second request
      // succeeding before the currently open lesson reflects its new state.
      document
        .querySelectorAll(".assignment-controls[data-assignment-key]")
        .forEach((controls) => {
          const focused = controls.contains(document.activeElement);
          const replacement = assignmentControls(
            controls.dataset.title,
            controls.dataset.variant,
            controls.dataset.showMastery === "true",
          );
          controls.replaceWith(replacement);
          if (focused)
            replacement.querySelector("button")?.focus({ preventScroll: true });
        });
      await loadJourneys();
    }
  }
}
for (const id of ["setup-link", "error-setup"])
  element(id).addEventListener("click", () => {
    element("setup").open = true;
    element("setup-toggle").focus();
  });
async function reconnect() {
  if (retrying || authFailed) return;
  retrying = true;
  element("refresh").disabled = true;
  element("retry").disabled = true;
  updateButton();
  try {
    await setup();
    await status();
    connectionProblem = "";
    await latest();
    await loadJourneys();
    showProblems();
  } catch (e) {
    connected = false;
    error(e.message);
  } finally {
    retrying = false;
    element("refresh").disabled = false;
    element("retry").disabled = false;
    updateButton();
    schedulePoll();
  }
}
element("refresh").addEventListener("click", reconnect);
element("retry").addEventListener("click", reconnect);
element("diagnostics").addEventListener("toggle", () => {
  if (element("diagnostics").open) status().catch((e) => error(e.message));
});
element("student").addEventListener("change", () => {
  storage("tutor-student", element("student").value);
  jobProblem = "";
  renderReport(null);
  renderJourney();
  updateButton();
  latest().catch((e) => error(e.message));
  status().catch((e) => error(e.message));
});
element("sync").addEventListener("click", () => startWorkflow());
element("check-connection").addEventListener("click", () =>
  checkTabletConnection().catch(() => {}),
);
element("finish-cleanup").addEventListener("click", async () => {
  if (finishingCleanup) return;
  finishingCleanup = true;
  tabletProblem = "";
  setText("state", "Finishing tablet cleanup…");
  setText("phase", "Checking app pinning, then returning Android to Home. The saved sync will not run again.");
  updateButton();
  try {
    const result = await api("/api/finish-cleanup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      timeoutMs: 30000,
    });
    renderConnectionChecks(result.checks);
    needsConnectionCheck = false;
    reportProblem = jobProblem = "";
    await status();
    await latest();
  } catch (e) {
    renderConnectionChecks(e.payload?.checks || []);
    tabletProblem = e.message;
    showProblems();
  } finally {
    finishingCleanup = false;
    updateButton();
  }
});
element("stop-sync").addEventListener("click", async () => {
  if (!running || stopping) return;
  stopping = true;
  setText("state", "Requesting a safe stop…");
  updateButton();
  try {
    await api("/api/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    await status();
  } catch (e) {
    jobProblem = e.message;
    showProblems();
  }
});
function schedulePoll() {
  clearTimeout(timer);
  if (authFailed || retrying) return;
  timer = setTimeout(
    poll,
    document.hidden
      ? 30000
      : running ||
          submitted ||
          teacherSession !== "closed" ||
          [...assignmentFeedbacks.keys()].some(assignmentPending) ||
          !connected
        ? 1000
        : 10000,
  );
}
async function poll() {
  try {
    await status();
  } catch (e) {
    connected = false;
    document.body.dataset.state = "offline";
    setText("state", "Local app unavailable. Reconnect to continue.");
    setText("phase", "A sync may still be running on your computer.");
    for (const feedback of assignmentFeedbacks.values())
      if (["queued", "working"].includes(feedback.state))
        feedback.message =
          "Connection lost. The tablet may still be working; reconnect to check before retrying.";
    updateButton();
    error(e.message);
  }
  schedulePoll();
}
document.addEventListener("visibilitychange", () => {
  updateActivityMotion();
  if (!document.hidden && !authFailed) poll();
  else schedulePoll();
});
function updateActivityMotion() {
  element("activity").classList.toggle(
    "motion-paused",
    document.hidden || !activityOnscreen,
  );
}
if ("IntersectionObserver" in window) {
  new IntersectionObserver(([entry]) => {
    activityOnscreen = entry.isIntersecting;
    updateActivityMotion();
  }).observe(element("activity"));
}
(async function initialize() {
  try {
    await setup();
    for (const load of [latest, status, loadJourneys]) {
      try {
        await load();
      } catch (e) {
        error(e.message);
      }
    }
  } catch (e) {
    error(e.message);
  }
  updateButton();
  schedulePoll();
})();

const journeyStates = {
  mastered: "Mastery evidence",
  practicing: "Practicing",
  not_assessed: "Not assessed",
  next: "Next practice",
};
async function loadJourneys() {
  const sequence = ++journeySequence;
  try {
    const data = await api("/api/journey");
    if (sequence !== journeySequence) return;
    if (!Array.isArray(data.readers) || !data.readers.every(validJourney))
      throw new Error(
        "Reading progress is unavailable. Reconnect to try again.",
      );
    journeys = data.readers;
    journeySignature = "";
    renderJourney(data.error);
    lastReport = "";
    renderReport(displayedReport);
    updateButton();
  } catch (e) {
    if (sequence !== journeySequence) return;
    setText(
      "journey-status",
      "Reading progress couldn't be refreshed. " + e.message,
    );
    element("journey-status").hidden = false;
    element("journey").setAttribute("aria-busy", "false");
  }
}
function validJourney(reader) {
  if (
    !reader ||
    ![...element("student").options].some(
      (option) => option.value === reader.student,
    ) ||
    typeof reader.available !== "boolean" ||
    !Array.isArray(reader.warnings)
  )
    return false;
  if (!reader.available) return true;
  return (
    [
      "mastered_families",
      "total_families",
      "weekly_mastered",
      "weekly_attempts",
    ].every((key) => Number.isInteger(reader[key]) && reader[key] >= 0) &&
    Array.isArray(reader.assessment_checks) &&
    reader.forecast &&
    typeof reader.forecast.reason === "string" &&
    Array.isArray(reader.milestones) &&
    (reader.phases === undefined ||
      (Array.isArray(reader.phases) &&
        reader.phases.every(
          (phase) =>
            phase &&
            typeof phase.id === "string" &&
            typeof phase.title === "string" &&
            typeof phase.description === "string" &&
            journeyStates[phase.state] &&
            Array.isArray(phase.milestone_ids) &&
            phase.milestone_ids.every((id) => typeof id === "string") &&
            ["mastered", "total", "weekly_gain"].every(
              (key) => Number.isInteger(phase[key]) && phase[key] >= 0,
            ),
        ))) &&
    reader.milestones.every(
      (milestone) =>
        milestone &&
        typeof milestone.title === "string" &&
        journeyStates[milestone.state] &&
        Array.isArray(milestone.lessons) &&
        milestone.lessons.every(
          (lesson) =>
            lesson &&
            typeof lesson.title === "string" &&
            Array.isArray(lesson.contexts) &&
            Array.isArray(lesson.activities) &&
            lesson.activities.every(
              (activity) =>
                activity &&
                typeof activity.variant === "string" &&
                Array.isArray(activity.scores) &&
                Array.isArray(activity.attempts),
            ),
        ),
    )
  );
}
function renderJourney(problem = "") {
  const selected = element("student").value;
  const signature = JSON.stringify([selected, journeys, problem]);
  if (signature === journeySignature) return;
  const sameReader = element("milestones").dataset.reader === selected;
  const activeHeading = document.activeElement;
  const focusedMilestone =
    sameReader && activeHeading?.matches(".milestone > summary")
      ? activeHeading.parentElement.dataset.milestone
      : null;
  const previousPhases = new Map(
    sameReader
      ? [...element("milestones").querySelectorAll(".journey-phase")].map(
          (detail) => [detail.dataset.phase, detail.open],
        )
      : [],
  );
  const previousMilestones = new Set(
    sameReader
      ? [...element("milestones").querySelectorAll(".milestone[open]")].map(
          (d) => d.dataset.milestone,
        )
      : [],
  );
  const previousLessons = new Map(
    sameReader
      ? [...element("milestones").querySelectorAll(".journey-lesson")].map(
          (d) => [
            d.dataset.lesson,
            {
              open: d.open,
              history: d.querySelector(".lesson-history")?.open,
              selected: d.classList.contains("is-selected"),
            },
          ],
        )
      : [],
  );
  const previousGroups = new Set(
    sameReader
      ? [
          ...element("milestones").querySelectorAll(
            ".mastered-lessons[open], .unrecorded-lessons[open]",
          ),
        ].map(
          (d) => d.closest(".milestone").dataset.milestone + ":" + d.className,
        )
      : [],
  );
  journeySignature = signature;
  element("journey").setAttribute("aria-busy", "false");
  const reader = journeys.find((reader) => reader.student === selected);
  const seen = new Set();
  const recently = reader?.available
    ? reader.milestones
        .flatMap((m) => m.lessons)
        .filter((lesson) => {
          if (seen.has(lesson.title)) return false;
          seen.add(lesson.title);
          return true;
        })
        .flatMap((lesson) =>
          lesson.activities
            .filter(
              (activity) =>
                activity.state === "mastered" &&
                activity.first_mastery_confidence === "exact" &&
                activity.first_mastery_date >= reader.weekly_start &&
                activity.first_mastery_date <= reader.as_of,
            )
            .map((activity) => ({
              title: lesson.title,
              variant: activity.variant,
              date: activity.first_mastery_date,
              score: activity.first_mastery_score,
            })),
        )
        .sort(
          (a, b) =>
            b.date.localeCompare(a.date) ||
            a.title.localeCompare(b.title) ||
            a.variant.localeCompare(b.variant),
        )
    : [];
  function recentItem(activity) {
    const item = node("li", "recent-lesson");
    const heading = node("span", "recent-heading");
    heading.append(
      node("strong", "", activity.title + " — " + activity.variant),
      node(
        "span",
        "qualifying-score",
        Number.isInteger(activity.score)
          ? "Mastered with " + activity.score + "%"
          : "Qualifying score unavailable",
      ),
    );
    item.append(
      icon("check"),
      heading,
      node("span", "muted", formatJourneyDate(activity.date)),
      assignmentControls(activity.title, activity.variant),
    );
    return item;
  }
  element("recent-mastery-list").replaceChildren(
    ...(recently.length
      ? recently.slice(0, 5).map(recentItem)
      : [
          node(
            "li",
            "muted",
            reader?.available
              ? "No new mastery evidence recorded in the last 7 days."
              : "Weekly reading history is not available yet.",
          ),
        ]),
  );
  if (recently.length > 5) {
    const more = node("li", "recent-more");
    const button = node(
      "button",
      "secondary",
      `Show all ${recently.length} recently mastered activities`,
    );
    button.type = "button";
    button.addEventListener("click", () => {
      const list = element("recent-mastery-list");
      list.replaceChildren(...recently.map(recentItem));
      list.tabIndex = -1;
      list.focus();
    });
    more.append(button);
    element("recent-mastery-list").append(more);
  }
  setText("journey-title", "The reading journey · " + readerName());
  element("journey-content").hidden = !reader?.available;
  element("journey-status").hidden = Boolean(reader?.available);
  setText(
    "journey-status",
    problem ||
      reader?.warnings?.join(" ") ||
      "No scored reading history is available yet. Sync after a session to start building an evidence-backed picture.",
  );
  setText(
    "journey-freshness",
    reader?.captured_on
      ? `${reader.archived ? "Archive captured" : "Last sync"}: ${formatJourneyDate(reader.captured_on)}`
      : "Local records · freshness not verified",
  );
  updateButton();
  if (!reader?.available) return;
  setText(
    "journey-focus",
    reader.archived ? "Archived reading history" : reader.current_focus,
  );
  const next = reader.recommendations?.[0];
  setText(
    "journey-next",
    next
      ? "Try next: " + next.title + " — " + next.variant
      : reader.archived
        ? "Progress is preserved without connecting to this reader's account."
        : "Sync to review the best next practice from the ten assigned lessons.",
  );
  setText(
    "journey-weekly",
    `${reader.weekly_mastered} topic${reader.weekly_mastered === 1 ? "" : "s"} mastered · ${reader.weekly_attempts} scored attempts`,
  );
  setText(
    "journey-window",
    `${formatJourneyDate(reader.weekly_start)}–${formatJourneyDate(reader.as_of)} · dated practice only`,
  );
  setText(
    "journey-coverage",
    `${reader.mastered_families} of ${reader.total_families} mapped topics mastered across the reading map and supporting skills.`,
  );
  setText("reading-level", "Reading level: " + reader.reading_level);
  element("journey-meter").max = reader.total_families || 1;
  element("journey-meter").value = reader.mastered_families;
  setText("journey-forecast", reader.forecast.reason);
  setText("coverage-explanation", reader.coverage_note);
  setText(
    "journey-unmapped",
    reader.unmapped_attempts
      ? `${reader.unmapped_attempts} scored attempts fall outside these reading categories and are not included.`
      : "All recorded attempts match a mapped reading category.",
  );
  element("reading-check-list").replaceChildren(
    ...reader.assessment_checks.map((check) => node("li", "", check)),
  );
  element("journey-warnings").replaceChildren(
    ...reader.warnings.map((warning) => node("li", "", warning)),
  );
  const exposure = reader.unscored_exposures || 0;
  element("journey-exposure").hidden = !exposure;
  setText(
    "journey-exposure",
    `${exposure} unscored reading or listening records are preserved separately. They show exposure, not demonstrated mastery.`,
  );
  const currentId = reader.archived
    ? null
    : reader.current_milestone_id ||
      reader.milestones.find((m) => m.title === reader.current_focus)?.id;
  element("milestones").dataset.reader = selected;
  const milestoneElements = reader.milestones.map((milestone) => {
    const detail = node("details", "milestone");
    detail.dataset.milestone = milestone.id;
    const summary = node("summary", "");
    const title = node("span", "milestone-title");
    title.append(
      node("strong", "", milestone.title),
      node("span", "muted", milestone.description),
    );
    if (milestone.id === currentId) {
      detail.classList.add("is-current");
      title.append(node("span", "current-focus-label", "Current focus"));
    }
    const counts = journeyCounts(milestone, "milestone-counts");
    summary.append(title, counts);
    detail.append(summary);
    let loaded = false;
    detail.addEventListener("toggle", () => {
      if (!detail.open || loaded) return;
      loaded = true;
      const remaining = milestone.lessons.filter(
        (lesson) => lesson.state !== "mastered",
      );
      const practicing = remaining.filter(
        (lesson) => lesson.state !== "not_assessed",
      );
      const unknown = remaining.filter(
        (lesson) => lesson.state === "not_assessed",
      );
      const mastered = milestone.lessons.filter(
        (lesson) => lesson.state === "mastered",
      );
      const evidenceByTitle = new Map();
      function evidenceFor(lesson) {
        if (!evidenceByTitle.has(lesson.title))
          evidenceByTitle.set(
            lesson.title,
            lessonEvidence(lesson, previousLessons.get(lesson.title)),
          );
        return evidenceByTitle.get(lesson.title);
      }
      function groupedLessons(label, lessons, className) {
        const group = node("details", className);
        group.append(node("summary", "", `${label} · ${lessons.length}`));
        let populated = false;
        function populate() {
          if (populated) return;
          populated = true;
          group.append(...lessons.map(evidenceFor));
        }
        group.addEventListener("toggle", () => {
          if (group.open) populate();
        });
        if (
          previousGroups.has(milestone.id + ":" + className) ||
          lessons.some((lesson) => previousLessons.get(lesson.title)?.open)
        ) {
          populate();
          group.open = true;
        }
        return { group, populate };
      }
      const completed = groupedLessons(
        "Mastery recorded",
        mastered,
        "mastered-lessons",
      );
      const unrecorded = groupedLessons(
        "No recorded scores",
        unknown,
        "unrecorded-lessons",
      );
      const isAlphabet = Boolean(
        milestone.lessons.length &&
        milestone.lessons.every((lesson) =>
          /^(Lowercase|Uppercase) [a-zA-Z]$/.test(lesson.title),
        ),
      );
      if (isAlphabet) {
        const alphabet = node("div", "alphabet-map");
        alphabet.setAttribute("role", "group");
        alphabet.setAttribute("aria-label", milestone.title + " mastery map");
        let selectedEvidence = null;
        const selectedPanel = node("div", "selected-letter-evidence");
        let selectionFrame = 0;
        for (const lesson of milestone.lessons) {
          const button = node(
            "button",
            "letter-cell " + lesson.state,
            lesson.title.slice(-1),
          );
          button.type = "button";
          button.setAttribute(
            "aria-label",
            `${lesson.title}: ${journeyStates[lesson.state]}${masteredVariants(lesson).length ? " — " + masteredVariants(lesson).join(", ") : ""}. Show scores`,
          );
          if (lesson.state === "mastered") button.append(icon("check"));
          if (previousLessons.get(lesson.title)?.selected) {
            selectedEvidence = evidenceFor(lesson);
            selectedPanel.append(selectedEvidence);
            button.setAttribute("aria-current", "true");
          }
          button.addEventListener("click", () => {
            const evidence = evidenceFor(lesson);
            evidence.open = true;
            if (selectedEvidence) {
              selectedEvidence.classList.remove("is-selected");
              const previousTopic = milestone.lessons.find(
                (topic) => topic.title === selectedEvidence.dataset.lesson,
              );
              if (
                previousTopic?.state === "practicing" &&
                selectedEvidence !== evidence
              )
                list.append(selectedEvidence);
            }
            selectedEvidence = evidence;
            selectedPanel.replaceChildren(evidence);
            evidence.classList.add("is-selected");
            for (const cell of alphabet.querySelectorAll("button"))
              cell.removeAttribute("aria-current");
            button.setAttribute("aria-current", "true");
            cancelAnimationFrame(selectionFrame);
            selectionFrame = requestAnimationFrame(() => {
              if (!evidence.isConnected) return;
              focusJourneyHeading(evidence.querySelector("summary"));
            });
          });
          alphabet.append(button);
        }
        detail.append(
          alphabet,
          node(
            "p",
            "map-legend muted",
            "Checked letters have mastery evidence. Select a letter to see scores.",
          ),
          selectedPanel,
        );
      }
      const list = node("div", "lesson-evidence remaining-lessons");
      detail.append(
        node(
          "h4",
          "",
          practicing.length
            ? `Practice to confirm mastery · ${practicing.length}`
            : unknown.length
              ? "Topics with no recorded scores"
              : "Mastery recorded for every lesson topic",
        ),
      );
      if (practicing.length) {
        detail.append(
          node(
            "p",
            "muted",
            "These topics have recorded practice but still need Main or another non-Basic activity to meet the mastery goal: 100% once, or two consecutive scores of at least 90%. Basic-only scores remain practice evidence.",
          ),
        );
        list.append(
          ...practicing
            .map((lesson) => {
              const evidence = evidenceFor(lesson);
              if (!previousLessons.has(lesson.title))
                evidence.open = practicing.length === 1;
              return evidence.classList.contains("is-selected") && isAlphabet
                ? null
                : evidence;
            })
            .filter(Boolean),
        );
        detail.append(list);
      }
      if (mastered.length && !isAlphabet) {
        detail.append(completed.group);
      }
      if (unknown.length && !isAlphabet)
        detail.append(
          node(
            "p",
            "muted",
            "Missing scores are not failed lessons. Open the unrecorded list to see what has not been assessed.",
          ),
          unrecorded.group,
        );
    });
    detail.open = previousMilestones.has(milestone.id);
    return detail;
  });
  const byId = new Map(
    milestoneElements.map((detail) => [detail.dataset.milestone, detail]),
  );
  const placed = new Set();
  const phases = (reader.phases || []).map((phase) => {
    const detail = node("details", "journey-phase");
    detail.dataset.phase = phase.id;
    if (phase.supporting) detail.classList.add("supporting-phase");
    const current = phase.milestone_ids.includes(currentId);
    if (current) detail.classList.add("is-current");
    const summary = node("summary", "");
    const title = node("span", "phase-title");
    title.append(
      node("strong", "", phase.title),
      node("span", "muted", phase.description),
    );
    if (current)
      title.append(node("span", "current-focus-label", "Current focus"));
    summary.append(title, journeyCounts(phase, "phase-counts"));
    detail.append(summary);
    const content = node("div", "phase-content");
    for (const id of phase.milestone_ids) {
      if (byId.has(id) && !placed.has(id)) {
        content.append(byId.get(id));
        placed.add(id);
      }
    }
    detail.append(content);
    detail.open = previousPhases.has(phase.id)
      ? previousPhases.get(phase.id)
      : current;
    return detail;
  });
  element("milestones").replaceChildren(
    ...phases,
    ...milestoneElements.filter(
      (detail) => !placed.has(detail.dataset.milestone),
    ),
  );
  const practiceLink = element("journey-practice-link");
  practiceLink.hidden = !currentId || !byId.has(currentId);
  practiceLink.onclick = () => {
    const milestone = byId.get(currentId);
    const phase = milestone.closest(".journey-phase");
    if (phase) phase.open = true;
    milestone.open = true;
    requestAnimationFrame(() => {
      const liveMilestone = [
        ...element("milestones").querySelectorAll(".milestone"),
      ].find((detail) => detail.dataset.milestone === currentId);
      if (liveMilestone) {
        const livePhase = liveMilestone.closest(".journey-phase");
        if (livePhase) livePhase.open = true;
        liveMilestone.open = true;
        focusJourneyHeading(liveMilestone.querySelector("summary"));
      }
    });
  };
  if (focusedMilestone && document.activeElement === document.body)
    byId
      .get(focusedMilestone)
      ?.querySelector("summary")
      .focus({ preventScroll: true });
  updateButton();
}
function focusJourneyHeading(heading) {
  if (!heading.isConnected) return;
  heading.focus({ preventScroll: true });
  const rect = heading.getBoundingClientRect();
  if (rect.top < 24 || rect.bottom > innerHeight - 24)
    heading.scrollIntoView({
      block: "start",
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
}
function journeyCounts(item, className) {
  const counts = node("span", className);
  const meter = node("meter", "mastery-meter");
  meter.min = 0;
  meter.max = item.total || 1;
  meter.value = item.mastered;
  meter.setAttribute(
    "aria-label",
    item.title + ": recorded lesson mastery coverage",
  );
  meter.textContent = `${item.mastered} of ${item.total}`;
  counts.append(
    node(
      "span",
      "milestone-state " + item.state,
      item.state === "mastered"
        ? "Topics mastered"
        : item.state === "practicing"
          ? "In progress"
          : item.state === "next"
            ? "Next practice"
            : "Not assessed",
    ),
    meter,
    node("span", "muted", `${item.mastered} of ${item.total} topics mastered`),
  );
  if (item.weekly_gain)
    counts.append(
      node("span", "weekly-gain", `+${item.weekly_gain} this week`),
    );
  return counts;
}
function masteredVariants(lesson) {
  return lesson.activities
    .filter((activity) => activity.state === "mastered")
    .map((activity) => activity.variant);
}
function formatJourneyDate(value) {
  if (!value) return "Date unknown";
  const date = new Date(value.length === 10 ? value + "T12:00:00" : value);
  return Number.isNaN(date.getTime())
    ? "Date unavailable"
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}
function lessonEvidence(lesson, previous = {}) {
  const detail = node("details", "journey-lesson");
  detail.dataset.lesson = lesson.title;
  detail.open = Boolean(previous.open);
  detail.classList.toggle("is-selected", Boolean(previous.selected));
  const summary = node("summary", "lesson-summary");
  const heading = node("span", "lesson-heading");
  heading.append(node("strong", "", lesson.title));
  const recorded = lesson.activities
    .filter((a) => a.variant !== "Basic")
    .flatMap((a) => [
      ...a.scores,
      ...(a.archived_scores || []).map((s) => s.score),
    ]);
  const basicOnly =
    !recorded.length &&
    lesson.activities.some(
      (activity) =>
        activity.variant === "Basic" &&
        (activity.scores.length || activity.archived_scores?.length),
    );
  heading.append(
    node(
      "span",
      "muted",
      recorded.length
        ? `Best recorded non-Basic score: ${Math.max(...recorded)}%`
        : basicOnly
          ? "Only Basic practice recorded; try Main or another activity"
          : "No non-Basic score recorded",
    ),
  );
  const mastered = masteredVariants(lesson);
  if (mastered.length)
    heading.append(
      node("span", "mastered-variants", "Mastered: " + mastered.join(", ")),
    );
  summary.append(heading);
  detail.append(summary);
  if (lesson.state === "mastered")
    detail.append(
      node(
        "p",
        "topic-mastery-note muted",
        "This topic has mastery evidence from a non-Basic activity. Other variants can still need practice; you do not need to complete every variant for topic coverage.",
      ),
    );
  const scores = node("dl", "variant-scores");
  const history = node("details", "lesson-history");
  history.open = Boolean(previous.history);
  history.append(node("summary", "", "Score history & sources"));
  history.append(
    node(
      "p",
      "muted",
      [...new Set(lesson.contexts.map((context) => context.skill))].join(" · "),
    ),
  );
  for (const activity of lesson.activities) {
    const row = node("div", "variant-row");
    const score = activity.scores.length
      ? activity.scores.at(-1) + "%"
      : activity.archived_scores?.length
        ? activity.archived_scores.map((s) => s.score + "%").join(", ")
        : "—";
    row.append(
      node("dt", "", activity.variant),
      node("dd", "variant-value", score),
      node("dd", "variant-status " + activity.state, masteryLabel(activity)),
    );
    const assignment = node("dd", "variant-assignment");
    assignment.append(assignmentControls(lesson.title, activity.variant));
    row.append(assignment);
    scores.append(row);
    const group = node("div", "activity-evidence");
    group.append(
      node("h4", "", activity.variant + " · " + journeyStates[activity.state]),
      node("p", "muted", activity.reason),
    );
    if (activity.archived_scores?.length) {
      group.append(
        node(
          "p",
          "muted",
          "Saved All Progress evidence: " +
            activity.archived_scores
              .map(
                (s) =>
                  `${s.score}% (captured ${formatJourneyDate(s.captured_on)}; lesson date unknown)`,
              )
              .join("; "),
        ),
      );
    }
    if (!activity.attempts.length && !activity.archived_scores?.length)
      group.append(node("p", "muted", "No dated attempts recorded."));
    else if (activity.attempts.length) {
      const list = node("ul", "score-evidence");
      list.append(
        ...activity.attempts.map((attempt) =>
          node(
            "li",
            "",
            `${attempt.score}% · ${formatJourneyDate(attempt.date)}${attempt.date_confidence === "inferred" ? " (year inferred)" : ""}`,
          ),
        ),
      );
      group.append(list);
    }
    history.append(group);
  }
  detail.append(
    scores,
    node(
      "p",
      "muted score-source-note",
      lesson.activities.some((a) => a.archived_scores?.length)
        ? "Saved scores come from All Progress; lesson date unknown. Dated attempts and source details are below."
        : "Latest recorded attempts shown. Open the history for all scores and dates.",
    ),
    history,
  );
  return detail;
}
