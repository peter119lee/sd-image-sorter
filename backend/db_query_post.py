"""Post-filter scanners and exact prompt/LoRA matchers (split from db_query.py).

``_fetch_post_filtered_page``, ``_fetch_post_filtered_ids``,
``_matches_exact_post_filters``, and ``_post_filter_results`` moved here
verbatim in the 2026-07 db_query split. The ``_fetch_*`` scanners take a
live connection in from the ``db_images_*`` readers; nothing here opens one.
Consumers keep importing through the ``db_query`` facade; do not import this
module directly from feature code.

Imports only from db_core / db_helpers / stdlib to avoid an import cycle
with the ``database`` facade.
"""
from typing import Optional, List, Dict, Any

from db_core import (
    PROMPT_MATCH_MODE_EXACT,
    PROMPT_MATCH_MODE_CONTAINS,
)
from db_helpers import (
    normalize_prompt_token,
    normalize_prompt_match_mode,
    normalize_lora_name,
    extract_prompt_tokens,
    extract_lora_names,
    _rows_to_dicts,
)


def _fetch_post_filtered_page(
    conn,
    base_query: str,
    base_params: List[Any],
    order_clause: str,
    prompt_terms: Optional[List[str]],
    loras: Optional[List[str]],
    *,
    prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
    post_offset: int = 0,
    limit: int,
    fetch_size: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch a post-filtered page by scanning SQL rows in deterministic chunks."""
    cursor = conn.cursor()
    if limit < 0:
        raise ValueError("limit must be >= 0")

    normalized_offset = max(0, int(post_offset))
    normalized_limit = max(0, int(limit))
    target_count = None if normalized_limit == 0 else normalized_offset + normalized_limit

    effective_fetch_size = int(fetch_size or 0)
    if effective_fetch_size <= 0:
        baseline = normalized_limit if normalized_limit > 0 else 50
        effective_fetch_size = max(baseline * 2, 50)

    raw_offset = 0
    collected: List[Dict[str, Any]] = []

    while True:
        query = f"{base_query} ORDER BY {order_clause} LIMIT ? OFFSET ?"
        params = list(base_params) + [effective_fetch_size, raw_offset]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        if not rows:
            break

        batch = _post_filter_results(
            _rows_to_dicts(rows),
            prompt_terms,
            loras,
            0,
            0,
            prompt_match_mode=prompt_match_mode,
        )
        collected.extend(batch)
        if target_count is not None and len(collected) >= target_count:
            break

        if len(rows) < effective_fetch_size:
            break
        raw_offset += effective_fetch_size

    if normalized_limit == 0:
        return collected[normalized_offset:]
    return collected[normalized_offset:normalized_offset + normalized_limit]


def _fetch_post_filtered_ids(
    conn,
    base_query: str,
    base_params: List[Any],
    order_clause: str,
    prompt_terms: Optional[List[str]],
    loras: Optional[List[str]],
    *,
    prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
    post_offset: int = 0,
    limit: Optional[int] = None,
    fetch_size: int = 5000,
) -> List[int]:
    """Fetch exact post-filtered IDs without materializing full image rows."""
    normalized_offset = max(0, int(post_offset or 0))
    normalized_limit = None if limit is None else max(0, int(limit))
    if normalized_limit == 0:
        return []

    target_count = None if normalized_limit is None else normalized_offset + normalized_limit
    effective_fetch_size = max(1, int(fetch_size or 5000))
    normalized_prompt_terms = [normalize_prompt_token(t) for t in (prompt_terms or [])]
    normalized_loras = [normalize_lora_name(l) for l in (loras or [])]

    cursor = conn.cursor()
    raw_offset = 0
    matched_ids: List[int] = []

    while True:
        query = f"{base_query} ORDER BY {order_clause} LIMIT ? OFFSET ?"
        params = list(base_params) + [effective_fetch_size, raw_offset]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        if not rows:
            break

        for row in rows:
            # base_query must select i.sidecar_caption alongside i.prompt; a
            # missing column raises here rather than silently dropping every
            # caption-only row from a "move all matching" set.
            if _matches_exact_post_filters(
                row["prompt"],
                row["loras"],
                normalized_prompt_terms,
                normalized_loras,
                prompt_match_mode=prompt_match_mode,
                sidecar_caption=row["sidecar_caption"],
            ):
                matched_ids.append(int(row["id"]))
                if target_count is not None and len(matched_ids) >= target_count:
                    return matched_ids[normalized_offset:]

        if len(rows) < effective_fetch_size:
            break
        raw_offset += effective_fetch_size

    if normalized_limit is None:
        return matched_ids[normalized_offset:]
    return matched_ids[normalized_offset:normalized_offset + normalized_limit]


def _matches_exact_post_filters(
    prompt: Optional[str],
    lora_text: Optional[str],
    normalized_prompt_terms: List[str],
    normalized_loras: List[str],
    *,
    prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
    sidecar_caption: Optional[str] = None,
) -> bool:
    """Apply the exact prompt/LORA matching semantics used by post-filter paths.

    This is the half of the prompt-terms filter that actually decides in
    ``exact`` mode — the SQL clause built by ``_apply_prompt_terms_filter`` is
    only a broad pre-filter. It therefore has to read the same two text columns
    that filter does: a term is satisfied by the ``prompt`` OR by the
    ``sidecar_caption`` (migration 042). Leaving the caption out here would
    reject every row whose text a rescan relocated, no matter what the SQL
    matched, and the batch-move snapshot runs through this function.

    ``sidecar_caption`` is keyword-only with a ``None`` default so a caller
    that legitimately has no caption to offer reads as prompt-only rather than
    accidentally positional.
    """
    if normalized_prompt_terms:
        if normalize_prompt_match_mode(prompt_match_mode) == PROMPT_MATCH_MODE_CONTAINS:
            searchable = (
                normalize_prompt_token(prompt or ""),
                normalize_prompt_token(sidecar_caption or ""),
            )
            if not all(
                any(term in text for text in searchable)
                for term in normalized_prompt_terms
            ):
                return False
        else:
            image_tokens = extract_prompt_tokens(prompt or "")
            image_tokens |= extract_prompt_tokens(sidecar_caption or "")
            if not all(term in image_tokens for term in normalized_prompt_terms):
                return False

    if normalized_loras:
        image_loras = extract_lora_names(lora_text or "", prompt or "")
        if not any(lora in image_loras for lora in normalized_loras):
            return False

    return True


def _post_filter_results(results: List[Dict[str, Any]],
                         prompt_terms: Optional[List[str]],
                         loras: Optional[List[str]],
                         offset: int,
                         limit: int,
                         *,
                         prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT) -> List[Dict[str, Any]]:
    """Apply in-memory post-filtering for exact matching."""
    if not prompt_terms and not loras:
        return results[offset:offset + limit] if limit else results[offset:]

    filtered_results = []
    normalized_prompt_terms = [normalize_prompt_token(t) for t in (prompt_terms or [])]
    normalized_loras = [normalize_lora_name(l) for l in (loras or [])]
    early_stop_count = offset + limit if limit else None

    for img in results:
        if _matches_exact_post_filters(
            img.get("prompt"),
            img.get("loras"),
            normalized_prompt_terms,
            normalized_loras,
            prompt_match_mode=prompt_match_mode,
            sidecar_caption=img.get("sidecar_caption"),
        ):
            filtered_results.append(img)

        if early_stop_count and len(filtered_results) >= early_stop_count:
            break

    return filtered_results[offset:offset + limit] if limit else filtered_results[offset:]
