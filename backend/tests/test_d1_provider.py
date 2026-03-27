from __future__ import annotations

from app.d1_bridge import D1Statement
from app.repositories import get_repository_provider
from app.models import BulkUploadPage, ExamBulkUploadFile, ExamIntakeJob, ExamKeyParseJob, ExamKeyParsePage
from app.repositories import d1_provider as d1_provider_module
from app.repositories import d1_bridge_exams
from app.repositories import d1_bridge_questions
from app.repositories import d1_bridge_reporting
from app.repositories import d1_bridge_submissions


class _FakeBridgeClient:
    def __init__(self) -> None:
        self.query_first_calls: list[tuple[str, list[object]]] = []
        self.query_all_calls: list[tuple[str, list[object]]] = []
        self.run_calls: list[tuple[str, list[object]]] = []
        self.batch_calls: list[list[D1Statement]] = []

    def query_first(self, sql: str, params: list[object] | None = None):
        bound_params = list(params or [])
        self.query_first_calls.append((sql, bound_params))
        normalized_sql = " ".join(sql.split())
        if normalized_sql.startswith("INSERT INTO question "):
            return {
                "id": 11,
                "exam_id": bound_params[0],
                "label": bound_params[1],
                "max_marks": bound_params[2],
                "rubric_json": bound_params[3],
                "created_at": bound_params[4],
            }
        if normalized_sql.startswith("UPDATE question "):
            return {
                "id": bound_params[3],
                "exam_id": 7,
                "label": bound_params[0],
                "max_marks": bound_params[1],
                "rubric_json": bound_params[2],
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("INSERT INTO questionregion "):
            return {
                "id": len([sql for sql, _ in self.query_first_calls if "INSERT INTO questionregion" in sql]),
                "question_id": bound_params[0],
                "page_number": bound_params[1],
                "x": bound_params[2],
                "y": bound_params[3],
                "w": bound_params[4],
                "h": bound_params[5],
                "created_at": bound_params[6],
            }
        if "FROM question WHERE exam_id = ? AND id = ?" in normalized_sql:
            return {
                "id": bound_params[1],
                "exam_id": bound_params[0],
                "label": "Q1",
                "max_marks": 4,
                "rubric_json": '{"a":1}',
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if "FROM exam WHERE id = ?" in normalized_sql:
            return {
                "id": bound_params[0],
                "name": "Bridge Exam",
                "created_at": "2026-03-26T00:00:00+00:00",
                "teacher_style_profile_json": None,
                "front_page_template_json": None,
                "class_list_json": None,
                "class_list_source_json": None,
                "status": "DRAFT",
            }
        if normalized_sql.startswith("UPDATE exam SET "):
            return {
                "id": bound_params[-1],
                "name": bound_params[0] if "name = ?" in normalized_sql else "Bridge Exam",
                "created_at": "2026-03-26T00:00:00+00:00",
                "teacher_style_profile_json": None,
                "front_page_template_json": '{"template":1}' if "front_page_template_json = ?" in normalized_sql else None,
                "class_list_json": None,
                "class_list_source_json": None,
                "status": bound_params[0] if normalized_sql.startswith("UPDATE exam SET status = ?") else "REVIEWING",
            }
        if "FROM examkeypage WHERE exam_id = ? AND page_number = ?" in normalized_sql:
            return {
                "id": 8,
                "exam_id": bound_params[0],
                "page_number": bound_params[1],
                "image_path": "/tmp/key-page.png",
                "blob_pathname": "exams/7/key-pages/page_0001.png",
                "blob_url": "https://example/key-page.png",
                "width": 1200,
                "height": 1600,
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if "FROM examkeyparsejob WHERE id = ?" in normalized_sql:
            return {
                "id": bound_params[0],
                "exam_id": 7,
                "status": "running",
                "page_count": 3,
                "pages_done": 1,
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:05+00:00",
                "cost_total": 0.2,
                "input_tokens_total": 100,
                "output_tokens_total": 50,
            }
        if "FROM examkeyparsejob WHERE exam_id = ? ORDER BY created_at DESC, id DESC LIMIT 1" in normalized_sql:
            return {
                "id": 21,
                "exam_id": bound_params[0],
                "status": "done",
                "page_count": 3,
                "pages_done": 3,
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:10+00:00",
                "cost_total": 0.3,
                "input_tokens_total": 120,
                "output_tokens_total": 60,
            }
        if normalized_sql.startswith("INSERT INTO examkeyparsejob "):
            return {
                "id": 31,
                "exam_id": bound_params[0],
                "status": bound_params[1],
                "page_count": bound_params[2],
                "pages_done": bound_params[3],
                "created_at": bound_params[4],
                "updated_at": bound_params[5],
                "cost_total": 0.0,
                "input_tokens_total": 0,
                "output_tokens_total": 0,
            }
        if normalized_sql.startswith("UPDATE examkeyparsejob SET "):
            return {
                "id": bound_params[-1],
                "exam_id": 7,
                "status": "done",
                "page_count": 3,
                "pages_done": 3,
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:10+00:00",
                "cost_total": 0.3,
                "input_tokens_total": 120,
                "output_tokens_total": 60,
            }
        if "FROM examkeyparsepage WHERE job_id = ? AND page_number = ?" in normalized_sql:
            return {
                "id": 41,
                "job_id": bound_params[0],
                "page_number": bound_params[1],
                "status": "pending",
                "confidence": 0.0,
                "model_used": None,
                "result_json": None,
                "error_json": None,
                "cost": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("INSERT INTO examkeyparsepage "):
            return {
                "id": 51,
                "job_id": bound_params[0],
                "page_number": bound_params[1],
                "status": bound_params[2],
                "confidence": 0.0,
                "model_used": None,
                "result_json": None,
                "error_json": None,
                "cost": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "created_at": bound_params[3],
                "updated_at": bound_params[4],
            }
        if normalized_sql.startswith("UPDATE examkeyparsepage SET "):
            return {
                "id": bound_params[-1],
                "job_id": 21,
                "page_number": 1,
                "status": "done",
                "confidence": 0.95,
                "model_used": "gpt-4.1-mini",
                "result_json": '{"questions":[{"label":"Q1"}]}',
                "error_json": None,
                "cost": 0.1,
                "input_tokens": 40,
                "output_tokens": 20,
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:05+00:00",
            }
        if "FROM examintakejob WHERE exam_id = ? ORDER BY created_at DESC, id DESC LIMIT 1" in normalized_sql:
            return {
                "id": 61,
                "exam_id": bound_params[0],
                "bulk_upload_id": 71,
                "status": "running",
                "stage": "detecting_names",
                "page_count": 4,
                "pages_built": 4,
                "pages_processed": 2,
                "submissions_created": 1,
                "candidates_ready": 1,
                "review_open_threshold": 1,
                "initial_review_ready": 0,
                "fully_warmed": 0,
                "review_ready": 0,
                "thinking_level": "low",
                "attempt_count": 1,
                "runner_id": "runner-1",
                "lease_expires_at": None,
                "started_at": None,
                "finished_at": None,
                "last_progress_at": "2026-03-26T00:00:00+00:00",
                "metrics_json": '{"page_count":4}',
                "error_message": None,
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:05+00:00",
            }
        if "FROM examintakejob WHERE id = ?" in normalized_sql:
            return {
                "id": bound_params[0],
                "exam_id": 7,
                "bulk_upload_id": 71,
                "status": "running",
                "stage": "creating_submissions",
                "page_count": 4,
                "pages_built": 4,
                "pages_processed": 4,
                "submissions_created": 2,
                "candidates_ready": 1,
                "review_open_threshold": 1,
                "initial_review_ready": 0,
                "fully_warmed": 0,
                "review_ready": 0,
                "thinking_level": "low",
                "attempt_count": 1,
                "runner_id": "runner-1",
                "lease_expires_at": None,
                "started_at": None,
                "finished_at": None,
                "last_progress_at": "2026-03-26T00:00:00+00:00",
                "metrics_json": '{"page_count":4}',
                "error_message": None,
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:05+00:00",
            }
        if normalized_sql.startswith("UPDATE examintakejob SET "):
            return {
                "id": bound_params[-1],
                "exam_id": 7,
                "bulk_upload_id": 71,
                "status": "running",
                "stage": "warming_initial_review",
                "page_count": 4,
                "pages_built": 4,
                "pages_processed": 4,
                "submissions_created": 2,
                "candidates_ready": 2,
                "review_open_threshold": 1,
                "initial_review_ready": 1,
                "fully_warmed": 0,
                "review_ready": 1,
                "thinking_level": "low",
                "attempt_count": 1,
                "runner_id": "runner-1",
                "lease_expires_at": None,
                "started_at": None,
                "finished_at": None,
                "last_progress_at": "2026-03-26T00:00:05+00:00",
                "metrics_json": '{"page_count":4}',
                "error_message": None,
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:05+00:00",
            }
        if normalized_sql.startswith("INSERT INTO examintakejob "):
            return {
                "id": 62,
                "exam_id": bound_params[0],
                "bulk_upload_id": bound_params[1],
                "status": bound_params[2],
                "stage": bound_params[3],
                "page_count": bound_params[4],
                "pages_built": bound_params[5],
                "pages_processed": bound_params[6],
                "submissions_created": bound_params[7],
                "candidates_ready": bound_params[8],
                "review_open_threshold": bound_params[9],
                "initial_review_ready": bound_params[10],
                "fully_warmed": bound_params[11],
                "review_ready": bound_params[12],
                "thinking_level": bound_params[13],
                "attempt_count": 0,
                "runner_id": None,
                "lease_expires_at": None,
                "started_at": None,
                "finished_at": None,
                "last_progress_at": bound_params[14],
                "metrics_json": bound_params[15],
                "error_message": None,
                "created_at": bound_params[16],
                "updated_at": bound_params[17],
            }
        if "FROM exambulkuploadfile WHERE id = ?" in normalized_sql:
            return {
                "id": bound_params[0],
                "exam_id": 7,
                "original_filename": "bulk.pdf",
                "stored_path": "exams/7/bulk/input.pdf",
                "source_manifest_json": '[{"local_name":"source.pdf"}]',
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("INSERT INTO exambulkuploadfile "):
            return {
                "id": 71,
                "exam_id": bound_params[0],
                "original_filename": bound_params[1],
                "stored_path": bound_params[2],
                "source_manifest_json": None,
                "created_at": bound_params[3],
            }
        if normalized_sql.startswith("UPDATE exambulkuploadfile SET "):
            return {
                "id": bound_params[-1],
                "exam_id": 7,
                "original_filename": bound_params[0],
                "stored_path": bound_params[1],
                "source_manifest_json": bound_params[2] if len(bound_params) == 4 else '[{"local_name":"source.pdf"}]',
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("INSERT INTO bulkuploadpage "):
            return {
                "id": 81,
                "bulk_upload_id": bound_params[0],
                "page_number": bound_params[1],
                "image_path": bound_params[2],
                "width": bound_params[3],
                "height": bound_params[4],
                "detected_student_name": bound_params[5],
                "detection_confidence": bound_params[6],
                "detection_evidence_json": bound_params[7],
                "front_page_usage_json": None,
                "created_at": bound_params[8],
            }
        if normalized_sql.startswith("UPDATE bulkuploadpage SET "):
            return {
                "id": bound_params[-1],
                "bulk_upload_id": 71,
                "page_number": 1,
                "image_path": "/tmp/page1.png",
                "width": 1200,
                "height": 1600,
                "detected_student_name": "Alice Johnson",
                "detection_confidence": 0.92,
                "detection_evidence_json": "{}",
                "front_page_usage_json": None,
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if "FROM submission WHERE id = ?" in normalized_sql:
            return {
                "id": bound_params[0],
                "exam_id": 7,
                "student_name": "Alice Johnson",
                "first_name": "Alice",
                "last_name": "Johnson",
                "status": "UPLOADED",
                "capture_mode": "front_page_totals",
                "front_page_totals_json": None,
                "front_page_candidates_json": '{"student_name":{"value_text":"Alice Johnson","confidence":0.9,"evidence":[]}}',
                "front_page_usage_json": '{"model":"mini"}',
                "front_page_reviewed_at": None,
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("INSERT INTO submission "):
            return {
                "id": 3,
                "exam_id": bound_params[0],
                "student_name": bound_params[1],
                "first_name": bound_params[2],
                "last_name": bound_params[3],
                "status": bound_params[4],
                "capture_mode": bound_params[5],
                "front_page_totals_json": None,
                "front_page_candidates_json": None,
                "front_page_usage_json": None,
                "front_page_reviewed_at": None,
                "created_at": bound_params[6],
            }
        if normalized_sql.startswith("UPDATE submission SET front_page_candidates_json = ?, front_page_usage_json = ?"):
            return {
                "id": bound_params[2],
                "exam_id": 7,
                "student_name": "Alice Johnson",
                "first_name": "Alice",
                "last_name": "Johnson",
                "status": "UPLOADED",
                "capture_mode": "front_page_totals",
                "front_page_totals_json": None,
                "front_page_candidates_json": bound_params[0],
                "front_page_usage_json": bound_params[1],
                "front_page_reviewed_at": None,
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("UPDATE submission SET status = ?"):
            return {
                "id": bound_params[1],
                "exam_id": 7,
                "student_name": "Alice Johnson",
                "first_name": "Alice",
                "last_name": "Johnson",
                "status": bound_params[0],
                "capture_mode": "front_page_totals",
                "front_page_totals_json": None,
                "front_page_candidates_json": None,
                "front_page_usage_json": None,
                "front_page_reviewed_at": None,
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("UPDATE submission SET capture_mode = ?"):
            return {
                "id": bound_params[1],
                "exam_id": 7,
                "student_name": "Alice Johnson",
                "first_name": "Alice",
                "last_name": "Johnson",
                "status": "UPLOADED",
                "capture_mode": bound_params[0],
                "front_page_totals_json": None,
                "front_page_candidates_json": None,
                "front_page_usage_json": None,
                "front_page_reviewed_at": None,
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if "FROM submissionpage WHERE submission_id = ? AND page_number = ?" in normalized_sql:
            return {
                "id": 71,
                "submission_id": bound_params[0],
                "page_number": bound_params[1],
                "image_path": "/tmp/submission-page.png",
                "width": 1200,
                "height": 1600,
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("INSERT INTO submissionpage "):
            return {
                "id": 72,
                "submission_id": bound_params[0],
                "page_number": bound_params[1],
                "image_path": bound_params[2],
                "width": bound_params[3],
                "height": bound_params[4],
                "created_at": bound_params[5],
            }
        if "FROM answercrop WHERE submission_id = ? AND question_id = ?" in normalized_sql:
            return {
                "id": 81,
                "submission_id": bound_params[0],
                "question_id": bound_params[1],
                "image_path": "/tmp/crop.png",
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("INSERT INTO answercrop "):
            return {
                "id": 82,
                "submission_id": bound_params[0],
                "question_id": bound_params[1],
                "image_path": bound_params[2],
                "created_at": bound_params[3],
            }
        if "FROM graderesult WHERE submission_id = ? AND question_id = ?" in normalized_sql:
            if bound_params[0] != 1 or bound_params[1] != 11:
                return None
            return {
                "id": 91,
                "submission_id": bound_params[0],
                "question_id": bound_params[1],
                "marks_awarded": 4.0,
                "breakdown_json": "{}",
                "feedback_json": "{}",
                "model_name": "manual",
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("INSERT INTO graderesult "):
            return {
                "id": 92,
                "submission_id": bound_params[0],
                "question_id": bound_params[1],
                "marks_awarded": bound_params[2],
                "breakdown_json": bound_params[3],
                "feedback_json": bound_params[4],
                "model_name": bound_params[5],
                "created_at": bound_params[6],
            }
        if normalized_sql.startswith("UPDATE graderesult SET "):
            return {
                "id": bound_params[4],
                "submission_id": 1,
                "question_id": 11,
                "marks_awarded": bound_params[0],
                "breakdown_json": bound_params[1],
                "feedback_json": bound_params[2],
                "model_name": bound_params[3],
                "created_at": "2026-03-26T00:00:00+00:00",
            }
        if normalized_sql.startswith("INSERT INTO transcription "):
            return {
                "id": 83,
                "submission_id": bound_params[0],
                "question_id": bound_params[1],
                "provider": bound_params[2],
                "text": bound_params[3],
                "confidence": bound_params[4],
                "raw_json": bound_params[5],
                "created_at": bound_params[6],
            }
        if normalized_sql.startswith("INSERT INTO submissionfile "):
            return {
                "id": 62,
                "submission_id": bound_params[0],
                "file_kind": bound_params[1],
                "original_filename": bound_params[2],
                "stored_path": bound_params[3],
                "blob_url": bound_params[4],
                "blob_pathname": bound_params[5],
                "content_type": bound_params[6],
                "size_bytes": bound_params[7],
                "created_at": bound_params[8],
            }
        return None

    def query_all(self, sql: str, params: list[object] | None = None):
        bound_params = list(params or [])
        self.query_all_calls.append((sql, bound_params))
        normalized_sql = " ".join(sql.split())
        if "FROM examkeypage WHERE exam_id = ?" in normalized_sql:
            return [
                {
                    "id": 8,
                    "exam_id": bound_params[0],
                    "page_number": 1,
                    "image_path": "/tmp/key-page.png",
                    "blob_pathname": "exams/7/key-pages/page_0001.png",
                    "blob_url": "https://example/key-page.png",
                    "width": 1200,
                    "height": 1600,
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM examkeyparsejob WHERE exam_id = ?" in normalized_sql:
            return [
                {
                    "id": 21,
                    "exam_id": bound_params[0],
                    "status": "done",
                    "page_count": 3,
                    "pages_done": 3,
                    "created_at": "2026-03-26T00:00:00+00:00",
                    "updated_at": "2026-03-26T00:00:10+00:00",
                    "cost_total": 0.3,
                    "input_tokens_total": 120,
                    "output_tokens_total": 60,
                }
            ]
        if "FROM examkeyparsepage WHERE job_id = ? AND status = 'pending'" in normalized_sql:
            return [
                {
                    "id": 41,
                    "job_id": bound_params[0],
                    "page_number": 1,
                    "status": "pending",
                    "confidence": 0.0,
                    "model_used": None,
                    "result_json": None,
                    "error_json": None,
                    "cost": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "created_at": "2026-03-26T00:00:00+00:00",
                    "updated_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM examkeyparsepage WHERE job_id = ?" in normalized_sql:
            return [
                {
                    "id": 41,
                    "job_id": bound_params[0],
                    "page_number": 1,
                    "status": "done",
                    "confidence": 0.95,
                    "model_used": "gpt-4.1-mini",
                    "result_json": '{"questions":[{"label":"Q1"}]}',
                    "error_json": None,
                    "cost": 0.1,
                    "input_tokens": 40,
                    "output_tokens": 20,
                    "created_at": "2026-03-26T00:00:00+00:00",
                    "updated_at": "2026-03-26T00:00:05+00:00",
                }
            ]
        if "FROM examintakejob WHERE exam_id IN (" in normalized_sql:
            return [
                {
                    "id": 61,
                    "exam_id": bound_params[0],
                    "bulk_upload_id": 71,
                    "status": "running",
                    "stage": "detecting_names",
                    "page_count": 4,
                    "pages_built": 4,
                    "pages_processed": 2,
                    "submissions_created": 1,
                    "candidates_ready": 1,
                    "review_open_threshold": 1,
                    "initial_review_ready": 0,
                    "fully_warmed": 0,
                    "review_ready": 0,
                    "thinking_level": "low",
                    "attempt_count": 1,
                    "runner_id": "runner-1",
                    "lease_expires_at": None,
                    "started_at": None,
                    "finished_at": None,
                    "last_progress_at": "2026-03-26T00:00:00+00:00",
                    "metrics_json": '{"page_count":4}',
                    "error_message": None,
                    "created_at": "2026-03-26T00:00:00+00:00",
                    "updated_at": "2026-03-26T00:00:05+00:00",
                }
            ]
        if "FROM examintakejob WHERE status IN ('queued', 'running')" in normalized_sql:
            return [
                {
                    "id": 61,
                    "exam_id": 7,
                    "bulk_upload_id": 71,
                    "status": "queued",
                    "stage": "queued",
                    "page_count": 4,
                    "pages_built": 0,
                    "pages_processed": 0,
                    "submissions_created": 0,
                    "candidates_ready": 0,
                    "review_open_threshold": 0,
                    "initial_review_ready": 0,
                    "fully_warmed": 0,
                    "review_ready": 0,
                    "thinking_level": "low",
                    "attempt_count": 0,
                    "runner_id": None,
                    "lease_expires_at": None,
                    "started_at": None,
                    "finished_at": None,
                    "last_progress_at": "2026-03-26T00:00:00+00:00",
                    "metrics_json": '{"page_count":4}',
                    "error_message": None,
                    "created_at": "2026-03-26T00:00:00+00:00",
                    "updated_at": "2026-03-26T00:00:05+00:00",
                }
            ]
        if "FROM bulkuploadpage WHERE bulk_upload_id = ?" in normalized_sql:
            return [
                {
                    "id": 81,
                    "bulk_upload_id": bound_params[0],
                    "page_number": 1,
                    "image_path": "/tmp/page1.png",
                    "width": 1200,
                    "height": 1600,
                    "detected_student_name": "Alice Johnson",
                    "detection_confidence": 0.92,
                    "detection_evidence_json": "{}",
                    "front_page_usage_json": None,
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM submissionfile WHERE submission_id = ?" in normalized_sql:
            return [
                {
                    "id": 61,
                    "submission_id": bound_params[0],
                    "file_kind": "image",
                    "original_filename": "page1.png",
                    "stored_path": "exams/7/submissions/1/page1.png",
                    "blob_url": None,
                    "blob_pathname": None,
                    "content_type": "image/png",
                    "size_bytes": 123,
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM submissionpage WHERE submission_id IN (" in normalized_sql:
            return [
                {
                    "id": 71,
                    "submission_id": bound_params[0],
                    "page_number": 1,
                    "image_path": "/tmp/submission-page.png",
                    "width": 1200,
                    "height": 1600,
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM submissionpage WHERE submission_id = ?" in normalized_sql:
            return [
                {
                    "id": 71,
                    "submission_id": bound_params[0],
                    "page_number": 1,
                    "image_path": "/tmp/submission-page.png",
                    "width": 1200,
                    "height": 1600,
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM answercrop WHERE submission_id = ?" in normalized_sql:
            return [
                {
                    "id": 81,
                    "submission_id": bound_params[0],
                    "question_id": 11,
                    "image_path": "/tmp/crop.png",
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM answercrop WHERE submission_id IN (" in normalized_sql:
            return [
                {
                    "id": 81,
                    "submission_id": bound_params[0],
                    "question_id": 11,
                    "image_path": "/tmp/crop.png",
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM transcription WHERE submission_id = ?" in normalized_sql:
            return [
                {
                    "id": 82,
                    "submission_id": bound_params[0],
                    "question_id": 11,
                    "provider": "stub",
                    "text": "answer",
                    "confidence": 0.9,
                    "raw_json": "{}",
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM transcription WHERE submission_id IN (" in normalized_sql:
            return [
                {
                    "id": 82,
                    "submission_id": bound_params[0],
                    "question_id": 11,
                    "provider": "stub",
                    "text": "answer",
                    "confidence": 0.9,
                    "raw_json": "{}",
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM graderesult WHERE submission_id = ?" in normalized_sql:
            return [
                {
                    "id": 91,
                    "submission_id": bound_params[0],
                    "question_id": 11,
                    "marks_awarded": 4.0,
                    "breakdown_json": "{}",
                    "feedback_json": "{}",
                    "model_name": "manual",
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM graderesult WHERE submission_id IN (" in normalized_sql:
            return [
                {
                    "id": 91,
                    "submission_id": bound_params[0],
                    "question_id": 11,
                    "marks_awarded": 4.0,
                    "breakdown_json": "{}",
                    "feedback_json": "{}",
                    "model_name": "manual",
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM question WHERE exam_id = ?" in normalized_sql:
            return [
                {
                    "id": 11,
                    "exam_id": bound_params[0],
                    "label": "Q1",
                    "max_marks": 4,
                    "rubric_json": '{"parse_order":1}',
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM submission WHERE exam_id = ? AND capture_mode = 'front_page_totals'" in normalized_sql:
            return [
                {
                    "id": 1,
                    "exam_id": bound_params[0],
                    "student_name": "Alice Johnson",
                    "first_name": "Alice",
                    "last_name": "Johnson",
                    "status": "UPLOADED",
                    "capture_mode": "front_page_totals",
                    "front_page_totals_json": None,
                    "front_page_candidates_json": None,
                    "front_page_usage_json": None,
                    "front_page_reviewed_at": None,
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM submission WHERE exam_id = ?" in normalized_sql:
            return [
                {
                    "id": 1,
                    "exam_id": bound_params[0],
                    "student_name": "Alice Johnson",
                    "first_name": "Alice",
                    "last_name": "Johnson",
                    "status": "UPLOADED",
                    "capture_mode": "front_page_totals",
                    "front_page_totals_json": None,
                    "front_page_candidates_json": None,
                    "front_page_usage_json": None,
                    "front_page_reviewed_at": None,
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        if "FROM submission WHERE id IN (" in normalized_sql:
            return [
                {
                    "id": submission_id,
                    "exam_id": 7,
                    "student_name": f"Student {submission_id}",
                    "first_name": "Student",
                    "last_name": str(submission_id),
                    "status": "UPLOADED",
                    "capture_mode": "front_page_totals",
                    "front_page_totals_json": None,
                    "front_page_candidates_json": None,
                    "front_page_usage_json": None,
                    "front_page_reviewed_at": None,
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
                for submission_id in bound_params
            ]
        if "FROM questionregion WHERE question_id = ?" in normalized_sql:
            return [
                {
                    "id": 101,
                    "question_id": bound_params[0],
                    "page_number": 1,
                    "x": 0.1,
                    "y": 0.2,
                    "w": 0.3,
                    "h": 0.4,
                    "created_at": "2026-03-26T00:00:00+00:00",
                },
                {
                    "id": 102,
                    "question_id": bound_params[0],
                    "page_number": 2,
                    "x": 0.2,
                    "y": 0.3,
                    "w": 0.4,
                    "h": 0.5,
                    "created_at": "2026-03-26T00:00:00+00:00",
                },
            ]
        if "FROM questionregion WHERE question_id IN (" in normalized_sql:
            return [
                {
                    "id": 101,
                    "question_id": bound_params[0],
                    "page_number": 1,
                    "x": 0.1,
                    "y": 0.2,
                    "w": 0.3,
                    "h": 0.4,
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            ]
        return [
            {
                "id": 1,
                "exam_id": bound_params[0],
                "label": "Q2",
                "max_marks": 3,
                "rubric_json": '{"parse_order":2}',
                "created_at": "2026-03-26T00:00:00+00:00",
            },
            {
                "id": 2,
                "exam_id": bound_params[0],
                "label": "Q1",
                "max_marks": 5,
                "rubric_json": '{"parse_order":1}',
                "created_at": "2026-03-26T00:00:00+00:00",
            },
        ]

    def run(self, sql: str, params: list[object] | None = None):
        self.run_calls.append((sql, list(params or [])))
        return {"success": True}

    def batch(self, statements: list[D1Statement]):
        self.batch_calls.append(statements)
        return [{"success": True} for _ in statements]


def test_d1_bridge_provider_uses_hybrid_question_repo(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_REPOSITORY_BACKEND", "d1-bridge")
    monkeypatch.delenv("SUPERMARKS_D1_BRIDGE_URL", raising=False)
    monkeypatch.delenv("SUPERMARKS_D1_BRIDGE_TOKEN", raising=False)
    monkeypatch.delenv("D1_BRIDGE_URL", raising=False)
    monkeypatch.delenv("D1_BRIDGE_TOKEN", raising=False)
    get_repository_provider.cache_clear()

    provider = get_repository_provider()

    assert provider.questions is not None
    assert provider.exams is not None
    assert provider.submissions is not None
    assert provider.reporting is not None

    get_repository_provider.cache_clear()


def test_d1_bridge_provider_falls_back_without_bridge_config(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_REPOSITORY_BACKEND", "d1-bridge")
    monkeypatch.delenv("SUPERMARKS_D1_BRIDGE_URL", raising=False)
    monkeypatch.delenv("SUPERMARKS_D1_BRIDGE_TOKEN", raising=False)
    monkeypatch.delenv("D1_BRIDGE_URL", raising=False)
    monkeypatch.delenv("D1_BRIDGE_TOKEN", raising=False)
    monkeypatch.delenv("BACKEND_API_KEY", raising=False)

    assert d1_provider_module._bridge_is_configured() is False
    get_repository_provider.cache_clear()

    provider = get_repository_provider()

    assert provider.questions.list_exam_questions is not d1_bridge_questions.list_exam_questions
    assert provider.reporting.load_exam_reporting_collections is not d1_bridge_reporting.load_exam_reporting_collections

    get_repository_provider.cache_clear()


def test_d1_backend_without_bridge_provider_still_raises(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_REPOSITORY_BACKEND", "d1")

    try:
        try:
            d1_provider_module.get_provider()
        except NotImplementedError as exc:
            assert "d1-bridge" in str(exc)
        else:
            raise AssertionError("expected NotImplementedError")
    finally:
        get_repository_provider.cache_clear()


def test_d1_bridge_questions_crud_and_regions(monkeypatch) -> None:
    fake_client = _FakeBridgeClient()
    original_query_all = fake_client.query_all

    def query_all_with_question_rows(sql: str, params: list[object] | None = None):
        normalized_sql = " ".join(sql.split())
        if "FROM question WHERE exam_id = ?" in normalized_sql:
            bound_params = list(params or [])
            return [
                {
                    "id": 1,
                    "exam_id": bound_params[0],
                    "label": "Q2",
                    "max_marks": 3,
                    "rubric_json": '{"parse_order":2}',
                    "created_at": "2026-03-26T00:00:00+00:00",
                },
                {
                    "id": 2,
                    "exam_id": bound_params[0],
                    "label": "Q1",
                    "max_marks": 5,
                    "rubric_json": '{"parse_order":1}',
                    "created_at": "2026-03-26T00:00:00+00:00",
                },
            ]
        return original_query_all(sql, params)

    fake_client.query_all = query_all_with_question_rows
    monkeypatch.setattr(d1_bridge_questions, "get_d1_bridge_client", lambda: fake_client)

    created = d1_bridge_questions.create_question(None, exam_id=7, label="Q1", max_marks=4, rubric_json='{"a":1}')
    assert created.id == 11
    assert created.exam_id == 7

    listed = d1_bridge_questions.list_exam_questions(None, 7)
    assert [question.label for question in listed] == ["Q2", "Q1"]
    assert d1_bridge_questions.question_sort_key(listed[1]) < d1_bridge_questions.question_sort_key(listed[0])

    updated = d1_bridge_questions.update_question(None, question=created, max_marks=6)
    assert updated.max_marks == 6

    d1_bridge_questions.delete_question_dependencies(None, 11)
    assert len(fake_client.batch_calls[-1]) == 5

    regions = d1_bridge_questions.replace_question_regions(
        None,
        11,
        [
            type("Region", (), {"page_number": 1, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4})(),
            type("Region", (), {"page_number": 2, "x": 0.2, "y": 0.3, "w": 0.4, "h": 0.5})(),
        ],
    )
    assert [region.page_number for region in regions] == [1, 2]

    d1_bridge_questions.replace_question_parse_evidence(
        None,
        question_id=11,
        exam_id=7,
        page_number=1,
        evidence_list=[
            {"kind": "question_box", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4, "confidence": 0.9},
            {"kind": "unsupported"},
        ],
    )
    assert len(fake_client.batch_calls[-1]) == 2

    d1_bridge_questions.delete_question(None, created)
    assert fake_client.run_calls[-1][1] == [11]


def test_d1_bridge_exams_parse_slice(monkeypatch) -> None:
    fake_client = _FakeBridgeClient()
    monkeypatch.setattr(d1_bridge_exams, "get_d1_bridge_client", lambda: fake_client)

    exam = d1_bridge_exams.get_exam(None, 7)
    assert exam is not None
    assert exam.id == 7

    updated_exam = d1_bridge_exams.update_exam(None, exam, status="REVIEWING")
    assert updated_exam.status.value == "REVIEWING"

    pages = d1_bridge_exams.list_exam_key_pages(None, 7)
    assert len(pages) == 1
    assert pages[0].page_number == 1

    parse_jobs = d1_bridge_exams.list_exam_parse_jobs(None, 7)
    assert len(parse_jobs) == 1

    parse_job = d1_bridge_exams.get_exam_parse_job(None, 21)
    assert isinstance(parse_job, ExamKeyParseJob)

    latest_job = d1_bridge_exams.get_latest_exam_parse_job(None, 7)
    assert latest_job is not None
    assert latest_job.id == 21

    created_job = d1_bridge_exams.create_exam_parse_job(
        None,
        exam_id=7,
        status="running",
        page_count=2,
        pages_done=0,
        created_at="2026-03-26T00:00:00+00:00",
        updated_at="2026-03-26T00:00:00+00:00",
    )
    assert created_job.id == 31

    updated_job = d1_bridge_exams.update_exam_parse_job(None, created_job, status="done", pages_done=3)
    assert updated_job.status == "done"

    parse_pages = d1_bridge_exams.list_exam_parse_pages(None, 21)
    assert len(parse_pages) == 1
    assert parse_pages[0].result_json == {"questions": [{"label": "Q1"}]}

    pending_pages = d1_bridge_exams.list_pending_exam_parse_pages(None, 21, limit=3)
    assert len(pending_pages) == 1
    assert pending_pages[0].status == "pending"

    parse_page = d1_bridge_exams.get_exam_parse_page(None, job_id=21, page_number=1)
    assert isinstance(parse_page, ExamKeyParsePage)

    created_page = d1_bridge_exams.create_exam_parse_page(
        None,
        job_id=21,
        page_number=2,
        status="pending",
        updated_at="2026-03-26T00:00:00+00:00",
    )
    assert created_page.id == 51

    updated_page = d1_bridge_exams.update_exam_parse_page(
        None,
        created_page,
        status="done",
        confidence=0.95,
        model_used="gpt-4.1-mini",
        result_json={"questions": [{"label": "Q1"}]},
        updated_at="2026-03-26T00:00:05+00:00",
    )
    assert updated_page.result_json == {"questions": [{"label": "Q1"}]}

    monkeypatch.setattr(
        d1_bridge_exams,
        "get_d1_bridge_client",
        lambda: type("RemainingWorkBridge", (), {"query_first": lambda self, sql, params=None: {"id": 1}})(),
    )
    assert d1_bridge_exams.exam_parse_job_has_remaining_work(None, 21) is True


def test_d1_bridge_exams_intake_and_bulk_slice(monkeypatch) -> None:
    fake_client = _FakeBridgeClient()
    monkeypatch.setattr(d1_bridge_exams, "get_d1_bridge_client", lambda: fake_client)

    latest_intake = d1_bridge_exams.get_latest_exam_intake_job(None, 7)
    assert isinstance(latest_intake, ExamIntakeJob)

    latest_by_exam = d1_bridge_exams.list_latest_exam_intake_jobs_by_exam_id(None, [7])
    assert list(latest_by_exam.keys()) == [7]

    intake_job = d1_bridge_exams.get_exam_intake_job(None, 61)
    assert intake_job is not None
    assert intake_job.id == 61

    updated_intake = d1_bridge_exams.update_exam_intake_job(None, intake_job, stage="warming_initial_review")
    assert updated_intake.stage == "warming_initial_review"

    queued_jobs = d1_bridge_exams.list_queued_or_running_exam_intake_jobs(None)
    assert len(queued_jobs) == 1

    created_intake = d1_bridge_exams.create_exam_intake_job(
        None,
        exam_id=7,
        bulk_upload_id=71,
        status="queued",
        stage="queued",
        page_count=4,
        pages_built=0,
        pages_processed=0,
        submissions_created=0,
        candidates_ready=0,
        review_open_threshold=0,
        initial_review_ready=False,
        fully_warmed=False,
        review_ready=False,
        thinking_level="low",
        last_progress_at="2026-03-26T00:00:00+00:00",
        metrics_json='{"page_count":4}',
    )
    assert created_intake.id == 62

    bulk = d1_bridge_exams.get_exam_bulk_upload(None, 71)
    assert isinstance(bulk, ExamBulkUploadFile)

    created_bulk = d1_bridge_exams.create_exam_bulk_upload(None, exam_id=7, original_filename="bulk.pdf", stored_path="bulk/path.pdf")
    assert created_bulk.id == 71

    updated_bulk = d1_bridge_exams.update_exam_bulk_upload(
        None,
        bulk=created_bulk,
        original_filename="bulk-renamed.pdf",
        stored_path="bulk/new-path.pdf",
        source_manifest_json='[{"local_name":"source.pdf"}]',
    )
    assert updated_bulk.original_filename == "bulk-renamed.pdf"

    bulk_pages = d1_bridge_exams.list_bulk_upload_pages(None, 71)
    assert len(bulk_pages) == 1

    created_page = d1_bridge_exams.create_bulk_upload_page(
        None,
        bulk_upload_id=71,
        page_number=1,
        image_path="/tmp/page1.png",
        width=1200,
        height=1600,
        detected_student_name="Alice Johnson",
        detection_confidence=0.92,
        detection_evidence_json="{}",
    )
    assert isinstance(created_page, BulkUploadPage)

    updated_page = d1_bridge_exams.update_bulk_upload_page(None, created_page, detected_student_name="Alice Johnson")
    assert updated_page.detected_student_name == "Alice Johnson"

    d1_bridge_exams.clear_bulk_upload_pages(None, bulk_upload_id=71)
    assert fake_client.run_calls[-1][1] == [71]


def test_d1_bridge_submissions_read_slice(monkeypatch) -> None:
    fake_client = _FakeBridgeClient()
    monkeypatch.setattr(d1_bridge_submissions, "get_d1_bridge_client", lambda: fake_client)

    submission = d1_bridge_submissions.get_submission(None, 1)
    assert submission is not None
    assert submission.id == 1

    files = d1_bridge_submissions.list_submission_files(None, 1)
    assert len(files) == 1

    pages = d1_bridge_submissions.list_submission_pages(None, 1)
    assert len(pages) == 1

    page = d1_bridge_submissions.get_submission_page(None, 1, 1)
    assert page is not None
    assert page.page_number == 1

    crops = d1_bridge_submissions.list_submission_crops(None, 1)
    assert len(crops) == 1

    crop = d1_bridge_submissions.get_submission_crop(None, 1, 11)
    assert crop is not None
    assert crop.question_id == 11

    transcriptions = d1_bridge_submissions.list_submission_transcriptions(None, 1)
    assert len(transcriptions) == 1

    grades = d1_bridge_submissions.list_submission_grades(None, 1)
    assert len(grades) == 1

    grade = d1_bridge_submissions.get_submission_grade(None, 1, 11)
    assert grade is not None
    assert grade.marks_awarded == 4.0

    questions = d1_bridge_submissions.list_exam_questions(None, 7)
    assert len(questions) == 1

    total_submissions = d1_bridge_submissions.list_exam_front_page_total_submissions(None, 7)
    assert len(total_submissions) == 1

    submissions_by_ids = d1_bridge_submissions.list_submissions_by_ids(None, [1, 2])
    assert [submission.id for submission in submissions_by_ids] == [1, 2]

    pages_by_ids = d1_bridge_submissions.list_submission_pages_for_submission_ids(None, [1, 2])
    assert len(pages_by_ids) == 1

    regions = d1_bridge_submissions.list_question_regions(None, 11)
    assert len(regions) == 2


def test_d1_bridge_submissions_write_slice(monkeypatch) -> None:
    fake_client = _FakeBridgeClient()
    monkeypatch.setattr(d1_bridge_submissions, "get_d1_bridge_client", lambda: fake_client)

    submission = d1_bridge_submissions.create_submission(
        None,
        exam_id=7,
        student_name="Alice Johnson",
        first_name="Alice",
        last_name="Johnson",
        status="UPLOADED",
        capture_mode="front_page_totals",
    )
    assert submission.id == 3

    updated_submission = d1_bridge_submissions.update_submission_front_page_data(
        None,
        submission,
        front_page_candidates_json='{"candidate":1}',
        front_page_usage_json='{"model":"mini"}',
    )
    assert updated_submission.front_page_candidates_json == '{"candidate":1}'

    file_row = d1_bridge_submissions.create_submission_file(
        None,
        submission_id=3,
        file_kind="image",
        original_filename="page1.png",
        stored_path="exams/7/submissions/3/page1.png",
        content_type="image/png",
        size_bytes=123,
    )
    assert file_row.id == 62

    page_row = d1_bridge_submissions.create_submission_page(
        None,
        submission_id=3,
        page_number=1,
        image_path="/tmp/page1.png",
        width=1200,
        height=1600,
    )
    assert page_row.id == 72

    crop_row = d1_bridge_submissions.create_submission_crop(
        None,
        submission_id=3,
        question_id=11,
        image_path="/tmp/crop.png",
    )
    assert crop_row.id == 82

    transcription_row = d1_bridge_submissions.create_submission_transcription(
        None,
        submission_id=3,
        question_id=11,
        provider="stub",
        text="answer",
        confidence=0.9,
        raw_json="{}",
    )
    assert transcription_row.id == 83

    new_grade = d1_bridge_submissions.upsert_submission_grade(
        None,
        submission_id=3,
        question_id=12,
        marks_awarded=3.0,
        breakdown_json="{}",
        feedback_json="{}",
        model_name="manual",
    )
    assert new_grade.id == 92

    updated_grade = d1_bridge_submissions.upsert_submission_grade(
        None,
        submission_id=1,
        question_id=11,
        marks_awarded=4.0,
        breakdown_json='{"updated":1}',
        feedback_json="{}",
        model_name="manual",
    )
    assert updated_grade.breakdown_json == '{"updated":1}'

    status_updated = d1_bridge_submissions.update_submission_status(None, submission, "PAGES_READY")
    assert status_updated.status == "PAGES_READY"

    capture_mode_updated = d1_bridge_submissions.update_submission_capture_mode(None, submission, "question_level")
    assert capture_mode_updated.capture_mode == "question_level"

    d1_bridge_submissions.clear_submission_pages(None, 3)
    d1_bridge_submissions.clear_submission_crops(None, 3)
    d1_bridge_submissions.clear_submission_transcriptions(None, 3)
    d1_bridge_submissions.clear_submission_grades(None, 3)
    assert [params for _sql, params in fake_client.run_calls[-4:]] == [[3], [3], [3], [3]]


def test_d1_bridge_reporting_slice(monkeypatch) -> None:
    fake_client = _FakeBridgeClient()
    monkeypatch.setattr(d1_bridge_reporting, "_bridge", lambda: fake_client)

    submission_collections = d1_bridge_reporting.load_submission_reporting_collections(None, 1, 7)
    assert len(submission_collections.pages) == 1
    assert len(submission_collections.crops) == 1
    assert len(submission_collections.transcriptions) == 1
    assert len(submission_collections.grades) == 1

    exam_collections = d1_bridge_reporting.load_exam_reporting_collections(None, 7)
    assert len(exam_collections.questions) == 1
    assert len(exam_collections.submissions) >= 1
    assert len(exam_collections.question_regions) == 1
    assert len(exam_collections.pages) == 1
    assert len(exam_collections.crops) == 1
    assert len(exam_collections.transcriptions) == 1
    assert len(exam_collections.grades) == 1
