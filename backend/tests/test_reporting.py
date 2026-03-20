import json

from app.models import AnswerCrop, GradeResult, Question, Submission, SubmissionCaptureMode, Transcription
from app.reporting import build_objective_summary_projections, objective_summary_text
from app.reporting_service import CsvExportArtifact, CsvZipArtifactSpec, ExamReportingContext, ExamReportingSnapshot, ExamSubmissionReportingData, FileZipArtifactSpec, MARKS_EXPORT_PREFIX_HEADERS, OBJECTIVES_SUMMARY_EXPORT_HEADERS, STUDENT_SUMMARY_EVIDENCE_HEADERS, STUDENT_SUMMARY_MANIFEST_HEADERS, SUMMARY_EXPORT_HEADERS, StudentReportingExportRow, StudentSummariesZipArtifacts, StudentSummaryEvidenceArtifactContent, StudentSummaryEvidenceRow, StudentSummaryManifestRow, StudentSummaryPackageArtifacts, TextZipArtifactSpec, ZipExportArtifact, _build_exam_marks_export_plan, _build_exam_marks_export_row, build_exam_export_layout, build_exam_marks_export_artifact, build_exam_marks_export_layout, build_exam_marks_export_spec, build_exam_marking_dashboard, build_exam_marking_dashboard_response, build_exam_objectives_summary_export_artifact, build_exam_objectives_summary_export_spec, build_exam_student_summaries_zip_artifact_specs, build_exam_student_summaries_zip_export_artifact, build_exam_student_summaries_zip_plan, build_exam_summary_export_artifact, build_exam_summary_export_spec, build_student_summary_evidence_export_spec, build_student_summary_manifest_export_spec, build_student_summaries_zip_artifacts, build_submission_evidence_artifact_content, build_submission_evidence_package_artifact_specs, build_submission_reporting_projection, build_student_summary_manifest_row, build_student_summary_package_artifacts, build_zip_export_content, marks_export_question_safe_label
from app.schemas import ObjectiveTotalRead, SubmissionDashboardRow


def test_build_objective_summary_projections_rolls_up_dashboard_rows() -> None:
    rows = [
        SubmissionDashboardRow(
            submission_id=1,
            student_name="Ada",
            workflow_status="complete",
            flagged_count=0,
            questions_total=2,
            teacher_marked_questions=2,
            marking_progress="2/2 marked",
            running_total=9,
            total_possible=10,
            objective_totals=[
                ObjectiveTotalRead(objective_code="OB1", marks_awarded=4, max_marks=4, questions_count=1),
                ObjectiveTotalRead(objective_code="OB2", marks_awarded=5, max_marks=6, questions_count=1),
            ],
            ready_for_marking=True,
            can_prepare_now=True,
        ),
        SubmissionDashboardRow(
            submission_id=2,
            student_name="Byron",
            workflow_status="in_progress",
            flagged_count=0,
            questions_total=2,
            teacher_marked_questions=1,
            marking_progress="1/2 marked",
            running_total=2,
            total_possible=10,
            objective_totals=[
                ObjectiveTotalRead(objective_code="OB1", marks_awarded=2, max_marks=4, questions_count=1),
                ObjectiveTotalRead(objective_code="OB2", marks_awarded=0, max_marks=6, questions_count=1),
            ],
            ready_for_marking=True,
            can_prepare_now=True,
        ),
        SubmissionDashboardRow(
            submission_id=3,
            student_name="Cara",
            workflow_status="blocked",
            flagged_count=1,
            questions_total=2,
            teacher_marked_questions=0,
            marking_progress="0/2 marked",
            running_total=0,
            total_possible=10,
            objective_totals=[],
            ready_for_marking=False,
            can_prepare_now=False,
            summary_reasons=["Missing crop"],
        ),
    ]

    projections = {projection.objective_code: projection for projection in build_objective_summary_projections(rows)}

    assert projections["OB1"].submissions_with_objective == 2
    assert projections["OB1"].complete_submissions_with_objective == 1
    assert projections["OB1"].incomplete_submissions_with_objective == 1
    assert projections["OB1"].total_awarded_complete == 4.0
    assert projections["OB1"].total_max_complete == 4.0
    assert projections["OB1"].average_awarded_complete == 4.0
    assert projections["OB1"].average_percent_complete == 100.0
    assert projections["OB1"].total_awarded_all_current == 6.0
    assert projections["OB1"].total_max_all_current == 8.0
    assert projections["OB1"].average_percent_all_current == 75.0
    assert projections["OB1"].strongest_complete_student == "Ada"
    assert projections["OB1"].weakest_complete_student == "Ada"
    assert projections["OB1"].weakest_complete_submission is not None
    assert projections["OB1"].weakest_complete_submission.submission_id == 1
    assert projections["OB1"].teacher_summary == "1/2 results export-ready; complete average 100.0%; strongest Ada (100.0%), weakest Ada (100.0%)"

    assert projections["OB2"].teacher_summary == "1/2 results export-ready; complete average 83.3%; strongest Ada (83.3%), weakest Ada (83.3%)"


def test_objective_summary_text_formats_shared_snapshot_string() -> None:
    summary = objective_summary_text([
        ObjectiveTotalRead(objective_code="OB1", marks_awarded=8, max_marks=10, questions_count=2),
        ObjectiveTotalRead(objective_code="OB2", marks_awarded=3.25, max_marks=4, questions_count=1),
    ])

    assert summary == "OB1 8.0/10.0 | OB2 3.2/4.0"


def test_exam_marks_export_layout_builds_reusable_exam_level_columns_and_headers() -> None:
    questions = [
        Question(id=11, exam_id=7, label="Q1 / Intro", max_marks=5, rubric_json=json.dumps({"objective_codes": ["OB1"]})),
        Question(id=12, exam_id=7, label="", max_marks=7, rubric_json=json.dumps({"objective_codes": ["OB2", "OB3"]})),
    ]

    assert marks_export_question_safe_label(questions[0]) == "Q1_Intro"
    assert marks_export_question_safe_label(questions[1]) == "question_12"

    export_plan = build_exam_marks_export_layout(questions)
    export_layout = build_exam_export_layout(questions)

    assert export_plan.headers[:16] == MARKS_EXPORT_PREFIX_HEADERS
    assert export_layout.marks == export_plan
    assert export_layout.summary_headers == SUMMARY_EXPORT_HEADERS
    assert export_layout.objectives_summary_headers == OBJECTIVES_SUMMARY_EXPORT_HEADERS
    assert export_layout.student_summary_manifest_headers == STUDENT_SUMMARY_MANIFEST_HEADERS
    assert export_layout.student_summary_evidence_headers == STUDENT_SUMMARY_EVIDENCE_HEADERS
    assert [column.objective_code for column in export_plan.objective_columns] == ["OB1", "OB2", "OB3"]
    assert [
        (column.question_id, column.awarded_header, column.max_header, column.objectives_header)
        for column in export_plan.question_columns
    ] == [
        (11, "Q1_Intro_awarded", "Q1_Intro_max", "Q1_Intro_objectives"),
        (12, "question_12_awarded", "question_12_max", "question_12_objectives"),
    ]
    assert export_plan.headers[16:] == [
        "objective_OB1_awarded",
        "objective_OB1_max",
        "objective_OB2_awarded",
        "objective_OB2_max",
        "objective_OB3_awarded",
        "objective_OB3_max",
        "Q1_Intro_awarded",
        "Q1_Intro_max",
        "Q1_Intro_objectives",
        "question_12_awarded",
        "question_12_max",
        "question_12_objectives",
    ]


def test_exam_marks_export_spec_and_rows_share_dashboard_export_state() -> None:
    questions = [
        Question(id=11, exam_id=7, label="Q1 / Intro", max_marks=5, rubric_json=json.dumps({"objective_codes": ["OB1"]})),
        Question(id=12, exam_id=7, label="Q2: Analysis", max_marks=7, rubric_json=json.dumps({"objective_codes": ["OB2", "OB3"]})),
    ]
    dashboard_row = SubmissionDashboardRow(
        submission_id=21,
        student_name="Ada",
        capture_mode=SubmissionCaptureMode.QUESTION_LEVEL,
        workflow_status="in_progress",
        flagged_count=1,
        questions_total=2,
        teacher_marked_questions=1,
        marking_progress="1/2 marked",
        running_total=4,
        total_possible=12,
        objective_totals=[
            ObjectiveTotalRead(objective_code="OB1", marks_awarded=4, max_marks=5, questions_count=1),
            ObjectiveTotalRead(objective_code="OB2", marks_awarded=0, max_marks=7, questions_count=1),
        ],
        ready_for_marking=True,
        can_prepare_now=True,
        export_ready=False,
        reporting_attention="Needs teacher follow-up",
        next_return_point="Q2: Analysis",
        next_action="Resume marking at Q2: Analysis.",
    )
    grade = GradeResult(question_id=11, submission_id=21, marks_awarded=4, model_name="teacher_manual")
    snapshot = ExamReportingSnapshot(
        question_regions_by_question_id={},
        submission_data_by_submission_id={
            21: ExamSubmissionReportingData(pages=[], crops=[], transcriptions=[], grades=[grade]),
        },
    )
    submission = Submission(id=21, exam_id=7, student_name="Ada", capture_mode=SubmissionCaptureMode.QUESTION_LEVEL)
    projection = build_submission_reporting_projection(
        submission,
        dashboard_row,
        questions=questions,
        snapshot=snapshot,
    )
    context = ExamReportingContext(
        exam=object(),
        questions=questions,
        submissions=[],
        dashboard_rows=[dashboard_row],
        submission_projections=[projection],
        snapshot=snapshot,
    )

    export_plan = _build_exam_marks_export_plan(context)

    assert [column.objective_code for column in export_plan.objective_columns] == ["OB1", "OB2", "OB3"]
    assert export_plan.headers[:16] == MARKS_EXPORT_PREFIX_HEADERS
    assert export_plan.headers[16:] == [
        "objective_OB1_awarded",
        "objective_OB1_max",
        "objective_OB2_awarded",
        "objective_OB2_max",
        "objective_OB3_awarded",
        "objective_OB3_max",
        "Q1_Intro_awarded",
        "Q1_Intro_max",
        "Q1_Intro_objectives",
        "Q2_Analysis_awarded",
        "Q2_Analysis_max",
        "Q2_Analysis_objectives",
    ]

    assert projection.grade_map == {11: grade}
    assert projection.marks_export_payload.prefix_values == [
        "Ada",
        "question_level",
        "in_progress",
        "no",
        1,
        1,
        2,
        "1/2 marked",
        4.0,
        12.0,
        33.3,
        "OB1 4.0/5.0 | OB2 0.0/7.0",
        2,
        "Needs teacher follow-up",
        "Q2: Analysis",
        "Resume marking at Q2: Analysis.",
    ]
    assert projection.marks_export_payload.objective_values_by_code["OB1"].marks_awarded == 4.0
    assert "OB3" not in projection.marks_export_payload.objective_values_by_code
    assert projection.marks_export_payload.question_values_by_question_id[11].marks_awarded == 4.0
    assert projection.marks_export_payload.question_values_by_question_id[12].max_marks == 7.0
    assert projection.marks_export_payload.question_values_by_question_id[12].objective_codes == ["OB2", "OB3"]

    marks_row = _build_exam_marks_export_row(
        projection,
        export_plan=export_plan,
    )

    assert marks_row.prefix_values == [
        "Ada",
        "question_level",
        "in_progress",
        "no",
        1,
        1,
        2,
        "1/2 marked",
        4.0,
        12.0,
        33.3,
        "OB1 4.0/5.0 | OB2 0.0/7.0",
        2,
        "Needs teacher follow-up",
        "Q2: Analysis",
        "Resume marking at Q2: Analysis.",
    ]
    assert marks_row.objective_values == [4.0, 5.0, 0.0, 7.0, "", ""]
    assert marks_row.question_values == [4.0, 5.0, "OB1", "", 7.0, "OB2; OB3"]
    assert marks_row.as_csv_row() == [
        "Ada",
        "question_level",
        "in_progress",
        "no",
        1,
        1,
        2,
        "1/2 marked",
        4.0,
        12.0,
        33.3,
        "OB1 4.0/5.0 | OB2 0.0/7.0",
        2,
        "Needs teacher follow-up",
        "Q2: Analysis",
        "Resume marking at Q2: Analysis.",
        4.0,
        5.0,
        0.0,
        7.0,
        "",
        "",
        4.0,
        5.0,
        "OB1",
        "",
        7.0,
        "OB2; OB3",
    ]

    marks_spec = build_exam_marks_export_spec(context, session=None)
    assert marks_spec.headers == export_plan.headers
    assert [row.as_csv_row() for row in marks_spec.rows] == [marks_row.as_csv_row()]

    summary_spec = build_exam_summary_export_spec(context)
    assert summary_spec.headers == SUMMARY_EXPORT_HEADERS
    assert [row.as_csv_row() for row in summary_spec.rows] == [[
        "Ada",
        "question_level",
        "in_progress",
        "no",
        "1/2 marked",
        4.0,
        12.0,
        33.3,
        1,
        2,
        2,
        "OB1 4.0/5.0 | OB2 0.0/7.0",
        "Q2: Analysis",
        "Resume marking at Q2: Analysis.",
        "Needs teacher follow-up",
    ]]

    objective_spec = build_exam_objectives_summary_export_spec(context)
    assert objective_spec.headers == OBJECTIVES_SUMMARY_EXPORT_HEADERS
    assert [row.as_csv_row() for row in objective_spec.rows] == [
        [
            "OB1",
            1,
            0,
            1,
            0.0,
            0.0,
            "",
            "",
            4.0,
            5.0,
            80.0,
            "",
            "",
            "",
            "",
            "0/1 results export-ready; complete average —%; 1 result(s) still in progress",
        ],
        [
            "OB2",
            1,
            0,
            1,
            0.0,
            0.0,
            "",
            "",
            0.0,
            7.0,
            0.0,
            "",
            "",
            "",
            "",
            "0/1 results export-ready; complete average —%; 1 result(s) still in progress",
        ],
    ]

    assert projection.export_row.as_summary_csv_row() == [
        "Ada",
        "question_level",
        "in_progress",
        "no",
        "1/2 marked",
        4.0,
        12.0,
        33.3,
        1,
        2,
        2,
        "OB1 4.0/5.0 | OB2 0.0/7.0",
        "Q2: Analysis",
        "Resume marking at Q2: Analysis.",
        "Needs teacher follow-up",
    ]
    assert [(row.label, row.status, row.teacher_note) for row in projection.question_rows] == [
        ("Q1 / Intro", "Teacher-marked", ""),
        ("Q2: Analysis", "Not yet marked", ""),
    ]


def test_exam_student_summaries_zip_plan_extracts_package_structure_and_manifest_state() -> None:
    questions = [Question(id=11, exam_id=7, label="Q1", max_marks=5, rubric_json=json.dumps({"objective_codes": ["OB1"]}))]
    dashboard_row = SubmissionDashboardRow(
        submission_id=21,
        student_name="Ada",
        capture_mode=SubmissionCaptureMode.QUESTION_LEVEL,
        workflow_status="complete",
        flagged_count=1,
        questions_total=1,
        teacher_marked_questions=1,
        marking_progress="1/1 marked",
        running_total=4,
        total_possible=5,
        objective_totals=[
            ObjectiveTotalRead(objective_code="OB1", marks_awarded=4, max_marks=5, questions_count=1),
        ],
        ready_for_marking=True,
        can_prepare_now=True,
        export_ready=True,
        reporting_attention="Every submission currently has a complete result.",
        next_return_point="Q1",
        next_action="Review results or return to the class queue.",
    )
    grade = GradeResult(question_id=11, submission_id=21, marks_awarded=4, model_name="teacher_manual", feedback_json=json.dumps({"teacher_note": "Strong setup"}))
    snapshot = ExamReportingSnapshot(
        question_regions_by_question_id={},
        submission_data_by_submission_id={
            21: ExamSubmissionReportingData(
                pages=[],
                crops=[],
                transcriptions=[],
                grades=[grade],
            ),
        },
    )
    submission = Submission(id=21, exam_id=7, student_name="Ada", capture_mode=SubmissionCaptureMode.QUESTION_LEVEL)
    projection = build_submission_reporting_projection(submission, dashboard_row, questions=questions, snapshot=snapshot)
    context = ExamReportingContext(
        exam=type("ExamStub", (), {"name": "Midterm"})(),
        questions=questions,
        submissions=[submission],
        dashboard_rows=[dashboard_row],
        submission_projections=[projection],
        snapshot=snapshot,
    )

    export_plan = build_exam_student_summaries_zip_plan(context)

    assert export_plan.archive_artifacts == StudentSummariesZipArtifacts(
        root_dirname="student-summaries",
        readme_relpath="student-summaries/README.txt",
        manifest_relpath="student-summaries/manifest.csv",
    )
    assert export_plan.readme_text.startswith("SuperMarks student summary package — Midterm")
    assert export_plan.manifest_export_spec.headers == STUDENT_SUMMARY_MANIFEST_HEADERS
    assert len(export_plan.submission_packages) == 1
    package = export_plan.submission_packages[0]
    assert package.package_dirname == "student-summaries/01-ada"
    assert package.summary_text_relpath == "student-summaries/01-ada/summary.txt"
    assert package.summary_html_relpath == "student-summaries/01-ada/summary.html"
    assert package.evidence_package is not None
    assert package.evidence_package.readme_relpath == "student-summaries/01-ada/evidence/README.txt"
    assert package.evidence_package.manifest_relpath == "student-summaries/01-ada/evidence/manifest.csv"
    assert package.evidence_package.file_count == 2
    assert [type(spec) for spec in package.artifact_specs] == [
        TextZipArtifactSpec,
        TextZipArtifactSpec,
        TextZipArtifactSpec,
        CsvZipArtifactSpec,
    ]
    assert [spec.relpath for spec in package.artifact_specs] == [
        "student-summaries/01-ada/summary.txt",
        "student-summaries/01-ada/summary.html",
        "student-summaries/01-ada/evidence/README.txt",
        "student-summaries/01-ada/evidence/manifest.csv",
    ]
    assert isinstance(package.artifact_specs[0], TextZipArtifactSpec)
    assert package.artifact_specs[0].text.startswith("Exam: Midterm\nStudent: Ada\n")
    assert isinstance(package.artifact_specs[1], TextZipArtifactSpec)
    assert "<h1>Ada</h1>" in package.artifact_specs[1].text
    evidence_specs = package.evidence_package.artifact_specs
    assert [type(spec) for spec in evidence_specs] == [TextZipArtifactSpec, CsvZipArtifactSpec]

    artifact_specs = build_exam_student_summaries_zip_artifact_specs(export_plan)
    assert [spec.relpath for spec in artifact_specs] == [
        "student-summaries/README.txt",
        "student-summaries/01-ada/summary.txt",
        "student-summaries/01-ada/summary.html",
        "student-summaries/01-ada/evidence/README.txt",
        "student-summaries/01-ada/evidence/manifest.csv",
        "student-summaries/manifest.csv",
    ]

    assert export_plan.manifest_rows == [
        StudentSummaryManifestRow(
            student="Ada",
            capture_mode="question_level",
            workflow_status="complete",
            export_ready="yes",
            flagged_questions=1,
            teacher_marked_questions=1,
            questions_total=1,
            marking_progress="1/1 marked",
            total_awarded=4.0,
            total_possible=5.0,
            total_percent=80.0,
            objective_summary="OB1 4.0/5.0",
            reporting_attention="Every submission currently has a complete result.",
            next_return_point="Q1",
            next_action="Review results or return to the class queue.",
            summary_text_file="student-summaries/01-ada/summary.txt",
            summary_html_file="student-summaries/01-ada/summary.html",
            evidence_manifest_file="student-summaries/01-ada/evidence/manifest.csv",
            evidence_file_count=2,
        )
    ]


def test_student_summaries_zip_artifact_helper_builds_top_level_paths() -> None:
    assert build_student_summaries_zip_artifacts() == StudentSummariesZipArtifacts(
        root_dirname="student-summaries",
        readme_relpath="student-summaries/README.txt",
        manifest_relpath="student-summaries/manifest.csv",
    )


def test_build_exam_marking_dashboard_response_loads_context_once(monkeypatch) -> None:
    context = ExamReportingContext(
        exam=type("ExamStub", (), {"id": 7, "name": "Midterm"})(),
        questions=[],
        submissions=[],
        dashboard_rows=[],
        submission_projections=[],
        snapshot=ExamReportingSnapshot(question_regions_by_question_id={}, submission_data_by_submission_id={}),
    )
    calls: list[tuple[str, object]] = []

    def fake_load(exam_id, session):
        calls.append(("load", exam_id))
        return context

    def fake_build(arg):
        calls.append(("build", arg))
        return {"ok": True, "exam_id": arg.exam.id}

    monkeypatch.setattr("app.reporting_service.load_exam_reporting_context", fake_load)
    monkeypatch.setattr("app.reporting_service.build_exam_marking_dashboard", fake_build)

    assert build_exam_marking_dashboard_response(7, session=object()) == {"ok": True, "exam_id": 7}
    assert calls == [("load", 7), ("build", context)]


def test_csv_export_artifact_helpers_wrap_route_facing_filenames(monkeypatch) -> None:
    context = ExamReportingContext(
        exam=type("ExamStub", (), {"id": 12, "name": "Midterm"})(),
        questions=[],
        submissions=[],
        dashboard_rows=[],
        submission_projections=[],
        snapshot=ExamReportingSnapshot(question_regions_by_question_id={}, submission_data_by_submission_id={}),
    )
    marks_spec = object()
    summary_spec = object()
    objectives_spec = object()
    zip_plan = object()
    zip_specs = [TextZipArtifactSpec(relpath="student-summaries/README.txt", text="hello")]

    monkeypatch.setattr("app.reporting_service.load_exam_reporting_context", lambda exam_id, session: context)
    monkeypatch.setattr("app.reporting_service.build_exam_marks_export_spec", lambda loaded_context, session: marks_spec)
    monkeypatch.setattr("app.reporting_service.build_exam_summary_export_spec", lambda loaded_context: summary_spec)
    monkeypatch.setattr("app.reporting_service.build_exam_objectives_summary_export_spec", lambda loaded_context: objectives_spec)
    monkeypatch.setattr("app.reporting_service.build_exam_student_summaries_zip_plan", lambda loaded_context: zip_plan)
    monkeypatch.setattr("app.reporting_service.build_exam_student_summaries_zip_artifact_specs", lambda export_plan: zip_specs)

    assert build_exam_marks_export_artifact(12, session=object()) == CsvExportArtifact(filename="exam-12-marks.csv", export_spec=marks_spec)
    assert build_exam_summary_export_artifact(12, session=object()) == CsvExportArtifact(filename="exam-12-summary.csv", export_spec=summary_spec)
    assert build_exam_objectives_summary_export_artifact(12, session=object()) == CsvExportArtifact(filename="exam-12-objectives-summary.csv", export_spec=objectives_spec)
    assert build_exam_student_summaries_zip_export_artifact(12, session=object()) == ZipExportArtifact(filename="exam-12-student-summaries.zip", artifact_specs=zip_specs)



def test_build_zip_export_content_writes_text_csv_and_files(tmp_path) -> None:
    source_path = tmp_path / "crop.png"
    source_path.write_bytes(b"png-bytes")

    export_bytes = build_zip_export_content([
        TextZipArtifactSpec(relpath="student-summaries/README.txt", text="hello"),
        CsvZipArtifactSpec(
            relpath="student-summaries/manifest.csv",
            export_spec=build_student_summary_manifest_export_spec(
                [],
                [
                    StudentSummaryManifestRow(
                        student="Ada",
                        capture_mode="question_level",
                        workflow_status="complete",
                        export_ready="yes",
                        flagged_questions=0,
                        teacher_marked_questions=1,
                        questions_total=1,
                        marking_progress="1/1 marked",
                        total_awarded=4.0,
                        total_possible=5.0,
                        total_percent=80.0,
                        objective_summary="OB1 4.0/5.0",
                        reporting_attention="Ready",
                        next_return_point="Q1",
                        next_action="Review",
                        summary_text_file="student-summaries/01-ada/summary.txt",
                        summary_html_file="student-summaries/01-ada/summary.html",
                        evidence_manifest_file="student-summaries/01-ada/evidence/manifest.csv",
                        evidence_file_count=4,
                    )
                ],
            ),
        ),
        FileZipArtifactSpec(relpath="student-summaries/01-ada/evidence/Q1-crop.png", source_path=str(source_path)),
    ])

    import zipfile
    from io import BytesIO

    archive = zipfile.ZipFile(BytesIO(export_bytes))
    assert sorted(archive.namelist()) == [
        "student-summaries/01-ada/evidence/Q1-crop.png",
        "student-summaries/README.txt",
        "student-summaries/manifest.csv",
    ]
    assert archive.read("student-summaries/README.txt").decode("utf-8") == "hello"
    manifest_text = archive.read("student-summaries/manifest.csv").decode("utf-8")
    assert "student,capture_mode,workflow_status" in manifest_text
    assert "Ada,question_level,complete" in manifest_text
    assert archive.read("student-summaries/01-ada/evidence/Q1-crop.png") == b"png-bytes"


def test_student_summary_package_helpers_share_relpaths_and_manifest_fields() -> None:
    submission = Submission(id=21, exam_id=7, student_name="Ada", capture_mode=SubmissionCaptureMode.QUESTION_LEVEL)
    export_row = StudentReportingExportRow(
        student="Ada",
        capture_mode="question_level",
        workflow_status="complete",
        export_ready="yes",
        flagged_questions=1,
        teacher_marked_questions=1,
        questions_total=1,
        marking_progress="1/1 marked",
        total_awarded=4.0,
        total_possible=5.0,
        total_percent=80.0,
        objective_summary="OB1 4.0/5.0",
        objective_count=1,
        reporting_attention="Ready to return.",
        next_return_point="Q1",
        next_action="Review results.",
    )

    package_artifacts = build_student_summary_package_artifacts(submission, 1)

    assert package_artifacts == StudentSummaryPackageArtifacts(
        package_dirname="student-summaries/01-ada",
        summary_text_relpath="student-summaries/01-ada/summary.txt",
        summary_html_relpath="student-summaries/01-ada/summary.html",
    )

    manifest_row = build_student_summary_manifest_row(export_row, package_artifacts, None)
    assert manifest_row == StudentSummaryManifestRow(
        student="Ada",
        capture_mode="question_level",
        workflow_status="complete",
        export_ready="yes",
        flagged_questions=1,
        teacher_marked_questions=1,
        questions_total=1,
        marking_progress="1/1 marked",
        total_awarded=4.0,
        total_possible=5.0,
        total_percent=80.0,
        objective_summary="OB1 4.0/5.0",
        reporting_attention="Ready to return.",
        next_return_point="Q1",
        next_action="Review results.",
        summary_text_file="student-summaries/01-ada/summary.txt",
        summary_html_file="student-summaries/01-ada/summary.html",
        evidence_manifest_file="",
        evidence_file_count=0,
    )


def test_submission_evidence_artifact_content_drives_paths_counts_and_specs(tmp_path) -> None:
    crop_path = tmp_path / "q1.png"
    crop_path.write_bytes(b"png-bytes")

    submission = Submission(id=21, exam_id=7, student_name="Ada", capture_mode=SubmissionCaptureMode.QUESTION_LEVEL)
    questions = [Question(id=11, exam_id=7, label="Q1", max_marks=5)]
    submission_data = ExamSubmissionReportingData(
        pages=[],
        crops=[AnswerCrop(question_id=11, submission_id=21, image_path=str(crop_path))],
        transcriptions=[
            Transcription(
                question_id=11,
                submission_id=21,
                text="Ada answer",
                raw_json="",
                provider="stub-ocr",
                confidence=0.98,
            )
        ],
        grades=[
            GradeResult(
                question_id=11,
                submission_id=21,
                marks_awarded=4,
                model_name="teacher_manual",
                feedback_json=json.dumps({"teacher_note": "Strong setup"}),
            )
        ],
    )

    artifact_content = build_submission_evidence_artifact_content(
        submission,
        questions,
        submission_data,
        "student-summaries/01-ada",
    )

    assert artifact_content == StudentSummaryEvidenceArtifactContent(
        readme_text=artifact_content.readme_text,
        readme_relpath="student-summaries/01-ada/evidence/README.txt",
        manifest_relpath="student-summaries/01-ada/evidence/manifest.csv",
        rows=[
            StudentSummaryEvidenceRow(
                question_id=11,
                question_label="Q1",
                crop_path=str(crop_path),
                crop_relpath="student-summaries/01-ada/evidence/Q1-crop.png",
                transcription_text="Ada answer",
                transcription_raw_json="",
                transcription_provider="stub-ocr",
                transcription_confidence=0.98,
                transcription_text_relpath="student-summaries/01-ada/evidence/Q1-transcription.txt",
                transcription_json_relpath="student-summaries/01-ada/evidence/Q1-transcription.json",
                grade_status="Teacher-marked",
                teacher_note="Strong setup",
            )
        ],
    )
    assert "Evidence guide — Ada" in artifact_content.readme_text

    artifact_specs = build_submission_evidence_package_artifact_specs(questions, artifact_content)
    assert [type(spec) for spec in artifact_specs] == [
        TextZipArtifactSpec,
        FileZipArtifactSpec,
        TextZipArtifactSpec,
        TextZipArtifactSpec,
        CsvZipArtifactSpec,
    ]
    assert [spec.relpath for spec in artifact_specs] == [
        "student-summaries/01-ada/evidence/README.txt",
        "student-summaries/01-ada/evidence/Q1-crop.png",
        "student-summaries/01-ada/evidence/Q1-transcription.txt",
        "student-summaries/01-ada/evidence/Q1-transcription.json",
        "student-summaries/01-ada/evidence/manifest.csv",
    ]
    assert isinstance(artifact_specs[3], TextZipArtifactSpec)
    assert artifact_specs[3].text == "{}"


def test_student_summary_package_csv_export_specs_reuse_typed_rows() -> None:
    questions = [Question(id=11, exam_id=7, label="Q1", max_marks=5)]
    manifest_row = StudentSummaryManifestRow(
        student="Ada",
        capture_mode="question_level",
        workflow_status="complete",
        export_ready="yes",
        flagged_questions=1,
        teacher_marked_questions=1,
        questions_total=1,
        marking_progress="1/1 marked",
        total_awarded=4.0,
        total_possible=5.0,
        total_percent=80.0,
        objective_summary="OB1 4.0/5.0",
        reporting_attention="Ready to return.",
        next_return_point="Q1",
        next_action="Review results.",
        summary_text_file="student-summaries/01-ada/summary.txt",
        summary_html_file="student-summaries/01-ada/summary.html",
        evidence_manifest_file="student-summaries/01-ada/evidence/manifest.csv",
        evidence_file_count=4,
    )
    evidence_row = StudentSummaryEvidenceRow(
        question_id=11,
        question_label="Q1",
        crop_path="/tmp/q1.png",
        crop_relpath="student-summaries/01-ada/evidence/Q1-crop.png",
        transcription_text="Ada answer",
        transcription_raw_json='{"text": "Ada answer"}',
        transcription_provider="stub-ocr",
        transcription_confidence=0.98,
        transcription_text_relpath="student-summaries/01-ada/evidence/Q1-transcription.txt",
        transcription_json_relpath="student-summaries/01-ada/evidence/Q1-transcription.json",
        grade_status="Teacher-marked",
        teacher_note="Strong setup",
    )

    manifest_spec = build_student_summary_manifest_export_spec(questions, [manifest_row])
    assert manifest_spec.headers == STUDENT_SUMMARY_MANIFEST_HEADERS
    assert [row.as_csv_row() for row in manifest_spec.rows] == [[
        "Ada",
        "question_level",
        "complete",
        "yes",
        1,
        1,
        1,
        "1/1 marked",
        4.0,
        5.0,
        80.0,
        "OB1 4.0/5.0",
        "Ready to return.",
        "Q1",
        "Review results.",
        "student-summaries/01-ada/summary.txt",
        "student-summaries/01-ada/summary.html",
        "student-summaries/01-ada/evidence/manifest.csv",
        4,
    ]]

    evidence_spec = build_student_summary_evidence_export_spec(questions, [evidence_row])
    assert evidence_spec.headers == STUDENT_SUMMARY_EVIDENCE_HEADERS
    assert [row.as_csv_row() for row in evidence_spec.rows] == [[
        "Q1",
        "Teacher-marked",
        "Strong setup",
        "stub-ocr",
        0.98,
        "student-summaries/01-ada/evidence/Q1-crop.png",
        "student-summaries/01-ada/evidence/Q1-transcription.txt",
        "student-summaries/01-ada/evidence/Q1-transcription.json",
    ]]
