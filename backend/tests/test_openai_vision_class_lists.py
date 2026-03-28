from __future__ import annotations

from app.ai.openai_vision import _normalize_class_list_name_output


def test_normalize_class_list_name_output_reorders_last_first_names() -> None:
    assert _normalize_class_list_name_output("Lee, Jordan") == "Jordan Lee"


def test_normalize_class_list_name_output_strips_commas_and_spacing() -> None:
    assert _normalize_class_list_name_output("  Stone,   Avery   ") == "Avery Stone"


def test_normalize_class_list_name_output_keeps_first_last_names() -> None:
    assert _normalize_class_list_name_output("Jordan Lee") == "Jordan Lee"


def test_normalize_class_list_name_output_reorders_single_cell_last_first_when_page_declares_it() -> None:
    assert _normalize_class_list_name_output("Lee Jordan", page_name_order="last_first") == "Jordan Lee"
