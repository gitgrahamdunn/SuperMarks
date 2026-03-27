from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.repositories import get_repository_provider
from app.models import SubmissionCaptureMode, SubmissionStatus


pytestmark = pytest.mark.skipif(
    not (
        (os.getenv("SUPERMARKS_D1_BRIDGE_URL") or "").strip()
        and (os.getenv("SUPERMARKS_D1_BRIDGE_TOKEN") or "").strip()
    ),
    reason="real D1 bridge env not configured",
)


def test_real_d1_bridge_repository_smoke() -> None:
    get_repository_provider.cache_clear()
    provider = get_repository_provider()

    suffix = uuid4().hex[:8]
    exam = None
    class_list = None
    try:
        exam = provider.exams.create_exam(None, name=f"bridge-smoke-{suffix}")
        assert exam.id is not None
        fetched_exam = provider.exams.get_exam(None, int(exam.id))
        assert fetched_exam is not None
        assert fetched_exam.name == f"bridge-smoke-{suffix}"
        assert any(item.id == exam.id for item in provider.exams.list_exams(None))

        class_list = provider.exams.create_class_list(None, name=f"bridge-classlist-{suffix}")
        class_list = provider.exams.update_class_list_payload(
            None,
            class_list=class_list,
            names_json='["Alice Example"]',
            source_json='{"source":"bridge-test"}',
        )
        assert provider.exams.get_class_list(None, int(class_list.id)).name == f"bridge-classlist-{suffix}"
        assert any(item.id == class_list.id for item in provider.exams.list_class_lists(None))

        exam = provider.exams.update_exam_class_list_payload(
            None,
            exam=exam,
            class_list_json=class_list.names_json,
            class_list_source_json=class_list.source_json or "{}",
        )
        assert exam.class_list_json == '["Alice Example"]'

        key_file = provider.exams.create_exam_key_file(
            None,
            exam_id=int(exam.id),
            original_filename="key-a.png",
            stored_path=f"exams/{int(exam.id)}/key/key-a.png",
            content_type="image/png",
            size_bytes=12,
            blob_pathname=f"exams/{int(exam.id)}/key/key-a.png",
            blob_url=None,
        )
        assert key_file.id is not None

        registered = provider.exams.register_exam_key_files(
            None,
            exam_id=int(exam.id),
            files=[
                {
                    "original_filename": "key-b.png",
                    "stored_path": f"exams/{int(exam.id)}/key/key-b.png",
                    "content_type": "image/png",
                    "size_bytes": 34,
                    "blob_pathname": f"exams/{int(exam.id)}/key/key-b.png",
                    "blob_url": None,
                }
            ],
        )
        assert registered == 1
        key_files = provider.exams.list_exam_key_files(None, int(exam.id))
        assert len(key_files) >= 2

        submission = provider.submissions.create_submission(
            None,
            exam_id=int(exam.id),
            student_name="Student Example",
            first_name="Student",
            last_name="Example",
            status=SubmissionStatus.UPLOADED,
            capture_mode=SubmissionCaptureMode.QUESTION_LEVEL,
        )
        assert submission.id is not None

        submission_registered = provider.submissions.register_submission_files(
            None,
            submission_id=int(submission.id),
            files=[
                {
                    "file_kind": "image",
                    "original_filename": "submission-a.png",
                    "stored_path": f"exams/{int(exam.id)}/submissions/{int(submission.id)}/submission-a.png",
                    "content_type": "image/png",
                    "size_bytes": 56,
                    "blob_pathname": f"exams/{int(exam.id)}/submissions/{int(submission.id)}/submission-a.png",
                    "blob_url": None,
                }
            ],
        )
        assert submission_registered == 1
        submission_files = provider.submissions.list_submission_files(None, int(submission.id))
        assert len(submission_files) == 1

        provider.exams.delete_exam_data(None, exam=exam)
        assert provider.exams.get_exam(None, int(exam.id)) is None
        exam = None

        provider.exams.delete_class_list(None, class_list=class_list)
        assert provider.exams.get_class_list(None, int(class_list.id)) is None
        class_list = None
    finally:
        if exam is not None:
            provider.exams.delete_exam_data(None, exam=exam)
        if class_list is not None:
            provider.exams.delete_class_list(None, class_list=class_list)
        get_repository_provider.cache_clear()
