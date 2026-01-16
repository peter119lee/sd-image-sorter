"""
Tag endpoints for SD Image Sorter.
Handles tag retrieval, tagging operations, import/export.
"""
import os
import re
import gc
import time
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

import database as db

router = APIRouter(prefix="/api", tags=["tags"])


# Pydantic models for this router
class TagRequest(BaseModel):
    image_ids: Optional[List[int]] = None
    threshold: float = 0.35
    character_threshold: float = 0.85
    retag_all: bool = False
    model_name: Optional[str] = None
    model_path: Optional[str] = None
    tags_path: Optional[str] = None
    use_gpu: bool = True


class TagImportRequest(BaseModel):
    images: List[dict]
    overwrite: bool = False


class BatchTagExportRequest(BaseModel):
    image_ids: List[int]
    output_folder: str
    blacklist: Optional[List[str]] = []
    prefix: Optional[str] = ""


# Progress state - will be set from main.py
tag_progress = {"status": "idle", "current": 0, "total": 0, "message": ""}

# Reference to get_tagger function - set from main.py
_get_tagger = None


def set_tagger_getter(tagger_getter):
    """Set the tagger getter function from main module."""
    global _get_tagger
    _get_tagger = tagger_getter


def get_tag_progress_state():
    """Get the current tag progress state."""
    return tag_progress


def set_tag_progress_state(state):
    """Set the tag progress state."""
    global tag_progress
    tag_progress = state


@router.get("/tags")
async def get_all_tags(limit: int = 500):
    """Get all unique tags with counts."""
    tags = db.get_all_tags()
    return {"tags": tags[:limit]}


@router.get("/generators")
async def get_generators():
    """Get all generators with counts."""
    generators = db.get_all_generators()
    return {"generators": generators}


@router.get("/tags/library")
async def get_tags_library(
    sort_by: str = Query(default="frequency", description="Sort by: frequency, alphabetical"),
    limit: int = 1000
):
    """Get tags library with frequency and sorting options."""
    tags = db.get_all_tags()
    
    if sort_by == "alphabetical":
        tags = sorted(tags, key=lambda x: x["tag"].lower())
    
    return {
        "tags": tags[:limit],
        "total": len(tags),
        "sort": sort_by
    }


def normalize_prompt_token(token: str) -> str:
    """Normalize a prompt token for consistent matching.
    
    Rules:
    1. Convert to lowercase
    2. Replace underscores with spaces
    3. Strip whitespace
    
    Example: "Best_quality" = "best quality" = "BeStQualITY" -> "best quality"
    """
    return token.lower().replace('_', ' ').strip()


@router.get("/prompts/library")
async def get_prompts_library(limit: int = 500):
    """Get unique prompt tokens from images with frequency counts.
    
    Rules:
    1. Split by comma only (whole phrases, not individual words)
    2. Case-insensitive (uppercase = lowercase)
    3. Normalize: underscore = space (best_quality = best quality)
    4. Display name is the normalized form (lowercase with spaces)
    
    Count = number of images that have this EXACT token as a comma-separated entry.
    This uses the same logic as the filter for consistency.
    """
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, prompt
            FROM images
            WHERE prompt IS NOT NULL AND prompt != ''
        """)
        
        # Token -> count mapping (exact match per image)
        token_counts = {}
        
        for row in cursor.fetchall():
            prompt = row["prompt"]
            
            # Remove XML-like tags and lora tags before parsing tokens
            clean_prompt = re.sub(r'<[^>]+>[^<]*</[^>]+>', '', prompt)
            clean_prompt = re.sub(r'<lora:[^>]+>', '', clean_prompt)
            clean_prompt = re.sub(r'<[^>]+>', '', clean_prompt)
            
            # Split by comma ONLY (no further splitting)
            # Track unique normalized tokens for THIS image (to avoid double-counting)
            image_tokens = set()
            
            tokens = [t.strip() for t in clean_prompt.split(',') if t.strip()]
            for token in tokens:
                # Remove leading/trailing parentheses and weight suffixes like :1.2
                clean_token = re.sub(r'^\(+|\)+$', '', token)
                clean_token = re.sub(r':\d+\.?\d*\)?$', '', clean_token)
                clean_token = clean_token.strip()
                
                if clean_token and len(clean_token) > 1:
                    # Normalize for consistent matching and display
                    normalized = normalize_prompt_token(clean_token)
                    if normalized and len(normalized) > 1:
                        image_tokens.add(normalized)
            
            # Count each unique exact token once per image
            for normalized in image_tokens:
                token_counts[normalized] = token_counts.get(normalized, 0) + 1
        
        # Sort by count and return with normalized display names
        sorted_tokens = sorted(token_counts.items(), key=lambda x: x[1], reverse=True)
        prompts = [{"prompt": normalized, "count": count} for normalized, count in sorted_tokens]
    
    return {
        "prompts": prompts[:limit],
        "total": len(prompts)
    }


@router.get("/tagger/models")
async def get_tagger_models():
    """Get available tagger models."""
    from tagger import get_available_models, DEFAULT_MODEL
    return {
        "models": get_available_models(),
        "default": DEFAULT_MODEL
    }


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


@router.get("/loras/library")
async def get_loras_library(limit: int = 500):
    """Get unique loras from images with frequency counts.
    
    Parses loras from each image's loras JSON array and prompts.
    Count = number of images that have this EXACT lora.
    
    LORA names are normalized by stripping weight notation (e.g. lora:0.8 -> lora).
    This uses the same exact matching logic as the filter for consistency.
    """
    import json
    
    with db.get_db() as conn:
        cursor = conn.cursor()
        
        # Fetch all images with loras in JSON or prompt
        cursor.execute("""
            SELECT id, loras, prompt
            FROM images
            WHERE (loras IS NOT NULL AND loras != '[]' AND loras != '')
               OR (prompt IS NOT NULL AND prompt LIKE '%<lora:%')
        """)
        
        # LORA -> count mapping (exact match per image)
        lora_counts = {}
        
        for row in cursor.fetchall():
            loras_str = row["loras"] or ""
            prompt_str = row["prompt"] or ""
            
            # Extract unique normalized LORAs for THIS image
            image_loras = set()
            
            # Extract from JSON array
            if loras_str:
                try:
                    loras_list = json.loads(loras_str)
                    for lora_name in loras_list:
                        if lora_name and len(lora_name) > 2:
                            normalized = normalize_lora_name(lora_name)
                            if normalized and len(normalized) > 2:
                                image_loras.add(normalized)
                except:
                    pass
            
            # Extract from prompt (format: <lora:name:weight>)
            if prompt_str:
                lora_matches = re.findall(r'<lora:([^:>]+)(?:[^>]*)?>',  prompt_str, re.IGNORECASE)
                for lora_name in lora_matches:
                    if lora_name and len(lora_name) > 2:
                        normalized = normalize_lora_name(lora_name)
                        if normalized and len(normalized) > 2:
                            image_loras.add(normalized)
            
            # Count each unique exact LORA once per image
            for normalized in image_loras:
                lora_counts[normalized] = lora_counts.get(normalized, 0) + 1
        
        # Sort by count and return with normalized names
        sorted_loras = sorted(lora_counts.items(), key=lambda x: x[1], reverse=True)
        loras = [{"lora": normalized, "count": count} for normalized, count in sorted_loras[:limit]]
    
    return {
        "loras": loras,
        "total": len(lora_counts)
    }



@router.get("/tags/export")
async def export_tags():
    """Export all image tags as JSON for backup/transfer."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT i.id, i.path, i.filename, i.generator, i.checkpoint,
                   GROUP_CONCAT(t.tag || ':' || t.confidence, '|||') as tags
            FROM images i
            LEFT JOIN tags t ON i.id = t.image_id
            WHERE i.tagged_at IS NOT NULL
            GROUP BY i.id
        """)
        
        export_data = []
        for row in cursor.fetchall():
            image_data = {
                "path": row["path"],
                "filename": row["filename"],
                "generator": row["generator"],
                "checkpoint": row["checkpoint"],
                "tags": []
            }
            
            if row["tags"]:
                for tag_pair in row["tags"].split("|||"):
                    if ":" in tag_pair:
                        tag, conf = tag_pair.rsplit(":", 1)
                        try:
                            image_data["tags"].append({"tag": tag, "confidence": float(conf)})
                        except ValueError:
                            image_data["tags"].append({"tag": tag_pair, "confidence": 0.5})
            
            export_data.append(image_data)
        
        return {
            "version": "1.0",
            "count": len(export_data),
            "images": export_data
        }


@router.post("/tags/import")
async def import_tags(request: TagImportRequest):
    """Import tags from exported JSON data."""
    imported = 0
    skipped = 0
    
    with db.get_db() as conn:
        cursor = conn.cursor()
        
        for img_data in request.images:
            path = img_data.get("path", "")
            filename = img_data.get("filename", "")
            tags = img_data.get("tags", [])
            
            if not tags:
                continue
            
            cursor.execute(
                "SELECT id, tagged_at FROM images WHERE path = ? OR filename = ?",
                (path, filename)
            )
            row = cursor.fetchone()
            
            if not row:
                skipped += 1
                continue
            
            image_id = row["id"]
            already_tagged = row["tagged_at"] is not None
            
            if already_tagged and not request.overwrite:
                skipped += 1
                continue
            
            if request.overwrite:
                cursor.execute("DELETE FROM tags WHERE image_id = ?", (image_id,))
            
            for tag_info in tags:
                tag = tag_info.get("tag", "")
                conf = tag_info.get("confidence", 0.5)
                if tag:
                    cursor.execute(
                        "INSERT OR REPLACE INTO tags (image_id, tag, confidence) VALUES (?, ?, ?)",
                        (image_id, tag, conf)
                    )
            
            cursor.execute(
                "UPDATE images SET tagged_at = CURRENT_TIMESTAMP WHERE id = ?",
                (image_id,)
            )
            imported += 1
        
        conn.commit()
    
    return {"imported": imported, "skipped": skipped}


@router.post("/tag/start")
@router.post("/tag")
async def start_tagging(request: TagRequest, background_tasks: BackgroundTasks):
    """Start tagging images with WD14 tagger."""
    global tag_progress
    
    if tag_progress["status"] == "running":
        raise HTTPException(status_code=400, detail="Tagging already in progress")
    
    if _get_tagger is None:
        raise HTTPException(status_code=500, detail="Tagger not initialized")
    
    def run_tagging():
        global tag_progress
        tag_progress = {"status": "running", "current": 0, "total": 0, "message": "Loading model..."}
        
        try:
            tagger = _get_tagger(
                model_name=request.model_name,
                model_path=request.model_path,
                tags_path=request.tags_path,
                threshold=request.threshold,
                character_threshold=request.character_threshold,
                use_gpu=request.use_gpu
            )
            
            if request.image_ids:
                images = [db.get_image_by_id(id) for id in request.image_ids]
                images = [img for img in images if img]
            elif request.retag_all:
                images = db.get_images(limit=999999)
            else:
                images = db.get_untagged_images(limit=999999)
            
            tag_progress["total"] = len(images)
            tag_progress["message"] = f"Tagging {len(images)} images..."
            
            for i, image in enumerate(images):
                tag_progress["current"] = i + 1
                tag_progress["message"] = f"Tagging: {image['filename']} ({i+1}/{len(images)})"
                
                try:
                    if os.path.exists(image["path"]):
                        result = tagger.tag(image["path"])
                        db.add_tags(image["id"], result["all_tags"])
                except Exception as e:
                    print(f"Error tagging {image['path']}: {e}")
                
                if (i + 1) % 50 == 0:
                    gc.collect()
                    time.sleep(0.5)
                    tag_progress["message"] = f"Processed {i+1}/{len(images)} - brief rest..."
            
            tag_progress = {
                "status": "done",
                "current": len(images),
                "total": len(images),
                "message": f"Completed! Tagged {len(images)} images."
            }
        except Exception as e:
            tag_progress = {
                "status": "error",
                "current": 0,
                "total": 0,
                "message": f"Error: {str(e)}"
            }
    
    background_tasks.add_task(run_tagging)
    return {"status": "started", "message": "Tagging started in background"}


@router.get("/tag/progress")
async def get_tag_progress():
    """Get current tagging progress."""
    return tag_progress


@router.post("/tags/export-batch")
async def export_tags_batch(request: BatchTagExportRequest):
    """
    Export tags for each image to individual .txt files.
    Each file is named {image_basename}.txt with comma-separated tags.
    """
    from utils.path_validation import validate_folder_path
    
    is_valid, error = validate_folder_path(request.output_folder, allow_create=True)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    os.makedirs(request.output_folder, exist_ok=True)
    
    exported = 0
    errors = 0
    
    for image_id in request.image_ids:
        try:
            image = db.get_image_by_id(image_id)
            if not image:
                errors += 1
                continue
            
            tags = db.get_image_tags(image_id)
            if not tags:
                continue
            
            # Filter out blacklisted tags
            filtered_tags = [t["tag"] for t in tags if t["tag"] not in request.blacklist]
            
            # Add prefix if specified
            if request.prefix:
                filtered_tags = [request.prefix + t for t in filtered_tags]
            
            # Write to file
            basename = os.path.splitext(image["filename"])[0]
            txt_path = os.path.join(request.output_folder, f"{basename}.txt")
            
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(", ".join(filtered_tags))
            
            exported += 1
        except Exception as e:
            print(f"Error exporting tags for image {image_id}: {e}")
            errors += 1
    
    return {"exported": exported, "errors": errors}


@router.post("/tags/fix-ratings")
async def fix_rating_tags():
    """
    Clean up duplicate rating tags in existing database.
    For each image, keeps only the highest confidence rating tag.
    Run this once to fix data from before the bug was fixed.
    """
    rating_tags = ['general', 'sensitive', 'questionable', 'explicit']
    fixed_count = 0
    
    with db.get_db() as conn:
        cursor = conn.cursor()
        
        # Get all images with their rating tags
        cursor.execute("""
            SELECT DISTINCT image_id 
            FROM tags 
            WHERE tag IN (?, ?, ?, ?)
        """, rating_tags)
        
        image_ids = [row[0] for row in cursor.fetchall()]
        
        for image_id in image_ids:
            # Get all rating tags for this image
            cursor.execute("""
                SELECT id, tag, confidence
                FROM tags 
                WHERE image_id = ? AND tag IN (?, ?, ?, ?)
                ORDER BY confidence DESC
            """, [image_id] + rating_tags)
            
            ratings = cursor.fetchall()
            
            if len(ratings) > 1:
                # Keep only the highest confidence one (first in ORDER BY DESC)
                keep_id = ratings[0]['id']
                remove_ids = [r['id'] for r in ratings[1:]]
                
                # Delete the extra ratings
                placeholders = ",".join("?" * len(remove_ids))
                cursor.execute(f"DELETE FROM tags WHERE id IN ({placeholders})", remove_ids)
                fixed_count += 1
        
        conn.commit()
    
    return {
        "status": "ok",
        "images_fixed": fixed_count,
        "message": f"Cleaned up rating tags for {fixed_count} images"
    }

