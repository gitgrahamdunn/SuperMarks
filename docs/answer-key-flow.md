# Answer Key Loading Flow

## Status lifecycle
`DRAFT -> KEY_UPLOADED -> KEY_PAGES_READY -> PARSED -> REVIEWING -> READY -> FAILED`

## Endpoints
- `POST /api/exams`
- `POST /api/exams/{exam_id}/key/upload`
- `POST /api/exams/{exam_id}/key/build-pages`
- `GET /api/exams/{exam_id}/key/pages`
- `GET /api/exams/{exam_id}/key/page/{page_number}`
- `POST /api/exams/{exam_id}/key/parse`
- `PATCH /api/exams/{exam_id}/questions/{question_id}`
- `POST /api/exams/{exam_id}/key/review/complete`

## Parse behavior
- Auto-builds pages if missing.
- Returns 200 with warnings + empty `questions` when no questions can be extracted.
- Includes `request_id`, `model_used`, `confidence_score`, `questions`, `warnings`, and `timings`.
- Persists parse runs and question evidence boxes for trust/review UX.
