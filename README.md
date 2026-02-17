# SuperMarks MVP Backend

FastAPI backend for an AI-assisted math test marking pipeline (Phase A: single teacher, single machine).

## Features
- FastAPI + SQLModel + SQLite
- File-backed artifact storage under `./data/`
- End-to-end pipeline:
  1. Exam creation
  2. Submission upload (PDF or images)
  3. Build normalized pages
  4. Configure questions and answer regions
  5. Build crops per question
  6. OCR transcription (stub + optional Pix2Text)
  7. Rule-based grading (plus LLM stub)
- Idempotent rebuild behavior for pages/crops/transcriptions/grades

## Project Structure
```text
app/
  main.py
  db.py
  models.py
  schemas.py
  storage.py
  settings.py
  routers/
    exams.py
    submissions.py
    questions.py
  pipeline/
    pages.py
    crops.py
    transcribe.py
    grade.py
  ocr/
    base.py
    stub.py
    pix2text_provider.py
  grading/
    base.py
    rule_based.py
    llm.py
tests/
PLANS.md
pyproject.toml
```

## Run Locally
### 1) Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

### 2) Start API
```bash
uvicorn app.main:app --reload
```

### 3) Run tests
```bash
pytest
```

## Configuration
Environment variables (`SUPERMARKS_` prefix):
- `SUPERMARKS_SQLITE_PATH` (default: `./data/supermarks.db`)
- `SUPERMARKS_DATA_DIR` (default: `./data`)
- `SUPERMARKS_MAX_UPLOAD_MB` (default: `25`)

## API Pipeline Sequence
1. `POST /exams`
2. `POST /exams/{exam_id}/submissions` (multipart)
3. `POST /submissions/{submission_id}/build-pages`
4. `POST /exams/{exam_id}/questions`
5. `POST /questions/{question_id}/regions`
6. `POST /submissions/{submission_id}/build-crops`
7. `POST /submissions/{submission_id}/transcribe?provider=stub`
8. `POST /submissions/{submission_id}/grade?grader=rule_based`
9. `GET /submissions/{submission_id}/results`

## Optional integrations
### PDF support
`POST /build-pages` uses an adapter around `pdf2image`.

To enable PDF conversion:
- Install `pdf2image`
- Install Poppler system binaries

If unavailable, API returns a clear 400 error for PDF submissions.

### Pix2Text OCR
`provider=pix2text` is optional. Install with:
```bash
pip install pix2text
```
If missing, API returns a clear 400 error.

## Notes
- `llm` grader is intentionally a stub (`NotImplementedError`) and does not call external APIs.
- CORS is permissive for localhost dev.
