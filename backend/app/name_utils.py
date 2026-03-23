from __future__ import annotations

import re


def normalize_name_parts(first_name: str | None, last_name: str | None) -> tuple[str, str]:
    normalized_first = normalize_student_name(first_name or "")
    normalized_last = normalize_student_name(last_name or "")
    return normalized_first, normalized_last


def split_student_name(value: str | None) -> tuple[str, str]:
    normalized = normalize_student_name(value or "")
    if not normalized:
        return "", ""
    parts = normalized.split()
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def compose_student_name(first_name: str | None, last_name: str | None) -> str:
    normalized_first, normalized_last = normalize_name_parts(first_name, last_name)
    return " ".join(part for part in (normalized_first, normalized_last) if part).strip()


def submission_name_parts(first_name: str | None, last_name: str | None, fallback_name: str | None) -> tuple[str, str]:
    normalized_first, normalized_last = normalize_name_parts(first_name, last_name)
    if normalized_first or normalized_last:
        return normalized_first, normalized_last
    return split_student_name(fallback_name)


def submission_display_name(first_name: str | None, last_name: str | None, fallback_name: str | None) -> str:
    normalized_first, normalized_last = submission_name_parts(first_name, last_name, fallback_name)
    return compose_student_name(normalized_first, normalized_last)


def normalize_student_name(value: str) -> str:
    collapsed = " ".join(str(value or "").strip().split())
    if not collapsed:
        return ""
    return " ".join(_normalize_token(token) for token in collapsed.split(" "))


def student_name_sort_key(value: str) -> tuple[str, str]:
    normalized = normalize_student_name(value)
    if not normalized:
        return ("", "")
    parts = normalized.split()
    last = parts[-1].casefold()
    rest = " ".join(parts[:-1]).casefold()
    return (last, rest)


def _normalize_token(token: str) -> str:
    parts = re.split(r"([\-'])", token)
    normalized_parts: list[str] = []
    for part in parts:
        if part in {"-", "'"}:
            normalized_parts.append(part)
        elif part:
            normalized_parts.append(part[:1].upper() + part[1:].lower())
    return "".join(normalized_parts)
