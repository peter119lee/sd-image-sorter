"""Regression tests for migration 043 (images.sidecar_fingerprint).

Background
==========
The scan's change detector was ``(source_mtime_ns, source_size)`` of the image
alone. A ``.txt`` caption written or edited *after* the image was indexed
changes neither, so the row stayed an "unchanged scan hit" and its caption text
was never read again. ``sidecar_fingerprint`` records the sidecars that were
present the last time the row was parsed so the scan can compare them against
the disk.

The migration runs against the owner's populated production database on the next
app start — observed 2026-08-17: schema_version 41, 6,842 rows, and
``sidecar_caption`` (migration 042) not yet applied either, so 042 and 043 both
run on that same first start. These tests therefore pin the two properties that
matter there: it is purely additive (no existing row value is rewritten) and it
is idempotent.
"""
from __future__ import annotations

import sqlite3

import migrations


def _get_migration_043():
    return next(m for m in migrations.get_migrations() if m.version == 43)


def test_migration_043_is_registered_and_versions_stay_unique():
    all_migrations = migrations.get_migrations()
    versions = [m.version for m in all_migrations]
    assert 43 in versions
    assert all_migrations[-1].version >= 43
    assert len(versions) == len(set(versions))


def test_migration_043_adds_sidecar_fingerprint_to_an_existing_table():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE images ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT, filename TEXT, source_mtime_ns INTEGER, source_size INTEGER)"
        )
        conn.commit()
        before = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        assert "sidecar_fingerprint" not in before, "precondition: column starts absent"

        _get_migration_043().apply(conn)

        after = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        assert "sidecar_fingerprint" in after
    finally:
        conn.close()


def test_migration_043_is_idempotent_against_a_populated_database():
    """Re-running must not raise and must not rewrite a single stored value."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE images ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT, filename TEXT, prompt TEXT, sidecar_caption TEXT, "
            "source_mtime_ns INTEGER, source_size INTEGER, content_fingerprint TEXT)"
        )
        conn.executemany(
            "INSERT INTO images "
            "(path, filename, prompt, sidecar_caption, source_mtime_ns, source_size, content_fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("/lib/a.png", "a.png", "1girl, masterpiece", None, 1723800000000000000, 4096, "abc"),
                ("/lib/b.png", "b.png", None, "1boy, armor", 1723800000000000001, 8192, None),
                ("/lib/c.png", "c.png", None, None, None, None, None),
            ],
        )
        conn.commit()
        before = [dict(row) for row in conn.execute("SELECT * FROM images ORDER BY id")]

        migration = _get_migration_043()
        migration.apply(conn)
        migration.apply(conn)  # second run must be a no-op, not "duplicate column"

        rows = [dict(row) for row in conn.execute("SELECT * FROM images ORDER BY id")]
        assert len(rows) == len(before)
        for original, current in zip(before, rows):
            for column, value in original.items():
                assert current[column] == value, f"{column} was rewritten by 043"
            # NULL, not '': the row has never been fingerprinted, which is not
            # the same claim as "this image has no sidecar".
            assert current["sidecar_fingerprint"] is None
    finally:
        conn.close()


def test_migration_043_skips_when_images_table_absent():
    conn = sqlite3.connect(":memory:")
    try:
        _get_migration_043().apply(conn)  # defensive early return, must not raise
    finally:
        conn.close()


def test_init_db_upgrade_backfills_missing_sidecar_fingerprint(tmp_path, monkeypatch):
    """End-to-end: an existing install gains the column through init_db()."""
    import database as db

    db_path = tmp_path / "pre_sidecar_fingerprint.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_path))
    db._pragmas_initialized = set()
    db.init_db()

    # Rewind to the real pre-043 state of an installed user's database: every
    # earlier migration applied, rows already stored, the column absent.
    raw = sqlite3.connect(str(db_path))
    try:
        raw.execute(
            "INSERT INTO images (path, filename, generator, prompt, sidecar_caption) "
            "VALUES (?, ?, ?, ?, ?)",
            ("/legacy/kept.png", "kept.png", "webui", "1girl, smile", "1girl, smile, tags"),
        )
        raw.execute("ALTER TABLE images DROP COLUMN sidecar_fingerprint")
        raw.execute("UPDATE schema_version SET version = 42 WHERE id = 1")
        raw.commit()
        cols_before = {row[1] for row in raw.execute("PRAGMA table_info(images)")}
        assert "sidecar_fingerprint" not in cols_before, "precondition: bug reproduced"
    finally:
        raw.close()

    db._pragmas_initialized = set()
    db.init_db()

    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        cols_after = {row["name"] for row in verify.execute("PRAGMA table_info(images)")}
        assert "sidecar_fingerprint" in cols_after

        row = verify.execute(
            "SELECT prompt, sidecar_caption, sidecar_fingerprint FROM images WHERE path = ?",
            ("/legacy/kept.png",),
        ).fetchone()
        assert row is not None
        assert row["prompt"] == "1girl, smile", "migration must not touch stored prompts"
        assert row["sidecar_caption"] == "1girl, smile, tags"
        assert row["sidecar_fingerprint"] is None
    finally:
        verify.close()
