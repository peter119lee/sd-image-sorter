"""Regression tests for migration 044 (images.sidecar_caption_format).

Background
==========
``sidecar_caption`` (migration 042) holds text somebody else wrote beside the
image. It arrives in two formats — Danbooru-style tag lists and
natural-language prose — and downstream features (Dataset Maker caption
templates, prompt conversion for natural-language-first target models) need to
know which. The owner's decision was explicitly **not** to add another text
column: format is a different axis from provenance, so it is recorded as one
small marker on the existing column.

This migration runs against a populated production database on the next app
start, so these tests pin the two properties that matter there: it is purely
additive (no existing row value is rewritten, and no row is even read) and it is
idempotent value-by-value.
"""
from __future__ import annotations

import sqlite3

import migrations


def _get_migration_044():
    return next(m for m in migrations.get_migrations() if m.version == 44)


def test_migration_044_is_registered_and_versions_stay_unique():
    all_migrations = migrations.get_migrations()
    versions = [m.version for m in all_migrations]
    assert 44 in versions
    assert all_migrations[-1].version >= 44
    assert len(versions) == len(set(versions))


def test_migration_044_adds_the_column_to_an_existing_table():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE images ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT, filename TEXT, prompt TEXT, sidecar_caption TEXT)"
        )
        conn.commit()
        before = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        assert "sidecar_caption_format" not in before, "precondition: column absent"

        _get_migration_044().apply(conn)

        after = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        assert "sidecar_caption_format" in after
    finally:
        conn.close()


def test_migration_044_is_idempotent_against_a_populated_database():
    """Snapshot every column of every row, apply twice, compare value by value."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE images ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT, filename TEXT, prompt TEXT, negative_prompt TEXT, "
            "sidecar_caption TEXT, sidecar_fingerprint TEXT)"
        )
        conn.executemany(
            "INSERT INTO images "
            "(path, filename, prompt, negative_prompt, sidecar_caption, sidecar_fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "/lib/tags.png",
                    "tags.png",
                    None,
                    None,
                    "masterpiece, best quality, 1girl, solo, looking at viewer",
                    "abc123",
                ),
                (
                    "/lib/prose.png",
                    "prose.png",
                    None,
                    None,
                    "A young woman stands in a sunlit field, holding a paper lantern.",
                    "def456",
                ),
                ("/lib/plain.png", "plain.png", "1girl, masterpiece", "lowres", None, None),
            ],
        )
        conn.commit()
        before = [dict(row) for row in conn.execute("SELECT * FROM images ORDER BY id")]

        migration = _get_migration_044()
        migration.apply(conn)
        migration.apply(conn)  # second run must be a no-op, not "duplicate column"

        rows = [dict(row) for row in conn.execute("SELECT * FROM images ORDER BY id")]
        assert len(rows) == len(before)
        for original, current in zip(before, rows):
            for column, value in original.items():
                assert current[column] == value, f"{column} was rewritten by 044"
            # Existing rows read NULL until their next scan or the recovery job.
            assert current["sidecar_caption_format"] is None
    finally:
        conn.close()


def test_migration_044_reads_no_row_and_writes_no_row():
    """A single guarded ALTER TABLE, nothing else — proven by trapping execute()."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE images ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, sidecar_caption TEXT)"
        )
        conn.execute("INSERT INTO images (path, sidecar_caption) VALUES ('/a.png', '1girl')")
        conn.commit()

        statements: list[str] = []
        conn.set_trace_callback(lambda sql: statements.append(" ".join(str(sql).split())))
        try:
            _get_migration_044().apply(conn)
        finally:
            conn.set_trace_callback(None)

        altered = [s for s in statements if s.upper().startswith("ALTER TABLE")]
        assert len(altered) == 1, statements
        assert "ADD COLUMN sidecar_caption_format" in altered[0]
        for statement in statements:
            upper = statement.upper()
            # Catalog lookups (sqlite_master / PRAGMA) are how the guard checks
            # whether the table and column exist; they touch no row. Nothing may
            # read or write image DATA, and nothing may modify a row at all.
            assert not upper.startswith(("UPDATE ", "INSERT ", "DELETE ")), statement
            if upper.startswith("SELECT"):
                assert "SQLITE_MASTER" in upper, statement
                assert "FROM IMAGES" not in upper, statement
    finally:
        conn.close()


def test_migration_044_skips_when_images_table_absent():
    conn = sqlite3.connect(":memory:")
    try:
        _get_migration_044().apply(conn)  # defensive early return, must not raise
    finally:
        conn.close()


def test_init_db_upgrade_backfills_missing_sidecar_caption_format(tmp_path, monkeypatch):
    """End-to-end: an installed database gains the column through init_db()."""
    import database as db

    db_path = tmp_path / "pre_sidecar_caption_format.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_path))
    db._pragmas_initialized = set()
    db.init_db()

    # Rewind to the owner's real pre-044 state: 042 and 043 already applied
    # (his database is at schema_version 43), rows already stored, only the new
    # column missing.
    raw = sqlite3.connect(str(db_path))
    try:
        raw.execute(
            "INSERT INTO images (path, filename, generator, prompt, sidecar_caption) "
            "VALUES (?, ?, ?, ?, ?)",
            ("/legacy/kept.png", "kept.png", "unknown", None, "1girl, smile, solo"),
        )
        raw.execute("ALTER TABLE images DROP COLUMN sidecar_caption_format")
        raw.execute("UPDATE schema_version SET version = 43 WHERE id = 1")
        raw.commit()
        cols_before = {row[1] for row in raw.execute("PRAGMA table_info(images)")}
        assert "sidecar_caption_format" not in cols_before, "precondition reproduced"
    finally:
        raw.close()

    db._pragmas_initialized = set()
    db.init_db()

    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        cols_after = {row["name"] for row in verify.execute("PRAGMA table_info(images)")}
        assert "sidecar_caption_format" in cols_after

        row = verify.execute(
            "SELECT prompt, sidecar_caption, sidecar_caption_format FROM images WHERE path = ?",
            ("/legacy/kept.png",),
        ).fetchone()
        assert row is not None
        assert row["sidecar_caption"] == "1girl, smile, solo", (
            "the migration must not touch stored caption text"
        )
        assert row["prompt"] is None
        assert row["sidecar_caption_format"] is None, (
            "no backfill: existing rows get the marker on their next scan or "
            "when the owner runs the recovery job"
        )
    finally:
        verify.close()
