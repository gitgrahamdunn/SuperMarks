from app.routers.exams import _should_escalate_parse_result


def test_parse_quality_escalates_empty_or_low_confidence_results() -> None:
    should_escalate, reasons = _should_escalate_parse_result(
        confidence=0.2,
        questions_payload=[],
        warnings=[],
    )
    assert should_escalate is True
    assert "no_questions" in reasons
    assert "low_confidence" in reasons


def test_parse_quality_escalates_suspicious_marks_and_fallback_shape() -> None:
    should_escalate, reasons = _should_escalate_parse_result(
        confidence=0.92,
        questions_payload=[
            {
                "label": "Q1",
                "max_marks": 0,
                "question_text": "Problem text here",
                "answer_key": "",
                "objective_codes": [],
            }
        ],
        warnings=["Needs teacher review", "another", "third"],
    )
    assert should_escalate is True
    assert "non_positive_marks" in reasons
    assert "fallback_only" in reasons
    assert "warning_heavy" in reasons


def test_parse_quality_allows_clean_structural_output() -> None:
    should_escalate, reasons = _should_escalate_parse_result(
        confidence=0.91,
        questions_payload=[
            {
                "label": "Q5",
                "max_marks": 2,
                "question_text": "Q5-OB2 Solve the triangle",
                "answer_key": "Use sine law",
                "objective_codes": ["OB2"],
            }
        ],
        warnings=[],
    )
    assert should_escalate is False
    assert reasons == []
