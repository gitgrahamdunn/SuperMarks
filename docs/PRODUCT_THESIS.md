# SuperMarks Product Thesis

## Guiding star

SuperMarks is a **teacher-first marking workstation** that helps teachers evaluate student work directly while the system removes the boring administrative drag of marking.

The product should preserve the teacher's connection to student thinking and skill level, not replace it.

## Core promise

SuperMarks helps teachers turn scanned assessments into structured, question-aligned marking workflows so they can:

- review student answers in context
- apply their own judgment against an answer key and criteria
- enter marks quickly and consistently
- avoid repetitive clerical work like score recording, totaling, and result organization

## Product stance

SuperMarks is **not primarily an autonomous grading engine**.

It is a teacher-operated marking system with room for AI assistance.

AI can help with:
- OCR / transcription
- answer segmentation
- suggested marks
- rubric hints
- exception detection

But the teacher remains the evaluator, especially in the core product wedge.

## Jobs to be done

### What teachers want to keep
- seeing how students think
- spotting misconceptions and partial understanding
- making judgment calls on quality and method
- staying connected to student skill levels

### What teachers want removed
- repetitive mark entry
- totaling and transferring scores
- organizing messy scanned pages by question
- hunting through pages to find the right answer region
- clerical overhead after the judgment is already made

## Mark capture modes

SuperMarks supports two primary mark-capture modes:

### 1. Front-page totals capture
Use this when the marked paper already contains the reporting totals the teacher needs to digitize.
That may include:
- one overall total
- multiple objective/category totals on the front page

In this mode, SuperMarks should capture and structure those totals without forcing question-by-question re-entry.
This is now a first-class workflow, not a fallback path.

### 2. Question-level capture
Use this when the paper does not already summarize the marks in the level of detail required, or when the teacher wants to use the in-app marking workflow directly.

## North-star workflow

1. Teacher creates an assessment and answer key.
2. Teacher decides the mark-capture mode:
   - front-page totals
   - question-level marks
3. If question-level mode is used, student papers are uploaded and organized into question-aligned review units.
4. If front-page totals mode is used, SuperMarks captures the overall total and any objective/category totals already summarized on the paper.
5. Teacher confirms the captured data.
6. SuperMarks handles totals, persistence, ordering, and result organization.
7. AI assistance improves speed and signal, but does not become a prerequisite for value.

## Phase ordering

### Phase 1 — Teacher-first capture workstation
Deliver value without requiring autonomous grading:
- reliable assessment ingestion
- front-page totals capture for overall and objective/category totals when already present on paper
- question/page alignment for question-level workflows
- clean teacher review UI
- fast mark entry where question-level capture is required
- durable totals/results storage
- correction/edit history
- export/reporting basics

### Phase 2 — Teacher assist
Add AI where it supports judgment rather than replacing it:
- OCR/transcription
- suggested marks
- rubric hints
- low-confidence / exception flags
- possible misconception clustering

### Phase 3 — Higher autonomy
Only after trust and review flow are strong:
- auto-draft marks
- bulk review queues
- confidence-based triage
- partial automation with human approval

## Product constraints

- Do not optimize for a fully autonomous grading story before the teacher-first review flow is excellent.
- Do not let deployment architecture drive product behavior.
- Do not confuse parsing/AI sophistication with actual user value.
- The first version must already be useful when the teacher is still the one making the grading decision.

## Success criteria

A good early version of SuperMarks should make a teacher say:

- "I can review papers faster without losing touch with student understanding."
- "I spend less time on mark entry and organization."
- "The system helps me stay structured instead of doing the thinking for me."
