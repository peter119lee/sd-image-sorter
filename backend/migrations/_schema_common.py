"""
Shared schema helpers for SQLite migrations.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from config import (
    FAVORITES_COLLECTION_NAME,
    FAVORITES_COLLECTION_SLUG,
    FAVORITES_FOLDER_PATH,
)


FULL_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS images (
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
        checkpoint TEXT,
        checkpoint_normalized TEXT,
        loras TEXT,
        embedding BLOB,
        ai_caption TEXT,
        nl_caption TEXT,
        sidecar_caption TEXT,
        model_hash TEXT,
        aesthetic_score REAL,
        is_readable INTEGER DEFAULT 1,
        read_error TEXT,
        source_mtime_ns INTEGER,
        source_size INTEGER,
        metadata_status TEXT DEFAULT 'complete',
        content_fingerprint TEXT,
        library_order_time DATETIME,
        source_file_mtime DATETIME,
        created_at DATETIME,
        indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        tagged_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        folder_path TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS collection_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_id INTEGER NOT NULL,
        source_image_id INTEGER NOT NULL,
        copied_path TEXT NOT NULL,
        prompt TEXT,
        negative_prompt TEXT,
        checkpoint TEXT,
        loras TEXT,
        metadata_json TEXT,
        created_at DATETIME,
        width INTEGER,
        height INTEGER,
        file_size INTEGER,
        added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(collection_id, source_image_id),
        FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
        FOREIGN KEY (source_image_id) REFERENCES images(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_id INTEGER NOT NULL,
        tag TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        source TEXT,
        category TEXT,
        FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tag_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tag TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL,
        subcategory TEXT,
        is_user_defined INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tag_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        category TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tag_set_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        set_id INTEGER NOT NULL,
        tag TEXT NOT NULL,
        weight REAL DEFAULT 1.0,
        is_required INTEGER DEFAULT 1,
        FOREIGN KEY (set_id) REFERENCES tag_sets(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tag_exclusions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_name TEXT NOT NULL,
        description TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tag_exclusion_conditions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exclusion_id INTEGER NOT NULL,
        condition_tag TEXT NOT NULL,
        condition_type TEXT DEFAULT 'present',
        FOREIGN KEY (exclusion_id) REFERENCES tag_exclusions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tag_exclusion_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exclusion_id INTEGER NOT NULL,
        excluded_tag TEXT,
        excluded_category TEXT,
        FOREIGN KEY (exclusion_id) REFERENCES tag_exclusions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prompt_presets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        config_json TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artist_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_id INTEGER NOT NULL UNIQUE,
        artist TEXT NOT NULL,
        confidence REAL NOT NULL,
        top_predictions TEXT,
        identified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS image_loras (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_id INTEGER NOT NULL,
        lora_name TEXT NOT NULL,
        FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
        UNIQUE(image_id, lora_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS image_prompt_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_id INTEGER NOT NULL,
        token TEXT NOT NULL,
        FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
        UNIQUE(image_id, token)
    )
    """,
)


INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_images_generator ON images(generator)",
    "CREATE INDEX IF NOT EXISTS idx_images_path ON images(path)",
    "CREATE INDEX IF NOT EXISTS idx_images_path_lower ON images(LOWER(path))",
    "CREATE INDEX IF NOT EXISTS idx_tag_categories_tag ON tag_categories(tag)",
    "CREATE INDEX IF NOT EXISTS idx_tag_categories_category ON tag_categories(category)",
    "CREATE INDEX IF NOT EXISTS idx_tag_set_members_set ON tag_set_members(set_id)",
    "CREATE INDEX IF NOT EXISTS idx_images_embedding ON images(embedding IS NOT NULL) WHERE embedding IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_images_library_order_time ON images(library_order_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_artist_predictions_artist ON artist_predictions(artist)",
    "CREATE INDEX IF NOT EXISTS idx_artist_predictions_image_id ON artist_predictions(image_id)",
    "CREATE INDEX IF NOT EXISTS idx_collections_slug ON collections(slug)",
    "CREATE INDEX IF NOT EXISTS idx_collection_items_collection_id ON collection_items(collection_id)",
    "CREATE INDEX IF NOT EXISTS idx_collection_items_source_image_id ON collection_items(source_image_id)",
    "CREATE INDEX IF NOT EXISTS idx_images_checkpoint ON images(checkpoint) WHERE checkpoint IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_images_checkpoint_normalized ON images(checkpoint_normalized COLLATE NOCASE) WHERE checkpoint_normalized IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_images_tagged_at ON images(tagged_at) WHERE tagged_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_tags_tag_image ON tags(tag, image_id)",
    "CREATE INDEX IF NOT EXISTS idx_tags_image_id_tag ON tags(image_id, tag)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_unique_image_tag ON tags(image_id, tag)",
    "CREATE INDEX IF NOT EXISTS idx_images_filename ON images(filename)",
    "CREATE INDEX IF NOT EXISTS idx_images_model_hash ON images(model_hash) WHERE model_hash IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_images_readable ON images(is_readable)",
    "CREATE INDEX IF NOT EXISTS idx_images_metadata_status ON images(metadata_status)",
    "CREATE INDEX IF NOT EXISTS idx_image_loras_lora_name ON image_loras(lora_name)",
    "CREATE INDEX IF NOT EXISTS idx_image_loras_image_id ON image_loras(image_id)",
    "CREATE INDEX IF NOT EXISTS idx_image_prompt_tokens_token ON image_prompt_tokens(token)",
    "CREATE INDEX IF NOT EXISTS idx_image_prompt_tokens_image_id ON image_prompt_tokens(image_id)",
)


DROP_REDUNDANT_INDEXES: tuple[str, ...] = (
    "DROP INDEX IF EXISTS idx_tags_tag",
    "DROP INDEX IF EXISTS idx_tags_image_id",
)


LEGACY_IMAGE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("checkpoint", "TEXT"),
    ("checkpoint_normalized", "TEXT"),
    ("loras", "TEXT"),
    ("embedding", "BLOB"),
    ("ai_caption", "TEXT"),
    # v3.3.3: pure VLM natural-language caption, kept separate from the fused
    # ai_caption so the dataset maker can show / export booru tags and the
    # natural-language sentence independently (point 1/2/3).
    ("nl_caption", "TEXT"),
    # v3.5.x: caption text lifted from a .txt/.json sidecar next to the image.
    # Kept apart from `prompt` (the SD generation prompt) because sidecar text
    # is usually a human/tagger tag list, and apart from ai_caption/nl_caption
    # because nothing in this app generated it (migration 042).
    ("sidecar_caption", "TEXT"),
    ("model_hash", "TEXT"),
    ("aesthetic_score", "REAL"),
    ("is_readable", "INTEGER DEFAULT 1"),
    ("read_error", "TEXT"),
    ("source_mtime_ns", "INTEGER"),
    ("source_size", "INTEGER"),
    ("metadata_status", "TEXT DEFAULT 'complete'"),
    ("content_fingerprint", "TEXT"),
    ("library_order_time", "DATETIME"),
    ("source_file_mtime", "DATETIME"),
    # v3.2.2: timestamp columns that exist in FULL_SCHEMA but were
    # missing from the legacy upgrade list. Without these, init_db()
    # on a sufficiently-old DB (e.g. one created before tagged_at /
    # indexed_at were introduced) failed with
    # ``sqlite3.OperationalError: no such column: tagged_at`` while
    # creating the partial-index ``idx_images_tagged_at``. Adding
    # them to the legacy backfill list keeps upgrades from very old
    # installations working.
    #
    # Note: ALTER TABLE ADD COLUMN cannot use non-constant defaults
    # (e.g. ``DEFAULT CURRENT_TIMESTAMP``), so we add ``indexed_at``
    # without a default. New rows from CREATE TABLE in FULL_SCHEMA get
    # CURRENT_TIMESTAMP; rows backfilled by this legacy path stay NULL
    # until the user re-scans.
    ("created_at", "DATETIME"),
    ("indexed_at", "DATETIME"),
    ("tagged_at", "DATETIME"),
    # v3.2.1 color analysis columns
    ("dominant_colors", "TEXT"),
    ("avg_brightness", "REAL"),
    ("color_temperature", "TEXT"),
    ("color_saturation", "REAL"),
    ("brightness_histogram", "TEXT"),
    ("brightness_skew", "REAL"),
    ("brightness_distribution", "TEXT"),
    # v3.5.0 dominant-hue tags (",red,white," wrapped list derived from
    # dominant_colors; backfilled from existing JSON by migration 022)
    ("dominant_color_tags", "TEXT"),
    # v3.5.0 metadata L3: gzipped raw ComfyUI prompt-chunk JSON, stored ONLY
    # when parsing failed to recover a positive prompt — parser upgrades can
    # then re-parse from the database even after the source file is gone
    # (migration 023).
    ("raw_metadata_gz", "BLOB"),
)


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def apply_statements(conn: sqlite3.Connection, statements: Iterable[str]) -> None:
    cursor = conn.cursor()
    for statement in statements:
        cursor.execute(statement)


def create_full_schema(conn: sqlite3.Connection) -> None:
    apply_statements(conn, FULL_SCHEMA_STATEMENTS)
    apply_statements(conn, INDEX_STATEMENTS)
    apply_statements(conn, DROP_REDUNDANT_INDEXES)
    conn.execute(
        """
        INSERT OR IGNORE INTO collections (slug, name, folder_path)
        VALUES (?, ?, ?)
        """,
        (FAVORITES_COLLECTION_SLUG, FAVORITES_COLLECTION_NAME, FAVORITES_FOLDER_PATH),
    )


def add_missing_legacy_image_columns(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "images"):
        return

    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(images)").fetchall()
    }
    for column_name, column_sql in LEGACY_IMAGE_COLUMNS:
        if column_name in existing_columns:
            continue
        conn.execute(f"ALTER TABLE images ADD COLUMN {column_name} {column_sql}")
