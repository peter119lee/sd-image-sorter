"""Regression tests for migration 042 (images.sidecar_caption).

Background
==========
Text recovered from a ``.txt``/``.json`` sidecar next to an image used to be
written into ``images.prompt``. For the owner's library that text is almost
always a Danbooru-style tag list written by a human or a tagger, not the SD
generation prompt that produced the image, so ``prompt`` stopped meaning what
Prompt Lab assumes it means. Migration 042 gives that text its own column.

The migration runs against a populated production database on the next app
start, so these tests pin the two properties that matter there: it is purely
additive (no existing row value is rewritten) and it is idempotent.
"""
from __future__ import annotations

import sqlite3

import migrations


def _get_migration_042():
    return next(m for m in migrations.get_migrations() if m.version == 42)


def test_migration_042_is_registered_and_versions_stay_unique():
    all_migrations = migrations.get_migrations()
    versions = [m.version for m in all_migrations]
    assert 42 in versions
    assert all_migrations[-1].version >= 42
    assert len(versions) == len(set(versions))


def test_migration_042_adds_sidecar_caption_to_an_existing_table():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE images ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT, filename TEXT, prompt TEXT)"
        )
        conn.commit()
        before = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        assert "sidecar_caption" not in before, "precondition: column starts absent"

        _get_migration_042().apply(conn)

        after = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        assert "sidecar_caption" in after
    finally:
        conn.close()


def test_migration_042_is_idempotent_against_a_populated_database():
    """Re-running must not raise and must not rewrite a single stored value."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE images ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT, filename TEXT, prompt TEXT, negative_prompt TEXT)"
        )
        conn.executemany(
            "INSERT INTO images (path, filename, prompt, negative_prompt) "
            "VALUES (?, ?, ?, ?)",
            [
                ("/lib/a.png", "a.png", "1girl, masterpiece", "lowres"),
                ("/lib/b.png", "b.png", None, None),
            ],
        )
        conn.commit()
        before = [dict(row) for row in conn.execute("SELECT * FROM images ORDER BY id")]

        migration = _get_migration_042()
        migration.apply(conn)
        migration.apply(conn)  # second run must be a no-op, not "duplicate column"

        rows = [dict(row) for row in conn.execute("SELECT * FROM images ORDER BY id")]
        assert len(rows) == len(before)
        for original, current in zip(before, rows):
            for column, value in original.items():
                assert current[column] == value, f"{column} was rewritten by 042"
            # Existing rows read NULL for the new column until recovery runs.
            assert current["sidecar_caption"] is None
    finally:
        conn.close()


def test_migration_042_skips_when_images_table_absent():
    conn = sqlite3.connect(":memory:")
    try:
        _get_migration_042().apply(conn)  # defensive early return, must not raise
    finally:
        conn.close()


def test_init_db_upgrade_backfills_missing_sidecar_caption(tmp_path, monkeypatch):
    """End-to-end: an existing install gains the column through init_db()."""
    import database as db

    db_path = tmp_path / "pre_sidecar_caption.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_path))
    db._pragmas_initialized = set()
    db.init_db()

    # Rewind to the real pre-042 state of an installed user's database: every
    # earlier migration applied, rows already stored, sidecar_caption absent.
    raw = sqlite3.connect(str(db_path))
    try:
        raw.execute(
            "INSERT INTO images (path, filename, generator, prompt) VALUES (?, ?, ?, ?)",
            ("/legacy/kept.png", "kept.png", "webui", "1girl, smile"),
        )
        raw.execute("ALTER TABLE images DROP COLUMN sidecar_caption")
        raw.execute("UPDATE schema_version SET version = 41 WHERE id = 1")
        raw.commit()
        cols_before = {row[1] for row in raw.execute("PRAGMA table_info(images)")}
        assert "sidecar_caption" not in cols_before, "precondition: bug reproduced"
    finally:
        raw.close()

    db._pragmas_initialized = set()
    db.init_db()

    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        cols_after = {row["name"] for row in verify.execute("PRAGMA table_info(images)")}
        assert "sidecar_caption" in cols_after

        row = verify.execute(
            "SELECT prompt, sidecar_caption FROM images WHERE path = ?",
            ("/legacy/kept.png",),
        ).fetchone()
        assert row is not None
        assert row["prompt"] == "1girl, smile", "migration must not touch stored prompts"
        assert row["sidecar_caption"] is None
    finally:
        verify.close()
