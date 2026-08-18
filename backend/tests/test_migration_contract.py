"""Contract checks for migration isolation and frozen helpers."""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path


def _load_migration_006_module():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "006_prompt_token_index.py"
    spec = importlib.util.spec_from_file_location("migration_006_prompt_token_index", migration_path)
    assert spec and spec.loader, "failed to load migration 006 module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_006_does_not_import_database_module_directly():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "006_prompt_token_index.py"
    source = migration_path.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:from\s+database\s+import|import\s+database)\b", source, re.MULTILINE), (
        "Migration 006 must remain self-contained and must not import runtime database helpers."
    )


def test_migration_006_tokenizer_matches_frozen_examples():
    migration = _load_migration_006_module()

    extract = migration._extract_prompt_tokens_v1
    assert extract("Best_Quality, MASTERPIECE, high res") == {"best quality", "masterpiece", "high res"}
    assert extract("cat, <lora:style:0.8>, dog") == {"cat", "dog"}
    assert extract("(cat:1.2), (dog:0.8)") == {"cat", "dog"}
    assert extract("") == set()


def _load_migration_003_module():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "003_legacy_backfills.py"
    spec = importlib.util.spec_from_file_location("migration_003_legacy_backfills", migration_path)
    assert spec and spec.loader, "failed to load migration 003 module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_003_does_not_import_database_module_directly():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "003_legacy_backfills.py"
    source = migration_path.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:from\s+database\s+import|import\s+database)\b", source, re.MULTILINE), (
        "Migration 003 must remain self-contained and must not import runtime database helpers."
    )


def test_migration_003_lora_extractor_matches_frozen_examples():
    migration = _load_migration_003_module()

    extract = migration._extract_lora_names_v1
    assert extract('["Anima\\\\anime\\\\My_Lora.safetensors", "bad"]', "") == {"my_lora", "bad"}
    assert extract("", "girl, <lora:StylePack:0.8>, <lora:path/name.ckpt:1>") == {"stylepack", "name"}
    assert extract("not-json", "<lora:abc:1>") == {"abc"}


def _load_migration_008_module():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "008_compact_persisted_metadata.py"
    spec = importlib.util.spec_from_file_location("migration_008_compact_persisted_metadata", migration_path)
    assert spec and spec.loader, "failed to load migration 008 module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_migration_009_module():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "009_compact_raw_only_metadata.py"
    spec = importlib.util.spec_from_file_location("migration_009_compact_raw_only_metadata", migration_path)
    assert spec and spec.loader, "failed to load migration 009 module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_008_does_not_import_runtime_database_helpers():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "008_compact_persisted_metadata.py"
    source = migration_path.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:from\s+database\s+import|import\s+database)\b", source, re.MULTILINE)
    assert "metadata_storage" not in source


def test_migration_008_compacts_heavy_metadata_payloads():
    migration = _load_migration_008_module()
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, metadata_json TEXT)")
        conn.execute("CREATE TABLE collection_items (id INTEGER PRIMARY KEY, metadata_json TEXT)")
        raw_payload = json.dumps({
            "prompt": {"huge": "x" * 10000},
            "workflow": {"nodes": ["y" * 10000]},
            "Comment": json.dumps({"prompt": "raw"}),
            "_parsed": {
                "generation_params": {"steps": 28, "model": "model.safetensors"},
                "prompt_nodes": [{"id": "1", "text": "kept summary"}],
            },
        })
        conn.execute("INSERT INTO images (metadata_json) VALUES (?)", (raw_payload,))
        conn.execute("INSERT INTO collection_items (metadata_json) VALUES (?)", (raw_payload,))

        assert migration.apply(conn) is True

        for table_name in ("images", "collection_items"):
            stored = conn.execute(f"SELECT metadata_json FROM {table_name}").fetchone()[0]
            parsed = json.loads(stored)
            assert parsed["_compact"]["version"] == 1
            assert parsed["_parsed"]["generation_params"]["steps"] == 28
            assert "prompt" not in parsed
            assert "workflow" not in parsed
            assert "Comment" not in parsed
            assert len(stored) < 512
    finally:
        conn.close()


def test_migration_008_compacts_raw_only_exif_xmp_payloads_without_parsed_summary():
    migration = _load_migration_008_module()
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, metadata_json TEXT)")
        raw_payload = json.dumps({
            "xmp": "<x:xmpmeta>" + ("x" * 10000) + "</x:xmpmeta>",
            "Description": "raw description " + ("y" * 10000),
            "ExifVersion": "0231",
        })
        conn.execute("INSERT INTO images (metadata_json) VALUES (?)", (raw_payload,))

        assert migration.apply(conn) is True

        stored = conn.execute("SELECT metadata_json FROM images").fetchone()[0]
        parsed = json.loads(stored)
        assert parsed == {"_compact": {"version": 1}}
        assert len(stored) < 64
    finally:
        conn.close()


def test_migration_008_skips_already_compact_rows_without_parsed_summary():
    migration = _load_migration_008_module()
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, metadata_json TEXT)")
        compact_payload = json.dumps({"_compact": {"version": 1}})
        conn.execute("INSERT INTO images (metadata_json) VALUES (?)", (compact_payload,))

        assert migration.apply(conn) is False

        stored = conn.execute("SELECT metadata_json FROM images").fetchone()[0]
        assert json.loads(stored) == {"_compact": {"version": 1}}
    finally:
        conn.close()


def test_migration_009_does_not_import_runtime_database_helpers():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "009_compact_raw_only_metadata.py"
    source = migration_path.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:from\s+database\s+import|import\s+database)\b", source, re.MULTILINE)
    assert "metadata_storage" not in source


def test_migration_009_compacts_raw_only_rows_missed_after_schema_version_8():
    migration = _load_migration_009_module()
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, metadata_json TEXT)")
        conn.execute("CREATE TABLE collection_items (id INTEGER PRIMARY KEY, metadata_json TEXT)")
        raw_payload = json.dumps({
            "xmp": "<x:xmpmeta>" + ("x" * 10000) + "</x:xmpmeta>",
            "Description": "raw EXIF description " + ("y" * 10000),
            "ExifVersion": "0231",
        })
        conn.execute("INSERT INTO images (metadata_json) VALUES (?)", (raw_payload,))
        conn.execute("INSERT INTO collection_items (metadata_json) VALUES (?)", (raw_payload,))

        assert migration.apply(conn) is True

        for table_name in ("images", "collection_items"):
            stored = conn.execute(f"SELECT metadata_json FROM {table_name}").fetchone()[0]
            assert json.loads(stored) == {"_compact": {"version": 1}}
            assert len(stored) < 64
    finally:
        conn.close()


def test_migration_009_preserves_compact_rows_and_unreadable_legacy_json():
    migration = _load_migration_009_module()
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, metadata_json TEXT)")
        compact_payload = json.dumps({"_compact": {"version": 1}})
        conn.execute("INSERT INTO images (metadata_json) VALUES (?)", (compact_payload,))
        conn.execute("INSERT INTO images (metadata_json) VALUES (?)", ("not-json",))

        assert migration.apply(conn) is False

        rows = [row[0] for row in conn.execute("SELECT metadata_json FROM images ORDER BY id").fetchall()]
        assert json.loads(rows[0]) == {"_compact": {"version": 1}}
        assert rows[1] == "not-json"
    finally:
        conn.close()


def test_migrations_are_unique_and_strictly_increasing():
    """Migration versions should stay deterministic and monotonic.

    Migrations are numbered and forward-only, so a duplicate or out-of-order
    version means the upgrade path depends on dict ordering rather than on the
    number written in the filename.
    """
    import migrations

    migration_list = migrations.get_migrations()
    versions = [migration.version for migration in migration_list]

    assert versions
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def test_new_database_schema_version_matches_latest_migration(test_db):
    """A fresh database must land on the newest migration, not part-way up.

    Stopping short leaves a new install claiming a schema version it does not
    have, and the next upgrade then skips the migrations in between.
    """
    import migrations
    import database as db

    latest_version = migrations.get_migrations()[-1].version

    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT version FROM schema_version WHERE id = 1")
        row = cursor.fetchone()

    assert row is not None
    assert int(row["version"]) == latest_version
