from __future__ import annotations

import csv
import os
import threading
import time
from io import StringIO

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import SQLModel, Session, create_engine, select

from app import db
from app.ai.openai_vision import FrontPageTotalsExtractResult
from app.main import app
from app.models import Submission, SubmissionPage
from app.settings import settings


def setup_test_db(tmp_path) -> None:
    settings.data_dir = str(tmp_path / 'data')
    settings.sqlite_path = str(tmp_path / 'test.db')
    db.engine = create_engine(settings.sqlite_url, connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(db.engine)


def test_create_submission_with_front_page_totals_mode(tmp_path) -> None:
    setup_test_db(tmp_path)

    with TestClient(app) as client:
        exam = client.post('/api/exams', json={'name': 'Science 20'})
        exam_id = exam.json()['id']

        response = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Avery', 'capture_mode': 'front_page_totals'},
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload['capture_mode'] == 'front_page_totals'
        assert payload['front_page_totals'] is None
        assert payload['first_name'] == 'Avery'
        assert payload['last_name'] == ''


def test_front_page_totals_candidate_extraction_returns_structured_candidates(tmp_path, monkeypatch) -> None:
    setup_test_db(tmp_path)
    monkeypatch.setenv('OPENAI_MOCK', '1')

    with TestClient(app) as client:
        exam = client.post('/api/exams', json={'name': 'ELA 20'})
        exam_id = exam.json()['id']
        submission = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Jordan', 'capture_mode': 'front_page_totals'},
        )
        submission_id = submission.json()['id']

        image_path = tmp_path / 'front-page.png'
        Image.new('RGB', (1200, 1600), color='white').save(image_path)
        with Session(db.engine) as session:
            session.add(SubmissionPage(submission_id=submission_id, page_number=1, image_path=str(image_path), width=1200, height=1600))
            session.commit()

        response = client.get(f'/api/submissions/{submission_id}/front-page-totals-candidates')
        assert response.status_code == 200
        payload = response.json()
        assert payload['source'] == 'mock-front-page-totals'
        assert payload['student_name']['value_text'] == 'Jordan Lee'
        assert payload['overall_marks_awarded']['value_text'] == '42'
        assert payload['overall_max_marks']['value_text'] == '50'
        assert payload['objective_scores'][0]['objective_code']['value_text'] == 'OB1'
        assert payload['objective_scores'][0]['marks_awarded']['value_text'] == '18'
        assert payload['objective_scores'][0]['max_marks']['value_text'] == '20'


def test_front_page_totals_candidates_accept_absolute_pixel_evidence(tmp_path, monkeypatch) -> None:
    setup_test_db(tmp_path)

    class StubExtractor:
        def extract(self, image_path, request_id):
            _ = (image_path, request_id)
            return FrontPageTotalsExtractResult(
                payload={
                    'student_name': {
                        'value_text': 'Jordan Lee',
                        'confidence': 0.93,
                        'evidence': [{'page_number': 1, 'quote': 'Name: Jordan Lee', 'x': 120, 'y': 80, 'w': 240, 'h': 64}],
                    },
                    'overall_marks_awarded': {
                        'value_text': '42',
                        'confidence': 0.95,
                        'evidence': [{'page_number': 1, 'quote': '42/50', 'x': 840, 'y': 120, 'w': 96, 'h': 48}],
                    },
                    'overall_max_marks': {
                        'value_text': '50',
                        'confidence': 0.95,
                        'evidence': [{'page_number': 1, 'quote': '42/50', 'x': 936, 'y': 120, 'w': 96, 'h': 48}],
                    },
                    'objective_scores': [],
                    'warnings': [],
                },
                model='stub-front-page',
            )

    monkeypatch.setattr('app.routers.submissions.get_front_page_totals_extractor', lambda: StubExtractor())

    with TestClient(app) as client:
        exam_id = client.post('/api/exams', json={'name': 'ELA 20'}).json()['id']
        submission_id = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Jordan', 'capture_mode': 'front_page_totals'},
        ).json()['id']

        image_path = tmp_path / 'front-page.png'
        Image.new('RGB', (1200, 1600), color='white').save(image_path)
        with Session(db.engine) as session:
            session.add(SubmissionPage(submission_id=submission_id, page_number=1, image_path=str(image_path), width=1200, height=1600))
            session.commit()

        response = client.get(f'/api/submissions/{submission_id}/front-page-totals-candidates')

    assert response.status_code == 200
    payload = response.json()
    assert payload['student_name']['evidence'][0]['x'] == 0.1
    assert payload['student_name']['evidence'][0]['y'] == 0.05
    assert payload['overall_marks_awarded']['evidence'][0]['w'] == 0.08
    assert payload['overall_max_marks']['evidence'][0]['h'] == 0.03


def test_front_page_totals_candidates_split_ratio_objective_scores(tmp_path, monkeypatch) -> None:
    setup_test_db(tmp_path)

    class StubExtractor:
        def extract(self, image_path, request_id):
            _ = (image_path, request_id)
            return FrontPageTotalsExtractResult(
                payload={
                    'student_name': {
                        'value_text': 'Jordan Lee',
                        'confidence': 0.93,
                        'evidence': [],
                    },
                    'overall_marks_awarded': {
                        'value_text': '22',
                        'confidence': 0.95,
                        'evidence': [],
                    },
                    'overall_max_marks': {
                        'value_text': '41',
                        'confidence': 0.95,
                        'evidence': [],
                    },
                    'objective_scores': [
                        {
                            'objective_code': {'value_text': '1/2', 'confidence': 0.9, 'evidence': []},
                            'marks_awarded': {'value_text': '16/25', 'confidence': 0.8, 'evidence': []},
                            'max_marks': None,
                        },
                    ],
                    'warnings': [],
                },
                model='stub-front-page',
            )

    monkeypatch.setattr('app.routers.submissions.get_front_page_totals_extractor', lambda: StubExtractor())

    with TestClient(app) as client:
        exam_id = client.post('/api/exams', json={'name': 'ELA 20'}).json()['id']
        submission_id = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Jordan', 'capture_mode': 'front_page_totals'},
        ).json()['id']

        image_path = tmp_path / 'front-page.png'
        Image.new('RGB', (1200, 1600), color='white').save(image_path)
        with Session(db.engine) as session:
            session.add(SubmissionPage(submission_id=submission_id, page_number=1, image_path=str(image_path), width=1200, height=1600))
            session.commit()

        response = client.get(f'/api/submissions/{submission_id}/front-page-totals-candidates')

    assert response.status_code == 200
    payload = response.json()
    assert payload['objective_scores'][0]['marks_awarded']['value_text'] == '16'
    assert payload['objective_scores'][0]['max_marks']['value_text'] == '25'


def test_front_page_totals_candidates_are_cached_after_first_extraction(tmp_path, monkeypatch) -> None:
    setup_test_db(tmp_path)
    calls: list[str] = []

    class StubExtractor:
        def extract(self, image_path, request_id):
            calls.append(f"{image_path}:{request_id}")
            return FrontPageTotalsExtractResult(
                payload={
                    'student_name': {
                        'value_text': 'Jordan Lee',
                        'confidence': 0.93,
                        'evidence': [],
                    },
                    'overall_marks_awarded': {
                        'value_text': '42',
                        'confidence': 0.95,
                        'evidence': [],
                    },
                    'overall_max_marks': {
                        'value_text': '50',
                        'confidence': 0.95,
                        'evidence': [],
                    },
                    'objective_scores': [],
                    'warnings': [],
                },
                model='stub-front-page',
            )

    monkeypatch.setattr('app.routers.submissions.get_front_page_totals_extractor', lambda: StubExtractor())

    with TestClient(app) as client:
        exam_id = client.post('/api/exams', json={'name': 'ELA 20'}).json()['id']
        submission_id = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Jordan', 'capture_mode': 'front_page_totals'},
        ).json()['id']

        image_path = tmp_path / 'front-page.png'
        Image.new('RGB', (1200, 1600), color='white').save(image_path)
        with Session(db.engine) as session:
            session.add(SubmissionPage(submission_id=submission_id, page_number=1, image_path=str(image_path), width=1200, height=1600))
            session.commit()

        first_response = client.get(f'/api/submissions/{submission_id}/front-page-totals-candidates')
        second_response = client.get(f'/api/submissions/{submission_id}/front-page-totals-candidates')

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json() == second_response.json()
    assert len(calls) == 1

    with Session(db.engine) as session:
        stored = session.exec(select(Submission).where(Submission.id == submission_id)).one()
        assert stored.front_page_candidates_json is not None


def test_front_page_totals_candidates_do_not_double_parse_under_concurrent_requests(tmp_path, monkeypatch) -> None:
    setup_test_db(tmp_path)
    calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    class StubExtractor:
        def extract(self, image_path, request_id):
            calls.append(f"{image_path}:{request_id}")
            started.set()
            release.wait(timeout=2)
            time.sleep(0.05)
            return FrontPageTotalsExtractResult(
                payload={
                    'student_name': {'value_text': 'Jordan Lee', 'confidence': 0.93, 'evidence': []},
                    'overall_marks_awarded': {'value_text': '42', 'confidence': 0.95, 'evidence': []},
                    'overall_max_marks': {'value_text': '50', 'confidence': 0.95, 'evidence': []},
                    'objective_scores': [],
                    'warnings': [],
                },
                model='stub-front-page',
            )

    monkeypatch.setattr('app.routers.submissions.get_front_page_totals_extractor', lambda: StubExtractor())

    with TestClient(app) as client:
        exam_id = client.post('/api/exams', json={'name': 'ELA 20'}).json()['id']
        submission_id = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Jordan', 'capture_mode': 'front_page_totals'},
        ).json()['id']

        image_path = tmp_path / 'front-page.png'
        Image.new('RGB', (1200, 1600), color='white').save(image_path)
        with Session(db.engine) as session:
            session.add(SubmissionPage(submission_id=submission_id, page_number=1, image_path=str(image_path), width=1200, height=1600))
            session.commit()

        responses: list[dict] = []

        def fetch_candidate() -> None:
            responses.append(client.get(f'/api/submissions/{submission_id}/front-page-totals-candidates').json())

        first_thread = threading.Thread(target=fetch_candidate)
        second_thread = threading.Thread(target=fetch_candidate)

        first_thread.start()
        started.wait(timeout=1)
        second_thread.start()
        release.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)

    assert len(calls) == 1
    assert len(responses) == 2
    assert responses[0] == responses[1]


def test_front_page_totals_drive_results_and_dashboard(tmp_path) -> None:
    setup_test_db(tmp_path)

    with TestClient(app) as client:
        exam = client.post('/api/exams', json={'name': 'Math 30'})
        exam_id = exam.json()['id']
        submission = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Jordan', 'capture_mode': 'front_page_totals'},
        )
        submission_id = submission.json()['id']

        save = client.put(
            f'/api/submissions/{submission_id}/front-page-totals',
            json={
                'overall_marks_awarded': 42,
                'overall_max_marks': 50,
                'objective_scores': [
                    {'objective_code': 'OB1', 'marks_awarded': 18, 'max_marks': 20},
                    {'objective_code': 'OB2', 'marks_awarded': 24, 'max_marks': 30},
                ],
                'teacher_note': 'Copied from front page.',
                'confirmed': True,
            },
        )
        assert save.status_code == 200
        assert save.json()['overall_marks_awarded'] == 42

        results = client.get(f'/api/submissions/{submission_id}/results')
        assert results.status_code == 200
        results_payload = results.json()
        assert results_payload['capture_mode'] == 'front_page_totals'
        assert results_payload['total_score'] == 42
        assert results_payload['total_possible'] == 50
        assert results_payload['grades'] == []
        assert results_payload['front_page_totals']['teacher_note'] == 'Copied from front page.'
        assert {row['objective_code'] for row in results_payload['objective_totals']} == {'OB1', 'OB2'}

        dashboard = client.get(f'/api/exams/{exam_id}/marking-dashboard')
        assert dashboard.status_code == 200
        row = dashboard.json()['submissions'][0]
        assert row['capture_mode'] == 'front_page_totals'
        assert row['workflow_status'] == 'complete'
        assert row['running_total'] == 42
        assert row['total_possible'] == 50

    with Session(db.engine) as session:
        stored = session.exec(select(Submission).where(Submission.id == submission_id)).one()
        assert stored.front_page_totals_json is not None
        assert stored.front_page_reviewed_at is not None


def test_front_page_totals_save_can_correct_student_name(tmp_path) -> None:
    setup_test_db(tmp_path)

    with TestClient(app) as client:
        exam_id = client.post('/api/exams', json={'name': 'Math 30'}).json()['id']
        submission_id = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Jordan Maybe', 'capture_mode': 'front_page_totals'},
        ).json()['id']

        save = client.put(
            f'/api/submissions/{submission_id}/front-page-totals',
            json={
                'student_name': 'Jordan Smith',
                'overall_marks_awarded': 42,
                'overall_max_marks': 50,
                'objective_scores': [],
                'teacher_note': 'Corrected during validation.',
                'confirmed': True,
            },
        )

        assert save.status_code == 200

        submission = client.get(f'/api/submissions/{submission_id}')
        assert submission.status_code == 200
        assert submission.json()['student_name'] == 'Jordan Smith'
        assert submission.json()['first_name'] == 'Jordan'
        assert submission.json()['last_name'] == 'Smith'


def test_front_page_totals_save_can_correct_first_and_last_name_separately(tmp_path) -> None:
    setup_test_db(tmp_path)

    with TestClient(app) as client:
        exam_id = client.post('/api/exams', json={'name': 'Math 30'}).json()['id']
        submission_id = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'JORDAN', 'capture_mode': 'front_page_totals'},
        ).json()['id']

        save = client.put(
            f'/api/submissions/{submission_id}/front-page-totals',
            json={
                'first_name': 'jordan',
                'last_name': 'smith',
                'overall_marks_awarded': 42,
                'overall_max_marks': 50,
                'objective_scores': [],
                'teacher_note': '',
                'confirmed': True,
            },
        )

        assert save.status_code == 200

        submission = client.get(f'/api/submissions/{submission_id}')
        assert submission.status_code == 200
        assert submission.json()['student_name'] == 'Jordan Smith'
        assert submission.json()['first_name'] == 'Jordan'
        assert submission.json()['last_name'] == 'Smith'


def test_mixed_mode_dashboard_and_summary_export_reflect_front_page_confirmation_state(tmp_path) -> None:
    setup_test_db(tmp_path)

    with TestClient(app) as client:
        exam_id = client.post('/api/exams', json={'name': 'Mixed Mode Exam'}).json()['id']
        q1 = client.post(
            f'/api/exams/{exam_id}/questions',
            json={'label': 'Q1', 'max_marks': 4, 'rubric_json': {'objective_codes': ['OB1'], 'criteria': []}},
        ).json()['id']
        client.post(
            f'/api/exams/{exam_id}/questions',
            json={'label': 'Q2', 'max_marks': 6, 'rubric_json': {'objective_codes': ['OB2'], 'criteria': []}},
        )

        question_level_submission_id = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Ada'},
        ).json()['id']
        front_page_submission_id = client.post(
            f'/api/exams/{exam_id}/submissions',
            json={'student_name': 'Byron', 'capture_mode': 'front_page_totals'},
        ).json()['id']

        client.put(
            f'/api/submissions/{question_level_submission_id}/questions/{q1}/manual-grade',
            json={'marks_awarded': 4, 'teacher_note': 'done'},
        )
        client.put(
            f'/api/submissions/{front_page_submission_id}/front-page-totals',
            json={
                'overall_marks_awarded': 18,
                'overall_max_marks': 20,
                'objective_scores': [
                    {'objective_code': 'OB1', 'marks_awarded': 8, 'max_marks': 10},
                    {'objective_code': 'OB2', 'marks_awarded': 10, 'max_marks': 10},
                ],
                'teacher_note': 'Needs one more check.',
                'confirmed': False,
            },
        )

        dashboard = client.get(f'/api/exams/{exam_id}/marking-dashboard')
        assert dashboard.status_code == 200
        dashboard_payload = dashboard.json()
        rows = {row['student_name']: row for row in dashboard_payload['submissions']}
        assert dashboard_payload['completion'] == {
            'total_submissions': 2,
            'ready_count': 1,
            'blocked_count': 0,
            'in_progress_count': 1,
            'complete_count': 0,
            'completion_percent': 0.0,
        }
        assert dashboard_payload['objectives'] == [
            {
                'objective_code': 'OB1',
                'marks_awarded': 12.0,
                'max_marks': 14.0,
                'questions_count': 1,
                'submissions_with_objective': 2,
                'complete_submissions_with_objective': 0,
                'incomplete_submissions_with_objective': 2,
                'total_awarded_complete': 0.0,
                'total_max_complete': 0.0,
                'average_awarded_complete': '',
                'average_percent_complete': '',
                'total_awarded_all_current': 12.0,
                'total_max_all_current': 14.0,
                'average_percent_all_current': 85.7,
                'strongest_complete_student': '',
                'strongest_complete_percent': '',
                'weakest_complete_student': '',
                'weakest_complete_percent': '',
                'weakest_complete_submission': None,
                'teacher_summary': '0/2 results export-ready; complete average —%; 2 result(s) still in progress',
                'attention_submissions': [
                    {
                        'submission_id': question_level_submission_id,
                        'student_name': 'Ada',
                        'capture_mode': 'question_level',
                        'workflow_status': 'in_progress',
                        'objective_percent': 100.0,
                        'next_return_point': 'Q1',
                        'next_action': 'Resume marking at Q1.',
                    },
                    {
                        'submission_id': front_page_submission_id,
                        'student_name': 'Byron',
                        'capture_mode': 'front_page_totals',
                        'workflow_status': 'ready',
                        'objective_percent': 80.0,
                        'next_return_point': '',
                        'next_action': 'Capture and confirm the front-page totals.',
                    },
                ],
            },
            {
                'objective_code': 'OB2',
                'marks_awarded': 10.0,
                'max_marks': 16.0,
                'questions_count': 1,
                'submissions_with_objective': 2,
                'complete_submissions_with_objective': 0,
                'incomplete_submissions_with_objective': 2,
                'total_awarded_complete': 0.0,
                'total_max_complete': 0.0,
                'average_awarded_complete': '',
                'average_percent_complete': '',
                'total_awarded_all_current': 10.0,
                'total_max_all_current': 16.0,
                'average_percent_all_current': 62.5,
                'strongest_complete_student': '',
                'strongest_complete_percent': '',
                'weakest_complete_student': '',
                'weakest_complete_percent': '',
                'weakest_complete_submission': None,
                'teacher_summary': '0/2 results export-ready; complete average —%; 2 result(s) still in progress',
                'attention_submissions': [
                    {
                        'submission_id': question_level_submission_id,
                        'student_name': 'Ada',
                        'capture_mode': 'question_level',
                        'workflow_status': 'in_progress',
                        'objective_percent': 0.0,
                        'next_return_point': 'Q1',
                        'next_action': 'Resume marking at Q1.',
                    },
                    {
                        'submission_id': front_page_submission_id,
                        'student_name': 'Byron',
                        'capture_mode': 'front_page_totals',
                        'workflow_status': 'ready',
                        'objective_percent': 100.0,
                        'next_return_point': '',
                        'next_action': 'Capture and confirm the front-page totals.',
                    },
                ],
            },
        ]
        assert rows['Ada']['workflow_status'] == 'in_progress'
        assert rows['Byron']['capture_mode'] == 'front_page_totals'
        assert rows['Byron']['workflow_status'] == 'ready'
        assert rows['Byron']['teacher_marked_questions'] == 0
        assert rows['Byron']['summary_reasons'] == ['Front-page totals still need teacher confirmation.']
        assert rows['Byron']['next_action_text'] == 'Capture and confirm the front-page totals.'
        assert rows['Byron']['export_ready'] is False
        assert rows['Byron']['reporting_attention'] == 'Front-page totals still need teacher confirmation.'
        assert rows['Byron']['next_return_point'] == ''
        assert rows['Byron']['next_action'] == 'Capture and confirm the front-page totals.'

        export = client.get(f'/api/exams/{exam_id}/export-summary.csv')
        rows = {row['student']: row for row in csv.DictReader(StringIO(export.text))}
        assert rows['Byron']['capture_mode'] == 'front_page_totals'
        assert rows['Byron']['workflow_status'] == 'ready'
        assert rows['Byron']['export_ready'] == 'no'
        assert rows['Byron']['marking_progress'] == 'pending front-page confirmation'
        assert rows['Byron']['running_total'] == '18.0'
        assert rows['Byron']['total_possible'] == '20.0'
        assert rows['Byron']['objective_summary'] == 'OB1 8.0/10.0 | OB2 10.0/10.0'
        assert rows['Byron']['reporting_attention'] == 'Front-page totals still need teacher confirmation.'

        objective_export = client.get(f'/api/exams/{exam_id}/export-objectives-summary.csv')
        objective_rows = {row['objective_code']: row for row in csv.DictReader(StringIO(objective_export.text))}
        assert objective_rows['OB1'] == {
            'objective_code': 'OB1',
            'submissions_with_objective': '2',
            'complete_submissions_with_objective': '0',
            'incomplete_submissions_with_objective': '2',
            'total_awarded_complete': '0.0',
            'total_max_complete': '0.0',
            'average_awarded_complete': '',
            'average_percent_complete': '',
            'total_awarded_all_current': '12.0',
            'total_max_all_current': '14.0',
            'average_percent_all_current': '85.7',
            'strongest_complete_student': '',
            'strongest_complete_percent': '',
            'weakest_complete_student': '',
            'weakest_complete_percent': '',
            'teacher_summary': '0/2 results export-ready; complete average —%; 2 result(s) still in progress',
        }
        assert objective_rows['OB2'] == {
            'objective_code': 'OB2',
            'submissions_with_objective': '2',
            'complete_submissions_with_objective': '0',
            'incomplete_submissions_with_objective': '2',
            'total_awarded_complete': '0.0',
            'total_max_complete': '0.0',
            'average_awarded_complete': '',
            'average_percent_complete': '',
            'total_awarded_all_current': '10.0',
            'total_max_all_current': '16.0',
            'average_percent_all_current': '62.5',
            'strongest_complete_student': '',
            'strongest_complete_percent': '',
            'weakest_complete_student': '',
            'weakest_complete_percent': '',
            'teacher_summary': '0/2 results export-ready; complete average —%; 2 result(s) still in progress',
        }

        marks_export = client.get(f'/api/exams/{exam_id}/export.csv')
        marks_rows = {row['student']: row for row in csv.DictReader(StringIO(marks_export.text))}
        assert marks_rows['Byron']['capture_mode'] == 'front_page_totals'
        assert marks_rows['Byron']['workflow_status'] == 'ready'
        assert marks_rows['Byron']['export_ready'] == 'no'
        assert marks_rows['Byron']['marking_progress'] == 'pending front-page confirmation'
        assert marks_rows['Byron']['total_awarded'] == '18.0'
        assert marks_rows['Byron']['total_possible'] == '20.0'
        assert marks_rows['Byron']['objective_summary'] == 'OB1 8.0/10.0 | OB2 10.0/10.0'
        assert marks_rows['Byron']['reporting_attention'] == 'Front-page totals still need teacher confirmation.'
        assert marks_rows['Byron']['next_return_point'] == ''
        assert marks_rows['Byron']['next_action'] == 'Capture and confirm the front-page totals.'

        confirm = client.put(
            f'/api/submissions/{front_page_submission_id}/front-page-totals',
            json={
                'overall_marks_awarded': 18,
                'overall_max_marks': 20,
                'objective_scores': [
                    {'objective_code': 'OB1', 'marks_awarded': 8, 'max_marks': 10},
                    {'objective_code': 'OB2', 'marks_awarded': 10, 'max_marks': 10},
                ],
                'teacher_note': 'Confirmed from the front page.',
                'confirmed': True,
            },
        )
        assert confirm.status_code == 200
        assert confirm.json()['confirmed'] is True

        dashboard = client.get(f'/api/exams/{exam_id}/marking-dashboard')
        rows = {row['student_name']: row for row in dashboard.json()['submissions']}
        assert rows['Byron']['workflow_status'] == 'complete'
        assert rows['Byron']['teacher_marked_questions'] == 1
        assert rows['Byron']['summary_reasons'] == []
        assert rows['Byron']['next_action_text'] == 'Review saved front-page totals.'
        assert rows['Byron']['export_ready'] is True
        assert rows['Byron']['reporting_attention'] == 'Every submission currently has a complete result.'
        assert rows['Byron']['next_return_point'] == ''
        assert rows['Byron']['next_action'] == 'Review saved front-page totals.'

        export = client.get(f'/api/exams/{exam_id}/export-summary.csv')
        rows = {row['student']: row for row in csv.DictReader(StringIO(export.text))}
        assert rows['Byron']['workflow_status'] == 'complete'
        assert rows['Byron']['export_ready'] == 'yes'
        assert rows['Byron']['marking_progress'] == 'confirmed totals'
        assert rows['Byron']['reporting_attention'] == 'Every submission currently has a complete result.'

        objective_export = client.get(f'/api/exams/{exam_id}/export-objectives-summary.csv')
        objective_rows = {row['objective_code']: row for row in csv.DictReader(StringIO(objective_export.text))}
        assert objective_rows['OB1'] == {
            'objective_code': 'OB1',
            'submissions_with_objective': '2',
            'complete_submissions_with_objective': '1',
            'incomplete_submissions_with_objective': '1',
            'total_awarded_complete': '8.0',
            'total_max_complete': '10.0',
            'average_awarded_complete': '8.0',
            'average_percent_complete': '80.0',
            'total_awarded_all_current': '12.0',
            'total_max_all_current': '14.0',
            'average_percent_all_current': '85.7',
            'strongest_complete_student': 'Byron',
            'strongest_complete_percent': '80.0',
            'weakest_complete_student': 'Byron',
            'weakest_complete_percent': '80.0',
            'teacher_summary': '1/2 results export-ready; complete average 80.0%; strongest Byron (80.0%), weakest Byron (80.0%)',
        }
        assert objective_rows['OB2'] == {
            'objective_code': 'OB2',
            'submissions_with_objective': '2',
            'complete_submissions_with_objective': '1',
            'incomplete_submissions_with_objective': '1',
            'total_awarded_complete': '10.0',
            'total_max_complete': '10.0',
            'average_awarded_complete': '10.0',
            'average_percent_complete': '100.0',
            'total_awarded_all_current': '10.0',
            'total_max_all_current': '16.0',
            'average_percent_all_current': '62.5',
            'strongest_complete_student': 'Byron',
            'strongest_complete_percent': '100.0',
            'weakest_complete_student': 'Byron',
            'weakest_complete_percent': '100.0',
            'teacher_summary': '1/2 results export-ready; complete average 100.0%; strongest Byron (100.0%), weakest Byron (100.0%)',
        }
