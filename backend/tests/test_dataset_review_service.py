"""Router integration coverage for the Dataset Review typed issue queue."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from metadata_parser import PARSED_METADATA_VERSION


ALL_ISSUE_KINDS = [
    "file_missing",
    "image_unreadable",
    "empty_caption",
    "rating_conflict",
    "low_tag_confidence",
    "metadata_provenance_risk",
    "sidecar_metadata_dependency",
    "small_image",
    "low_aesthetic",
    "duplicate_group",
]


def _current_model_metadata(
    source_mode: str,
    match_type: str,
    confidence: str,
) -> Dict[str, object]:
    return {
        "_parsed": {
            "version": PARSED_METADATA_VERSION,
            "model_assets": {
                "checkpoint_candidates": [
                    {
                        "name": "model.safetensors",
                        "source_mode": source_mode,
                        "match_type": match_type,
                        "confidence": confidence,
                    }
                ],
            },
        },
    }


def _sidecar_fallback_metadata(
    evidence: List[Dict[str, object]],
) -> Dict[str, object]:
    return {
        "_parsed": {
            "version": PARSED_METADATA_VERSION,
            "sidecar_fallback": {
                "schema_version": 1,
                "evaluated": True,
                "evidence": evidence,
            },
        },
    }


def _set_stored_provenance(
    test_client,
    image_id: int,
    metadata: Dict[str, object] | str | None,
    ai_caption: str | None,
    nl_caption: str | None,
) -> None:
    metadata_json = (
        metadata
        if isinstance(metadata, str) or metadata is None
        else json.dumps(metadata)
    )
    with test_client.test_db.get_db() as connection:
        connection.execute(
            """
            UPDATE images
            SET metadata_json = ?, ai_caption = ?, nl_caption = ?
            WHERE id = ?
            """,
            (metadata_json, ai_caption, nl_caption, image_id),
        )


def _add_provenance_tag_rows(
    test_client,
    image_id: int,
    rows: List[tuple[str, float, str | None, str | None]],
) -> None:
    with test_client.test_db.get_db() as connection:
        connection.executemany(
            """
            INSERT INTO tags (image_id, tag, confidence, source, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (image_id, tag, confidence, source, category)
                for tag, confidence, source, category in rows
            ],
        )


def _add_image(
    test_client,
    path: Path,
    *,
    width: int,
    height: int,
    is_readable: bool,
    aesthetic_score: float | None,
) -> int:
    image_id = test_client.test_db.add_image(
        path=str(path),
        filename=path.name,
        width=width,
        height=height,
        is_readable=is_readable,
        read_error="decode failed" if not is_readable else None,
    )
    if aesthetic_score is not None:
        with test_client.test_db.get_db() as connection:
            connection.execute(
                "UPDATE images SET aesthetic_score = ? WHERE id = ?",
                (aesthetic_score, image_id),
            )
    return image_id


def _add_rating_tags(test_client, image_id: int, tags: List[str]) -> None:
    test_client.test_db.add_tags(
        image_id,
        [{"tag": tag, "confidence": 0.9} for tag in tags],
        content_fingerprint=f"review-rating-{image_id}",
    )


def _add_tag_rows(
    test_client,
    image_id: int,
    rows: List[tuple[str, float | None]],
) -> None:
    with test_client.test_db.get_db() as connection:
        connection.executemany(
            "INSERT INTO tags (image_id, tag, confidence) VALUES (?, ?, ?)",
            [(image_id, tag, confidence) for tag, confidence in rows],
        )


def _payload(
    image_ids: List[int],
    *,
    caption_states: List[Dict[str, object]] | None = None,
    issue_kinds: List[str] | None = None,
    include_persisted_duplicates: bool = False,
    minimum_dimension: int | None = None,
    minimum_aesthetic: float | None = None,
    logical_count: int | None = None,
    local_path_count: int = 0,
    cursor: str | None = None,
    limit: int = 50,
) -> Dict[str, object]:
    unique_ids = list(dict.fromkeys(image_ids))
    return {
        "schema_version": 1,
        "image_ids": image_ids,
        "caption_states": caption_states
        if caption_states is not None
        else [{"image_id": image_id, "has_content": True} for image_id in unique_ids],
        "logical_count": logical_count if logical_count is not None else len(unique_ids),
        "local_path_count": local_path_count,
        "minimum_dimension": minimum_dimension,
        "minimum_aesthetic": minimum_aesthetic,
        "include_persisted_duplicates": include_persisted_duplicates,
        "issue_kinds": issue_kinds if issue_kinds is not None else list(ALL_ISSUE_KINDS),
        "cursor": cursor,
        "limit": limit,
    }


def _write_duplicate_state(
    path: Path,
    groups: List[Dict[str, Any]],
    scanned_at: float,
) -> None:
    from services.duplicate_group_service import _RESULT_VERSION

    path.write_text(
        json.dumps(
            {
                # Bound to the live constant: this fixture stands for "a
                # persisted scan the loader still accepts", not one shape of it.
                "version": _RESULT_VERSION,
                "scanned_at": scanned_at,
                "threshold": 0.95,
                "summary": {
                    "embedded_count": 10,
                    "group_count": len(groups),
                    "redundant_count": sum(max(0, len(group["members"]) - 1) for group in groups),
                    "reclaimable_bytes": 200,
                    "threshold": 0.95,
                },
                "groups": groups,
            }
        ),
        encoding="utf-8",
    )


def _duplicate_member(image_id: int, path: Path, *, keep: bool) -> Dict[str, object]:
    return {
        "id": image_id,
        "path": str(path),
        "filename": path.name,
        "width": 512,
        "height": 512,
        "file_size": 100,
        "aesthetic_score": 5.0,
        "user_rating": 0,
        "suggested_keep": keep,
    }


def test_review_queue_requires_every_request_field_and_ignores_extra_fields(test_client, tmp_path):
    image_path = tmp_path / "required.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    payload = _payload([image_id], issue_kinds=["empty_caption"])

    for field in payload:
        incomplete = dict(payload)
        incomplete.pop(field)
        response = test_client.post("/api/dataset/review-queue", json=incomplete)
        assert response.status_code == 400, field

    payload["future_field"] = {"safe": True}
    response = test_client.post("/api/dataset/review-queue", json=payload)
    assert response.status_code == 200


@pytest.mark.parametrize(
    "image_ids,caption_states",
    [
        ([], []),
        ([0], [{"image_id": 0, "has_content": True}]),
        ([1, 1], [{"image_id": 1, "has_content": True}]),
        ([1], []),
        ([1], [{"image_id": 1, "has_content": True}, {"image_id": 1, "has_content": False}]),
        ([1], [{"image_id": 2, "has_content": True}]),
    ],
)
def test_review_queue_rejects_invalid_scope_and_caption_evidence(
    test_client,
    image_ids,
    caption_states,
):
    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(image_ids, caption_states=caption_states, issue_kinds=["empty_caption"]),
    )
    assert response.status_code == 400


def test_review_queue_rejects_empty_kinds_oversized_scope_and_weak_booleans(test_client):
    empty_kinds = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [1],
            caption_states=[{"image_id": 1, "has_content": True}],
            issue_kinds=[],
        ),
    )
    assert empty_kinds.status_code == 400

    oversized_ids = list(range(1, 20_002))
    oversized = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            oversized_ids,
            caption_states=[
                {"image_id": image_id, "has_content": True}
                for image_id in oversized_ids
            ],
            issue_kinds=["file_missing"],
        ),
    )
    assert oversized.status_code == 400
    assert "maximum is 20000" in str(oversized.json()["details"]).lower()

    weak_boolean = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [1],
            caption_states=[{"image_id": 1, "has_content": "false"}],
            issue_kinds=["empty_caption"],
        ),
    )
    assert weak_boolean.status_code == 400


def test_review_queue_returns_deterministic_typed_mixed_issues(test_client, tmp_path):
    missing_id = _add_image(
        test_client,
        tmp_path / "missing.png",
        width=1024,
        height=1024,
        is_readable=True,
        aesthetic_score=8.0,
    )
    unreadable_path = tmp_path / "unreadable.png"
    unreadable_path.write_bytes(b"broken")
    unreadable_id = _add_image(
        test_client,
        unreadable_path,
        width=1024,
        height=1024,
        is_readable=False,
        aesthetic_score=8.0,
    )
    review_path = tmp_path / "review.png"
    review_path.write_bytes(b"image")
    review_id = _add_image(
        test_client,
        review_path,
        width=128,
        height=256,
        is_readable=True,
        aesthetic_score=3.25,
    )
    caption_states = [
        {"image_id": missing_id, "has_content": True},
        {"image_id": unreadable_id, "has_content": True},
        {"image_id": review_id, "has_content": False},
    ]

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [review_id, missing_id, unreadable_id],
            caption_states=caption_states,
            issue_kinds=[
                "small_image",
                "file_missing",
                "low_aesthetic",
                "empty_caption",
                "image_unreadable",
            ],
            minimum_dimension=512,
            minimum_aesthetic=4.5,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert len(body["scope_fingerprint"]) == 64
    assert body["total"] == 5
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    assert [issue["kind"] for issue in body["issues"]] == [
        "file_missing",
        "image_unreadable",
        "empty_caption",
        "low_aesthetic",
        "small_image",
    ]
    assert len({issue["issue_id"] for issue in body["issues"]}) == 5
    for issue in body["issues"]:
        assert issue["title_en"] and issue["title_zh"]
        assert issue["detail_en"] and issue["detail_zh"]
        assert issue["subjects"]
        assert issue["evidence"]
        assert all(
            set(row) == {"label_en", "label_zh", "value_en", "value_zh"}
            for row in issue["evidence"]
        )
        assert issue["source_provider"] in {"database", "caption_states"}
        assert issue["evidence_status"] == "available"
        assert issue["action"]["kind"] == "open_image"
    missing = body["issues"][0]
    assert missing["action"]["availability"] == "not_available"
    assert next(issue for issue in body["issues"] if issue["kind"] == "low_aesthetic")["heuristic"] is True


def test_review_queue_cursor_has_no_gaps_or_repeats_and_rejects_changed_evidence(
    test_client,
    tmp_path,
):
    image_ids = []
    for index in range(5):
        path = tmp_path / f"caption-{index}.png"
        path.write_bytes(b"image")
        image_ids.append(
            _add_image(
                test_client,
                path,
                width=512,
                height=512,
                is_readable=True,
                aesthetic_score=None,
            )
        )
    caption_states = [{"image_id": image_id, "has_content": False} for image_id in image_ids]
    first_payload = _payload(
        image_ids,
        caption_states=caption_states,
        issue_kinds=["empty_caption"],
        limit=2,
    )

    first = test_client.post("/api/dataset/review-queue", json=first_payload)
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["has_more"] is True
    assert first_body["next_cursor"]

    cursor_parts = first_body["next_cursor"].split(".")
    encoded_payload = cursor_parts[0]
    padding = "=" * (-len(encoded_payload) % 4)
    decoded_payload = json.loads(
        base64.urlsafe_b64decode((encoded_payload + padding).encode("ascii")).decode("utf-8")
    )
    decoded_payload["last_key"] = [9, 9, "tampered-boundary"]
    tampered_payload = base64.urlsafe_b64encode(
        json.dumps(decoded_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    tampered_cursor = ".".join([tampered_payload, *cursor_parts[1:]])
    tampered = test_client.post(
        "/api/dataset/review-queue",
        json={**first_payload, "cursor": tampered_cursor},
    )
    assert tampered.status_code == 400
    assert "cursor" in tampered.json()["error"].lower()

    second_payload = {**first_payload, "cursor": first_body["next_cursor"]}
    second = test_client.post("/api/dataset/review-queue", json=second_payload)
    assert second.status_code == 200
    second_body = second.json()
    assert not ({row["issue_id"] for row in first_body["issues"]} & {row["issue_id"] for row in second_body["issues"]})

    third_payload = {**first_payload, "cursor": second_body["next_cursor"]}
    third = test_client.post("/api/dataset/review-queue", json=third_payload)
    assert third.status_code == 200
    all_ids = [
        row["issue_id"]
        for page in (first_body, second_body, third.json())
        for row in page["issues"]
    ]
    assert len(all_ids) == 5
    assert len(set(all_ids)) == 5

    changed_captions = [dict(row) for row in caption_states]
    changed_captions[-1]["has_content"] = True
    stale = test_client.post(
        "/api/dataset/review-queue",
        json={**second_payload, "caption_states": changed_captions},
    )
    assert stale.status_code == 409
    assert "evidence" in stale.json()["error"].lower()


def test_review_queue_rejects_malformed_cross_scope_and_changed_filter_cursors(
    test_client,
    tmp_path,
):
    image_ids = []
    for index in range(3):
        path = tmp_path / f"cursor-{index}.png"
        path.write_bytes(b"image")
        image_ids.append(
            _add_image(
                test_client,
                path,
                width=100,
                height=100,
                is_readable=True,
                aesthetic_score=None,
            )
        )
    base = _payload(
        image_ids,
        caption_states=[{"image_id": value, "has_content": False} for value in image_ids],
        issue_kinds=["empty_caption", "small_image"],
        minimum_dimension=200,
        limit=1,
    )
    first = test_client.post("/api/dataset/review-queue", json=base).json()

    malformed = test_client.post(
        "/api/dataset/review-queue",
        json={**base, "cursor": "not-a-review-cursor"},
    )
    assert malformed.status_code == 400
    assert "cursor" in malformed.json()["error"].lower()

    cross_scope = test_client.post(
        "/api/dataset/review-queue",
        json={
            **base,
            "image_ids": image_ids[:2],
            "caption_states": base["caption_states"][:2],
            "logical_count": 2,
            "cursor": first["next_cursor"],
        },
    )
    assert cross_scope.status_code == 409
    assert "scope" in cross_scope.json()["error"].lower()

    changed_filter = test_client.post(
        "/api/dataset/review-queue",
        json={**base, "minimum_dimension": 300, "cursor": first["next_cursor"]},
    )
    assert changed_filter.status_code == 409
    assert "filter" in changed_filter.json()["error"].lower()


def test_review_queue_ignores_one_rating_category(test_client, tmp_path):
    image_path = tmp_path / "single-rating.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _add_rating_tags(test_client, image_id, ["general"])

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["rating_conflict"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["issues"] == []
    providers = [
        row for row in body["provider_states"] if row["provider"] == "tag_integrity"
    ]
    assert len(providers) == 1
    assert providers[0]["status"] == "available"


def test_review_queue_reports_distinct_rating_categories(test_client, tmp_path):
    image_path = tmp_path / "rating-conflict.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _add_rating_tags(test_client, image_id, ["general", "sensitive"])

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["rating_conflict"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    issue = body["issues"][0]
    assert issue["issue_id"] == f"rating_conflict:{image_id}"
    assert issue["kind"] == "rating_conflict"
    assert issue["title_en"] and issue["title_zh"]
    assert issue["detail_en"] and issue["detail_zh"]
    assert issue["source_provider"] == "database"
    assert issue["heuristic"] is True
    assert issue["action"]["kind"] == "open_image"
    assert issue["action"]["availability"] == "available"
    assert issue["evidence"]
    assert all(
        row["label_en"] and row["label_zh"] and row["value_en"] and row["value_zh"]
        for row in issue["evidence"]
    )


def test_review_queue_deduplicates_rating_spelling_variants(test_client, tmp_path):
    image_path = tmp_path / "rating-spelling.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _add_rating_tags(test_client, image_id, ["general", "General", "general_"])

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["rating_conflict"]),
    )

    assert response.status_code == 200
    assert response.json()["issues"] == []


def test_review_queue_reports_low_current_tag_confidence_with_strict_boundaries(
    test_client,
    tmp_path,
):
    image_path = tmp_path / "low-tag-confidence.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _add_tag_rows(
        test_client,
        image_id,
        [
            ("zeta_tag", 0.49),
            ("Alpha Tag", 0.2),
            ("boundary", 0.5),
            ("zero", 0.0),
            ("missing", None),
            ("infinite", float("inf")),
        ],
    )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["low_tag_confidence"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    issue = body["issues"][0]
    assert issue["issue_id"] == f"low_tag_confidence:{image_id}"
    assert issue["kind"] == "low_tag_confidence"
    assert issue["severity"] == "medium"
    assert issue["source_provider"] == "database"
    assert issue["heuristic"] is True
    assert issue["action"]["availability"] == "available"
    assert issue["evidence"] == [
        {
            "label_en": "Low-confidence tags",
            "label_zh": "低置信标签",
            "value_en": "alpha tag (0.2000), zeta tag (0.4900)",
            "value_zh": "alpha tag (0.2000)、zeta tag (0.4900)",
        },
        {
            "label_en": "Low-confidence threshold",
            "label_zh": "低置信阈值",
            "value_en": "0.5000",
            "value_zh": "0.5000",
        },
    ]
    provider = next(
        row
        for row in body["provider_states"]
        if row["provider"] == "tag_integrity"
    )
    assert provider["status"] == "available"


def test_review_queue_ignores_unknown_and_non_low_tag_confidence(
    test_client,
    tmp_path,
):
    image_path = tmp_path / "known-tag-confidence.png"
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _add_tag_rows(
        test_client,
        image_id,
        [
            ("boundary", 0.5),
            ("high", 0.99),
            ("zero", 0.0),
            ("negative", -0.1),
            ("missing", None),
            ("infinite", float("inf")),
        ],
    )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["low_tag_confidence"]),
    )

    assert response.status_code == 200
    assert response.json()["issues"] == []


def test_review_queue_skips_tag_lookup_when_rating_conflicts_are_not_requested(
    test_client,
    tmp_path,
    monkeypatch,
):
    image_path = tmp_path / "no-rating-review.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )

    def forbidden(_image_ids):
        raise AssertionError("tag lookup was called")

    monkeypatch.setattr(test_client.test_db, "get_image_tags_map", forbidden)
    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["empty_caption"]),
    )

    assert response.status_code == 200
    providers = [
        row
        for row in response.json()["provider_states"]
        if row["provider"] == "tag_integrity"
    ]
    assert len(providers) == 1
    assert providers[0]["status"] == "not_requested"


def test_review_queue_reads_rating_tags_in_five_hundred_image_chunks(
    test_client,
    tmp_path,
    monkeypatch,
):
    image_ids = [
        _add_image(
            test_client,
            tmp_path / f"rating-chunk-{index}.png",
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        for index in range(501)
    ]
    _add_rating_tags(test_client, image_ids[-1], ["questionable", "explicit"])
    _add_tag_rows(test_client, image_ids[-1], [("soft_focus", 0.3)])
    original_get_image_tags_map = test_client.test_db.get_image_tags_map
    observed_chunks: List[List[int]] = []

    def recording_get_image_tags_map(chunk):
        observed_chunks.append(list(chunk))
        return original_get_image_tags_map(chunk)

    monkeypatch.setattr(
        test_client.test_db,
        "get_image_tags_map",
        recording_get_image_tags_map,
    )
    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            image_ids,
            issue_kinds=["rating_conflict", "low_tag_confidence"],
        ),
    )

    assert response.status_code == 200
    assert [len(chunk) for chunk in observed_chunks] == [500, 1]
    assert [issue["issue_id"] for issue in response.json()["issues"]] == [
        f"rating_conflict:{image_ids[-1]}",
        f"low_tag_confidence:{image_ids[-1]}",
    ]


def test_review_queue_shares_tag_lookup_with_metadata_provenance(
    test_client,
    tmp_path,
    monkeypatch,
):
    image_path = tmp_path / "shared-tag-provenance.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(
        test_client,
        image_id,
        _current_model_metadata("explicit_metadata", "explicit_metadata", "high"),
        None,
        None,
    )
    _add_rating_tags(test_client, image_id, ["general", "explicit"])
    original_get_image_tags_map = test_client.test_db.get_image_tags_map
    observed_chunks: List[List[int]] = []

    def recording_get_image_tags_map(chunk):
        observed_chunks.append(list(chunk))
        return original_get_image_tags_map(chunk)

    monkeypatch.setattr(
        test_client.test_db,
        "get_image_tags_map",
        recording_get_image_tags_map,
    )
    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [image_id],
            issue_kinds=["rating_conflict", "metadata_provenance_risk"],
        ),
    )

    assert response.status_code == 200
    assert observed_chunks == [[image_id]]
    assert [issue["kind"] for issue in response.json()["issues"]] == [
        "rating_conflict",
        "metadata_provenance_risk",
    ]


def test_review_queue_paginates_mixed_caption_and_rating_issues_without_gaps(
    test_client,
    tmp_path,
):
    paths = [tmp_path / "empty-caption.png", tmp_path / "mixed-rating.png"]
    for path in paths:
        path.write_bytes(b"image")
    image_ids = [
        _add_image(
            test_client,
            path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        for path in paths
    ]
    _add_rating_tags(test_client, image_ids[1], ["general", "explicit"])
    payload = _payload(
        image_ids,
        caption_states=[
            {"image_id": image_ids[0], "has_content": False},
            {"image_id": image_ids[1], "has_content": True},
        ],
        issue_kinds=["empty_caption", "rating_conflict"],
        limit=1,
    )

    issue_ids: List[str] = []
    cursor = None
    while True:
        response = test_client.post(
            "/api/dataset/review-queue",
            json={**payload, "cursor": cursor},
        )
        assert response.status_code == 200
        body = response.json()
        issue_ids.extend(issue["issue_id"] for issue in body["issues"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert issue_ids == [
        f"empty_caption:{image_ids[0]}",
        f"rating_conflict:{image_ids[1]}",
    ]
    assert len(set(issue_ids)) == 2


def test_review_queue_rejects_cursor_after_rating_tags_change(test_client, tmp_path):
    paths = [tmp_path / f"rating-cursor-{index}.png" for index in range(2)]
    for path in paths:
        path.write_bytes(b"image")
    image_ids = [
        _add_image(
            test_client,
            path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        for path in paths
    ]
    payload = _payload(
        image_ids,
        caption_states=[
            {"image_id": image_id, "has_content": False}
            for image_id in image_ids
        ],
        issue_kinds=["empty_caption", "rating_conflict"],
        limit=1,
    )
    first = test_client.post("/api/dataset/review-queue", json=payload)
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor

    _add_rating_tags(test_client, image_ids[-1], ["general", "sensitive"])
    stale = test_client.post(
        "/api/dataset/review-queue",
        json={**payload, "cursor": cursor},
    )

    assert stale.status_code == 409
    assert "evidence" in stale.json()["error"].lower()


def test_review_queue_rejects_cursor_after_tag_confidence_changes(
    test_client,
    tmp_path,
):
    paths = [tmp_path / f"tag-confidence-cursor-{index}.png" for index in range(2)]
    for path in paths:
        path.write_bytes(b"image")
    image_ids = [
        _add_image(
            test_client,
            path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        for path in paths
    ]
    for image_id in image_ids:
        _add_tag_rows(test_client, image_id, [("soft_focus", 0.3)])
    payload = _payload(
        image_ids,
        issue_kinds=["low_tag_confidence"],
        limit=1,
    )
    first = test_client.post("/api/dataset/review-queue", json=payload)
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor

    with test_client.test_db.get_db() as connection:
        connection.execute(
            "UPDATE tags SET confidence = ? WHERE image_id = ? AND tag = ?",
            (0.6, image_ids[-1], "soft_focus"),
        )
    stale = test_client.post(
        "/api/dataset/review-queue",
        json={**payload, "cursor": cursor},
    )

    assert stale.status_code == 409
    assert "evidence" in stale.json()["error"].lower()


@pytest.mark.parametrize("issue_kind", ["rating_conflict", "low_tag_confidence"])
def test_review_queue_propagates_tag_database_failures(
    test_client,
    tmp_path,
    monkeypatch,
    issue_kind,
):
    image_path = tmp_path / "rating-db-failure.png"
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )

    def fail_lookup(_image_ids):
        raise RuntimeError("tag database unavailable")

    monkeypatch.setattr(test_client.test_db, "get_image_tags_map", fail_lookup)
    with pytest.raises(RuntimeError, match="tag database unavailable"):
        test_client.post(
            "/api/dataset/review-queue",
            json=_payload([image_id], issue_kinds=[issue_kind]),
        )


def test_review_queue_propagates_database_failures(test_client, monkeypatch):
    def fail_lookup(_image_ids):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(test_client.test_db, "get_images_by_ids", fail_lookup)
    with pytest.raises(RuntimeError, match="database unavailable"):
        test_client.post(
            "/api/dataset/review-queue",
            json=_payload([1], caption_states=[{"image_id": 1, "has_content": True}]),
        )


def test_review_queue_discloses_unavailable_stored_threshold_evidence(test_client):
    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [999_999],
            caption_states=[{"image_id": 999_999, "has_content": True}],
            issue_kinds=["empty_caption", "small_image", "low_aesthetic"],
            minimum_dimension=512,
            minimum_aesthetic=4.5,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["issues"] == []
    providers = {row["provider"]: row for row in body["provider_states"]}
    assert providers["scope"]["status"] == "partial"
    assert providers["caption_integrity"]["status"] == "partial"
    assert providers["dimensions"]["status"] == "partial"
    assert "unavailable for 1 image" in providers["dimensions"]["reason_en"]
    assert providers["aesthetic_scores"]["status"] == "partial"
    assert "unavailable for 1 image" in providers["aesthetic_scores"]["reason_en"]


@pytest.mark.parametrize(
    "invalid_score",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_review_queue_marks_non_finite_stored_aesthetic_unavailable(
    test_client,
    tmp_path,
    invalid_score,
):
    image_path = tmp_path / "invalid-aesthetic.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=invalid_score,
    )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [image_id],
            issue_kinds=["low_aesthetic"],
            minimum_aesthetic=4.5,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["issues"] == []
    provider = next(
        row
        for row in body["provider_states"]
        if row["provider"] == "aesthetic_scores"
    )
    assert provider["status"] == "partial"
    assert "unavailable for 1 image" in provider["reason_en"]


def test_review_queue_marks_unselected_review_axes_not_requested(test_client, tmp_path):
    image_path = tmp_path / "provider-state.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [image_id],
            issue_kinds=["file_missing", "small_image", "low_aesthetic"],
            minimum_dimension=None,
            minimum_aesthetic=None,
        ),
    )

    assert response.status_code == 200
    providers = {row["provider"]: row for row in response.json()["provider_states"]}
    assert providers["scope"]["status"] == "available"
    assert providers["file_integrity"]["status"] == "available"
    assert providers["caption_integrity"]["status"] == "not_requested"
    assert providers["tag_integrity"]["status"] == "not_requested"
    assert providers["dimensions"]["status"] == "not_requested"
    assert providers["aesthetic_scores"]["status"] == "not_requested"
    assert providers["persisted_duplicates"]["status"] == "not_requested"


def test_metadata_provenance_reports_unknown_tag_sources_without_model_guessing(
    test_client,
    tmp_path,
):
    image_path = tmp_path / "unknown-provenance.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(
        test_client,
        image_id,
        _current_model_metadata("explicit_metadata", "explicit_metadata", "high"),
        None,
        None,
    )
    _add_provenance_tag_rows(
        test_client,
        image_id,
        [
            ("legacy_null", 0.9, None, None),
            ("legacy_blank", 0.9, "", "general"),
            ("unknown_source", 0.9, "unsupported_writer", "general"),
        ],
    )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["metadata_provenance_risk"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    issue = body["issues"][0]
    assert issue["issue_id"] == f"metadata_provenance_risk:{image_id}"
    assert issue["kind"] == "metadata_provenance_risk"
    assert issue["source_provider"] == "metadata_provenance"
    assert issue["heuristic"] is False
    evidence = {row["label_en"]: row["value_en"] for row in issue["evidence"]}
    assert evidence["Persisted tag source"] == "3 legacy/unknown row(s)"
    rendered = json.dumps(issue).lower()
    for unsupported_claim in ("wd14", "vlm", "translation", "sidecar"):
        assert unsupported_claim not in rendered
    provider = next(
        row
        for row in body["provider_states"]
        if row["provider"] == "metadata_provenance"
    )
    assert provider["status"] == "available"
    assert "sidecar" not in provider["reason_en"].lower()


def test_metadata_provenance_reports_unversioned_captions_without_writer_guessing(
    test_client,
    tmp_path,
):
    image_path = tmp_path / "unversioned-captions.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(
        test_client,
        image_id,
        _current_model_metadata("explicit_metadata", "explicit_metadata", "high"),
        "persisted composed caption",
        "Persisted natural-language caption.",
    )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["metadata_provenance_risk"]),
    )

    assert response.status_code == 200
    issue = response.json()["issues"][0]
    evidence = {row["label_en"]: row["value_en"] for row in issue["evidence"]}
    assert evidence["Unversioned caption fields"] == "ai_caption, nl_caption"
    rendered = json.dumps(issue).lower()
    for unsupported_claim in ("wd14", "vlm", "translation", "sidecar"):
        assert unsupported_claim not in rendered


def test_metadata_provenance_discloses_stale_and_weak_model_asset_evidence(
    test_client,
    tmp_path,
):
    cases: List[tuple[str, Dict[str, object] | None, str]] = [
        ("missing", None, "Missing"),
        (
            "old",
            {
                "_parsed": {
                    "version": 6,
                    "model_assets": _current_model_metadata(
                        "explicit_metadata",
                        "explicit_metadata",
                        "high",
                    )["_parsed"]["model_assets"],
                },
            },
            f"Parsed metadata version 6 is older than current version {PARSED_METADATA_VERSION}",
        ),
        (
            "malformed-assets",
            {"_parsed": {"version": PARSED_METADATA_VERSION, "model_assets": "broken"}},
            "Malformed",
        ),
        (
            "blank-candidate",
            {
                "_parsed": {
                    "version": PARSED_METADATA_VERSION,
                    "model_assets": {
                        "checkpoint_candidates": [
                            {
                                "name": " ",
                                "match_type": "explicit_metadata",
                                "confidence": "high",
                            }
                        ],
                    },
                },
            },
            "Malformed",
        ),
        (
            "missing-source-mode",
            {
                "_parsed": {
                    "version": PARSED_METADATA_VERSION,
                    "model_assets": {
                        "checkpoint_candidates": [
                            {
                                "name": "model.safetensors",
                                "match_type": "explicit_metadata",
                                "confidence": "high",
                            }
                        ],
                    },
                },
            },
            "Malformed",
        ),
        (
            "unsupported-source-mode",
            _current_model_metadata(
                "unsupported_source_mode",
                "explicit_metadata",
                "high",
            ),
            "Malformed",
        ),
        (
            "malformed-source-mode",
            {
                "_parsed": {
                    "version": PARSED_METADATA_VERSION,
                    "model_assets": {
                        "checkpoint_candidates": [
                            {
                                "name": "model.safetensors",
                                "source_mode": 7,
                                "match_type": "explicit_metadata",
                                "confidence": "high",
                            }
                        ],
                    },
                },
            },
            "Malformed",
        ),
        (
            "non-explicit",
            _current_model_metadata("explicit_metadata", "heuristic_name_scan", "high"),
            "Non-explicit candidate",
        ),
        (
            "weak-confidence",
            _current_model_metadata("explicit_metadata", "explicit_metadata", "medium"),
            "Below high confidence",
        ),
    ]
    image_ids: List[int] = []
    expected_by_id: Dict[int, str] = {}
    for name, metadata, expected in cases:
        image_path = tmp_path / f"{name}.png"
        image_path.write_bytes(b"image")
        image_id = _add_image(
            test_client,
            image_path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        _set_stored_provenance(test_client, image_id, metadata, None, None)
        image_ids.append(image_id)
        expected_by_id[image_id] = expected

    accepted_ids: List[int] = []
    for source_mode in (
        "webui_parameters",
        "forge_parameters",
        "reforge_parameters",
        "fooocus_comment",
        "easy_diffusion_text",
        "invokeai_metadata",
        "swarmui_parameters",
        "drawthings_xmp",
        "fast_path",
        "nai_usercomment",
        "nai_comment",
        "nai_description",
        "nai_software_tag",
        "explicit_metadata",
    ):
        accepted_path = tmp_path / f"accepted-{source_mode}.png"
        accepted_path.write_bytes(b"image")
        accepted_id = _add_image(
            test_client,
            accepted_path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        _set_stored_provenance(
            test_client,
            accepted_id,
            _current_model_metadata(
                source_mode,
                "explicit_metadata",
                "high",
            ),
            None,
            None,
        )
        image_ids.append(accepted_id)
        accepted_ids.append(accepted_id)

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(image_ids, issue_kinds=["metadata_provenance_risk"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(cases)
    issue_image_ids = {
        issue["subjects"][0]["image_id"] for issue in body["issues"]
    }
    assert not issue_image_ids.intersection(accepted_ids)
    for issue in body["issues"]:
        image_id = issue["subjects"][0]["image_id"]
        values = " | ".join(row["value_en"] for row in issue["evidence"])
        assert expected_by_id[image_id] in values


@pytest.mark.parametrize(
    ("source_mode", "match_type"),
    [
        ("global_candidate_fallback", "explicit_input"),
        ("workflow_widget_fallback", "workflow_widget_value"),
        ("global_graph_fallback", "explicit_input"),
    ],
    ids=["global-candidate", "workflow-widget", "global-graph"],
)
def test_metadata_provenance_discloses_fallback_source_modes(
    test_client,
    tmp_path,
    source_mode,
    match_type,
):
    image_path = tmp_path / f"{source_mode}.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(
        test_client,
        image_id,
        _current_model_metadata(source_mode, match_type, "high"),
        None,
        None,
    )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["metadata_provenance_risk"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    values = " | ".join(
        evidence["value_en"]
        for evidence in body["issues"][0]["evidence"]
    )
    assert f"Fallback source mode: {source_mode}" in values


def test_metadata_provenance_is_not_parsed_when_issue_kind_is_omitted(
    test_client,
    tmp_path,
):
    image_path = tmp_path / "not-requested.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(test_client, image_id, "{not-json", None, None)
    with test_client.test_db.get_db() as connection:
        connection.execute(
            """
            INSERT INTO tags (image_id, tag, confidence, source, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (image_id, "malformed-row", 0.9, 7, b"binary"),
        )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["empty_caption"]),
    )

    assert response.status_code == 200
    provider = next(
        row
        for row in response.json()["provider_states"]
        if row["provider"] == "metadata_provenance"
    )
    assert provider["status"] == "not_requested"


def test_metadata_provenance_change_invalidates_cursor(test_client, tmp_path):
    image_ids: List[int] = []
    for index in range(2):
        image_path = tmp_path / f"provenance-cursor-{index}.png"
        image_path.write_bytes(b"image")
        image_id = _add_image(
            test_client,
            image_path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        _set_stored_provenance(test_client, image_id, None, None, None)
        image_ids.append(image_id)
    payload = _payload(
        image_ids,
        issue_kinds=["metadata_provenance_risk"],
        limit=1,
    )
    first = test_client.post("/api/dataset/review-queue", json=payload)
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor

    _set_stored_provenance(
        test_client,
        image_ids[-1],
        _current_model_metadata("explicit_metadata", "explicit_metadata", "high"),
        None,
        None,
    )
    stale = test_client.post(
        "/api/dataset/review-queue",
        json={**payload, "cursor": cursor},
    )

    assert stale.status_code == 409
    assert "evidence" in stale.json()["error"].lower()


@pytest.mark.parametrize(
    "changed_field",
    ["revision", "content_fingerprint"],
)
def test_clean_current_wd14_writer_change_invalidates_cursor(
    test_client,
    tmp_path,
    changed_field,
):
    image_ids: List[int] = []
    for index in range(2):
        image_path = tmp_path / f"clean-wd14-cursor-{index}.png"
        image_path.write_bytes(b"image")
        image_id = _add_image(
            test_client,
            image_path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        _set_stored_provenance(
            test_client,
            image_id,
            _current_model_metadata("explicit_metadata", "explicit_metadata", "high"),
            None,
            None,
        )
        test_client.test_db.add_tags_batch(
            [
                {
                    "image_id": image_id,
                    "tags": [{"tag": f"low-confidence-{index}", "confidence": 0.4}],
                    "content_fingerprint": str(index + 1) * 64,
                    "writer_provenance": {
                        "writer_family": "wd14",
                        "provider": "huggingface",
                        "model": "SmilingWolf/wd-swinv2-tagger-v3",
                        "revision": f"sha256:{'a' * 64}",
                        "runtime_provider": "CPUExecutionProvider",
                    },
                }
            ],
            default_source="tagger",
            replace_scope="pipeline",
        )
        image_ids.append(image_id)

    payload = _payload(
        image_ids,
        issue_kinds=["low_tag_confidence", "metadata_provenance_risk"],
        limit=1,
    )
    first = test_client.post("/api/dataset/review-queue", json=payload)
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 2
    assert all(issue["kind"] != "metadata_provenance_risk" for issue in body["issues"])
    cursor = body["next_cursor"]
    assert cursor

    changed_image_id = image_ids[-1]
    with test_client.test_db.get_db() as connection:
        if changed_field == "revision":
            connection.execute(
                "UPDATE tag_writer_provenance SET revision = ? WHERE image_id = ?",
                (f"sha256:{'b' * 64}", changed_image_id),
            )
        else:
            changed_fingerprint = "f" * 64
            connection.execute(
                "UPDATE images SET content_fingerprint = ? WHERE id = ?",
                (changed_fingerprint, changed_image_id),
            )
            connection.execute(
                """
                UPDATE tag_writer_provenance
                SET content_fingerprint = ?
                WHERE image_id = ?
                """,
                (changed_fingerprint, changed_image_id),
            )

    stale = test_client.post(
        "/api/dataset/review-queue",
        json={**payload, "cursor": cursor},
    )

    assert stale.status_code == 409
    assert "evidence" in stale.json()["error"].lower()


def test_metadata_provenance_rejects_malformed_json_and_tag_rows(
    test_client,
    tmp_path,
):
    json_path = tmp_path / "malformed-json.png"
    json_path.write_bytes(b"image")
    json_id = _add_image(
        test_client,
        json_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(test_client, json_id, "{not-json", None, None)
    with pytest.raises(ValueError, match="metadata_json"):
        test_client.post(
            "/api/dataset/review-queue",
            json=_payload([json_id], issue_kinds=["metadata_provenance_risk"]),
        )

    tag_path = tmp_path / "malformed-tag.png"
    tag_path.write_bytes(b"image")
    tag_id = _add_image(
        test_client,
        tag_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(
        test_client,
        tag_id,
        _current_model_metadata("explicit_metadata", "explicit_metadata", "high"),
        None,
        None,
    )
    with test_client.test_db.get_db() as connection:
        connection.execute(
            """
            INSERT INTO tags (image_id, tag, confidence, source, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tag_id, "malformed-row", 0.9, 7, "general"),
        )
    with pytest.raises(ValueError, match="tag row"):
        test_client.post(
            "/api/dataset/review-queue",
            json=_payload([tag_id], issue_kinds=["metadata_provenance_risk"]),
        )


def test_metadata_provenance_propagates_tag_database_failure(
    test_client,
    tmp_path,
    monkeypatch,
):
    image_path = tmp_path / "provenance-db-failure.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )

    def fail_lookup(_image_ids):
        raise RuntimeError("metadata provenance database unavailable")

    monkeypatch.setattr(test_client.test_db, "get_image_tags_map", fail_lookup)
    with pytest.raises(RuntimeError, match="metadata provenance database unavailable"):
        test_client.post(
            "/api/dataset/review-queue",
            json=_payload([image_id], issue_kinds=["metadata_provenance_risk"]),
        )


def test_sidecar_metadata_dependency_reports_only_persisted_extraction_evidence(
    test_client,
    tmp_path,
):
    image_path = tmp_path / "sidecar-dependent.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=1024,
        height=1024,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(
        test_client,
        image_id,
        _sidecar_fallback_metadata(
            [
                {
                    "carrier": "json",
                    "basename": "sidecar-dependent.json",
                    "method": "sidecar_fallback",
                    "confidence": "high",
                    "parser_version": PARSED_METADATA_VERSION,
                    "fields": ["prompt", "checkpoint", "loras"],
                }
            ]
        ),
        None,
        None,
    )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["sidecar_metadata_dependency"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    issue = body["issues"][0]
    assert issue["issue_id"] == f"sidecar_metadata_dependency:{image_id}"
    assert issue["kind"] == "sidecar_metadata_dependency"
    assert issue["source_provider"] == "metadata_provenance"
    assert issue["heuristic"] is False
    evidence = {row["label_en"]: row["value_en"] for row in issue["evidence"]}
    assert evidence == {
        "Sidecar carrier": "JSON",
        "Sidecar file": "sidecar-dependent.json",
        "Affected fields": "prompt, checkpoint, loras",
        "Extraction method": "sidecar_fallback",
        "Confidence": "high",
        "Parser version": str(PARSED_METADATA_VERSION),
    }
    assert issue["action"]["availability"] == "available"
    provider = next(
        row
        for row in body["provider_states"]
        if row["provider"] == "metadata_provenance"
    )
    assert provider["status"] == "available"
    assert "persisted sidecar fallback evidence" in provider["reason_en"].lower()


def test_sidecar_metadata_dependency_marks_legacy_records_partial_without_guessing(
    test_client,
    tmp_path,
):
    legacy_path = tmp_path / "legacy.png"
    evaluated_path = tmp_path / "evaluated.png"
    legacy_path.write_bytes(b"image")
    evaluated_path.write_bytes(b"image")
    legacy_id = _add_image(
        test_client,
        legacy_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    evaluated_id = _add_image(
        test_client,
        evaluated_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(
        test_client,
        legacy_id,
        {"_parsed": {"version": PARSED_METADATA_VERSION}},
        None,
        None,
    )
    _set_stored_provenance(
        test_client,
        evaluated_id,
        _sidecar_fallback_metadata([]),
        None,
        None,
    )

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [legacy_id, evaluated_id],
            issue_kinds=["sidecar_metadata_dependency"],
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    provider = next(
        row
        for row in body["provider_states"]
        if row["provider"] == "metadata_provenance"
    )
    assert provider["status"] == "partial"
    assert "1" in provider["reason_en"]
    assert "unevaluated" in provider["reason_en"].lower()
    rendered = json.dumps(body).lower()
    assert "embedded" not in rendered
    assert "clean" not in rendered


@pytest.mark.parametrize(
    "evidence",
    [
        {
            "carrier": "yaml",
            "basename": "sample.yaml",
            "method": "sidecar_fallback",
            "confidence": "high",
            "parser_version": PARSED_METADATA_VERSION,
            "fields": ["prompt"],
        },
        {
            "carrier": "txt",
            "basename": "C:/private/sample.txt",
            "method": "sidecar_fallback",
            "confidence": "high",
            "parser_version": PARSED_METADATA_VERSION,
            "fields": ["prompt"],
        },
        {
            "carrier": "json",
            "basename": "sample.json",
            "method": "sidecar_fallback",
            "confidence": "high",
            "parser_version": PARSED_METADATA_VERSION,
            "fields": ["unsupported_field"],
        },
        {
            "carrier": "txt",
            "basename": "C:private.txt",
            "method": "sidecar_fallback",
            "confidence": "high",
            "parser_version": PARSED_METADATA_VERSION,
            "fields": ["prompt"],
        },
        {
            "carrier": "txt",
            "basename": "sample.txt:stream.txt",
            "method": "sidecar_fallback",
            "confidence": "high",
            "parser_version": PARSED_METADATA_VERSION,
            "fields": ["prompt"],
        },
        {
            "carrier": "txt",
            "basename": "sample\x00.txt",
            "method": "sidecar_fallback",
            "confidence": "high",
            "parser_version": PARSED_METADATA_VERSION,
            "fields": ["prompt"],
        },
        {
            "carrier": "txt",
            "basename": "sample?.txt",
            "method": "sidecar_fallback",
            "confidence": "high",
            "parser_version": PARSED_METADATA_VERSION,
            "fields": ["prompt"],
        },
        {
            "carrier": "txt",
            "basename": "CON.txt",
            "method": "sidecar_fallback",
            "confidence": "high",
            "parser_version": PARSED_METADATA_VERSION,
            "fields": ["prompt"],
        },
        {
            "carrier": "txt",
            "basename": "sample..txt.",
            "method": "sidecar_fallback",
            "confidence": "high",
            "parser_version": PARSED_METADATA_VERSION,
            "fields": ["prompt"],
        },
    ],
    ids=[
        "unsupported-carrier",
        "absolute-path",
        "unsupported-field",
        "drive-relative-path",
        "ntfs-ads",
        "nul-character",
        "windows-invalid-character",
        "windows-device-name",
        "windows-trailing-dot",
    ],
)
def test_sidecar_metadata_dependency_rejects_malformed_persisted_evidence(
    test_client,
    tmp_path,
    evidence,
):
    image_path = tmp_path / "malformed-sidecar-evidence.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(
        test_client,
        image_id,
        _sidecar_fallback_metadata([evidence]),
        None,
        None,
    )

    with pytest.raises(ValueError, match="sidecar fallback"):
        test_client.post(
            "/api/dataset/review-queue",
            json=_payload(
                [image_id],
                issue_kinds=["sidecar_metadata_dependency"],
            ),
        )


def test_sidecar_metadata_dependency_is_not_parsed_when_filter_is_omitted(
    test_client,
    tmp_path,
):
    image_path = tmp_path / "unrequested-sidecar.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    _set_stored_provenance(test_client, image_id, "{not-json", None, None)

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload([image_id], issue_kinds=["empty_caption"]),
    )

    assert response.status_code == 200
    provider = next(
        row
        for row in response.json()["provider_states"]
        if row["provider"] == "metadata_provenance"
    )
    assert provider["status"] == "not_requested"


def test_sidecar_metadata_dependency_change_invalidates_cursor(
    test_client,
    tmp_path,
):
    image_ids: List[int] = []
    for index in range(2):
        image_path = tmp_path / f"sidecar-cursor-{index}.png"
        image_path.write_bytes(b"image")
        image_id = _add_image(
            test_client,
            image_path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        _set_stored_provenance(
            test_client,
            image_id,
            _sidecar_fallback_metadata(
                [
                    {
                        "carrier": "txt",
                        "basename": f"sidecar-cursor-{index}.txt",
                        "method": "sidecar_fallback",
                        "confidence": "high",
                        "parser_version": PARSED_METADATA_VERSION,
                        "fields": ["prompt"],
                    }
                ]
            ),
            None,
            None,
        )
        image_ids.append(image_id)
    payload = _payload(
        image_ids,
        issue_kinds=["sidecar_metadata_dependency"],
        limit=1,
    )
    first = test_client.post("/api/dataset/review-queue", json=payload)
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor

    _set_stored_provenance(
        test_client,
        image_ids[-1],
        _sidecar_fallback_metadata(
            [
                {
                    "carrier": "txt",
                    "basename": "sidecar-cursor-1.txt",
                    "method": "sidecar_fallback",
                    "confidence": "high",
                    "parser_version": PARSED_METADATA_VERSION,
                    "fields": ["prompt", "checkpoint"],
                }
            ]
        ),
        None,
        None,
    )
    stale = test_client.post(
        "/api/dataset/review-queue",
        json={**payload, "cursor": cursor},
    )

    assert stale.status_code == 409
    assert "evidence" in stale.json()["error"].lower()


def test_review_queue_discloses_missing_and_corrupt_duplicate_state(
    test_client,
    tmp_path,
    monkeypatch,
):
    from services import duplicate_group_service

    image_path = tmp_path / "dup-source.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    state_path = tmp_path / "duplicate-groups.json"
    monkeypatch.setattr(duplicate_group_service, "_state_path", lambda: state_path)
    payload = _payload(
        [image_id],
        issue_kinds=["duplicate_group"],
        include_persisted_duplicates=True,
    )

    missing = test_client.post("/api/dataset/review-queue", json=payload)
    assert missing.status_code == 200
    missing_state = next(row for row in missing.json()["provider_states"] if row["provider"] == "persisted_duplicates")
    assert missing_state["status"] == "not_available"
    assert "not been completed" in missing_state["reason_en"].lower()

    _write_duplicate_state(
        state_path,
        [
            {
                "group_id": 0,
                "similarity": "0.98",
                "members": [
                    _duplicate_member(image_id, image_path, keep=True),
                    _duplicate_member(image_id + 1, tmp_path / "other.png", keep=False),
                ],
            }
        ],
        1234.5,
    )
    corrupt = test_client.post("/api/dataset/review-queue", json=payload)
    assert corrupt.status_code == 200
    corrupt_state = next(row for row in corrupt.json()["provider_states"] if row["provider"] == "persisted_duplicates")
    assert corrupt_state["status"] == "not_available"
    assert "invalid" in corrupt_state["reason_en"].lower()


@pytest.mark.parametrize(
    "corruption",
    ["duplicate_member", "duplicate_group_id", "repeated_member_set"],
)
def test_review_queue_rejects_semantically_duplicate_group_state(
    test_client,
    tmp_path,
    monkeypatch,
    corruption,
):
    from services import duplicate_group_service

    paths = [tmp_path / f"semantic-duplicate-{index}.png" for index in range(4)]
    for path in paths:
        path.write_bytes(b"image")
    image_ids = [
        _add_image(
            test_client,
            path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        for path in paths
    ]
    if corruption == "duplicate_member":
        groups = [{
            "group_id": 0,
            "similarity": 0.98,
            "members": [
                _duplicate_member(image_ids[0], paths[0], keep=True),
                _duplicate_member(image_ids[0], paths[0], keep=False),
            ],
        }]
    elif corruption == "duplicate_group_id":
        groups = [
            {
                "group_id": 0,
                "similarity": 0.98,
                "members": [
                    _duplicate_member(image_ids[0], paths[0], keep=True),
                    _duplicate_member(image_ids[1], paths[1], keep=False),
                ],
            },
            {
                "group_id": 0,
                "similarity": 0.97,
                "members": [
                    _duplicate_member(image_ids[2], paths[2], keep=True),
                    _duplicate_member(image_ids[3], paths[3], keep=False),
                ],
            },
        ]
    elif corruption == "repeated_member_set":
        groups = [
            {
                "group_id": 0,
                "similarity": 0.98,
                "members": [
                    _duplicate_member(image_ids[0], paths[0], keep=True),
                    _duplicate_member(image_ids[1], paths[1], keep=False),
                ],
            },
            {
                "group_id": 1,
                "similarity": 0.97,
                "members": [
                    _duplicate_member(image_ids[1], paths[1], keep=True),
                    _duplicate_member(image_ids[0], paths[0], keep=False),
                ],
            },
        ]
    else:
        raise AssertionError(f"Unhandled corruption case: {corruption}")

    state_path = tmp_path / "duplicate-groups.json"
    _write_duplicate_state(state_path, groups, 1234.5)
    monkeypatch.setattr(duplicate_group_service, "_state_path", lambda: state_path)
    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            image_ids,
            issue_kinds=["duplicate_group"],
            include_persisted_duplicates=True,
            limit=1,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["issues"] == []
    assert body["total"] == 0
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    provider = next(
        row
        for row in body["provider_states"]
        if row["provider"] == "persisted_duplicates"
    )
    assert provider["status"] == "not_available"
    assert "invalid" in provider["reason_en"].lower()


@pytest.mark.parametrize(
    "similarity",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_review_queue_rejects_non_finite_duplicate_similarity(
    test_client,
    tmp_path,
    monkeypatch,
    similarity,
):
    from services import duplicate_group_service

    paths = [tmp_path / f"non-finite-{index}.png" for index in range(2)]
    for path in paths:
        path.write_bytes(b"image")
    image_ids = [
        _add_image(
            test_client,
            path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        for path in paths
    ]
    state_path = tmp_path / "duplicate-groups.json"
    _write_duplicate_state(
        state_path,
        [{
            "group_id": 0,
            "similarity": similarity,
            "members": [
                _duplicate_member(image_ids[0], paths[0], keep=True),
                _duplicate_member(image_ids[1], paths[1], keep=False),
            ],
        }],
        1234.5,
    )
    monkeypatch.setattr(duplicate_group_service, "_state_path", lambda: state_path)

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            image_ids,
            issue_kinds=["duplicate_group"],
            include_persisted_duplicates=True,
            limit=1,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["issues"] == []
    assert body["total"] == 0
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    provider = next(
        row
        for row in body["provider_states"]
        if row["provider"] == "persisted_duplicates"
    )
    assert provider["status"] == "not_available"
    assert "invalid" in provider["reason_en"].lower()


@pytest.mark.parametrize("scanned_at", [float("nan"), float("inf"), 1e100])
def test_review_queue_rejects_invalid_duplicate_scan_timestamps_without_failing_request(
    test_client,
    tmp_path,
    monkeypatch,
    scanned_at,
):
    from services import duplicate_group_service

    image_path = tmp_path / "timestamp-source.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    state_path = tmp_path / "duplicate-groups.json"
    _write_duplicate_state(state_path, [], scanned_at)
    monkeypatch.setattr(duplicate_group_service, "_state_path", lambda: state_path)

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [image_id],
            issue_kinds=["duplicate_group"],
            include_persisted_duplicates=True,
        ),
    )

    assert response.status_code == 200
    provider = next(
        row
        for row in response.json()["provider_states"]
        if row["provider"] == "persisted_duplicates"
    )
    assert provider["status"] == "not_available"
    assert "invalid" in provider["reason_en"].lower()


def test_review_queue_filters_persisted_duplicate_members_to_current_scope(
    test_client,
    tmp_path,
    monkeypatch,
):
    from services import duplicate_group_service

    paths = [tmp_path / f"dup-{index}.png" for index in range(5)]
    for path in paths:
        path.write_bytes(b"image")
    image_ids = [
        _add_image(
            test_client,
            path,
            width=512,
            height=512,
            is_readable=True,
            aesthetic_score=None,
        )
        for path in paths
    ]
    state_path = tmp_path / "duplicate-groups.json"
    _write_duplicate_state(
        state_path,
        [
            {
                "group_id": 0,
                "similarity": 0.9812,
                "members": [
                    _duplicate_member(image_ids[0], paths[0], keep=True),
                    _duplicate_member(image_ids[1], paths[1], keep=False),
                    _duplicate_member(image_ids[3], paths[3], keep=False),
                ],
            },
            {
                "group_id": 1,
                "similarity": 0.99,
                "members": [
                    _duplicate_member(image_ids[2], paths[2], keep=True),
                    _duplicate_member(image_ids[4], paths[4], keep=False),
                ],
            },
        ],
        1234.5,
    )
    monkeypatch.setattr(duplicate_group_service, "_state_path", lambda: state_path)

    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            image_ids[:3],
            issue_kinds=["duplicate_group"],
            include_persisted_duplicates=True,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    issue = body["issues"][0]
    assert issue["kind"] == "duplicate_group"
    assert [subject["image_id"] for subject in issue["subjects"]] == image_ids[:2]
    assert issue["evidence_status"] == "partial"
    assert issue["heuristic"] is True
    assert issue["action"]["availability"] == "available"
    state = next(row for row in body["provider_states"] if row["provider"] == "persisted_duplicates")
    assert state["status"] == "partial"
    assert state["observed_at"] is not None
    assert "scope fingerprint" in state["reason_en"].lower()


def test_review_queue_discloses_unsupported_logical_items_and_calls_no_heavy_provider(
    test_client,
    tmp_path,
    monkeypatch,
):
    from services import character_purity_service, dataset_audit_service, tag_score_service
    import similarity

    def forbidden(*_args, **_kwargs):
        raise AssertionError("heavy provider was called")

    monkeypatch.setattr(dataset_audit_service, "audit_dataset", forbidden)
    monkeypatch.setattr(character_purity_service, "start_character_purity", forbidden)
    monkeypatch.setattr(tag_score_service, "get_stats", forbidden)
    monkeypatch.setattr(similarity, "ensure_clip_model_ready", forbidden)

    image_path = tmp_path / "partial.png"
    image_path.write_bytes(b"image")
    image_id = _add_image(
        test_client,
        image_path,
        width=512,
        height=512,
        is_readable=True,
        aesthetic_score=None,
    )
    response = test_client.post(
        "/api/dataset/review-queue",
        json=_payload(
            [image_id],
            issue_kinds=["empty_caption"],
            logical_count=3,
            local_path_count=1,
        ),
    )

    assert response.status_code == 200
    providers = {row["provider"]: row for row in response.json()["provider_states"]}
    assert providers["scope"]["status"] == "partial"
    assert providers["caption_integrity"]["status"] == "partial"
    assert providers["persisted_duplicates"]["status"] == "not_requested"
    assert "1 local-path" in providers["scope"]["reason_en"]
