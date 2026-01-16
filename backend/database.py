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


def normalize_prompt_token(token: str) -> str:
    """Normalize a prompt token for consistent matching.
    
    Rules:
    1. Convert to lowercase
    2. Replace underscores with spaces
    3. Strip whitespace
    
    Example: "Best_quality" = "best quality" = "BeStQualITY" -> "best quality"
    """
    return token.lower().replace('_', ' ').strip()


def normalize_lora_name(lora_name: str) -> str:
    """Normalize a LORA name for consistent matching.
    
    Strips weight notation and file extensions for cleaner display:
    - "my_lora:0.8" -> "my_lora"
    - "my_lora.safetensors" -> "my_lora"
    - "my-lora_v2.ckpt" -> "my-lora_v2"
    - Lowercase for matching
    """
    # Strip weight notation (everything after last colon if it's a number)
    if ':' in lora_name:
        parts = lora_name.rsplit(':', 1)
        # Check if the part after colon is a weight (number)
        try:
            float(parts[1])
            lora_name = parts[0]
        except ValueError:
            pass
    
    # Strip common model file extensions
    extensions_to_strip = ['.safetensors', '.ckpt', '.pt', '.pth', '.bin']
    lora_lower = lora_name.lower()
    for ext in extensions_to_strip:
        if lora_lower.endswith(ext):
            lora_name = lora_name[:-len(ext)]
            break
    
    return lora_name.lower().strip()



import re

def extract_prompt_tokens(prompt: str) -> set:
    """Extract normalized tokens from a prompt string.
    
    Used for exact token matching in filters.
    Splits by comma only, cleans parentheses/weights, normalizes.
    """
    if not prompt:
        return set()
    
    # Remove XML-like tags and lora tags
    clean_prompt = re.sub(r'<[^>]+>[^<]*</[^>]+>', '', prompt)
    clean_prompt = re.sub(r'<lora:[^>]+>', '', clean_prompt)
    clean_prompt = re.sub(r'<[^>]+>', '', clean_prompt)
    
    tokens = set()
    for token in clean_prompt.split(','):
        token = token.strip()
        if not token:
            continue
        # Remove leading/trailing parentheses and weight suffixes
        clean_token = re.sub(r'^\(+|\)+$', '', token)
        clean_token = re.sub(r':\d+\.?\d*\)?$', '', clean_token)
        clean_token = clean_token.strip()
        
        if clean_token and len(clean_token) > 1:
            normalized = normalize_prompt_token(clean_token)
            if normalized and len(normalized) > 1:
                tokens.add(normalized)
    
    return tokens


def extract_lora_names(loras_json: str, prompt: str) -> set:
    """Extract normalized LORA names from loras JSON and prompt.
    
    Used for exact LORA matching in filters.
    """
    loras = set()
    
    # Extract from JSON array
    if loras_json:
        try:
            loras_list = json.loads(loras_json)
            for lora_name in loras_list:
                if lora_name and len(lora_name) > 2:
                    normalized = normalize_lora_name(lora_name)
                    if normalized and len(normalized) > 2:
                        loras.add(normalized)
        except:
            pass
    
    # Extract from prompt (format: <lora:name:weight>)
    if prompt:
        lora_matches = re.findall(r'<lora:([^:>]+)(?::[^>]+)?>', prompt, re.IGNORECASE)
        for lora_name in lora_matches:
            if lora_name and len(lora_name) > 2:
                normalized = normalize_lora_name(lora_name)
                if normalized and len(normalized) > 2:
                    loras.add(normalized)
    
    return loras

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
    offset: int = 0,
    min_width: Optional[int] = None,
    max_width: Optional[int] = None,
    min_height: Optional[int] = None,
    max_height: Optional[int] = None,
    prompt_terms: Optional[List[str]] = None,  # Multi-prompt filter (AND logic)
    aspect_ratio: Optional[str] = None  # 'square', 'landscape', 'portrait'
) -> List[Dict[str, Any]]:
    """
    Get images with optional filters.
    - generators: Filter by generator type (OR logic)
    - tags: Filter by tags (AND logic - image must have ALL tags)
    - ratings: Filter by rating tags (OR logic - image must have ANY rating OR be untagged)
    - checkpoints: Filter by checkpoint names (OR logic)
    - loras: Filter by lora names (AND logic - image must have ALL loras)
    - search_query: Search in prompt text
    - sort_by: Sorting method (newest, oldest, name_asc, name_desc, generator, prompt_length, tag_count, rating, character_count, random, file_size)
    - min_width, max_width, min_height, max_height: Dimension filters
    - aspect_ratio: Filter by aspect ratio ('square', 'landscape', 'portrait')
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
            
        # Filter by loras (OR logic - image has ANY of the selected loras)
        # Match on lora name in loras column, metadata_json, or prompt
        # Use same normalization as library: strip weight notation and lowercase
        if loras:
            lora_conditions = []
            for lora in loras:
                # Strip weight notation (name:0.8 -> name) and lowercase
                lora_normalized = normalize_lora_name(lora)
                # Match lora name in loras column, metadata_json, or prompt
                lora_conditions.append("(LOWER(i.loras) LIKE ? OR LOWER(i.metadata_json) LIKE ? OR LOWER(i.prompt) LIKE ?)")
                params.append(f"%{lora_normalized}%")
                params.append(f"%{lora_normalized}%")
                params.append(f"%{lora_normalized}%")
            conditions.append(f"({' OR '.join(lora_conditions)})")
        
        # Search in prompt (full-text single term) - with normalization
        # Normalize: lowercase and replace underscore with space
        if search_query:
            normalized_search = normalize_prompt_token(search_query)
            conditions.append("(REPLACE(LOWER(i.prompt), '_', ' ') LIKE ? OR LOWER(i.filename) LIKE ?)")
            params.extend([f"%{normalized_search}%", f"%{search_query.lower()}%"])
        
        # Multi-prompt filter (AND logic - prompt must contain ALL terms)
        # Uses substring matching (LIKE %term%) with normalization
        # Library counting will use the same logic for consistency
        if prompt_terms:
            for term in prompt_terms:
                normalized_term = normalize_prompt_token(term)
                conditions.append("REPLACE(LOWER(i.prompt), '_', ' ') LIKE ?")
                params.append(f"%{normalized_term}%")
        
        # Dimension filters
        if min_width:
            conditions.append("i.width >= ?")
            params.append(min_width)
        if max_width:
            conditions.append("i.width <= ?")
            params.append(max_width)
        if min_height:
            conditions.append("i.height >= ?")
            params.append(min_height)
        if max_height:
            conditions.append("i.height <= ?")
            params.append(max_height)
        
        # Aspect ratio filter
        if aspect_ratio:
            if aspect_ratio == 'square':
                conditions.append("ABS(CAST(i.width AS FLOAT) / i.height - 1.0) < 0.1")
            elif aspect_ratio == 'landscape':
                conditions.append("CAST(i.width AS FLOAT) / i.height > 1.1")
            elif aspect_ratio == 'portrait':
                conditions.append("CAST(i.width AS FLOAT) / i.height < 0.9")
        
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
            "random": "RANDOM()",
            "file_size": "i.file_size DESC",
            "file_size_asc": "i.file_size ASC"
        }
        order_clause = sort_options.get(sort_by, "i.created_at DESC")
        
        # For exact matching filters, we fetch more than needed and post-filter
        # This ensures exact token/LORA matching consistency with library counting
        needs_post_filter = bool(prompt_terms) or bool(loras)
        
        if needs_post_filter:
            # Fetch all candidates without limit (we'll apply limit after post-filtering)
            query += f" ORDER BY {order_clause}"
        else:
            query += f" ORDER BY {order_clause} LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        results = [dict(row) for row in rows]
        
        # Post-filter for exact matching if needed
        if needs_post_filter:
            filtered_results = []
            
            # Normalize filter terms
            normalized_prompt_terms = [normalize_prompt_token(t) for t in (prompt_terms or [])]
            normalized_loras = [normalize_lora_name(l) for l in (loras or [])]
            
            for img in results:
                # Check prompt tokens (AND logic - must have ALL terms)
                if normalized_prompt_terms:
                    image_tokens = extract_prompt_tokens(img.get('prompt', ''))
                    if not all(term in image_tokens for term in normalized_prompt_terms):
                        continue
                
                # Check LORAs (OR logic - must have ANY of the loras)
                if normalized_loras:
                    image_loras = extract_lora_names(img.get('loras', ''), img.get('prompt', ''))
                    if not any(lora in image_loras for lora in normalized_loras):
                        continue
                
                filtered_results.append(img)
            
            # Apply offset and limit after post-filtering
            results = filtered_results[offset:offset + limit] if limit else filtered_results[offset:]
        
        return results



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
