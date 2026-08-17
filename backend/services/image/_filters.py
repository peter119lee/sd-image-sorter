"""Selection-filter coercers and gallery filter sanitizers (pure functions).

Moved verbatim from services/image_service.py (decomposition 2026-07).
services/image_service.py re-imports every name so the facade attributes the
pin suite calls (image_service._coerce_* / _sanitize_filter_values) keep
resolving. No test monkeypatches these names (string-form patches NONE; module-object patches are only db / move_file_to_trash /
SELECTION_IDS_MAX_RESPONSE), so sibling mixins import them directly and the
call sites stay verbatim.
"""

import re
from typing import Any, List, Optional

from fastapi import HTTPException

from services.image._constants import (
    PROMPT_MATCH_MODE_EXACT,
    VALID_PROMPT_MATCH_MODES,
)



def _invalid_selection_token() -> HTTPException:
    return HTTPException(status_code=400, detail="Invalid selection token")


def _coerce_optional_int_filter(value: Any, field_name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise _invalid_selection_token()
    try:
        return int(value)
    except (TypeError, ValueError):
        raise _invalid_selection_token()


def _coerce_optional_float_filter(value: Any, field_name: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise _invalid_selection_token()
    try:
        return float(value)
    except (TypeError, ValueError):
        raise _invalid_selection_token()


_DATE_FILTER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _coerce_optional_date_filter(value: Any, field_name: str) -> Optional[str]:
    """ISO day string for the file-time date filter; anything else is an
    invalid selection token, same failure mode as the other coercers."""
    if value is None:
        return None
    if not isinstance(value, str) or not _DATE_FILTER_RE.match(value.strip()):
        raise _invalid_selection_token()
    return value.strip()


def _coerce_optional_string_filter(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        raise _invalid_selection_token()
    text = str(value).strip()
    return text or None


def _coerce_optional_bool_filter(value: Any, field_name: str) -> Optional[bool]:
    """Coerce a tri-state boolean filter for the selection contract.

    None stays None (no-op filter). Real bools pass through. Strings/ints that
    look boolean ("true"/"1"/"false"/"0") are accepted so a JSON-decoded token
    round-trips cleanly; anything else is a malformed token.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
    raise _invalid_selection_token()


def _coerce_prompt_match_mode(value: Any) -> str:
    mode = _coerce_optional_string_filter(value, "promptMatchMode") or PROMPT_MATCH_MODE_EXACT
    mode = mode.lower()
    if mode not in VALID_PROMPT_MATCH_MODES:
        raise _invalid_selection_token()
    return mode


def _coerce_tag_mode(value: Any) -> str:
    mode = _coerce_optional_string_filter(value, "tagMode") or "and"
    mode = mode.lower()
    if mode not in {"and", "or"}:
        raise _invalid_selection_token()
    return mode


def _coerce_selection_id_list(value: Any, field_name: str, *, max_length: int) -> List[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid_selection_token()
    if len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"{field_name} exceeds max length of {max_length}")

    normalized: List[int] = []
    seen_ids: set[int] = set()
    for raw_id in value:
        if isinstance(raw_id, bool):
            raise _invalid_selection_token()
        try:
            image_id = int(raw_id)
        except (TypeError, ValueError):
            raise _invalid_selection_token()
        if image_id <= 0:
            raise _invalid_selection_token()
        if image_id in seen_ids:
            continue
        seen_ids.add(image_id)
        normalized.append(image_id)
    return normalized



def _sanitize_filter_value(value: str) -> str:
    """
    Sanitize a filter value to prevent potential injection or corruption.
    
    - Strips leading/trailing whitespace
    - Removes null bytes
    - Limits length to prevent abuse
    """
    if not value:
        return value
    # Remove null bytes and strip whitespace
    sanitized = value.replace('\x00', '').strip()
    # Limit length to reasonable maximum (1000 chars)
    if len(sanitized) > 1000:
        sanitized = sanitized[:1000]
    return sanitized


def _sanitize_filter_list(items: Optional[str]) -> Optional[List[str]]:
    """
    Parse and sanitize a comma-separated filter string into a list.
    
    Returns None if input is None or empty after sanitization.
    """
    if not items:
        return None
    # Split and sanitize each item
    parts = items.split(',')
    sanitized = [_sanitize_filter_value(p) for p in parts]
    # Filter out empty strings
    result = [p for p in sanitized if p]
    return result if result else None


def _sanitize_filter_values(items: Any) -> Optional[List[str]]:
    """Normalize string or iterable filter inputs into one sanitized string list."""
    if items is None:
        return None

    if isinstance(items, str):
        return _sanitize_filter_list(items)

    if isinstance(items, (list, tuple, set)):
        result: List[str] = []
        for item in items:
            sanitized = _sanitize_filter_value(str(item or ""))
            if sanitized:
                result.append(sanitized)
        return result or None

    sanitized = _sanitize_filter_value(str(items))
    return [sanitized] if sanitized else None
