# SuperMarks Build Plan

This is the canonical path from the current repo state to a first production-ready SuperMarks release.

SuperMarks is a **teacher-first marking workstation**.
It is **not** being built as an autonomous grading engine first.

## Guiding star

> Teachers can ingest an assessment, confirm parsed marking data, prepare submissions, mark quickly, track progress across the class, and export usable results — without losing connection to student understanding.

If a feature does not make that workflow faster, safer, clearer, or more operationally complete, it is probably not on the shortest path to production.

---

# 1. Current position

SuperMarks is already beyond prototype territory.

## Already working
- local backend/frontend dev loop exists
- backend tests run locally
- frontend builds locally
- answer-key parsing exists
- parse contract is lighter and objective-aware
- flagged parse pages are visible
- real page-level retry exists
- parsed-data confirmation UI exists
- submission marking workspace exists
- teacher manual marks persist durably into results storage
- prepare-for-marking diagnostics and one-click recovery exist
- exam-level marking dashboard exists
- front-page totals capture exists as a first-class submission workflow
- CSV export exists
- safe-retry / stale-asset guardrails exist
- marking workspace throughput polish exists
- teacher workflow UI has received a full polish pass

## Current usable workflow
1. Create exam
2. Upload answer key
3. Parse and confirm key data
4. Upload student submissions
5. Choose the appropriate mark capture path:
   - front-page totals capture when the paper already summarizes the totals/objective totals needed
   - question-level capture when deeper detail is required
6. Prepare assets for marking where question-level capture is used
7. Track progress across submissions
8. Export results

That means the job is no longer “invent the product.”
The job is now “finish the teacher workstation, harden it, and make it trustworthy enough to use repeatedly.”

## Deployment execution plan (current)

### Phase 1 — Local backend first (active)

- Keep the backend running locally on `127.0.0.1:8000` with local config.
- Run the frontend with `VITE_API_BASE_URL=/api` and Vite proxy to local backend.
- Validate via `./scripts/verify-local.sh` after each meaningful stack change.
- Run release-adjacent UI checks against local public host or local production-safe stack before any Cloudflare Pages changes.

### Phase 2 — Hosted backend verification

- Optional: point `VITE_API_BASE_URL` to the Render backend for targeted checks.
- Keep this as a short verification slice, not the default loop.

### Phase 3 — Cloudflare Pages + Render release

- Use Cloudflare Pages for the hosted static frontend only when a release bundle is ready.
- Run the backend on Render as a native Python web service.
- Store hosted metadata in Cloudflare D1 through the Worker-side bridge.
- Move durable uploaded-file storage to Cloudflare R2.

---

# 2. Production target - Main Focus!!!!

SuperMarks reaches its first real production milestone when a teacher can:

- upload the first page of the test that contains the score of the test and student name. This score may be broken out into more than one outcome, e.g. Outcomes A: 16/25, Outcome B: 6/16, etc. and Total
- confirm that data on these totals is parsed in correctly - teacher can verify via a check feature that is fast and smooth - Reads totals grab and displays result next to it
- upload and prepare submissions
- gather the marks an entire class with low friction
- recover from suspicious, missing, or stale states safely
- see class progress clearly
- export trustworthy totals and objective-based results
-Have either an excel or a csv file for export with this information.

without needing engineering help in the middle.

That is the production target.

---

# 3. What production means

## Usability
- no dead ends in the core teacher workflow
- low-friction mark entry across a whole class
- clear next actions at each step
- understandable flagged/blocked states

## Reliability
- parse/review/marking states are durable
- retries do not silently destroy teacher work
- missing/stale assets are surfaced clearly
- exports are trustworthy and reproducible

## Operational usefulness
- per-student and per-question marks are exportable
- objective-based totals are usable
- class progress is visible at a glance
- interrupted work is easy to resume

## Trust
- teacher-entered work is protected
- AI is assistive, not silently authoritative
- uncertainty is surfaced instead of hidden

---

# 4. Ruthless priority order

## Phase 1 — Finish the teacher workstation

This is the highest-priority phase.
Nothing else should outrank it.

## Phase 2 — Harden speed, observability, and state integrity

Once the workstation is strong, make it reliable and less frustrating under real usage.

## Phase 3 — Operational closeout

Make the system easy to finish, export, and use repeatedly.

## Phase 4 — AI assistance beyond the current wedge

Only after the teacher-first workflow is clearly good.

---

# 5. Immediate execution plan

These are the next practical slices from the current repo state.

## NOW

### 5.1 Front-page totals workflow completion
**Why now:**
Front-page totals capture is now a real first-class lane and may be the fastest path to day-one value for many teachers.

**Build:**
- improve evidence presentation for extracted totals
- strengthen queue-through-confirm-next behavior across many submissions
- make mismatches (student name, totals, objectives) clearer and easier to resolve
- make saved vs extracted vs teacher-edited state obvious
- ensure front-page capture exports cleanly and operationally

**Done when:**
- a teacher can move through a stack of front-page totals quickly
- extracted totals feel trustworthy enough to confirm fast
- the workflow does not force question-level entry when the paper already contains the needed totals

### 5.2 Mode-aware whole-class workflow
**Why now:**
The product now has two real capture modes, so the dashboard and queue logic must reflect that explicitly.

**Build:**
- clearer queue segmentation and next-action logic across front-page totals vs question-level submissions
- cleaner movement from exam dashboard into the correct workflow lane
- clearer completion logic across mixed capture modes
- stronger return paths across exam, submission, marking, and totals-confirmation views

**Done when:**
- a teacher can understand what kind of work each submission needs at a glance
- mixed-mode exams still feel coherent and easy to operate

### 5.3 Objective-aware reporting/export across both modes
**Why now:**
Objective-based grading is part of the real product promise, whether totals come from the front page or question-level entry.

**Build:**
- better objective totals in the dashboard
- clearer objective representation in CSV export
- per-student objective summaries
- clearer total vs objective breakdown in UI for both modes

**Done when:**
- a teacher can understand performance by objective without manual spreadsheet cleanup
- exported data preserves objective structure cleanly in both capture modes

### 5.4 Question-level parse performance tuning from measured data
**Why now:**
The question-level path is usable but still slow enough to matter.

**Build:**
- finish timing visibility everywhere it matters
- tune concurrency from real timings
- continue trimming first-pass parse workload
- keep suspicious pages flagged/escalated rather than blindly accepted
- evaluate a better smaller first-pass visual provider when available

**Done when:**
- parse times are measured, not guessed
- a normal answer-key parse feels acceptable in teacher workflow terms
- suspicious pages are surfaced cleanly without excessive latency explosion

---

# 6. Near-term backlog after the immediate slices

## 6.1 Stronger state lineage
**Goal:** reduce ambiguity around stale/generated artifacts.

**Build:**
- clearer timestamps/version lineage for crops/transcriptions/results
- stronger stale detection beyond region replacement alone
- easier operator/debug visibility for asset provenance

**Done when:**
- the system can explain why an asset is missing, stale, valid, or unsafe to rebuild

## 6.2 Production-grade validation
**Goal:** catch teacher-workflow regressions early.

**Build:**
- broader backend integration coverage around parse → prepare → mark → export
- minimal but meaningful frontend/e2e smoke coverage
- explicit protection for production-critical routes

**Done when:**
- the main teacher workflow has automated smoke protection
- regressions are caught before they turn into debugging hell

## 6.3 Finalize exam workflow
**Goal:** give teachers a clear operational finish line.

**Build:**
- exam completion/finalization state
- unresolved flagged-item warnings before finalize
- locked/export-ready state for finished exams

**Done when:**
- a teacher can clearly move an exam from active marking to done

## 6.4 Better export package
**Goal:** make SuperMarks outputs immediately useful outside the app.

**Build:**
- stronger CSV variants
- per-student summary export
- per-objective summary export
- optional JSON export for future integrations

**Done when:**
- export feels like a product feature, not an internal data dump

## 6.5 Multi-class readiness
**Goal:** ensure repeated real-world use stays understandable.

**Build:**
- archive/history views as needed
- stronger list filtering/search where needed
- exam/submission lifecycle cleanup

**Done when:**
- the system still feels clear with many exams/submissions in play

---

# 7. AI roadmap after the workstation is strong

AI should be layered onto a working teacher workflow, not used as a substitute for product clarity.

## Good next AI slices
- OCR/transcription quality improvements
- suggested marks
- confidence flags
- low-confidence routing
- misconception clustering
- exception-first sorting

## Later only
- auto-draft marks
- bulk review queues
- confidence-based prefill
- any autonomy that weakens teacher trust or hides uncertainty

## Rule
If the feature makes the system feel more magical but less trustworthy, it is not ready.

---

# 8. What not to do yet

Do **not** prioritize these over the plan above:
- full autonomous grading
- rich rubric generation for its own sake
- broad AI-provider experimentation without workflow gains
- complicated admin systems unrelated to teacher throughput
- overbuilding recovery/escalation paths before the teacher workstation is excellent

---

# 9. Decision filter

Before building a feature, ask:

1. Does this make teacher marking faster?
2. Does this reduce clerical drag?
3. Does this preserve teacher judgment?
4. Does this make errors and recovery easier to understand?
5. Would a teacher still value this if AI quality were only moderate?

If the answer is mostly no, it is not on the shortest path to production.

---

# 10. Executive summary

## What SuperMarks is
A teacher-first marking workstation.

## What must happen next
1. tighten whole-class navigation and flagged-question flow
2. improve objective-aware reporting/export
3. tune parse speed using measured data
4. harden state lineage and workflow validation
5. add finalization and stronger exports

## What counts as success
A teacher can run a class through the full workflow and get trustworthy results out without engineering help.
