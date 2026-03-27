from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw
from sqlmodel import Session, SQLModel, create_engine

from app import db
from app.models import AnswerCrop, Exam, ExamKeyPage, GradeResult, Question, QuestionRegion, Submission, SubmissionCaptureMode, SubmissionFile, SubmissionPage, SubmissionStatus, Transcription
from app.settings import settings

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "acceptance"
DATA_DIR = ARTIFACT_ROOT / "data"
DB_PATH = ARTIFACT_ROOT / "supermarks-acceptance.db"
METADATA_PATH = ARTIFACT_ROOT / "seed-metadata.json"


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def make_image(path: Path, *, title: str, subtitle: str, accent: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (1280, 1660), color=(252, 252, 250))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((60, 60, 1220, 1600), radius=28, outline=accent, width=8)
    draw.text((110, 110), title, fill=(20, 20, 20))
    draw.text((110, 170), subtitle, fill=(60, 60, 60))
    draw.line((100, 250, 1180, 250), fill=accent, width=5)
    for index in range(6):
        top = 320 + index * 180
        draw.rounded_rectangle((110, top, 1170, top + 110), radius=18, outline=(190, 190, 190), width=3)
        draw.text((140, top + 24), f"Visible worksheet content block {index + 1}", fill=(90, 90, 90))
    image.save(path)


def make_pdf(path: Path, *, title: str, subtitle: str, accent: tuple[int, int, int]) -> None:
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(48, 48, 564, 744))
    shape.finish(color=tuple(channel / 255 for channel in accent), width=3)
    shape.commit()
    page.insert_text((72, 92), title, fontsize=24)
    page.insert_text((72, 124), subtitle, fontsize=14)
    for index in range(6):
        top = 180 + index * 78
        page.draw_rect(fitz.Rect(72, top, 540, top + 48), color=(0.75, 0.75, 0.75), width=1)
        page.insert_text((88, top + 28), f"Visible worksheet content block {index + 1}", fontsize=12)
    doc.save(path)
    doc.close()


def main() -> None:
    ensure_clean_dir(ARTIFACT_ROOT)
    ensure_clean_dir(DATA_DIR)

    settings.data_dir = str(DATA_DIR)
    settings.sqlite_path = str(DB_PATH)
    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.drop_all(db.engine)
    SQLModel.metadata.create_all(db.engine)

    key_page_path = DATA_DIR / "key-page-1.png"
    question1_crop_path = DATA_DIR / "submission-1-q1.png"
    question2_crop_path = DATA_DIR / "submission-1-q2.png"
    submission_page_path = DATA_DIR / "submission-1-page-1.png"
    front_page_pdf_path = DATA_DIR / "submission-2-front-page.pdf"

    make_image(key_page_path, title="SuperMarks acceptance key", subtitle="Q1 and Q2 answer-key visual", accent=(34, 102, 204))
    make_image(question1_crop_path, title="Avery — Q1 crop", subtitle="Factor x^2 - 9", accent=(46, 125, 50))
    make_image(question2_crop_path, title="Avery — Q2 crop", subtitle="Solve the linear equation", accent=(198, 83, 44))
    make_image(submission_page_path, title="Avery — full submission page", subtitle="Contains both answer regions", accent=(114, 77, 171))
    make_pdf(front_page_pdf_path, title="Jordan — front page", subtitle="Totals page for acceptance testing", accent=(0, 121, 107))

    with Session(db.engine) as session:
        exam = Exam(name="Acceptance Seed Exam", status="READY")
        session.add(exam)
        session.commit()
        session.refresh(exam)

        q1 = Question(
            exam_id=exam.id,
            label="Q1",
            max_marks=4,
            rubric_json=json.dumps({
                "question_text": "Factor x^2 - 9",
                "answer_key": "(x-3)(x+3)",
                "objective_codes": ["ALG1"],
                "key_page_number": 1,
            }),
        )
        q2 = Question(
            exam_id=exam.id,
            label="Q2",
            max_marks=6,
            rubric_json=json.dumps({
                "question_text": "Solve 2x + 5 = 17",
                "answer_key": "x = 6",
                "objective_codes": ["ALG2"],
                "key_page_number": 1,
            }),
        )
        session.add(q1)
        session.add(q2)
        session.commit()
        session.refresh(q1)
        session.refresh(q2)

        session.add(ExamKeyPage(exam_id=exam.id, page_number=1, image_path=str(key_page_path), width=1280, height=1660))
        session.add(QuestionRegion(question_id=q1.id, page_number=1, x=0.10, y=0.20, w=0.35, h=0.18))
        session.add(QuestionRegion(question_id=q2.id, page_number=1, x=0.10, y=0.44, w=0.35, h=0.18))

        question_level_submission = Submission(
            exam_id=exam.id,
            student_name="Avery",
            status=SubmissionStatus.GRADED,
            capture_mode=SubmissionCaptureMode.QUESTION_LEVEL,
        )
        front_page_submission = Submission(
            exam_id=exam.id,
            student_name="Jordan",
            status=SubmissionStatus.UPLOADED,
            capture_mode=SubmissionCaptureMode.FRONT_PAGE_TOTALS,
        )
        session.add(question_level_submission)
        session.add(front_page_submission)
        session.commit()
        session.refresh(question_level_submission)
        session.refresh(front_page_submission)

        session.add(SubmissionPage(submission_id=question_level_submission.id, page_number=1, image_path=str(submission_page_path), width=1280, height=1660))
        session.add(
            SubmissionFile(
                submission_id=front_page_submission.id,
                file_kind="pdf",
                original_filename=front_page_pdf_path.name,
                stored_path=str(front_page_pdf_path),
                content_type="application/pdf",
                size_bytes=front_page_pdf_path.stat().st_size,
            )
        )
        session.add(AnswerCrop(submission_id=question_level_submission.id, question_id=q1.id, image_path=str(question1_crop_path)))
        session.add(AnswerCrop(submission_id=question_level_submission.id, question_id=q2.id, image_path=str(question2_crop_path)))
        session.add(Transcription(submission_id=question_level_submission.id, question_id=q1.id, provider="stub", text="(x-3)(x+3)", confidence=0.99, raw_json=json.dumps({"source": "seed"})))
        session.add(Transcription(submission_id=question_level_submission.id, question_id=q2.id, provider="stub", text="x = 6", confidence=0.97, raw_json=json.dumps({"source": "seed"})))
        session.add(GradeResult(
            submission_id=question_level_submission.id,
            question_id=q1.id,
            marks_awarded=4,
            breakdown_json=json.dumps({"source": "teacher_manual", "objective_codes": ["ALG1"], "max_marks": 4}),
            feedback_json=json.dumps({"teacher_note": "Strong factoring."}),
            model_name="teacher_manual",
        ))
        session.commit()

    metadata = {
        "exam_id": 1,
        "question_level_submission_id": 1,
        "front_page_submission_id": 2,
        "question_ids": {"Q1": 1, "Q2": 2},
        "paths": {
            "db": str(DB_PATH),
            "data_dir": str(DATA_DIR),
        },
    }
    METADATA_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
