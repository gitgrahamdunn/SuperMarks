from __future__ import annotations

import re


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
