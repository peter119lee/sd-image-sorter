"""Re-parse must not skip rows whose is_readable was never backfilled (audit F7).

Every other readable check in the codebase spells the guard
``COALESCE(is_readable, 1) = 1``, because ``is_readable`` was added later and
migration 003 exists precisely for databases that predate its backfill. The
re-parse job used a bare ``is_readable = 1``, so on such a database a row with
NULL there is silently dropped from the candidate set - the user clicks
"Re-parse Missing Prompts", it reports success, and those images are never
looked at.
"""

from __future__ import annotations

from services import metadata_repair_service


def _insert_promptless(test_db, filename, is_readable):
    image_id = test_db.add_image(path=f"L:/library/{filename}", filename=filename, metadata_json="{}")
    with test_db.get_db() as conn:
        conn.execute(
            "UPDATE images SET prompt = NULL, is_readable = ? WHERE id = ?",
            (is_readable, image_id),
        )
    return image_id


def test_legacy_null_readable_rows_are_still_repair_candidates(test_db):
    legacy_id = _insert_promptless(test_db, "legacy-null-readable.png", None)
    readable_id = _insert_promptless(test_db, "explicitly-readable.png", 1)
    unreadable_id = _insert_promptless(test_db, "known-unreadable.png", 0)

    candidates = metadata_repair_service.snapshot_missing_prompt_ids()

    assert readable_id in candidates
    assert legacy_id in candidates, (
        "a row whose is_readable was never backfilled is not an unreadable row; "
        "excluding it silently drops it from the repair job"
    )
    assert unreadable_id not in candidates
