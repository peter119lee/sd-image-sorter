"""
SQLite database for storing image metadata and tags.
"""
import sqlite3
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

DATABASE_PATH = os.path.join(os.path.dirname(__file__), "images.db")


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Images table
        cursor.execute("""
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
                loras TEXT, -- JSON array of lora names
                created_at DATETIME,
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                tagged_at DATETIME
            )
        """)
        
        # Schema Migration: Add checkpoint and loras columns if they don't exist
        cursor.execute("PRAGMA table_info(images)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'checkpoint' not in columns:
            cursor.execute("ALTER TABLE images ADD COLUMN checkpoint TEXT")
        if 'loras' not in columns:
            cursor.execute("ALTER TABLE images ADD COLUMN loras TEXT")
        
        # Tags table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
            )
        """)
        
        # Create indexes for fast searching
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_image_id ON tags(image_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_generator ON images(generator)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_path ON images(path)")
        
        conn.commit()


def add_image(
    path: str,
    filename: str,
    generator: str = "unknown",
    prompt: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    metadata_json: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    file_size: Optional[int] = None,
    checkpoint: Optional[str] = None,
    loras: Optional[List[str]] = None,
    created_at: Optional[datetime] = None
) -> int:
    """Add an image to the database. Returns the image ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO images 
            (path, filename, generator, prompt, negative_prompt, metadata_json, 
             width, height, file_size, checkpoint, loras, created_at, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (path, filename, generator, prompt, negative_prompt, metadata_json,
              width, height, file_size, checkpoint, json.dumps(loras) if loras else None, created_at))
        return cursor.lastrowid


def add_tags(image_id: int, tags: List[Dict[str, Any]]):
    """Add tags for an image. Each tag dict should have 'tag' and optionally 'confidence'."""
    with get_db() as conn:
        cursor = conn.cursor()
        # Clear existing tags
        cursor.execute("DELETE FROM tags WHERE image_id = ?", (image_id,))
        # Add new tags
        for tag_data in tags:
            tag = tag_data.get("tag", "")
            confidence = tag_data.get("confidence", 1.0)
            if tag:
                cursor.execute(
                    "INSERT INTO tags (image_id, tag, confidence) VALUES (?, ?, ?)",
                    (image_id, tag, confidence)
                )
        # Update tagged timestamp
        cursor.execute(
            "UPDATE images SET tagged_at = CURRENT_TIMESTAMP WHERE id = ?",
            (image_id,)
        )


def get_images(
    generators: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    ratings: Optional[List[str]] = None,
    checkpoints: Optional[List[str]] = None,
    loras: Optional[List[str]] = None,
    search_query: Optional[str] = None,
    sort_by: str = "newest",
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Get images with optional filters.
    - generators: Filter by generator type (OR logic)
    - tags: Filter by tags (AND logic - image must have ALL tags)
    - ratings: Filter by rating tags (OR logic - image must have ANY rating OR be untagged)
    - checkpoints: Filter by checkpoint names (OR logic)
    - loras: Filter by lora names (AND logic - image must have ALL loras)
    - search_query: Search in prompt text
    - sort_by: Sorting method (newest, oldest, name_asc, name_desc, generator, prompt_length, tag_count, rating, character_count, random)
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Base query - add subqueries for tag-based sorting
        if sort_by == "tag_count":
            query = """SELECT DISTINCT i.*, 
                       (SELECT COUNT(*) FROM tags t WHERE t.image_id = i.id) as tag_count 
                       FROM images i"""
        elif sort_by == "character_count":
            query = """SELECT DISTINCT i.*, 
                       (SELECT COUNT(*) FROM tags t WHERE t.image_id = i.id AND t.tag LIKE '%character%') as char_count 
                       FROM images i"""
        elif sort_by == "rating":
            # Priority: explicit > questionable > sensitive > general > unrated
            query = """SELECT DISTINCT i.*, 
                       CASE 
                           WHEN EXISTS (SELECT 1 FROM tags t WHERE t.image_id = i.id AND t.tag = 'explicit') THEN 1
                           WHEN EXISTS (SELECT 1 FROM tags t WHERE t.image_id = i.id AND t.tag = 'questionable') THEN 2
                           WHEN EXISTS (SELECT 1 FROM tags t WHERE t.image_id = i.id AND t.tag = 'sensitive') THEN 3
                           WHEN EXISTS (SELECT 1 FROM tags t WHERE t.image_id = i.id AND t.tag = 'general') THEN 4
                           ELSE 5
                       END as rating_order
                       FROM images i"""
        else:
            query = "SELECT DISTINCT i.* FROM images i"
        
        conditions = []
        params = []
        
        # Join with tags if filtering by tags (AND logic)
        if tags:
            for i, tag in enumerate(tags):
                alias = f"t{i}"
                query += f" INNER JOIN tags {alias} ON i.id = {alias}.image_id AND {alias}.tag LIKE ?"
                params.append(f"%{tag}%")
        
        # Filter by generators
        if generators:
            placeholders = ",".join("?" * len(generators))
            conditions.append(f"i.generator IN ({placeholders})")
            params.extend(generators)
        
        # Filter by ratings (OR logic)
        # When all 4 ratings are selected, don't filter at all (show everything)
        # When some ratings are selected, show images with those rating tags OR untagged images
        all_ratings = {'general', 'sensitive', 'questionable', 'explicit'}
        if ratings:
            selected_ratings = set(ratings)
            # Only apply filter if not all ratings are selected
            if selected_ratings != all_ratings:
                rating_placeholders = ",".join("?" * len(ratings))
                # Image has one of the selected ratings OR image has no tags at all (untagged)
                conditions.append(f"""(
                    EXISTS (SELECT 1 FROM tags rt WHERE rt.image_id = i.id AND rt.tag IN ({rating_placeholders}))
                    OR i.tagged_at IS NULL
                )""")
                params.extend(ratings)
        
        # Filter by checkpoints (OR logic)
        if checkpoints:
            placeholders = ",".join("?" * len(checkpoints))
            conditions.append(f"i.checkpoint IN ({placeholders})")
            params.extend(checkpoints)
            
        # Filter by loras (AND logic)
        if loras:
            for lora in loras:
                conditions.append("i.loras LIKE ?")
                params.append(f'"%{lora}"%')
        
        # Search in prompt
        if search_query:
            conditions.append("(i.prompt LIKE ? OR i.filename LIKE ?)")
            params.extend([f"%{search_query}%", f"%{search_query}%"])
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        # Sorting
        sort_options = {
            "newest": "i.created_at DESC",
            "oldest": "i.created_at ASC",
            "name_asc": "i.filename ASC",
            "name_desc": "i.filename DESC",
            "generator": "i.generator ASC, i.created_at DESC",
            "prompt_length": "LENGTH(COALESCE(i.prompt, '')) DESC",
            "tag_count": "tag_count DESC",
            "rating": "rating_order ASC",
            "character_count": "char_count DESC",
            "random": "RANDOM()"
        }
        order_clause = sort_options.get(sort_by, "i.created_at DESC")
        query += f" ORDER BY {order_clause} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        return [dict(row) for row in rows]



def get_image_by_id(image_id: int) -> Optional[Dict[str, Any]]:
    """Get a single image by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM images WHERE id = ?", (image_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_image_tags(image_id: int) -> List[Dict[str, Any]]:
    """Get all tags for an image."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT tag, confidence FROM tags WHERE image_id = ? ORDER BY confidence DESC",
            (image_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


def get_all_tags() -> List[Dict[str, Any]]:
    """Get all unique tags with their counts."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tag, COUNT(*) as count 
            FROM tags 
            GROUP BY tag 
            ORDER BY count DESC
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_all_generators() -> List[Dict[str, Any]]:
    """Get all generators with their counts."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT generator, COUNT(*) as count 
            FROM images 
            GROUP BY generator 
            ORDER BY count DESC
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_untagged_images(limit: int = 100) -> List[Dict[str, Any]]:
    """Get images that haven't been tagged yet."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM images WHERE tagged_at IS NULL LIMIT ?",
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]


def update_image_path(image_id: int, new_path: str):
    """Update the path of an image (after moving)."""
    with get_db() as conn:
        cursor = conn.cursor()
        new_filename = os.path.basename(new_path)
        cursor.execute(
            "UPDATE images SET path = ?, filename = ? WHERE id = ?",
            (new_path, new_filename, image_id)
        )


def delete_image(image_id: int):
    """Delete an image from the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM images WHERE id = ?", (image_id,))


def get_image_count() -> int:
    """Get total number of images in database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM images")
        return cursor.fetchone()[0]


# Initialize database on module import
init_db()
