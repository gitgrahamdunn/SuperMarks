# SuperMarks MVP Backend Plan (Phase A)

## Goal
Build a single-machine FastAPI backend for exam ingestion, page extraction, question-region cropping, OCR transcription, and rubric-based grading with SQLite persistence.

## Architecture
- **API Layer (FastAPI routers)**
  - `exams`: exam lifecycle + upload + question management
  - `submissions`: pipeline execution + result retrieval
  - `questions`: region replacement
- **Domain/Storage Layer**
  - SQLModel models and relationships in `app/models.py`
  - Pydantic/SQLModel request-response schemas in `app/schemas.py`
  - DB session and startup initialization in `app/db.py`
  - File operations via `app/storage.py`
- **Pipeline Layer**
  - `pages.py`: uploaded file -> normalized page PNGs
  - `crops.py`: question regions -> stitched answer crop images
  - `transcribe.py`: pluggable OCR provider dispatch
  - `grade.py`: pluggable grader dispatch
- **Provider Interfaces**
  - OCR interface in `ocr/base.py`; providers: `stub.py`, optional `pix2text_provider.py`
  - Grading interface in `grading/base.py`; providers: `rule_based.py`, `llm.py` stub

## End-to-End Pipeline
1. Create exam.
2. Upload one PDF or multiple images to create submission (`UPLOADED`).
3. Build pages (`PAGES_READY`) by converting PDF (if available) or normalizing images.
4. Define questions and per-question regions.
5. Build crops (`CROPS_READY`) by extracting and stitching regions.
6. Transcribe (`TRANSCRIBED`) using selected OCR provider.
7. Grade (`GRADED`) using selected grader.
8. Fetch combined results.

## Idempotency & Transactions
- Rebuild endpoints clear prior rows/artifacts and recreate.
- Each pipeline stage runs in DB transaction boundaries and updates submission status only on success.

## Testing Strategy
- Use pytest + FastAPI TestClient with SQLite temp DB and temp data directory.
- Generate images with PIL in tests (no external binaries).
- Exercise complete happy-path pipeline including grading outputs.

## Optional Dependencies
- PDF conversion using `pdf2image` if installed, with clear error otherwise.
- Pix2Text OCR provider optional and erroring gracefully when missing.
