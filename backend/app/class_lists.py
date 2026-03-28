"""Class-list parsing and matching helpers."""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from app.name_utils import normalize_student_name

_HEADER_WORDS = {
    "student",
    "students",
    "name",
    "names",
    "first",
    "last",
    "first name",
    "last name",
    "student name",
    "student names",
    "class",
    "period",
    "homeroom",
    "id",
    "student id",
    "number",
    "no.",
    "email",
}

_NAME_LABEL_PATTERN = re.compile(r"^(name|student|student name)\s*[:\-]\s*", re.IGNORECASE)
_VALID_NAME_ORDERS = {"first_last", "last_first"}


def _clean_cell_text(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", str(value or "").strip())
    return _NAME_LABEL_PATTERN.sub("", collapsed).strip()


def _is_header_row(values: list[str]) -> bool:
    lowered = {" ".join(_clean_cell_text(value).lower().split()) for value in values if _clean_cell_text(value)}
    return bool(lowered) and lowered.issubset(_HEADER_WORDS)


def _row_name_order(values: list[str]) -> str | None:
    lowered = [" ".join(_clean_cell_text(value).lower().split()) for value in values if _clean_cell_text(value)]
    if "last name" in lowered and "first name" in lowered:
        if lowered.index("last name") < lowered.index("first name"):
            return "last_first"
        return "first_last"
    if "last" in lowered and "first" in lowered:
        if lowered.index("last") < lowered.index("first"):
            return "last_first"
        return "first_last"
    return None


def _is_name_like(value: str) -> bool:
    cleaned = _clean_cell_text(value)
    if not cleaned:
        return False
    if "@" in cleaned:
        return False
    if len(cleaned) > 60:
        return False
    if re.fullmatch(r"[\d\W_]+", cleaned):
        return False
    alpha_chunks = re.findall(r"[A-Za-z][A-Za-z'.-]*", cleaned)
    if not alpha_chunks:
        return False
    if len(alpha_chunks) > 6:
        return False
    return True


def _normalize_name_candidate(value: str) -> str | None:
    if not _is_name_like(value):
        return None
    cleaned = _clean_cell_text(value)
    if "," in cleaned:
        parts = [_strip_name_fragment(part) for part in cleaned.split(",", 1)]
        last_name, first_name = (part for part in parts)
        normalized = normalize_student_name(" ".join(part for part in (first_name, last_name) if part))
    else:
        normalized = normalize_student_name(_strip_name_fragment(cleaned))
    return normalized or None


def _coerce_name_order(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if normalized in _VALID_NAME_ORDERS else None


def _combine_name_like_values(left: str, right: str, *, order: str | None) -> str | None:
    first_value = _clean_cell_text(left)
    second_value = _clean_cell_text(right)
    if order == "last_first":
        combined = f"{second_value} {first_value}"
    else:
        combined = f"{first_value} {second_value}"
    return _normalize_name_candidate(combined)


def _strip_name_fragment(value: str) -> str:
    return re.sub(r"^[,\s]+|[,\s]+$", "", value or "").strip()


def _normalize_single_name_like_value(value: str, *, order: str | None) -> str | None:
    normalized = _normalize_name_candidate(value)
    if not normalized:
        return None
    if order != "last_first":
        return normalized
    parts = normalized.split()
    if len(parts) != 2:
        return normalized
    reordered = " ".join(parts[1:] + [parts[0]])
    return normalize_student_name(reordered) or normalized


def _apply_name_order(normalized: str, *, order: str | None) -> str:
    coerced_order = _coerce_name_order(order)
    if coerced_order != "last_first":
        return normalized
    parts = normalized.split()
    if len(parts) != 2:
        return normalized
    return normalize_student_name(" ".join(parts[1:] + [parts[0]])) or normalized


def _dedupe_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        normalized = _normalize_name_candidate(name)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def normalize_class_list_names(names: list[str], *, forced_order: str | None = None) -> list[str]:
    normalized_names: list[str] = []
    coerced_order = _coerce_name_order(forced_order)
    for name in names:
        normalized = _normalize_single_name_like_value(name, order=coerced_order)
        if normalized:
            normalized_names.append(normalized)
    return _dedupe_names(normalized_names)


def extract_names_from_rows(rows: list[list[str]], *, forced_order: str | None = None) -> list[str]:
    names: list[str] = []
    inferred_order: str | None = _coerce_name_order(forced_order)
    for row in rows:
        cleaned = [_clean_cell_text(value) for value in row if _clean_cell_text(value)]
        if not cleaned:
            continue
        header_order = _row_name_order(cleaned)
        if header_order and inferred_order is None:
            inferred_order = header_order
        if _is_header_row(cleaned):
            continue

        name_like = [value for value in cleaned if _is_name_like(value)]
        if not name_like:
            continue

        if len(name_like) >= 2:
            combined = _combine_name_like_values(name_like[0], name_like[1], order=inferred_order)
            if combined:
                names.append(combined)
                continue

        single = _normalize_single_name_like_value(name_like[0], order=inferred_order)
        if single:
            names.append(single)

    return _dedupe_names(names)


def parse_class_list_csv_bytes(data: bytes, *, forced_order: str | None = None) -> list[str]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [[cell for cell in row] for row in reader]
    return extract_names_from_rows(rows, forced_order=forced_order)


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    strings: list[str] = []
    namespace = {"x": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    for node in root.findall(".//x:si" if namespace else ".//si", namespace):
        texts = [text.strip() for text in node.itertext() if text and text.strip()]
        strings.append(" ".join(texts).strip())
    return strings


def parse_class_list_xlsx_bytes(data: bytes, *, forced_order: str | None = None) -> list[str]:
    rows: list[list[str]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared_strings = _xlsx_shared_strings(zf)
        worksheet_names = sorted(
            [name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
        )
        for worksheet_name in worksheet_names:
            root = ET.fromstring(zf.read(worksheet_name))
            namespace = {"x": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
            row_nodes = root.findall(".//x:row" if namespace else ".//row", namespace)
            for row_node in row_nodes:
                current_row: list[str] = []
                for cell in row_node.findall("x:c" if namespace else "c", namespace):
                    cell_type = cell.attrib.get("t", "")
                    if cell_type == "inlineStr":
                        value = " ".join(text.strip() for text in cell.itertext() if text and text.strip())
                    else:
                        value_node = cell.find("x:v" if namespace else "v", namespace)
                        raw_value = value_node.text.strip() if value_node is not None and value_node.text else ""
                        if cell_type == "s":
                            try:
                                value = shared_strings[int(raw_value)]
                            except (ValueError, IndexError):
                                value = raw_value
                        else:
                            value = raw_value
                    if value:
                        current_row.append(value)
                if current_row:
                    rows.append(current_row)
    return extract_names_from_rows(rows, forced_order=forced_order)


def parse_class_list_tabular_bytes(filename: str, data: bytes, *, forced_order: str | None = None) -> list[str]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return parse_class_list_csv_bytes(data, forced_order=forced_order)
    if suffix in {".xlsx", ".xlsm"}:
        return parse_class_list_xlsx_bytes(data, forced_order=forced_order)
    return []


def nearest_known_student_name(name: str, known_names: list[str], *, minimum_ratio: float = 0.72) -> str:
    normalized = normalize_student_name(name)
    if not normalized or not known_names:
        return normalized or name
    best = normalized
    best_score = 0.0
    for candidate in known_names:
        score = SequenceMatcher(None, normalized.casefold(), candidate.casefold()).ratio()
        if score > best_score:
            best = candidate
            best_score = score
    return best if best_score >= minimum_ratio else normalized


def parse_class_list_names_json(raw_payload: str | None) -> list[str]:
    payload = (raw_payload or "").strip()
    if not payload:
        return []
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [name for name in _dedupe_names([str(item) for item in parsed]) if name]


def build_class_list_payload(
    names: list[str],
    *,
    source: str,
    filenames: list[str] | None = None,
    class_list_id: int | None = None,
    class_list_name: str | None = None,
    created_at: datetime | None = None,
) -> tuple[str, str]:
    deduped = _dedupe_names(names)
    source_payload = {
        "source": source,
        "entry_count": len(deduped),
        "filenames": filenames or [],
        "class_list_id": class_list_id,
        "class_list_name": (class_list_name or "").strip(),
        "created_at": created_at.isoformat() if created_at is not None else None,
    }
    return json.dumps(deduped), json.dumps(source_payload)
