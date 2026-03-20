# SuperMarks Development Plan

This plan is derived from `docs/PRODUCT_THESIS.md`.

## Development principle

Build the shortest credible path to a **teacher-first marking workstation**.

Do not treat autonomous grading as the primary milestone. Treat it as a later accelerant layered onto a strong teacher review workflow.

---

## Track 1 — Stabilize the development loop first

### Goal
Reduce debugging hell by making changes cheap to verify.

### Tasks
1. Establish a canonical local development setup for both backend and frontend.
2. Add a single documented smoke path for local development.
3. Ensure backend tests run locally with one command.
4. Ensure frontend build runs locally with one command.
5. Add a minimal frontend verification layer if missing.
6. Add a top-level developer checklist for common debug loops.

### Deliverables
- reproducible backend venv / install commands
- reproducible frontend install/build commands
- one local smoke workflow documented in repo root
- quick diagnosis section for common integration failures

### Why this is first
Without a reliable local loop, every bug becomes slow, and the team will keep debugging deploy/runtime drift instead of product behavior.

---

## Track 2 — Lock the core teacher workflow

### Goal
Make the teacher-operated marking loop strong before expanding AI autonomy.

### Core workflow
1. Create assessment
2. Upload answer key
3. Confirm parsed marking data (label, marks, objective codes, answer-key text) with source-page evidence visible
4. Upload student submission
5. Generate question-aligned review units
6. Review answer with key/criteria visible
7. Enter marks quickly
8. Persist totals and results reliably

### Must-be-solid behaviors
- deterministic page/question mapping
- deterministic review ordering
- reliable persistence for metadata and blobs
- clear review state transitions
- recoverable/editable mark entries
- low-friction navigation between answers

### Deliverables
- explicit review state model
- teacher mark-entry UI and result persistence path
- durable totals calculation
- "what happened and why" diagnostics around mapping/order decisions

---

## Track 3 — Prioritize boring value over clever value

### Goal
Ship the clerical relief first.

### High-priority product slices
- front-page totals capture for exams that already summarize final and objective totals
- fast mark-entry controls for question-level workflows
- per-question scoring workflow where needed
- totals and rollups
- answer/key side-by-side review
- review progress state
- export of marks/results

### Lower priority for now
- highly autonomous grading
- complex rubric inference
- broad AI-first UX claims
- model/provider experimentation that does not improve teacher workflow

---

## Track 4 — Improve observability at the fragile seams

### Goal
Make the system easier to debug where state crosses boundaries.

### Fragile seams to instrument
- blob write/read identity
- metadata ↔ blob consistency
- key page selection
- question/page mapping
- review ordering
- parse/transcription job state transitions

### Deliverables
- structured debug metadata per pipeline stage
- traceable identifiers in logs and API responses where appropriate
- a visible diagnostics panel for key mapping/pipeline decisions

---

## Track 5 — Add AI assistance only where it clearly accelerates teacher work

### Goal
Layer AI onto a working marking workstation.

### Good AI slices
- OCR/transcription to reduce reading/typing burden
- suggested marks that the teacher accepts/edits
- rubric hints
- low-confidence flagging
- exception-first sorting

### Bad AI slices right now
- replacing teacher judgment before trust exists
- optimizing model complexity before review UX is smooth
- adding autonomy without strong correction/edit flows

---

## Recommended near-term roadmap

### Slice A — Local dev reset
- make backend testable locally
- make frontend buildable locally
- document exact local run commands
- add one top-level smoke path

### Slice B — Mark-entry wedge
- teacher mark entry per answer/question
- running totals
- durable results save/load
- simple completion/progress states
- flagged-first submission marking workspace with key/crop/transcription visible

### Slice C — Review workstation polish
- faster navigation
- key/criteria visibility improvements
- diagnostics for mapping/order issues
- correction/edit affordances
- prepare-for-marking recovery inside the marking workspace (missing pages/crops/transcriptions surfaced with one-click prep when recoverable)
- flagged answer-key pages become actionable recovery work: page-level retry, exact page drilldown into parsed-data review, and a clear placeholder path for deeper retry flows
- shipped: marking workspace keyboard-first throughput pass (auto-focus/select mark input, digit-to-mark entry, Ctrl/Cmd+Enter save+advance, [ and ] navigation, J jump to next needs-entry item, clearer completion banner, stronger next-action cue)

### Slice D — Export/report basics
- export marks/results
- simple result summaries by student/question
- shipped: exam-level summary CSV export that mirrors the class reporting view with one row per student, export-ready posture, return point, next action, and teacher-facing objective summary
- shipped: per-objective summary CSV export with one row per objective, export-ready coverage, complete-only averages, current whole-class totals, and strongest/weakest complete-result scanability

### Slice E — AI assist
- transcription assistance
- suggested marks
- confidence flags

---

## Decision filter for future work

Before building a feature, ask:

1. Does this make teacher review faster or clearer?
2. Does this reduce clerical drag?
3. Does this preserve teacher judgment rather than bypass it?
4. Would a teacher still value this if AI grading were weak?

If the answer is mostly no, it is probably not core yet.
