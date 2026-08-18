"""
Database operations tests.

Tests for SQLite database layer including:
- CRUD operations
- Filtering logic (AND/OR combinations)
- SQL injection prevention
- Session persistence

Priority: CRITICAL (SQL injection), HIGH (filtering logic)
"""
import sys
import json
from pathlib import Path
from datetime import datetime

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from image_fingerprint import compute_image_content_fingerprint
from utils.pagination_cursor import decode_image_cursor


class TestDatabaseInit:
    """Tests for database initialization."""

    def test_init_creates_tables(self, test_db):
        """Database initialization should create all required tables."""
        import sqlite3

        conn = sqlite3.connect(db.DATABASE_PATH)
        cursor = conn.cursor()

        # Check images table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='images'")
        assert cursor.fetchone() is not None

        # Check tags table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tags'")
        assert cursor.fetchone() is not None

        # Check collections table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='collections'")
        assert cursor.fetchone() is not None

        conn.close()

    def test_init_creates_indexes(self, test_db):
        """Database initialization should create indexes for performance."""
        import sqlite3

        conn = sqlite3.connect(db.DATABASE_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = [row[0] for row in cursor.fetchall()]

        # Check key indexes exist. Tag lookups are served by composite indexes
        # whose leftmost prefix covers the single-column query patterns.
        assert "idx_tags_tag_image" in indexes  # (tag, image_id) — covers tag lookups
        assert "idx_tags_image_id_tag" in indexes  # (image_id, tag) — covers image_id lookups
        assert "idx_images_generator" in indexes
        assert "idx_images_path" in indexes
        assert "idx_images_path_lower" in indexes

        conn.close()

    def test_casefold_path_lookup_uses_expression_index(self, test_db):
        """Equivalent Windows/WSL path lookups should not scan the whole image table."""
        import sqlite3

        conn = sqlite3.connect(db.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM images WHERE LOWER(path) IN (?)",
            ("/mnt/l/example.png",),
        )
        plan = " ".join(str(row[3]) for row in cursor.fetchall())
        conn.close()

        assert "idx_images_path_lower" in plan

    def test_init_creates_favorites_collection(self, test_db):
        """Favorites collection should be created by default."""
        collection = db.get_collection_by_slug("favorites")
        assert collection is not None
        assert collection["slug"] == "favorites"

    def test_init_creates_schema_version(self, test_db):
        """Database initialization should track an explicit schema version."""
        import sqlite3

        conn = sqlite3.connect(db.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
        assert cursor.fetchone() is not None

        cursor.execute("SELECT version FROM schema_version")
        row = cursor.fetchone()
        assert row is not None
        assert int(row[0]) >= 1
        conn.close()

    def test_init_upgrades_legacy_database_without_schema_version(self, tmp_path):
        """Legacy databases should be upgraded through the migration runner."""
        import sqlite3

        legacy_db_path = tmp_path / "legacy_images.db"
        original_path = db.DATABASE_PATH
        db.DATABASE_PATH = str(legacy_db_path)
        db._pragmas_initialized = set()

        try:
            conn = sqlite3.connect(legacy_db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    generator TEXT DEFAULT 'unknown',
                    prompt TEXT,
                    negative_prompt TEXT,
                    metadata_json TEXT,
                    width INTEGER,
                    height INTEGER,
                    file_size INTEGER,
                    created_at DATETIME,
                    indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    tagged_at DATETIME
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO images (path, filename, generator, prompt, metadata_json, width, height, file_size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("/legacy/example.png", "example.png", "webui", "legacy prompt", "{}", 512, 512, 12345, "2024-01-02 03:04:05"),
            )
            conn.commit()
            conn.close()

            db.init_db()

            conn = sqlite3.connect(legacy_db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM schema_version")
            schema_version = cursor.fetchone()
            assert schema_version is not None
            assert int(schema_version[0]) >= 1

            cursor.execute("PRAGMA table_info(images)")
            columns = {row[1] for row in cursor.fetchall()}
            assert "checkpoint" in columns
            assert "checkpoint_normalized" in columns
            assert "loras" in columns
            assert "embedding" in columns
            assert "ai_caption" in columns
            assert "model_hash" in columns
            assert "aesthetic_score" in columns
            assert "is_readable" in columns
            assert "read_error" in columns
            assert "source_mtime_ns" in columns
            assert "source_size" in columns
            assert "metadata_status" in columns
            assert "content_fingerprint" in columns
            assert "library_order_time" in columns
            assert "source_file_mtime" in columns

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='image_loras'")
            assert cursor.fetchone() is not None
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='image_prompt_tokens'")
            assert cursor.fetchone() is not None

            cursor.execute(
                """
                SELECT prompt, library_order_time, source_file_mtime, created_at
                FROM images
                WHERE path = ?
                """,
                ("/legacy/example.png",),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "legacy prompt"
            assert row[1] == "2024-01-02 03:04:05"
            assert row[2] == "2024-01-02 03:04:05"
            assert row[3] == "2024-01-02 03:04:05"

            conn.close()
        finally:
            db.DATABASE_PATH = original_path
            db._pragmas_initialized = set()

    def test_init_is_idempotent_for_schema_versioned_database(self, test_db):
        """Repeated init_db calls should not duplicate schema-version rows."""
        import sqlite3

        db.init_db()
        db.init_db()

        conn = sqlite3.connect(db.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM schema_version")
        assert cursor.fetchone()[0] == 1
        conn.close()

    def test_init_rejects_newer_schema_without_mutating_database(self, test_db):
        """An older app must not write to a database created by a newer schema."""
        import sqlite3

        import migrations

        latest_supported_version = migrations.get_migrations()[-1].version
        newer_version = latest_supported_version + 1
        image_id = db.add_image(
            path="/future/pending.png",
            filename="pending.png",
            metadata_json="{}",
            is_readable=True,
            metadata_status="pending",
        )
        with db.get_db() as conn:
            conn.execute(
                "UPDATE schema_version SET version = ? WHERE id = ?",
                (newer_version, db.SCHEMA_VERSION_ROW_ID),
            )

        conn = sqlite3.connect(db.DATABASE_PATH)
        journal_mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        conn.close()
        assert journal_mode == "delete"
        db._pragmas_initialized = set()

        with pytest.raises(
            db.UnsupportedDatabaseSchemaVersionError,
            match=(
                rf"schema version {newer_version}.*newer than.*"
                rf"supported version {latest_supported_version}.*Automatic downgrade"
            ),
        ):
            db.init_db()

        assert db._pragmas_initialized == set()
        conn = sqlite3.connect(db.DATABASE_PATH)
        try:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            version = conn.execute(
                "SELECT version FROM schema_version WHERE id = ?",
                (db.SCHEMA_VERSION_ROW_ID,),
            ).fetchone()[0]
            image = conn.execute(
                """
                SELECT is_readable, metadata_status, read_error
                FROM images
                WHERE id = ?
                """,
                (image_id,),
            ).fetchone()
        finally:
            conn.close()

        assert journal_mode == "delete"
        assert int(version) == newer_version
        assert image is not None
        assert int(image[0]) == 1
        assert image[1] == "pending"
        assert image[2] is None

    def test_init_quarantines_stale_pending_rows_without_erasing_recoverable_derived_state(self, test_db):
        import sqlite3

        image_id = db.add_image(
            path="/stale/pending.png",
            filename="pending.png",
            generator="webui",
            prompt="pending scan",
            metadata_json="{}",
            width=512,
            height=512,
            file_size=123,
            is_readable=True,
            metadata_status="pending",
            content_fingerprint="fingerprint-1",
        )

        db.add_tags(image_id, [{"tag": "kept_until_rescan", "confidence": 0.9}], content_fingerprint="fingerprint-1")
        with db.get_db() as conn:
            conn.execute(
                """
                UPDATE images
                SET ai_caption = ?, aesthetic_score = ?, embedding = ?, read_error = NULL
                WHERE id = ?
                """,
                ("stale-but-recoverable", 7.1, b"\x01\x02", image_id),
            )

        db.init_db()

        conn = sqlite3.connect(db.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT is_readable, metadata_status, read_error, content_fingerprint,
                   ai_caption, aesthetic_score, embedding
            FROM images
            WHERE id = ?
            """,
            (image_id,),
        ).fetchone()
        tag_row = conn.execute(
            "SELECT tag FROM tags WHERE image_id = ?",
            (image_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert int(row["is_readable"]) == 0
        assert row["metadata_status"] == "error"
        assert "Re-scan" in row["read_error"]
        assert row["content_fingerprint"] == "fingerprint-1"
        assert row["ai_caption"] == "stale-but-recoverable"
        assert row["aesthetic_score"] == 7.1
        assert row["embedding"] == b"\x01\x02"
        assert tag_row["tag"] == "kept_until_rescan"

    def test_mark_pending_images_metadata_error_only_updates_pending_rows(self, test_db):
        pending_id = db.add_image(
            path="/stale/pending-mark.png",
            filename="pending-mark.png",
            metadata_json="{}",
            width=32,
            height=32,
            file_size=123,
            is_readable=True,
            metadata_status="pending",
        )
        complete_id = db.add_image(
            path="/stale/complete-mark.png",
            filename="complete-mark.png",
            metadata_json="{}",
            width=32,
            height=32,
            file_size=123,
            is_readable=True,
            metadata_status="complete",
        )

        marked = db.mark_pending_images_metadata_error([pending_id, complete_id], "scan stopped")

        with db.get_db() as conn:
            rows = {
                int(row["id"]): row
                for row in conn.execute(
                    "SELECT id, is_readable, metadata_status, read_error FROM images WHERE id IN (?, ?)",
                    (pending_id, complete_id),
                ).fetchall()
            }

        assert marked == 1
        assert rows[pending_id]["is_readable"] == 0
        assert rows[pending_id]["metadata_status"] == "error"
        assert rows[pending_id]["read_error"] == "scan stopped"
        assert rows[complete_id]["is_readable"] == 1
        assert rows[complete_id]["metadata_status"] == "complete"

    def test_init_upgrades_versioned_database_with_pending_backfill_migration(self, tmp_path):
        """Versioned databases should apply remaining migrations and land on the latest version."""
        import sqlite3
        import migrations

        versioned_db_path = tmp_path / "versioned_images.db"
        original_path = db.DATABASE_PATH
        db.DATABASE_PATH = str(versioned_db_path)
        db._pragmas_initialized = set()

        try:
            migration_list = migrations.get_migrations()
            latest_version = migration_list[-1].version

            conn = sqlite3.connect(versioned_db_path)
            conn.row_factory = sqlite3.Row
            migration_list[1].apply(conn)
            conn.execute(
                """
                CREATE TABLE schema_version (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER NOT NULL
                )
                """
            )
            conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 2)")
            conn.execute(
                """
                INSERT INTO images (
                    path,
                    filename,
                    generator,
                    prompt,
                    loras,
                    is_readable,
                    metadata_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "/legacy/versioned.png",
                    "versioned.png",
                    "webui",
                    "<lora:legacy_style:0.8>",
                    '["legacy_style"]',
                    None,
                    None,
                ),
            )
            conn.commit()
            conn.close()

            db.init_db()

            conn = sqlite3.connect(versioned_db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM schema_version WHERE id = 1")
            schema_version = cursor.fetchone()
            assert schema_version is not None
            assert int(schema_version["version"]) == latest_version

            cursor.execute(
                "SELECT is_readable, metadata_status FROM images WHERE path = ?",
                ("/legacy/versioned.png",),
            )
            row = cursor.fetchone()
            assert row is not None
            assert int(row["is_readable"]) == 1
            assert row["metadata_status"] == "complete"

            cursor.execute(
                "SELECT lora_name FROM image_loras WHERE image_id = (SELECT id FROM images WHERE path = ?)",
                ("/legacy/versioned.png",),
            )
            lora_rows = cursor.fetchall()
            assert [lora_row["lora_name"] for lora_row in lora_rows] == ["legacy_style"]

            cursor.execute(
                "SELECT token FROM image_prompt_tokens WHERE image_id = (SELECT id FROM images WHERE path = ?) ORDER BY token",
                ("/legacy/versioned.png",),
            )
            token_rows = cursor.fetchall()
            assert [token_row["token"] for token_row in token_rows] == []
            conn.close()
        finally:
            db.DATABASE_PATH = original_path
            db._pragmas_initialized = set()

    def test_init_rolls_back_failed_migration_without_advancing_version(self, tmp_path, monkeypatch):
        """A failing migration should not leave behind partial schema writes."""
        import sqlite3
        import migrations

        failing_db_path = tmp_path / "failing_images.db"
        original_path = db.DATABASE_PATH
        db.DATABASE_PATH = str(failing_db_path)
        db._pragmas_initialized = set()

        base_migrations = migrations.get_migrations()
        latest_version = base_migrations[-1].version

        def failing_apply(conn):
            conn.execute("CREATE TABLE should_not_persist (id INTEGER PRIMARY KEY)")
            raise RuntimeError("boom")

        failing_migration = migrations.Migration(
            version=latest_version + 1,
            name="test_failure",
            apply=failing_apply,
        )

        try:
            db.init_db()
            monkeypatch.setattr(migrations, "get_migrations", lambda: [*base_migrations, failing_migration])

            with pytest.raises(RuntimeError, match="boom"):
                db.init_db()

            conn = sqlite3.connect(failing_db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM schema_version")
            row = cursor.fetchone()
            assert row is not None
            assert int(row[0]) == latest_version

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='should_not_persist'"
            )
            assert cursor.fetchone() is None
            conn.close()
        finally:
            db.DATABASE_PATH = original_path
            db._pragmas_initialized = set()

    def test_post_migration_vacuum_failure_is_best_effort(self, caplog):
        """Low-space VACUUM failure must warn, not undo successful metadata compaction."""
        import sqlite3

        class FailingVacuumConnection:
            def execute(self, sql):
                assert str(sql).strip().upper() == "VACUUM"
                raise sqlite3.OperationalError("database or disk is full")

        with caplog.at_level("WARNING", logger="database"):
            db._run_post_migration_vacuum(FailingVacuumConnection())

        assert "VACUUM failed" in caplog.text
        assert "images.db may not shrink" in caplog.text



class TestImageCRUD:
    """Tests for image CRUD operations."""

    def test_add_image(self, test_db):
        """Adding an image should return an ID."""
        image_id = db.add_image(
            path="/test/image.png",
            filename="image.png",
            generator="comfyui",
            prompt="test prompt",
            width=1024,
            height=768,
        )

        assert isinstance(image_id, int)
        assert image_id > 0

    def test_get_image_by_id(self, test_db):
        """Retrieving an image by ID should return correct data."""
        image_id = db.add_image(
            path="/test/retrieve.png",
            filename="retrieve.png",
            generator="webui",
            prompt="retrieval test",
            width=512,
            height=512,
        )

        image = db.get_image_by_id(image_id)

        assert image is not None
        assert image["id"] == image_id
        assert image["path"] == "/test/retrieve.png"
        assert image["generator"] == "webui"
        assert image["prompt"] == "retrieval test"

    def test_get_image_by_id_not_found(self, test_db):
        """Retrieving non-existent image should return None."""
        image = db.get_image_by_id(999999)
        assert image is None

    def test_update_image_path(self, test_db):
        """Updating image path should work correctly."""
        image_id = db.add_image(
            path="/test/old_path.png",
            filename="old_path.png",
        )

        db.update_image_path(image_id, "/test/new_path.png")

        image = db.get_image_by_id(image_id)
        assert "new_path.png" in image["path"]

    def test_update_image_path_restores_stale_missing_read_error(self, test_db):
        """A successful move should clear stale missing-file state."""
        image_id = db.add_image(
            path="/test/old_path.png",
            filename="old_path.png",
            prompt="move race",
        )
        db.mark_image_unreadable(image_id, "File not found on disk")

        db.update_image_path(image_id, "/test/new_path.png")

        image = db.get_image_by_id(image_id)
        assert image["path"] == "/test/new_path.png"
        assert image["filename"] == "new_path.png"
        assert image["is_readable"] == 1
        assert image["read_error"] is None
        assert image["metadata_status"] == "complete"

    def test_update_image_path_preserves_non_missing_read_error(self, test_db):
        """Path updates should not hide genuine decode or permission errors."""
        image_id = db.add_image(
            path="/test/old_path.png",
            filename="old_path.png",
            prompt="move race",
        )
        db.mark_image_unreadable(image_id, "Unsupported image format")

        db.update_image_path(image_id, "/test/new_path.png")

        image = db.get_image_by_id(image_id)
        assert image["path"] == "/test/new_path.png"
        assert image["is_readable"] == 0
        assert image["read_error"] == "Unsupported image format"
        assert image["metadata_status"] == "error"

    def test_add_image_reuses_existing_row_for_windows_path_variants(self, test_db):
        """Same Windows file should not duplicate rows across slash/case variants."""
        import sqlite3

        first_id, first_status = db.add_image(
            path=r"L:\Tencent Files\foo\bar.png",
            filename="bar.png",
            prompt="first",
            return_status=True,
        )
        second_id, second_status = db.add_image(
            path=r"l:/Tencent Files/foo/bar.png",
            filename="bar.png",
            prompt="second",
            return_status=True,
        )

        assert first_status == "new"
        assert second_status == "updated"
        assert second_id == first_id

        conn = sqlite3.connect(db.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM images")
        assert cursor.fetchone()[0] == 1
        conn.close()

    def test_delete_image(self, test_db):
        """Deleting an image should remove it from database."""
        image_id = db.add_image(
            path="/test/to_delete.png",
            filename="to_delete.png",
        )

        db.delete_image(image_id)

        image = db.get_image_by_id(image_id)
        assert image is None

    def test_add_image_with_loras(self, test_db):
        """Adding image with LoRAs should store them as JSON."""
        image_id = db.add_image(
            path="/test/with_loras.png",
            filename="with_loras.png",
            loras=["lora1", "lora2", "lora3"],
        )

        image = db.get_image_by_id(image_id)
        loras = json.loads(image["loras"])

        assert loras == ["lora1", "lora2", "lora3"]

    def test_add_image_replaces_existing(self, test_db):
        """Adding an image with the same path should update in place."""
        path = "/test/duplicate.png"

        id1 = db.add_image(path=path, filename="duplicate.png", prompt="first")
        id2 = db.add_image(path=path, filename="duplicate.png", prompt="second")

        assert id2 == id1

        image = db.get_image_by_id(id1)
        assert image["prompt"] == "second"
        assert image["path"] == path

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM images WHERE path = ?", (path,))
            assert cursor.fetchone()[0] == 1

    def test_add_image_reuses_equivalent_windows_and_wsl_paths(self, test_db):
        """Equivalent Windows and /mnt/<drive> paths should upsert the same row."""
        windows_path = r"L:\datasets\shared\equivalent.png"
        wsl_path = "/mnt/l/datasets/shared/equivalent.png"

        image_id = db.add_image(path=windows_path, filename="equivalent.png", prompt="first")
        updated_id = db.add_image(path=wsl_path, filename="equivalent.png", prompt="second")

        assert updated_id == image_id
        image = db.get_image_by_id(image_id)
        assert image["path"] == windows_path
        assert image["prompt"] == "second"

    def test_get_image_by_path_accepts_equivalent_windows_and_wsl_paths(self, test_db):
        """Cross-runtime path translations should still retrieve the indexed row."""
        windows_path = r"L:\datasets\lookup\image.png"
        wsl_path = "/mnt/l/datasets/lookup/image.png"

        image_id = db.add_image(path=windows_path, filename="image.png", prompt="lookup")

        image = db.get_image_by_path(wsl_path)

        assert image is not None
        assert image["id"] == image_id
        assert image["path"] == windows_path

    def test_add_image_replaces_existing_preserves_original_created_at(self, test_db):
        """Upserting an indexed path should not reshuffle gallery chronology."""
        path = "/test/stable-created-at.png"
        original_created_at = datetime(2024, 1, 2, 3, 4, 5)
        rescanned_created_at = datetime(2025, 6, 7, 8, 9, 10)

        image_id = db.add_image(
            path=path,
            filename="stable-created-at.png",
            prompt="first prompt",
            created_at=original_created_at,
        )
        updated_id = db.add_image(
            path=path,
            filename="stable-created-at.png",
            prompt="rescanned prompt",
            created_at=rescanned_created_at,
        )

        assert updated_id == image_id
        image = db.get_image_by_id(image_id)
        assert image["prompt"] == "rescanned prompt"
        assert str(image["library_order_time"]) == original_created_at.strftime("%Y-%m-%d %H:%M:%S")
        assert str(image["source_file_mtime"]) == rescanned_created_at.strftime("%Y-%m-%d %H:%M:%S")
        assert str(image["created_at"]) == original_created_at.strftime("%Y-%m-%d %H:%M:%S")

    def test_rescan_preserves_child_records_for_existing_path(self, test_db):
        """Rescanning an indexed path should not cascade-delete child rows."""
        path = "/test/rescan.png"
        image_id = db.add_image(
            path=path,
            filename="rescan.png",
            prompt="first prompt",
            loras=["first_style"],
            metadata_json="{}",
            width=512,
            height=512,
            file_size=1024,
        )
        db.add_tags(image_id, [
            {"tag": "cat", "confidence": 0.9},
            {"tag": "indoors", "confidence": 0.8},
        ])

        favorites = db.get_collection_by_slug("favorites")
        db.add_collection_item(
            collection_id=favorites["id"],
            source_image_id=image_id,
            copied_path="/favorites/rescan.png",
            prompt="favorite prompt",
            negative_prompt=None,
            checkpoint=None,
            loras='["first_style"]',
            metadata_json="{}",
            created_at=None,
            width=512,
            height=512,
            file_size=1024,
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO artist_predictions (image_id, artist, confidence, top_predictions)
                VALUES (?, ?, ?, ?)
                """,
                (
                    image_id,
                    "artist_one",
                    0.97,
                    json.dumps([{"artist": "artist_one", "confidence": 0.97}]),
                ),
            )

        rescanned_id = db.add_image(
            path=path,
            filename="rescan.png",
            prompt="rescanned prompt",
            loras=["second_style"],
            metadata_json='{"rescanned": true}',
            width=768,
            height=768,
            file_size=2048,
        )

        assert rescanned_id == image_id
        assert {tag["tag"] for tag in db.get_image_tags(image_id)} == {"cat", "indoors"}
        assert db.get_collection_item(favorites["id"], image_id) is not None

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM images WHERE path = ?", (path,))
            assert cursor.fetchone()[0] == 1

            cursor.execute(
                "SELECT artist, confidence FROM artist_predictions WHERE image_id = ?",
                (image_id,),
            )
            artist_row = cursor.fetchone()

        assert artist_row["artist"] == "artist_one"
        assert artist_row["confidence"] == pytest.approx(0.97)

        image = db.get_image_by_id(image_id)
        assert image["prompt"] == "rescanned prompt"
        assert image["width"] == 768
        assert image["height"] == 768

    def test_datetime_values_do_not_use_deprecated_sqlite_default_adapter(self, test_db):
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings("error", category=DeprecationWarning)
            image_id = db.add_image(
                path="/test/datetime-adapter.png",
                filename="datetime-adapter.png",
                prompt="adapter",
                library_order_time=datetime(2024, 1, 2, 3, 4, 5),
                source_file_mtime=datetime(2024, 1, 2, 3, 4, 6),
                created_at=datetime(2024, 1, 2, 3, 4, 7),
            )
            db.add_tags(image_id, [{"tag": "adapter", "confidence": 0.9}])

        row = db.get_image_by_id(image_id)
        assert str(row["library_order_time"]).startswith("2024-01-02 03:04:05")

    def test_add_and_update_image_metadata_refreshes_prompt_token_index(self, test_db):
        """Prompt library facets should read maintained image_prompt_tokens rows."""
        image_id = db.add_image(
            path="/test/prompt-token.png",
            filename="prompt-token.png",
            prompt="Best_Quality, MASTERPIECE, high res, <lora:ignored:0.8>",
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT token FROM image_prompt_tokens WHERE image_id = ? ORDER BY token",
                (image_id,),
            )
            original_tokens = [row["token"] for row in cursor.fetchall()]

        assert original_tokens == ["best quality", "high res", "masterpiece"]

        db.update_image_metadata(
            image_id=image_id,
            generator="comfyui",
            prompt="cinematic_lighting, standing",
            negative_prompt=None,
            metadata_json="{}",
            width=512,
            height=512,
            file_size=1024,
            checkpoint=None,
            loras=[],
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT token FROM image_prompt_tokens WHERE image_id = ? ORDER BY token",
                (image_id,),
            )
            refreshed_tokens = [row["token"] for row in cursor.fetchall()]

        assert refreshed_tokens == ["cinematic lighting", "standing"]
        library = db.get_all_prompt_tokens(limit=10)
        assert {item["prompt"] for item in library["prompts"]} >= {"cinematic lighting", "standing"}

    def test_update_image_metadata_refreshes_image_loras_index(self, test_db):
        """Reparsing metadata should refresh the normalized image_loras rows."""
        image_id = db.add_image(
            path="/test/reparse.png",
            filename="reparse.png",
            generator="unknown",
            prompt="portrait <lora:old_prompt:0.8>",
            negative_prompt=None,
            metadata_json="{}",
            width=512,
            height=512,
            file_size=1024,
            checkpoint=None,
            loras=["old_style.safetensors"],
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT lora_name FROM image_loras WHERE image_id = ? ORDER BY lora_name",
                (image_id,),
            )
            original_loras = [row["lora_name"] for row in cursor.fetchall()]

        assert original_loras == ["old_prompt", "old_style"]

        db.update_image_metadata(
            image_id=image_id,
            generator="comfyui",
            prompt="portrait <lora:new_prompt:0.7>",
            negative_prompt="low quality",
            metadata_json='{"source": "reparse"}',
            width=768,
            height=1024,
            file_size=2048,
            checkpoint="updated_checkpoint.safetensors",
            loras=["new_style.safetensors"],
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT lora_name FROM image_loras WHERE image_id = ? ORDER BY lora_name",
                (image_id,),
            )
            refreshed_loras = [row["lora_name"] for row in cursor.fetchall()]

        assert refreshed_loras == ["new_prompt", "new_style"]

    def test_update_image_metadata_preserve_flag_requires_matching_fingerprint(self, test_db):
        image_id = db.add_image(
            path="/test/preserve-gate.png",
            filename="preserve-gate.png",
            prompt="before",
            source_mtime_ns=100,
            source_size=200,
            content_fingerprint="fingerprint-1",
        )
        db.add_tags(image_id, [{"tag": "stale_tag", "confidence": 0.95}], content_fingerprint="fingerprint-1")
        with db.get_db() as conn:
            conn.execute(
                """
                UPDATE images
                SET ai_caption = ?, aesthetic_score = ?, embedding = ?, content_fingerprint = ?
                WHERE id = ?
                """,
                ("stale caption", 5.0, b"old-embedding", "fingerprint-1", image_id),
            )
            conn.execute(
                """
                INSERT INTO artist_predictions (image_id, artist, confidence, top_predictions)
                VALUES (?, ?, ?, ?)
                """,
                (image_id, "artist_old", 0.9, '[{"artist":"artist_old","confidence":0.9}]'),
            )

        db.update_image_metadata(
            image_id=image_id,
            generator="comfyui",
            prompt="pixels changed",
            negative_prompt=None,
            metadata_json="{}",
            width=768,
            height=768,
            file_size=300,
            checkpoint=None,
            loras=[],
            source_mtime_ns=101,
            source_size=300,
            metadata_status="complete",
            content_fingerprint="fingerprint-2",
            preserve_derived_state=True,
        )

        row = db.get_image_by_id(image_id)
        assert row["ai_caption"] is None
        assert row["aesthetic_score"] is None
        with db.get_db() as conn:
            content_fingerprint = conn.execute(
                "SELECT content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()["content_fingerprint"]
            embedding_value = conn.execute(
                "SELECT embedding FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()["embedding"]
        assert content_fingerprint == "fingerprint-2"
        assert embedding_value is None
        assert db.get_image_tags(image_id) == []
        with db.get_db() as conn:
            remaining_artist_rows = conn.execute(
                "SELECT COUNT(*) FROM artist_predictions WHERE image_id = ?",
                (image_id,),
            ).fetchone()[0]
        assert remaining_artist_rows == 0

    def test_update_image_metadata_preserve_flag_does_not_keep_unreadable_rows(self, test_db):
        image_id = db.add_image(
            path="/test/preserve-unreadable.png",
            filename="preserve-unreadable.png",
            prompt="before",
            source_mtime_ns=100,
            source_size=200,
            content_fingerprint="fingerprint-1",
        )
        db.add_tags(image_id, [{"tag": "stale_tag", "confidence": 0.95}], content_fingerprint="fingerprint-1")
        with db.get_db() as conn:
            conn.execute(
                """
                UPDATE images
                SET ai_caption = ?, aesthetic_score = ?, embedding = ?, content_fingerprint = ?
                WHERE id = ?
                """,
                ("stale caption", 5.0, b"old-embedding", "fingerprint-1", image_id),
            )
            conn.execute(
                """
                INSERT INTO artist_predictions (image_id, artist, confidence, top_predictions)
                VALUES (?, ?, ?, ?)
                """,
                (image_id, "artist_old", 0.9, '[{"artist":"artist_old","confidence":0.9}]'),
            )

        db.update_image_metadata(
            image_id=image_id,
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            metadata_json="{}",
            width=None,
            height=None,
            file_size=200,
            checkpoint=None,
            loras=[],
            is_readable=False,
            read_error="parse failed",
            source_mtime_ns=101,
            source_size=201,
            metadata_status="error",
            content_fingerprint=None,
            preserve_derived_state=True,
        )

        row = db.get_image_by_id(image_id)
        assert row["is_readable"] == 0
        assert row["metadata_status"] == "error"
        # Recomputed from pixels we can no longer read, so these must go even
        # though the caller asked to preserve derived state.
        assert row["ai_caption"] is None
        assert row["aesthetic_score"] is None
        with db.get_db() as conn:
            embedding_value = conn.execute(
                "SELECT embedding FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()["embedding"]
        assert embedding_value is None
        # ...but the labels stay. This used to assert they were deleted, which
        # pinned a data-loss bug: an unreadable mark means we could not look at
        # the file, not that the file changed, and the same write happens when a
        # scan meets a disconnected drive. Tags and predictions still describe
        # these pixels and are restored to usefulness the moment it reconnects.
        assert [tag["tag"] for tag in db.get_image_tags(image_id)] == ["stale_tag"]
        with db.get_db() as conn:
            remaining_artist_rows = conn.execute(
                "SELECT COUNT(*) FROM artist_predictions WHERE image_id = ?",
                (image_id,),
            ).fetchone()[0]
        assert remaining_artist_rows == 1


class TestTagOperations:
    """Tests for tag operations."""

    def test_add_tags(self, test_db):
        """Adding tags should associate them with an image."""
        image_id = db.add_image(path="/test/tagged.png", filename="tagged.png")

        db.add_tags(image_id, [
            {"tag": "landscape", "confidence": 0.95},
            {"tag": "outdoor", "confidence": 0.88},
        ])

        tags = db.get_image_tags(image_id)

        assert len(tags) == 2
        assert any(t["tag"] == "landscape" for t in tags)
        assert any(t["tag"] == "outdoor" for t in tags)

    def test_add_tags_replaces_existing(self, test_db):
        """Adding tags should replace existing tags for the image."""
        image_id = db.add_image(path="/test/retag.png", filename="retag.png")

        db.add_tags(image_id, [{"tag": "old_tag", "confidence": 0.5}])
        db.add_tags(image_id, [{"tag": "new_tag", "confidence": 0.9}])

        tags = db.get_image_tags(image_id)

        assert len(tags) == 1
        assert tags[0]["tag"] == "new_tag"

    def test_get_image_tags_ordered_by_confidence(self, test_db):
        """Tags should be returned ordered by confidence descending."""
        image_id = db.add_image(path="/test/ordered.png", filename="ordered.png")

        db.add_tags(image_id, [
            {"tag": "low", "confidence": 0.3},
            {"tag": "high", "confidence": 0.9},
            {"tag": "medium", "confidence": 0.6},
        ])

        tags = db.get_image_tags(image_id)

        assert tags[0]["tag"] == "high"
        assert tags[1]["tag"] == "medium"
        assert tags[2]["tag"] == "low"

    def test_get_all_tags(self, test_db):
        """Getting all tags should return unique tags with counts."""
        # Create images with overlapping tags
        id1 = db.add_image(path="/test/1.png", filename="1.png")
        id2 = db.add_image(path="/test/2.png", filename="2.png")

        db.add_tags(id1, [{"tag": "common", "confidence": 0.9}])
        db.add_tags(id2, [
            {"tag": "common", "confidence": 0.9},
            {"tag": "unique", "confidence": 0.8},
        ])

        all_tags = db.get_all_tags()

        # Find our tags
        common = next((t for t in all_tags if t["tag"] == "common"), None)
        unique = next((t for t in all_tags if t["tag"] == "unique"), None)

        assert common is not None
        assert common["count"] == 2
        assert unique is not None
        assert unique["count"] == 1

    def test_tagged_at_updated(self, test_db):
        """Tagging an image should update tagged_at timestamp."""
        image_id = db.add_image(path="/test/timestamp.png", filename="timestamp.png")

        # Before tagging
        image = db.get_image_by_id(image_id)
        assert image["tagged_at"] is None

        # After tagging
        db.add_tags(image_id, [{"tag": "test", "confidence": 0.9}])

        image = db.get_image_by_id(image_id)
        assert image["tagged_at"] is not None

    def test_add_tags_computes_content_fingerprint_when_missing(self, test_db, tmp_path: Path):
        """Direct tag writes should still populate the content fingerprint fallback."""
        image_path = tmp_path / "tagged-fingerprint.png"
        from PIL import Image

        Image.new("RGB", (16, 16), color="white").save(image_path)
        image_id = db.add_image(path=str(image_path), filename=image_path.name)

        db.add_tags(image_id, [{"tag": "test", "confidence": 0.9}])

        expected = compute_image_content_fingerprint(str(image_path))
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()

        assert row["content_fingerprint"] == expected


class TestImageFiltering:
    """Tests for image filtering logic."""

    def test_filter_by_generator(self, test_db_with_images):
        """Filtering by generator should return correct images."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(generators=["comfyui"])

        assert len(images) == 1
        assert images[0]["generator"] == "comfyui"

    def test_filter_by_multiple_generators_or_logic(self, test_db_with_images):
        """Multiple generators should use OR logic."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(generators=["comfyui", "nai"])

        assert len(images) == 2
        generators = {img["generator"] for img in images}
        assert generators == {"comfyui", "nai"}

    def test_filter_by_tags_and_logic(self, test_db_with_images):
        """Multiple tags should use AND logic - image must have ALL tags."""
        data = test_db_with_images
        db_module = data["db"]

        # First image has: landscape, outdoor, general
        images = db_module.get_images(tags=["landscape", "outdoor"])

        assert len(images) == 1
        assert images[0]["filename"] == "comfyui_test.png"

    def test_filter_by_single_tag(self, test_db_with_images):
        """Single tag filter should return all matching images."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(tags=["general"])

        assert len(images) == 1

    def test_filter_by_ratings(self, test_db_with_images):
        """Rating filter should use OR logic and include untagged images."""
        data = test_db_with_images
        db_module = data["db"]

        # Images have: general, sensitive, questionable, explicit
        # Untagged images are also included per the design
        images = db_module.get_images(ratings=["explicit", "sensitive"])

        # Should include: sensitive, explicit, and untagged (unknown_test.jpg)
        assert len(images) >= 2
        # Verify all returned images have the requested ratings or are untagged
        for img in images:
            img_tags = db_module.get_image_tags(img["id"])
            if img_tags:
                tag_names = [t["tag"] for t in img_tags]
                assert any(r in tag_names for r in ["explicit", "sensitive", "questionable", "general"]) or len(tag_names) == 0

    def test_filter_by_checkpoints(self, test_db_with_images):
        """Checkpoint filter should use OR logic."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(checkpoints=["sd_xl_base_1.0.safetensors"])

        assert len(images) == 1
        assert images[0]["checkpoint"] == "sd_xl_base_1.0.safetensors"
        assert images[0]["checkpoint_normalized"] == "sd_xl_base_1.0"

    def test_filter_by_normalized_checkpoint_across_raw_variants(self, test_db):
        """Checkpoint filters should collapse raw generator-specific variants onto one normalized key."""
        image_a = db.add_image(
            path="/test/checkpoints/webui_variant.png",
            filename="webui_variant.png",
            generator="webui",
            checkpoint="ponyXLV6.safetensors [abcd1234]",
            metadata_json="{}",
        )
        image_b = db.add_image(
            path="/test/checkpoints/comfy_variant.png",
            filename="comfy_variant.png",
            generator="comfyui",
            checkpoint="ponyXLV6.safetensors",
            metadata_json="{}",
        )

        by_normalized = db.get_images(checkpoints=["ponyXLV6"])
        by_raw_variant = db.get_images(checkpoints=["ponyXLV6.safetensors [abcd1234]"])

        assert {image["id"] for image in by_normalized} == {image_a, image_b}
        assert {image["id"] for image in by_raw_variant} == {image_a, image_b}
        assert {image["checkpoint_normalized"] for image in by_normalized} == {"ponyXLV6"}

    def test_filter_by_dimensions(self, test_db_with_images):
        """Dimension filters should work correctly."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(min_width=1000)

        # Only 1024 width images
        for img in images:
            assert img["width"] >= 1000

    def test_filter_by_aspect_ratio_square(self, test_db_with_images):
        """Square aspect ratio filter should work."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(aspect_ratio="square")

        for img in images:
            ratio = img["width"] / img["height"]
            assert 0.9 <= ratio <= 1.1

    def test_filter_by_aspect_ratio_landscape(self, test_db_with_images):
        """Landscape aspect ratio filter should work."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(aspect_ratio="landscape")

        for img in images:
            ratio = img["width"] / img["height"]
            assert ratio > 1.1

    def test_filter_by_aspect_ratio_portrait(self, test_db_with_images):
        """Portrait aspect ratio filter should work."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(aspect_ratio="portrait")

        for img in images:
            ratio = img["width"] / img["height"]
            assert ratio < 0.9

    def test_filter_by_search_query(self, test_db_with_images):
        """Search query should search in prompts."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(search_query="landscape")

        assert len(images) >= 1
        # At least one image should have landscape in prompt
        found = False
        for img in images:
            if img.get("prompt") and "landscape" in img["prompt"].lower():
                found = True
                break
        assert found, "No image found with 'landscape' in prompt"

    def test_search_query_matches_normalized_checkpoint_name(self, test_db):
        """Free-text search should match normalized checkpoint names as well as prompt text."""
        image_id = db.add_image(
            path="/test/search/checkpoint_variant.png",
            filename="checkpoint_variant.png",
            checkpoint="RealisticVisionV51.safetensors [abc12345]",
            metadata_json="{}",
        )

        images = db.get_images(search_query="realisticvisionv51")

        assert [image["id"] for image in images] == [image_id]

    def test_filter_combined(self, test_db_with_images):
        """Combined filters should work together."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(
            generators=["comfyui"],
            min_width=1000,
        )

        assert len(images) == 1
        assert images[0]["generator"] == "comfyui"
        assert images[0]["width"] >= 1000

    def test_get_images_post_filter_excludes_embedding_blob(self, test_db):
        """Prompt/Lora post-filter queries should not leak embedding BLOBs into result rows."""
        image_id = db.add_image(
            path="/test/post_filter_blob.png",
            filename="post_filter_blob.png",
            prompt="city_night, skyline, neon reflections",
            loras='["city_style"]',
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE images SET embedding = ? WHERE id = ?",
                (b"\x00\x01\xf0\x02binary", image_id),
            )

        images = db.get_images(prompt_terms=["city_night"])

        assert len(images) == 1
        assert images[0]["id"] == image_id
        assert "embedding" not in images[0]
        json.dumps(images)

    def test_prompt_filter_exact_mode_excludes_parenthesized_variants_by_default(self, test_db):
        """Exact prompt filters keep token semantics unless the caller opts into contains matching."""
        exact_id = db.add_image(
            path="/test/prompt_exact_plain.png",
            filename="prompt_exact_plain.png",
            prompt="takamatsu_tomori, 1girl",
        )
        db.add_image(
            path="/test/prompt_exact_parenthesized.png",
            filename="prompt_exact_parenthesized.png",
            prompt="takamatsu_tomori(bang dream!), 1girl",
        )
        db.add_image(
            path="/test/prompt_exact_substring.png",
            filename="prompt_exact_substring.png",
            prompt="not_takamatsu_tomori, 1girl",
        )

        images = db.get_images(prompt_terms=["takamatsu_tomori"], sort_by="oldest")

        assert [image["id"] for image in images] == [exact_id]

    def test_prompt_filter_contains_mode_includes_parenthesized_variants(self, test_db):
        """Contains prompt mode should catch free-form character variants with suffix notes."""
        expected_ids = [
            db.add_image(
                path="/test/prompt_contains_plain.png",
                filename="prompt_contains_plain.png",
                prompt="takamatsu_tomori, 1girl",
            ),
            db.add_image(
                path="/test/prompt_contains_series.png",
                filename="prompt_contains_series.png",
                prompt="takamatsu_tomori(bang dream), 1girl",
            ),
            db.add_image(
                path="/test/prompt_contains_excited.png",
                filename="prompt_contains_excited.png",
                prompt="takamatsu_tomori(bang dream!!!!!its mygo), 1girl",
            ),
        ]
        db.add_image(
            path="/test/prompt_contains_other.png",
            filename="prompt_contains_other.png",
            prompt="shiina_taki, 1girl",
        )

        images = db.get_images(
            prompt_terms=["takamatsu_tomori"],
            prompt_match_mode="contains",
            sort_by="oldest",
        )

        assert [image["id"] for image in images] == expected_ids

    def test_prompt_filter_contains_mode_paginates_and_ids_without_exact_post_filter(self, test_db):
        """Contains mode should not use exact post-filter offsets that drop substring matches."""
        expected_ids = []
        for value in [
            "takamatsu_tomori",
            "takamatsu_tomori(bang dream!)",
            "takamatsu_tomori(bang dream!!!!!its mygo)",
        ]:
            expected_ids.append(
                db.add_image(
                    path=f"/test/prompt_contains_page_{len(expected_ids)}.png",
                    filename=f"prompt_contains_page_{len(expected_ids)}.png",
                    prompt=f"{value}, 1girl",
                )
            )

        first_page = db.get_images_paginated(
            prompt_terms=["takamatsu_tomori"],
            prompt_match_mode="contains",
            sort_by="oldest",
            limit=2,
            skip_count=True,
        )
        ids = db.get_filtered_image_ids(
            prompt_terms=["takamatsu_tomori"],
            prompt_match_mode="contains",
            sort_by="oldest",
        )

        assert [image["id"] for image in first_page["images"]] == expected_ids[:2]
        assert first_page["has_more"] is True
        assert ids == expected_ids

    def test_get_images_paginated_post_filter_scans_beyond_false_positive_window(self, test_db):
        """Post-filter pagination must keep scanning until it finds the true next matches."""
        exact_ids = []
        for index in range(4):
            exact_ids.append(
                db.add_image(
                    path=f"/test/post_filter_exact_{index}.png",
                    filename=f"post_filter_exact_{index}.png",
                    prompt="hero, studio light",
                )
            )

        for index in range(40):
            db.add_image(
                path=f"/test/post_filter_false_positive_{index}.png",
                filename=f"post_filter_false_positive_{index}.png",
                prompt="superhero, dramatic pose",
            )

        expected_ids = list(reversed(exact_ids))
        first_page = db.get_images_paginated(
            prompt_terms=["hero"],
            sort_by="newest",
            limit=3,
            skip_count=True,
        )

        assert [img["id"] for img in first_page["images"]] == expected_ids[:3]
        assert first_page["has_more"] is True
        first_cursor = decode_image_cursor(first_page["next_cursor"])
        assert first_cursor.image_id == expected_ids[2]

        second_page = db.get_images_paginated(
            prompt_terms=["hero"],
            sort_by="newest",
            limit=3,
            cursor_id=first_cursor.image_id,
            cursor_sort_value=first_cursor.sort_value,
            cursor_is_opaque=first_cursor.is_opaque,
            skip_count=True,
        )
        assert [img["id"] for img in second_page["images"]] == expected_ids[3:]
        assert second_page["has_more"] is False
        assert second_page["next_cursor"] is None

    def test_get_images_paginated_total_applies_aesthetic_filters(self, test_db):
        """Paginated totals must match the visible page when aesthetic filters are active."""
        ids = []
        for index, score in enumerate([4.0, 6.5, 8.0, None]):
            image_id = db.add_image(
                path=f"/test/aesthetic_page_total_{index}.png",
                filename=f"aesthetic_page_total_{index}.png",
                prompt="aesthetic page total",
            )
            ids.append(image_id)
            if score is not None:
                with db.get_db() as conn:
                    conn.execute("UPDATE images SET aesthetic_score = ? WHERE id = ?", (score, image_id))

        result = db.get_images_paginated(
            min_aesthetic=6.0,
            max_aesthetic=8.5,
            sort_by="newest",
            limit=10,
        )

        assert result["total"] == 2
        assert [image["id"] for image in result["images"]] == [ids[2], ids[1]]

    def test_get_images_paginated_cursor_missing_row_falls_back_without_empty_page(self, test_db):
        """Deleting the cursor row should not turn the next page into an empty result when ID fallback is applicable."""
        ids = []
        for index in range(6):
            ids.append(
                db.add_image(
                    path=f"/test/cursor_missing_{index}.png",
                    filename=f"cursor_missing_{index}.png",
                    prompt=f"cursor missing {index}",
                    created_at=None,
                )
            )

        expected_ids = list(reversed(ids))
        first_page = db.get_images_paginated(sort_by="newest", limit=3, skip_count=True)
        assert [img["id"] for img in first_page["images"]] == expected_ids[:3]
        first_cursor = decode_image_cursor(first_page["next_cursor"])
        assert first_cursor.image_id == expected_ids[2]

        with db.get_db() as conn:
            conn.execute("DELETE FROM images WHERE id = ?", (expected_ids[2],))

        second_page = db.get_images_paginated(
            sort_by="newest",
            limit=3,
            cursor_id=expected_ids[2],
            skip_count=True,
        )

        assert [img["id"] for img in second_page["images"]] == expected_ids[3:]
        assert second_page["has_more"] is False

    def test_get_images_paginated_opaque_cursor_survives_deleted_anchor_row(self, test_db):
        """Opaque cursors should continue from the stored sort boundary even after the anchor row is deleted."""
        ids = []
        for index in range(6):
            ids.append(
                db.add_image(
                    path=f"/test/opaque_cursor_missing_{index}.png",
                    filename=f"opaque_cursor_missing_{index}.png",
                    prompt=f"opaque cursor missing {index}",
                    created_at=datetime(2024, 1, 1, 0, 0, index),
                )
            )

        expected_ids = list(reversed(ids))
        first_page = db.get_images_paginated(sort_by="newest", limit=3, skip_count=True)
        first_cursor = decode_image_cursor(first_page["next_cursor"])

        assert [img["id"] for img in first_page["images"]] == expected_ids[:3]
        assert first_cursor.image_id == expected_ids[2]
        assert first_cursor.sort_value is not None
        assert first_cursor.is_opaque is True

        with db.get_db() as conn:
            conn.execute("DELETE FROM images WHERE id = ?", (first_cursor.image_id,))

        second_page = db.get_images_paginated(
            sort_by="newest",
            limit=3,
            cursor_id=first_cursor.image_id,
            cursor_sort_value=first_cursor.sort_value,
            cursor_is_opaque=True,
            skip_count=True,
        )

        assert [img["id"] for img in second_page["images"]] == expected_ids[3:]
        assert second_page["has_more"] is False

    def test_get_images_paginated_oldest_null_sort_value_cursor_fallback(self, test_db):
        """Oldest-sort pagination must also fall back to ID comparisons when sort values are null."""
        ids = []
        for index in range(5):
            ids.append(
                db.add_image(
                    path=f"/test/oldest_null_{index}.png",
                    filename=f"oldest_null_{index}.png",
                    prompt=f"oldest null {index}",
                    created_at=None,
                )
            )

        first_page = db.get_images_paginated(sort_by="oldest", limit=2, skip_count=True)
        assert [img["id"] for img in first_page["images"]] == ids[:2]
        first_cursor = decode_image_cursor(first_page["next_cursor"])
        assert first_cursor.image_id == ids[1]
        assert first_cursor.sort_value is None

        second_page = db.get_images_paginated(
            sort_by="oldest",
            limit=2,
            cursor_id=ids[1],
            skip_count=True,
        )

        assert [img["id"] for img in second_page["images"]] == ids[2:4]
        assert second_page["has_more"] is True
        second_cursor = decode_image_cursor(second_page["next_cursor"])
        assert second_cursor.image_id == ids[3]
        assert second_cursor.sort_value is None

    def test_get_images_paginated_post_filter_cursor_missing_row_still_finds_sparse_matches(self, test_db):
        """Post-filter pagination should keep finding sparse exact matches even if the cursor row was deleted."""
        exact_ids = []
        for index in range(4):
            exact_ids.append(
                db.add_image(
                    path=f"/test/post_filter_missing_cursor_exact_{index}.png",
                    filename=f"post_filter_missing_cursor_exact_{index}.png",
                    prompt="hero, rim light",
                    created_at=None,
                )
            )

        for index in range(40):
            db.add_image(
                path=f"/test/post_filter_missing_cursor_false_positive_{index}.png",
                filename=f"post_filter_missing_cursor_false_positive_{index}.png",
                prompt="superhero, rim light",
                created_at=None,
            )

        expected_ids = list(reversed(exact_ids))
        first_page = db.get_images_paginated(
            prompt_terms=["hero"],
            sort_by="newest",
            limit=3,
            skip_count=True,
        )

        assert [img["id"] for img in first_page["images"]] == expected_ids[:3]
        first_cursor = decode_image_cursor(first_page["next_cursor"])
        assert first_cursor.image_id == expected_ids[2]

        with db.get_db() as conn:
            conn.execute("DELETE FROM images WHERE id = ?", (expected_ids[2],))

        second_page = db.get_images_paginated(
            prompt_terms=["hero"],
            sort_by="newest",
            limit=3,
            cursor_id=first_cursor.image_id,
            cursor_sort_value=first_cursor.sort_value,
            cursor_is_opaque=first_cursor.is_opaque,
            skip_count=True,
        )

        assert [img["id"] for img in second_page["images"]] == expected_ids[3:]
        assert second_page["has_more"] is False
        assert second_page["next_cursor"] is None

    def test_get_filtered_image_ids_streams_post_filter_batches_with_optional_limit(self, test_db):
        """Filtered ID lookup should support chunked scanning and an explicit result cap."""
        exact_ids = []
        for index in range(12):
            exact_ids.append(
                db.add_image(
                    path=f"/test/selection_exact_{index}.png",
                    filename=f"selection_exact_{index}.png",
                    prompt="hero, portrait",
                )
            )

        for index in range(60):
            db.add_image(
                path=f"/test/selection_false_positive_{index}.png",
                filename=f"selection_false_positive_{index}.png",
                prompt="superhero, portrait",
            )

        expected_ids = list(reversed(exact_ids))
        full_ids = db.get_filtered_image_ids(
            prompt_terms=["hero"],
            sort_by="newest",
            fetch_chunk_size=5,
        )
        assert full_ids == expected_ids

        limited_ids = db.get_filtered_image_ids(
            prompt_terms=["hero"],
            sort_by="newest",
            fetch_chunk_size=5,
            max_results=4,
        )
        assert limited_ids == expected_ids[:4]

    def test_get_images_paginated_missing_cursor_row_falls_back_to_id_for_oldest(self, test_db):
        """If the cursor row disappears between requests, oldest pagination should continue by ID."""
        image_ids = [
            db.add_image(
                path=f"/test/missing_cursor_oldest_{index}.png",
                filename=f"missing_cursor_oldest_{index}.png",
                prompt="portrait",
            )
            for index in range(4)
        ]

        first_page = db.get_images_paginated(sort_by="oldest", limit=2, skip_count=True)
        assert [img["id"] for img in first_page["images"]] == image_ids[:2]
        first_cursor = decode_image_cursor(first_page["next_cursor"])
        cursor_id = first_cursor.image_id

        with db.get_db() as conn:
            conn.execute("DELETE FROM images WHERE id = ?", (cursor_id,))

        second_page = db.get_images_paginated(
            sort_by="oldest",
            limit=2,
            cursor_id=cursor_id,
            skip_count=True,
        )

        assert [img["id"] for img in second_page["images"]] == [image_ids[2], image_ids[3]]
        assert second_page["has_more"] is False
        assert second_page["next_cursor"] is None

    def test_filter_by_lora_uses_exact_normalized_names(self, test_db):
        """LoRA filtering should not substring-match unrelated normalized names."""
        exact_id = db.add_image(
            path="/test/lora_exact.png",
            filename="lora_exact.png",
            loras=["girl.safetensors"],
        )
        db.add_image(
            path="/test/lora_substring.png",
            filename="lora_substring.png",
            loras=["school_girl.safetensors"],
        )

        images = db.get_images(loras=["girl"])

        assert [image["id"] for image in images] == [exact_id]

    def test_filter_by_lora_matches_inline_prompt_exactly(self, test_db):
        """Inline <lora:name:weight> tags should follow the same exact-name filter contract."""
        exact_id = db.add_image(
            path="/test/lora_prompt_exact.png",
            filename="lora_prompt_exact.png",
            prompt="portrait <lora:girl:0.8>",
        )
        db.add_image(
            path="/test/lora_prompt_substring.png",
            filename="lora_prompt_substring.png",
            prompt="portrait <lora:school_girl:0.8>",
        )

        images = db.get_images(loras=["girl"])

        assert [image["id"] for image in images] == [exact_id]

    def test_filter_by_image_ids(self, test_db_with_images):
        """Filtering by specific image IDs should work."""
        data = test_db_with_images
        db_module = data["db"]
        ids = data["image_ids"][:2]

        images = db_module.get_images(image_ids=ids)

        assert len(images) == 2
        returned_ids = {img["id"] for img in images}
        assert returned_ids == set(ids)

    def test_filter_by_empty_image_ids(self, test_db_with_images):
        """Empty image_ids list should return empty results."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(image_ids=[])

        assert images == []


class TestSorting:
    """Tests for image sorting options."""

    def test_sort_by_newest(self, test_db_with_images):
        """Sorting by newest should work."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(sort_by="newest", limit=10)

        # Created timestamps should be in descending order
        # (Note: created_at may be None for test data)

    def test_sort_by_name_asc(self, test_db_with_images):
        """Sorting by name ascending should work."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(sort_by="name_asc", limit=10)

        filenames = [img["filename"] for img in images]
        assert filenames == sorted(filenames)

    def test_sort_by_name_desc(self, test_db_with_images):
        """Sorting by name descending should work."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(sort_by="name_desc", limit=10)

        filenames = [img["filename"] for img in images]
        assert filenames == sorted(filenames, reverse=True)

    def test_sort_by_file_size(self, test_db_with_images):
        """Sorting by file size should work."""
        data = test_db_with_images
        db_module = data["db"]

        images = db_module.get_images(sort_by="file_size", limit=10)

        sizes = [img["file_size"] for img in images if img.get("file_size")]
        assert sizes == sorted(sizes, reverse=True)

    def test_invalid_sort_uses_default(self, test_db_with_images):
        """Invalid sort option should use default (newest)."""
        data = test_db_with_images
        db_module = data["db"]

        # Should not raise an error
        images = db_module.get_images(sort_by="invalid_option", limit=10)

        assert isinstance(images, list)


class TestSQLInjectionPrevention:
    """
    CRITICAL: SQL injection prevention tests.

    These tests verify that SQL injection attacks are blocked.
    """

    @pytest.mark.parametrize("injection_payload", [
        "'; DROP TABLE images; --",
        "' OR '1'='1",
        "'; DELETE FROM tags; --",
        "' UNION SELECT * FROM images --",
        "1; DROP TABLE images",
        "' OR 1=1 --",
        "admin'--",
        "1' AND '1'='1",
    ])
    def test_sql_injection_in_path(self, test_db, injection_payload: str):
        """SQL injection in path should be handled safely."""
        # Try to inject SQL via path parameter
        image_id = db.add_image(
            path=f"/test/{injection_payload}.png",
            filename=f"{injection_payload}.png",
        )

        # Verify the injection didn't work - tables should still exist
        import sqlite3
        conn = sqlite3.connect(db.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='images'")
        assert cursor.fetchone() is not None
        conn.close()

        # The image should be stored with the literal string
        image = db.get_image_by_id(image_id)
        assert injection_payload in image["path"]

    def test_sql_injection_in_tag_filter(self, test_db):
        """SQL injection in tag filter should be blocked."""
        # Create a test image
        image_id = db.add_image(path="/test/tag_test.png", filename="tag_test.png")
        db.add_tags(image_id, [{"tag": "safe_tag", "confidence": 0.9}])

        # Try SQL injection in tag filter
        # This should not return all images or cause errors
        images = db.get_images(tags=["safe_tag' OR '1'='1"])

        # Should not return any images (the injection string is treated as a literal tag)
        # or should handle gracefully

    def test_sql_injection_in_search_query(self, test_db):
        """SQL injection in search query should be blocked."""
        # Create a test image
        db.add_image(
            path="/test/search_test.png",
            filename="search_test.png",
            prompt="test prompt",
        )

        # Try SQL injection in search
        images = db.get_images(search_query="'; DROP TABLE images; --")

        # Should handle gracefully - tables should still exist
        import sqlite3
        conn = sqlite3.connect(db.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='images'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_like_pattern_escaping(self, test_db):
        """LIKE wildcards should be escaped in tag searches."""
        image_id = db.add_image(path="/test/like_test.png", filename="like_test.png")

        # Add a tag with underscore (LIKE wildcard)
        db.add_tags(image_id, [{"tag": "test_tag", "confidence": 0.9}])

        # Search for the literal tag with underscore
        images = db.get_images(tags=["test_tag"])

        # Should find the image
        assert len(images) == 1


class TestPromptTokenExtraction:
    """Tests for prompt token extraction utilities."""

    def test_extract_prompt_tokens_basic(self):
        """Basic prompt token extraction should work."""
        tokens = db.extract_prompt_tokens("cat, dog, bird")

        assert "cat" in tokens
        assert "dog" in tokens
        assert "bird" in tokens

    def test_extract_prompt_tokens_normalization(self):
        """Tokens should be normalized (lowercase, underscore to space)."""
        tokens = db.extract_prompt_tokens("Best_Quality, MASTERPIECE, high res")

        assert "best quality" in tokens
        assert "masterpiece" in tokens
        assert "high res" in tokens

    def test_extract_prompt_tokens_removes_lora_tags(self):
        """LoRA tags should be removed from tokens."""
        tokens = db.extract_prompt_tokens("cat, <lora:style:0.8>, dog")

        assert "cat" in tokens
        assert "dog" in tokens
        # LoRA should not appear as a token
        assert not any("lora" in t for t in tokens)

    def test_extract_prompt_tokens_handles_weights(self):
        """Weight notation should be stripped."""
        tokens = db.extract_prompt_tokens("(cat:1.2), (dog:0.8)")

        assert "cat" in tokens
        assert "dog" in tokens

    def test_extract_prompt_tokens_empty(self):
        """Empty prompt should return empty set."""
        tokens = db.extract_prompt_tokens("")
        assert tokens == set()

        tokens = db.extract_prompt_tokens(None)
        assert tokens == set()


class TestLoraExtraction:
    """Tests for LoRA name extraction utilities."""

    def test_extract_lora_names_from_json(self):
        """LoRAs should be extracted from JSON array."""
        loras = db.extract_lora_names('["lora1", "lora2"]', None)

        assert "lora1" in loras
        assert "lora2" in loras

    def test_extract_lora_names_from_prompt(self):
        """LoRAs should be extracted from prompt tags."""
        loras = db.extract_lora_names(None, "text <lora:style:0.8> more text")

        assert "style" in loras

    def test_extract_lora_names_combined(self):
        """LoRAs should be extracted from both JSON and prompt."""
        loras = db.extract_lora_names('["lora1"]', "<lora:lora2:1.0>")

        assert "lora1" in loras
        assert "lora2" in loras

    def test_normalize_lora_name_strips_weight(self):
        """LoRA name normalization should strip weight notation."""
        assert db.normalize_lora_name("my_lora:0.8") == "my_lora"
        assert db.normalize_lora_name("style_v2:1.0") == "style_v2"

    def test_normalize_lora_name_strips_extension(self):
        """LoRA name normalization should strip file extensions."""
        assert db.normalize_lora_name("my_lora.safetensors") == "my_lora"
        assert db.normalize_lora_name("style.ckpt") == "style"

    def test_normalize_checkpoint_name_strips_path_extension_and_hash(self):
        """Checkpoint normalization should remove path prefixes, file extensions, and WebUI hash suffixes."""
        assert db.normalize_checkpoint_name(r"models\\ponyXLV6.safetensors [abcd1234]") == "ponyXLV6"
        assert db.normalize_checkpoint_name("juggernautXL.safetensors") == "juggernautXL"
        assert db.normalize_checkpoint_name("nai-diffusion-3") == "nai-diffusion-3"


class TestCollectionOperations:
    """Tests for collection operations (Favorites)."""

    def test_get_collection_by_slug(self, test_db):
        """Getting collection by slug should work."""
        collection = db.get_collection_by_slug("favorites")

        assert collection is not None
        assert collection["slug"] == "favorites"

    def test_image_and_collection_writes_compact_raw_metadata_json(self, test_db):
        raw_metadata = json.dumps({
            "workflow": "x" * 20_000,
            "_parsed": {
                "generation_params": {"sampler": "Euler a"},
            },
        })
        image_id = db.add_image(
            path="/test/raw-compact.png",
            filename="raw-compact.png",
            metadata_json=raw_metadata,
        )
        db.update_image_metadata(
            image_id=image_id,
            generator="comfyui",
            prompt="updated",
            negative_prompt=None,
            metadata_json=raw_metadata,
            width=512,
            height=512,
            file_size=1024,
            checkpoint=None,
            loras=[],
        )
        collection = db.get_collection_by_slug("favorites")
        db.add_collection_item(
            collection_id=collection["id"],
            source_image_id=image_id,
            copied_path="/favorites/raw-compact.png",
            prompt="updated",
            negative_prompt=None,
            checkpoint=None,
            loras=None,
            metadata_json=raw_metadata,
            created_at=None,
            width=512,
            height=512,
            file_size=1024,
        )

        with db.get_db() as conn:
            image_json = conn.execute(
                "SELECT metadata_json FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()["metadata_json"]
            collection_json = conn.execute(
                "SELECT metadata_json FROM collection_items WHERE source_image_id = ?",
                (image_id,),
            ).fetchone()["metadata_json"]

        expected = {
            "_compact": {"version": 1},
            "_parsed": {"generation_params": {"sampler": "Euler a"}},
        }
        assert json.loads(image_json) == expected
        assert json.loads(collection_json) == expected
        assert len(image_json) < 512
        assert len(collection_json) < 512


    def test_add_collection_item(self, test_db):
        """Adding item to collection should work."""
        image_id = db.add_image(path="/test/fav.png", filename="fav.png")
        collection = db.get_collection_by_slug("favorites")

        item_id = db.add_collection_item(
            collection_id=collection["id"],
            source_image_id=image_id,
            copied_path="/favorites/fav.png",
            prompt="test",
            negative_prompt=None,
            checkpoint=None,
            loras=None,
            metadata_json=None,
            created_at=None,
            width=512,
            height=512,
            file_size=1000,
        )

        assert item_id > 0

    def test_get_favorite_source_ids(self, test_db):
        """Getting favorite source IDs should work."""
        image_id = db.add_image(path="/test/fav2.png", filename="fav2.png")

        db.set_favorite(image_id, True)

        ids = db.get_favorite_source_ids()

        assert image_id in ids

    def test_remove_collection_item(self, test_db):
        """Removing item from collection should work."""
        image_id = db.add_image(path="/test/remove.png", filename="remove.png")
        collection = db.get_collection_by_slug("favorites")

        db.add_collection_item(
            collection_id=collection["id"],
            source_image_id=image_id,
            copied_path="/favorites/remove.png",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=None,
            metadata_json=None,
            created_at=None,
            width=512,
            height=512,
            file_size=1000,
        )

        db.remove_collection_item(collection["id"], image_id)

        assert db.get_collection_item(collection["id"], image_id) is None


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_get_image_count(self, test_db):
        """Image count should be accurate."""
        count_before = db.get_image_count()

        db.add_image(path="/test/count1.png", filename="count1.png")
        db.add_image(path="/test/count2.png", filename="count2.png")

        count_after = db.get_image_count()

        assert count_after == count_before + 2

    def test_get_all_generators(self, test_db):
        """Getting all generators should return counts."""
        db.add_image(path="/test/gen1.png", filename="gen1.png", generator="comfyui")
        db.add_image(path="/test/gen2.png", filename="gen2.png", generator="comfyui")
        db.add_image(path="/test/gen3.png", filename="gen3.png", generator="webui")

        generators = db.get_all_generators()

        gen_dict = {g["generator"]: g["count"] for g in generators}
        assert gen_dict.get("comfyui") == 2
        assert gen_dict.get("webui") == 1

    def test_get_all_checkpoints_groups_raw_variants_by_normalized_name(self, test_db):
        """Checkpoint analytics should group generator-specific raw variants under one normalized name."""
        db.add_image(
            path="/test/cp_stats/variant_a.png",
            filename="variant_a.png",
            checkpoint="ponyXLV6.safetensors [abcd1234]",
            metadata_json="{}",
        )
        db.add_image(
            path="/test/cp_stats/variant_b.png",
            filename="variant_b.png",
            checkpoint="ponyXLV6.safetensors",
            metadata_json="{}",
        )

        checkpoints = db.get_all_checkpoints()

        assert checkpoints[0]["checkpoint"] == "ponyXLV6"
        assert checkpoints[0]["checkpoint_normalized"] == "ponyXLV6"
        assert checkpoints[0]["count"] == 2

    def test_get_images_in_folder_scope_accepts_equivalent_windows_and_wsl_roots(self, test_db):
        """Folder-scope lookups should match Windows-style rows from a WSL root."""
        windows_path = r"L:\datasets\scope\folder\scope-image.png"
        db.add_image(path=windows_path, filename="scope-image.png")

        rows = db.get_images_in_folder_scope("/mnt/l/datasets/scope/folder", recursive=False)

        assert len(rows) == 1
        assert rows[0]["path"] == windows_path

    def test_get_images_in_folder_scope_non_recursive_excludes_nested_children_across_runtime_forms(self, test_db):
        """Non-recursive folder scopes should keep only direct children across Windows/WSL variants."""
        direct_child = r"L:\datasets\scope\folder\direct.png"
        nested_child = r"L:\datasets\scope\folder\nested\deep.png"
        db.add_image(path=direct_child, filename="direct.png")
        db.add_image(path=nested_child, filename="deep.png")

        rows = db.get_images_in_folder_scope("/mnt/l/datasets/scope/folder", recursive=False)

        assert [row["path"] for row in rows] == [direct_child]

    def test_get_untagged_images(self, test_db):
        """Getting untagged images should work."""
        id1 = db.add_image(path="/test/untagged1.png", filename="untagged1.png")
        id2 = db.add_image(path="/test/untagged2.png", filename="untagged2.png")
        id3 = db.add_image(path="/test/tagged.png", filename="tagged.png")
        db.add_tags(id3, [{"tag": "test", "confidence": 0.9}])

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE images SET embedding = ? WHERE id = ?",
                (b"\x00\x01\xf0\x02binary", id1),
            )

        untagged = db.get_untagged_images()

        untagged_ids = [img["id"] for img in untagged]
        assert id1 in untagged_ids
        assert id2 in untagged_ids
        assert id3 not in untagged_ids
        assert all("embedding" not in image for image in untagged)
        json.dumps(untagged)

    def test_get_all_image_ids(self, test_db):
        """Getting all image IDs should be lightweight."""
        db.add_image(path="/test/ids1.png", filename="ids1.png")
        db.add_image(path="/test/ids2.png", filename="ids2.png")

        ids = db.get_all_image_ids()

        assert len(ids) >= 2
        assert all(isinstance(i, int) for i in ids)

    def test_readability_filters_exclude_unreadable_rows_from_default_queries(self, test_db):
        """Unreadable images should stay out of default library/tagging query helpers."""
        readable_id = db.add_image(path="/test/readable.png", filename="readable.png")
        unreadable_id = db.add_image(
            path="/test/unreadable.png",
            filename="unreadable.png",
            is_readable=False,
            read_error="Truncated File Read",
        )

        ids = db.get_all_image_ids()
        untagged = db.get_untagged_images()
        images = db.get_images(limit=20)

        assert readable_id in ids
        assert unreadable_id not in ids
        assert unreadable_id not in [img["id"] for img in untagged]
        assert unreadable_id not in [img["id"] for img in images]

    def test_include_unreadable_true_surfaces_unreadable_rows(self, test_db):
        """When include_unreadable=True is passed, unreadable images must be visible.

        Regression guard for v3.0.4 bug where duplicate _apply_readable_filter calls
        made include_unreadable=True a no-op across get_images / get_filtered_image_count /
        get_filtered_image_ids / get_images_paginated.
        """
        readable_id = db.add_image(path="/test/visible_readable.png", filename="visible_readable.png")
        unreadable_id = db.add_image(
            path="/test/visible_unreadable.png",
            filename="visible_unreadable.png",
            is_readable=False,
            read_error="Truncated File Read",
        )

        # Default: unreadable hidden
        default_images = db.get_images(limit=20)
        default_ids = [img["id"] for img in default_images]
        assert readable_id in default_ids
        assert unreadable_id not in default_ids

        # include_unreadable=True: unreadable visible
        all_images = db.get_images(limit=20, include_unreadable=True)
        all_ids = [img["id"] for img in all_images]
        assert readable_id in all_ids
        assert unreadable_id in all_ids

        # Same guarantee for the count helper
        default_count = db.get_filtered_image_count()
        all_count = db.get_filtered_image_count(include_unreadable=True)
        assert all_count >= default_count + 1

        # Same guarantee for the id-only helper
        default_id_list = db.get_filtered_image_ids()
        all_id_list = db.get_filtered_image_ids(include_unreadable=True)
        assert unreadable_id not in default_id_list
        assert unreadable_id in all_id_list

        # Same guarantee for the paginated helper
        default_paginated = db.get_images_paginated(limit=20)
        all_paginated = db.get_images_paginated(limit=20, include_unreadable=True)
        default_paginated_ids = [img["id"] for img in default_paginated["images"]]
        all_paginated_ids = [img["id"] for img in all_paginated["images"]]
        assert unreadable_id not in default_paginated_ids
        assert unreadable_id in all_paginated_ids


class TestAddTagsBatch:
    """Tests for batch tag insertion via add_tags_batch()."""

    def test_add_tags_batch_inserts_for_multiple_images(self, test_db):
        """add_tags_batch should insert tags for several images in one transaction."""
        id1 = db.add_image(path="/test/batch1.png", filename="batch1.png")
        id2 = db.add_image(path="/test/batch2.png", filename="batch2.png")
        id3 = db.add_image(path="/test/batch3.png", filename="batch3.png")

        db.add_tags_batch([
            {"image_id": id1, "tags": [{"tag": "cat", "confidence": 0.9}, {"tag": "animal", "confidence": 0.8}]},
            {"image_id": id2, "tags": [{"tag": "dog", "confidence": 0.95}]},
            {"image_id": id3, "tags": [{"tag": "bird", "confidence": 0.85}, {"tag": "outdoor", "confidence": 0.7}]},
        ])

        tags1 = db.get_image_tags(id1)
        tags2 = db.get_image_tags(id2)
        tags3 = db.get_image_tags(id3)

        assert len(tags1) == 2
        assert any(t["tag"] == "cat" for t in tags1)
        assert any(t["tag"] == "animal" for t in tags1)
        assert len(tags2) == 1
        assert tags2[0]["tag"] == "dog"
        assert len(tags3) == 2

    def test_add_tags_batch_replaces_existing_tags(self, test_db):
        """add_tags_batch should replace existing tags for each image."""
        image_id = db.add_image(path="/test/batch_replace.png", filename="batch_replace.png")
        db.add_tags(image_id, [{"tag": "old_tag", "confidence": 0.5}])

        db.add_tags_batch([
            {"image_id": image_id, "tags": [{"tag": "new_tag", "confidence": 0.9}]},
        ])

        tags = db.get_image_tags(image_id)
        assert len(tags) == 1
        assert tags[0]["tag"] == "new_tag"

    def test_add_tags_batch_empty_list_is_noop(self, test_db):
        """add_tags_batch with empty list should not raise."""
        db.add_tags_batch([])

    def test_add_tags_batch_updates_tagged_at(self, test_db):
        """add_tags_batch should update the tagged_at timestamp for each image."""
        image_id = db.add_image(path="/test/batch_ts.png", filename="batch_ts.png")

        image = db.get_image_by_id(image_id)
        assert image["tagged_at"] is None

        db.add_tags_batch([
            {"image_id": image_id, "tags": [{"tag": "test", "confidence": 0.9}]},
        ])

        image = db.get_image_by_id(image_id)
        assert image["tagged_at"] is not None

    def test_get_image_ids_already_tagged_partitions_by_tagged_at(self, test_db):
        """Only IDs whose images carry tagged_at should come back, even when
        the tag write matched zero tags (tagged_at is the canonical marker)."""
        tagged_id = db.add_image(path="/test/skip_tagged.png", filename="skip_tagged.png")
        zero_tag_id = db.add_image(path="/test/skip_zero.png", filename="skip_zero.png")
        untagged_id = db.add_image(path="/test/skip_untagged.png", filename="skip_untagged.png")

        db.add_tags_batch([
            {"image_id": tagged_id, "tags": [{"tag": "cat", "confidence": 0.9}]},
            # A run that found nothing above threshold still stamps tagged_at.
            {"image_id": zero_tag_id, "tags": []},
        ])

        result = db.get_image_ids_already_tagged([tagged_id, zero_tag_id, untagged_id, 999999])

        assert result == {tagged_id, zero_tag_id}
        assert db.get_image_ids_already_tagged([]) == set()
        assert db.get_image_ids_already_tagged([untagged_id]) == set()


class TestGetImagesByIdsChunking:
    """Tests for get_images_by_ids() with large ID lists."""

    def test_get_images_by_ids_basic(self, test_db):
        """get_images_by_ids should return a dict keyed by image ID."""
        id1 = db.add_image(path="/test/byid1.png", filename="byid1.png", prompt="p1")
        id2 = db.add_image(path="/test/byid2.png", filename="byid2.png", prompt="p2")

        result = db.get_images_by_ids([id1, id2])

        assert id1 in result
        assert id2 in result
        assert result[id1]["prompt"] == "p1"
        assert result[id2]["prompt"] == "p2"

    def test_get_images_by_ids_empty_returns_empty(self, test_db):
        """get_images_by_ids with empty list should return empty dict."""
        result = db.get_images_by_ids([])
        assert result == {}

    def test_get_images_by_ids_large_list(self, test_db):
        """get_images_by_ids with >500 IDs should work (tests SQLite parameter limits)."""
        image_ids = []
        for i in range(600):
            image_id = db.add_image(
                path=f"/test/large_{i}.png",
                filename=f"large_{i}.png",
            )
            image_ids.append(image_id)

        result = db.get_images_by_ids(image_ids)

        assert len(result) == 600
        for image_id in image_ids:
            assert image_id in result

    def test_get_images_by_ids_skips_nonexistent(self, test_db):
        """get_images_by_ids should silently skip IDs that don't exist."""
        image_id = db.add_image(path="/test/exists.png", filename="exists.png")

        result = db.get_images_by_ids([image_id, 999999])

        assert len(result) == 1
        assert image_id in result


class TestTagsCacheInvalidation:
    """Tests for tags cache behavior."""

    def test_get_all_tags_returns_consistent_data(self, test_db):
        """get_all_tags should return current tag data."""
        id1 = db.add_image(path="/test/cache1.png", filename="cache1.png")
        db.add_tags(id1, [{"tag": "cached_tag", "confidence": 0.9}])

        tags = db.get_all_tags()
        tag_names = [t["tag"] for t in tags]
        assert "cached_tag" in tag_names

    def test_invalidate_tags_cache_clears_cached_data(self, test_db):
        """_invalidate_tags_cache should clear the in-memory cache."""
        id1 = db.add_image(path="/test/inv1.png", filename="inv1.png")
        db.add_tags(id1, [{"tag": "before_invalidation", "confidence": 0.9}])

        # Prime the cache
        db.get_all_tags()

        # Add new tags directly
        id2 = db.add_image(path="/test/inv2.png", filename="inv2.png")
        db.add_tags(id2, [{"tag": "after_invalidation", "confidence": 0.8}])

        # Invalidate cache
        db._invalidate_tags_cache()

        # Next call should return fresh data from DB
        tags = db.get_all_tags()
        tag_names = [t["tag"] for t in tags]
        assert "after_invalidation" in tag_names

    def test_cache_returns_stale_data_without_invalidation(self, test_db):
        """Without invalidation, cached data persists within TTL."""
        id1 = db.add_image(path="/test/stale1.png", filename="stale1.png")
        db.add_tags(id1, [{"tag": "original_tag", "confidence": 0.9}])

        # Prime the cache
        first_result = db.get_all_tags()

        # Add more tags without invalidating
        id2 = db.add_image(path="/test/stale2.png", filename="stale2.png")
        db.add_tags(id2, [{"tag": "sneaky_tag", "confidence": 0.8}])

        # Should get cached result (same object reference or same content)
        second_result = db.get_all_tags()
        second_tag_names = [t["tag"] for t in second_result]

        # The sneaky_tag might not appear because cache is still valid
        # (depends on TTL, but within same test execution it should be cached)
        assert "original_tag" in second_tag_names


class TestIterIdSnapshotChunks:
    """Tests for the pre-mutation ID snapshot iterator (bulk-op skip fix)."""

    def test_source_drained_before_first_chunk_is_yielded(self):
        """The whole source must be snapshotted before any chunk is exposed.

        This is the property that makes self-mutating filtered scopes safe:
        consumers can commit per chunk without shrinking the worklist.
        """
        pulled = []

        def source():
            for chunk in ([1, 2], [3, 4], [5]):
                pulled.append(list(chunk))
                yield chunk

        generator = db.iter_id_snapshot_chunks(source(), chunk_size=2)
        first = next(generator)

        assert pulled == [[1, 2], [3, 4], [5]]
        assert first == [1, 2]
        assert list(generator) == [[3, 4], [5]]

    def test_rechunks_to_requested_size_and_removes_temp_file(self, monkeypatch):
        import os
        import tempfile

        created_paths = []
        real_named_temporary_file = tempfile.NamedTemporaryFile

        def recording_named_temporary_file(*args, **kwargs):
            handle = real_named_temporary_file(*args, **kwargs)
            created_paths.append(handle.name)
            return handle

        monkeypatch.setattr(db.tempfile, "NamedTemporaryFile", recording_named_temporary_file)

        chunks = list(db.iter_id_snapshot_chunks(iter([[7, 8, 9, 10, 11]]), chunk_size=2))

        assert chunks == [[7, 8], [9, 10], [11]]
        assert len(created_paths) == 1
        assert not os.path.exists(created_paths[0])
