"""
Contract tests for derived-state ownership and invalidation.

These tests intentionally pin today's derived writer surface before the
implementation is centralized further. New feature-local writers should fail
here first instead of silently advancing `content_fingerprint` from another
entry point.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pytest

# Add parent directory to path for imports when this file is run directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import services.derived_state_service as derived_state_service
from services.derived_state_service import (
    write_image_aesthetic_score,
    write_artist_prediction,
    write_artist_predictions,
    write_image_content_fingerprint,
    write_image_embeddings,
)


# The two length bounds are only there to stop the scanner running away across
# unrelated code; they are not a statement about the statements. They used to be
# 900/500, which the scan upsert in db_images_write._upsert_image_record had
# almost outgrown: adding one more column to its SET list (migration 043's
# `sidecar_fingerprint`) pushed it past the tail bound and dropped the app's
# largest `content_fingerprint` writer out of this audit entirely, silently. A
# wider window can only ever find more statements, never fewer.
DERIVED_IMAGE_UPDATE_RE = re.compile(
    r"UPDATE\s+images\s+SET[\s\S]{0,1400}?content_fingerprint[\s\S]{0,1000}?"
    r"WHERE\s+id\s*=\s*\?(?:\s+AND\s+content_fingerprint\s*(?:=\s*\?|IS\s+NULL))?",
    re.IGNORECASE | re.MULTILINE,
)

SKIPPED_SOURCE_DIRS = {"tests", "venv", "__pycache__"}


# Current allowed writer surface. This is intentionally explicit; the long-term
# direction is to shrink this list toward a single derived-state owner.
EXPECTED_DERIVED_IMAGE_UPDATE_STATEMENTS = Counter({
    (
        # BE-3 (v3.5.x): ai_rating / ai_rating_confidence are derived from tag
        # rows (db_tags._sync_ai_rating), so the derived-state clear NULLs them
        # with the other tag-derived columns — same statement, same owner.
        "db_images_write.py",
        "UPDATE images SET content_fingerprint = NULL, embedding = NULL, "
        "tagged_at = NULL, ai_caption = NULL, nl_caption = NULL, aesthetic_score = NULL, "
        "ai_rating = NULL, ai_rating_confidence = NULL WHERE id = ?",
    ): 1,
    (
        # Metadata L3 (v3.5.0): the scan upsert also maintains the
        # raw_metadata_gz invariant (raw envelopes only live on rows whose
        # prompt is missing) — same statement, no new fingerprint writer.
        # Migration 042 added `sidecar_caption` to the same SET list: it is a
        # parsed text field written from the scan record like `prompt`, not a
        # derived-state field, so this stays one writer with one owner.
        # Migration 043 added `sidecar_fingerprint` next to it. That IS a
        # fingerprint, but of the caption files beside the image rather than of
        # the image's own pixels: it gates whether the scan re-reads a sidecar
        # and never participates in derived-state invalidation, so it belongs to
        # the scan upsert and adds no new `content_fingerprint` writer.
        # Migration 044 added `sidecar_caption_format`, derived here from the
        # caption text this same statement stores (never copied from the record)
        # so the marker cannot describe different text. Also not derived state:
        # it is a property of a parsed text field, cleared and rewritten with it.
        "db_images_write.py",
        "UPDATE images SET filename = ?, generator = ?, prompt = ?, sidecar_caption = ?, "
        "sidecar_caption_format = ?, negative_prompt = ?, "
        "metadata_json = ?, width = ?, height = ?, file_size = ?, checkpoint = ?, "
        "checkpoint_normalized = ?, loras = ?, model_hash = COALESCE(?, model_hash), "
        "is_readable = ?, read_error = ?, source_mtime_ns = COALESCE(?, source_mtime_ns), "
        "source_size = COALESCE(?, source_size), metadata_status = ?, "
        "content_fingerprint = COALESCE(?, content_fingerprint), "
        "sidecar_fingerprint = COALESCE(?, sidecar_fingerprint), "
        "library_order_time = COALESCE(library_order_time, created_at, ?), "
        "source_file_mtime = COALESCE(?, source_file_mtime), "
        "created_at = COALESCE(library_order_time, created_at, ?), "
        "raw_metadata_gz = CASE WHEN ? IS NOT NULL THEN ? "
        "WHEN ? IS NOT NULL AND TRIM(?) != '' THEN NULL "
        "ELSE raw_metadata_gz END, "
        "indexed_at = CURRENT_TIMESTAMP WHERE id = ?",
    ): 1,
    (
        "db_images_write.py",
        "UPDATE images SET tagged_at = CURRENT_TIMESTAMP, "
        "content_fingerprint = COALESCE(?, content_fingerprint) WHERE id = ?",
    ): 1,
    (
        "db_tags.py",
        "UPDATE images SET tagged_at = CURRENT_TIMESTAMP, ai_caption = COALESCE(?, ai_caption), "
        "nl_caption = COALESCE(?, nl_caption), "
        "content_fingerprint = COALESCE(?, content_fingerprint) WHERE id = ?",
    ): 1,
    (
        "db_images_write.py",
        "UPDATE images SET tagged_at = ?, ai_caption = ?, nl_caption = ?, aesthetic_score = ?, embedding = ?, "
        "content_fingerprint = COALESCE(?, content_fingerprint) WHERE id = ?",
    ): 1,
    (
        # Metadata L3 (v3.5.0): file-based re-parse clears the stored raw
        # envelope once a prompt is recovered (same invariant as above).
        # Migration 042: `sidecar_caption` is rewritten only when the caller
        # actually re-parsed the file (the CASE guard), so a caller that knows
        # nothing about captions cannot silently drop one. Still one writer.
        # Migration 044: the format marker carries the SAME CASE guard as the
        # caption text, so the pair is rewritten together or not at all.
        "db_images_write.py",
        "UPDATE images SET generator = ?, prompt = ?, "
        "sidecar_caption = CASE WHEN ? = 1 THEN ? ELSE sidecar_caption END, "
        "sidecar_caption_format = CASE WHEN ? = 1 THEN ? ELSE sidecar_caption_format END, "
        "negative_prompt = ?, metadata_json = ?, "
        "width = ?, height = ?, file_size = ?, checkpoint = ?, checkpoint_normalized = ?, "
        "loras = ?, model_hash = COALESCE(?, model_hash), is_readable = COALESCE(?, is_readable), "
        "read_error = ?, source_mtime_ns = COALESCE(?, source_mtime_ns), "
        "source_size = COALESCE(?, source_size), metadata_status = COALESCE(?, metadata_status), "
        "content_fingerprint = COALESCE(?, content_fingerprint), "
        "raw_metadata_gz = CASE WHEN ? IS NOT NULL AND TRIM(?) != '' THEN NULL "
        "ELSE raw_metadata_gz END, "
        "indexed_at = CURRENT_TIMESTAMP WHERE id = ?",
    ): 1,
    (
        "services/derived_state_service.py",
        "UPDATE images SET embedding = ?, content_fingerprint = ? "
        "WHERE id = ? AND content_fingerprint = ?",
    ): 1,
    (
        "services/derived_state_service.py",
        "UPDATE images SET content_fingerprint = ? WHERE id = ? AND content_fingerprint IS NULL",
    ): 1,
    (
        "services/derived_state_service.py",
        "UPDATE images SET aesthetic_score = ?, content_fingerprint = ? "
        "WHERE id = ? AND content_fingerprint = ?",
    ): 1,
    (
        "services/derived_state_service.py",
        "UPDATE images SET content_fingerprint = COALESCE(?, content_fingerprint) WHERE id = ?",
    ): 1,
})


def _normalize_sql_snippet(value: str) -> str:
    return " ".join(value.split()).strip()


def _collect_derived_image_update_statements() -> Counter[tuple[str, str]]:
    backend_root = Path(__file__).resolve().parents[1]
    statements: Counter[tuple[str, str]] = Counter()

    files: list[Path] = []
    for root, dirs, filenames in os.walk(backend_root):
        dirs[:] = [name for name in dirs if name not in SKIPPED_SOURCE_DIRS]
        root_path = Path(root)
        files.extend(root_path / filename for filename in filenames if filename.endswith(".py"))

    for file_path in sorted(files):
        relative_path = file_path.relative_to(backend_root).as_posix()
        source = file_path.read_text(encoding="utf-8")
        for match in DERIVED_IMAGE_UPDATE_RE.finditer(source):
            statements[(relative_path, _normalize_sql_snippet(match.group(0)))] += 1

    return statements


def _derived_row(image_id: int) -> sqlite3.Row:
    with db.get_db() as conn:
        cursor = conn.cursor()
        return cursor.execute(
            """
            SELECT tagged_at, ai_caption, nl_caption, aesthetic_score, embedding, content_fingerprint
            FROM images
            WHERE id = ?
            """,
            (image_id,),
        ).fetchone()


def _seed_expensive_derived_state(image_id: int) -> None:
    db.add_tags(
        image_id,
        [{"tag": "kept_tag", "confidence": 0.9}],
        content_fingerprint="fingerprint-1",
    )
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE images
            SET ai_caption = ?, nl_caption = ?, aesthetic_score = ?, embedding = ?, content_fingerprint = ?
            WHERE id = ?
            """,
            ("caption before rewrite", "nl before rewrite", 8.5, b"embedding-bytes", "fingerprint-1", image_id),
        )


def test_derived_content_fingerprint_writers_stay_on_explicit_allowlist():
    current = _collect_derived_image_update_statements()

    assert current == EXPECTED_DERIVED_IMAGE_UPDATE_STATEMENTS, (
        "Derived-state image writers changed. Do not add feature-local "
        "`content_fingerprint` writers silently; route them through a shared "
        "derived-state owner or update this allowlist with an explicit reason.\n"
        f"Unexpected: {current - EXPECTED_DERIVED_IMAGE_UPDATE_STATEMENTS}\n"
        f"Missing: {EXPECTED_DERIVED_IMAGE_UPDATE_STATEMENTS - current}"
    )


def test_artist_prediction_writes_stay_on_shared_helper_surface():
    backend_root = Path(__file__).resolve().parents[1]
    service_file = backend_root / "services" / "artist_service.py"
    source = service_file.read_text(encoding="utf-8")

    assert "INSERT OR REPLACE INTO artist_predictions" not in source, (
        "Artist batch writes must not inject raw artist_predictions SQL in feature services. "
        "Route writes through services/derived_state_service.py helper surface."
    )
    assert "write_artist_predictions(" in source, (
        "Artist batch path must use write_artist_predictions() helper."
    )


def test_metadata_only_rewrite_preserves_expensive_derived_state(test_db):
    image_id = db.add_image(
        path="/test/metadata-only.png",
        filename="metadata-only.png",
        prompt="before",
        source_mtime_ns=100,
        source_size=200,
        content_fingerprint="fingerprint-1",
    )
    _seed_expensive_derived_state(image_id)

    db.update_image_metadata(
        image_id=image_id,
        generator="comfyui",
        prompt="metadata changed only",
        negative_prompt=None,
        metadata_json='{"metadata": true}',
        width=512,
        height=512,
        file_size=200,
        checkpoint=None,
        loras=[],
        source_mtime_ns=100,
        source_size=200,
        metadata_status="complete",
        content_fingerprint="fingerprint-1",
    )

    row = _derived_row(image_id)
    assert row["tagged_at"] is not None
    assert row["ai_caption"] == "caption before rewrite"
    assert row["nl_caption"] == "nl before rewrite"
    assert row["aesthetic_score"] == 8.5
    assert row["embedding"] == b"embedding-bytes"
    assert row["content_fingerprint"] == "fingerprint-1"


def test_pixel_change_rewrite_clears_stale_expensive_derived_state(test_db):
    image_id = db.add_image(
        path="/test/pixel-change.png",
        filename="pixel-change.png",
        prompt="before",
        source_mtime_ns=100,
        source_size=200,
        content_fingerprint="fingerprint-1",
    )
    _seed_expensive_derived_state(image_id)

    db.update_image_metadata(
        image_id=image_id,
        generator="comfyui",
        prompt="pixels changed",
        negative_prompt=None,
        metadata_json='{"metadata": true}',
        width=768,
        height=768,
        file_size=300,
        checkpoint=None,
        loras=[],
        source_mtime_ns=101,
        source_size=300,
        metadata_status="complete",
        content_fingerprint="fingerprint-2",
    )

    row = _derived_row(image_id)
    assert row["tagged_at"] is None
    assert row["ai_caption"] is None
    assert row["nl_caption"] is None
    assert row["aesthetic_score"] is None
    assert row["embedding"] is None
    assert row["content_fingerprint"] == "fingerprint-2"


def test_derived_writer_helpers_preserve_existing_fingerprint_when_unknown(test_db):
    image_id = db.add_image(
        path="/test/helper-preserve.png",
        filename="helper-preserve.png",
        content_fingerprint="fingerprint-1",
    )

    with db.get_db() as conn:
        cursor = conn.cursor()
        write_image_embeddings(cursor, [(b"embedding-bytes", "fingerprint-1", image_id)])
        write_image_content_fingerprint(
            cursor,
            image_id=image_id,
            content_fingerprint=None,
        )

    row = _derived_row(image_id)
    assert row["aesthetic_score"] is None
    assert row["embedding"] == b"embedding-bytes"
    assert row["content_fingerprint"] == "fingerprint-1"


def test_derived_writer_helpers_advance_fingerprint_when_known(test_db):
    image_id = db.add_image(
        path="/test/helper-advance.png",
        filename="helper-advance.png",
        content_fingerprint="fingerprint-1",
    )

    with db.get_db() as conn:
        cursor = conn.cursor()
        write_image_embeddings(cursor, [(b"embedding-v2", "fingerprint-1", image_id)])
        write_image_content_fingerprint(
            cursor,
            image_id=image_id,
            content_fingerprint="fingerprint-4",
        )

    row = _derived_row(image_id)
    assert row["aesthetic_score"] is None
    assert row["embedding"] == b"embedding-v2"
    assert row["content_fingerprint"] == "fingerprint-4"


def test_aesthetic_writer_publishes_after_legacy_fingerprint_initialization(test_db):
    image_id = db.add_image(
        path="/test/helper-aesthetic-legacy.png",
        filename="helper-aesthetic-legacy.png",
    )

    with db.get_db() as conn:
        cursor = conn.cursor()
        initialized = derived_state_service.initialize_image_content_fingerprint(
            cursor,
            image_id=image_id,
            content_fingerprint="fingerprint-1",
        )
        written = write_image_aesthetic_score(
            cursor,
            image_id=image_id,
            aesthetic_score=8.0,
            content_fingerprint="fingerprint-1",
        )

    row = _derived_row(image_id)
    assert initialized is True
    assert written is True
    assert row["aesthetic_score"] == 8.0
    assert row["content_fingerprint"] == "fingerprint-1"


def test_aesthetic_writer_rejects_stale_score_and_preserves_newer_state(test_db):
    image_id = db.add_image(
        path="/test/helper-stale-aesthetic.png",
        filename="helper-stale-aesthetic.png",
        content_fingerprint="fingerprint-2",
    )
    with db.get_db() as conn:
        conn.execute(
            "UPDATE images SET aesthetic_score = ? WHERE id = ?",
            (9.5, image_id),
        )

    with db.get_db() as conn:
        written = write_image_aesthetic_score(
            conn.cursor(),
            image_id=image_id,
            aesthetic_score=2.0,
            content_fingerprint="fingerprint-1",
        )

    row = _derived_row(image_id)
    assert written is False
    assert row["aesthetic_score"] == 9.5
    assert row["content_fingerprint"] == "fingerprint-2"


def test_aesthetic_writer_requires_a_non_empty_fingerprint(test_db):
    image_id = db.add_image(
        path="/test/helper-empty-aesthetic-fingerprint.png",
        filename="helper-empty-aesthetic-fingerprint.png",
    )

    with db.get_db() as conn:
        with pytest.raises(ValueError, match="content_fingerprint must be non-empty"):
            write_image_aesthetic_score(
                conn.cursor(),
                image_id=image_id,
                aesthetic_score=8.0,
                content_fingerprint="",
            )


def test_embedding_writer_rejects_result_for_a_newer_content_fingerprint(test_db):
    image_id = db.add_image(
        path="/test/helper-stale-embedding.png",
        filename="helper-stale-embedding.png",
        content_fingerprint="fingerprint-2",
    )

    with db.get_db() as conn:
        written_image_ids = write_image_embeddings(
            conn.cursor(),
            [(b"stale-embedding", "fingerprint-1", image_id)],
        )

    row = _derived_row(image_id)
    assert written_image_ids == []
    assert row["embedding"] is None
    assert row["content_fingerprint"] == "fingerprint-2"


def test_initialize_image_fingerprint_only_claims_an_empty_row(test_db):
    image_id = db.add_image(
        path="/test/helper-initialize-fingerprint.png",
        filename="helper-initialize-fingerprint.png",
    )

    with db.get_db() as conn:
        initialized = derived_state_service.initialize_image_content_fingerprint(
            conn.cursor(),
            image_id=image_id,
            content_fingerprint="fingerprint-1",
        )
    with db.get_db() as conn:
        rejected = derived_state_service.initialize_image_content_fingerprint(
            conn.cursor(),
            image_id=image_id,
            content_fingerprint="fingerprint-2",
        )

    row = _derived_row(image_id)
    assert initialized is True
    assert rejected is False
    assert row["content_fingerprint"] == "fingerprint-1"


def test_artist_prediction_batch_helper_writes_predictions_for_current_fingerprint(test_db):
    image_id = db.add_image(
        path="/test/artist-helper-batch.png",
        filename="artist-helper-batch.png",
        content_fingerprint="fingerprint-2",
    )

    with db.get_db() as conn:
        cursor = conn.cursor()
        written_image_ids = write_artist_predictions(
            cursor,
            [
                {
                    "image_id": image_id,
                    "artist": "artist_new",
                    "confidence": 0.88,
                    "top_predictions": [{"artist": "artist_new", "confidence": 0.88}],
                    "content_fingerprint": "fingerprint-2",
                }
            ],
        )

    row = _derived_row(image_id)
    assert written_image_ids == [image_id]
    assert row["content_fingerprint"] == "fingerprint-2"
    with db.get_db() as conn:
        artist_row = conn.execute(
            "SELECT artist, confidence FROM artist_predictions WHERE image_id = ?",
            (image_id,),
        ).fetchone()
    assert artist_row["artist"] == "artist_new"
    assert artist_row["confidence"] == 0.88


def test_artist_prediction_writer_rejects_result_for_a_newer_fingerprint_and_prediction(test_db):
    image_id = db.add_image(
        path="/test/helper-stale-artist.png",
        filename="helper-stale-artist.png",
        content_fingerprint="fingerprint-2",
    )
    with db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO artist_predictions (image_id, artist, confidence, top_predictions)
            VALUES (?, ?, ?, ?)
            """,
            (image_id, "current_artist", 0.95, "[{'artist': 'current_artist'}]"),
        )

    with db.get_db() as conn:
        written = write_artist_prediction(
            conn.cursor(),
            image_id=image_id,
            artist="stale_artist",
            confidence=0.25,
            top_predictions=[{"artist": "stale_artist", "confidence": 0.25}],
            content_fingerprint="fingerprint-1",
        )

    row = _derived_row(image_id)
    with db.get_db() as conn:
        artist_row = conn.execute(
            "SELECT artist, confidence FROM artist_predictions WHERE image_id = ?",
            (image_id,),
        ).fetchone()
    assert written is False
    assert row["content_fingerprint"] == "fingerprint-2"
    assert artist_row["artist"] == "current_artist"
    assert artist_row["confidence"] == 0.95


def test_artist_prediction_batch_writer_returns_only_current_fingerprint_rows(test_db):
    current_id = db.add_image(
        path="/test/helper-current-artist-batch.png",
        filename="helper-current-artist-batch.png",
        content_fingerprint="fingerprint-1",
    )
    stale_id = db.add_image(
        path="/test/helper-stale-artist-batch.png",
        filename="helper-stale-artist-batch.png",
        content_fingerprint="fingerprint-2",
    )

    with db.get_db() as conn:
        written_image_ids = write_artist_predictions(
            conn.cursor(),
            [
                {
                    "image_id": current_id,
                    "artist": "current_artist",
                    "confidence": 0.88,
                    "top_predictions": [{"artist": "current_artist", "confidence": 0.88}],
                    "content_fingerprint": "fingerprint-1",
                },
                {
                    "image_id": stale_id,
                    "artist": "stale_artist",
                    "confidence": 0.77,
                    "top_predictions": [{"artist": "stale_artist", "confidence": 0.77}],
                    "content_fingerprint": "fingerprint-1",
                },
            ],
        )

    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT image_id, artist FROM artist_predictions ORDER BY image_id"
        ).fetchall()
    assert written_image_ids == [current_id]
    assert [(row["image_id"], row["artist"]) for row in rows] == [
        (current_id, "current_artist")
    ]
