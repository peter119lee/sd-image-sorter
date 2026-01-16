"""
Sorting endpoints for SD Image Sorter.
Handles scanning, moving, batch operations, and manual sort sessions.
"""
import os
import json
from typing import Optional, List

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

import database as db
from image_manager import scan_folder, move_image

router = APIRouter(prefix="/api", tags=["sorting"])


# Pydantic models for this router
class ScanRequest(BaseModel):
    folder_path: str
    recursive: bool = True


class MoveRequest(BaseModel):
    image_ids: List[int]
    destination_folder: str


class BatchMoveRequest(BaseModel):
    generators: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    ratings: Optional[List[str]] = None
    checkpoints: Optional[List[str]] = None
    loras: Optional[List[str]] = None
    prompts: Optional[List[str]] = None
    min_width: Optional[int] = None
    max_width: Optional[int] = None
    min_height: Optional[int] = None
    max_height: Optional[int] = None
    aspect_ratio: Optional[str] = None
    destination_folder: str


class FolderConfig(BaseModel):
    folders: dict


class BatchTagExportRequest(BaseModel):
    image_ids: List[int]
    output_folder: str
    blacklist: Optional[List[str]] = []
    prefix: Optional[str] = ""


# Progress and session state - managed from main module
scan_progress = {"status": "idle", "current": 0, "total": 0, "message": ""}
sort_session = {
    "active": False,
    "images": [],
    "current_index": 0,
    "folders": {},
    "history": []
}


def get_scan_progress_state():
    """Get the current scan progress."""
    return scan_progress


def set_scan_progress_state(state):
    """Set the scan progress state."""
    global scan_progress
    scan_progress = state


def get_sort_session():
    """Get the current sort session."""
    return sort_session


def set_sort_session(session):
    """Set the sort session."""
    global sort_session
    sort_session = session


@router.post("/scan")
async def start_scan(request: ScanRequest, background_tasks: BackgroundTasks):
    """Start scanning a folder for images."""
    global scan_progress
    
    from utils.path_validation import validate_folder_path
    
    is_valid, error = validate_folder_path(request.folder_path)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error or "Invalid folder path")
    
    if scan_progress["status"] == "running":
        raise HTTPException(status_code=400, detail="Scan already in progress")
    
    def run_scan():
        global scan_progress
        scan_progress = {"status": "running", "current": 0, "total": 0, "message": "Starting..."}
        
        def progress_cb(current, total, filename):
            scan_progress["current"] = current
            scan_progress["total"] = total
            scan_progress["message"] = f"Processing: {filename}"
        
        result = scan_folder(request.folder_path, request.recursive, progress_cb)
        scan_progress = {
            "status": "done",
            "current": result["total"],
            "total": result["total"],
            "message": f"Completed! {result['new']} images indexed.",
            "result": result
        }
    
    background_tasks.add_task(run_scan)
    return {"status": "started", "message": "Scan started in background"}


@router.get("/scan/progress")
async def get_scan_progress():
    """Get current scan progress."""
    return scan_progress


@router.post("/move")
async def move_images(request: MoveRequest):
    """Move specific images to a folder."""
    from utils.path_validation import validate_folder_path
    
    is_valid, error = validate_folder_path(request.destination_folder, allow_create=True)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error or "Invalid destination folder")
    
    os.makedirs(request.destination_folder, exist_ok=True)
    
    results = []
    for image_id in request.image_ids:
        image = db.get_image_by_id(image_id)
        if image and os.path.exists(image["path"]):
            try:
                new_path = move_image(image_id, request.destination_folder, image["path"])
                results.append({"id": image_id, "new_path": new_path, "success": True})
            except Exception as e:
                results.append({"id": image_id, "error": str(e), "success": False})
        else:
            results.append({"id": image_id, "error": "Image not found", "success": False})
    
    return {"results": results}


@router.post("/batch-move")
async def batch_move_images(request: BatchMoveRequest, background_tasks: BackgroundTasks):
    """Move all images matching filters to a folder."""
    from utils.path_validation import validate_folder_path
    
    print(f"[batch-move] Request received: {request}")
    
    is_valid, error = validate_folder_path(request.destination_folder, allow_create=True)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error or "Invalid destination folder")
    
    # Normalize empty lists to None for proper filtering
    generators = request.generators if request.generators else None
    tags = request.tags if request.tags else None
    ratings = request.ratings if request.ratings else None
    checkpoints = request.checkpoints if request.checkpoints else None
    loras = request.loras if request.loras else None
    prompts = request.prompts if request.prompts else None
    
    # Combine tags and ratings if both exist
    tag_list = list(tags) if tags else []
    if ratings:
        tag_list = tag_list + list(ratings)
    
    print(f"[batch-move] Querying with: generators={generators}, tags={tag_list or None}, checkpoints={checkpoints}, loras={loras}, prompts={prompts}")
    
    images = db.get_images(
        generators=generators,
        tags=tag_list if tag_list else None,
        checkpoints=checkpoints,
        loras=loras,
        prompt_terms=prompts,
        min_width=request.min_width,
        max_width=request.max_width,
        min_height=request.min_height,
        max_height=request.max_height,
        aspect_ratio=request.aspect_ratio,
        limit=999999
    )
    
    print(f"[batch-move] Found {len(images)} images matching filters")
    
    if not images:
        return {"message": "No images match the filters", "count": 0}
    
    os.makedirs(request.destination_folder, exist_ok=True)
    
    moved = 0
    errors = []
    for image in images:
        if os.path.exists(image["path"]):
            try:
                move_image(image["id"], request.destination_folder, image["path"])
                moved += 1
            except Exception as e:
                errors.append(f"Error moving {image['path']}: {e}")
                print(f"[batch-move] Error moving {image['path']}: {e}")
    
    print(f"[batch-move] Successfully moved {moved} images")
    return {"message": f"Moved {moved} images", "count": moved}


@router.post("/sort/start")
async def start_sort_session(
    generators: Optional[str] = None,
    tags: Optional[str] = None,
    ratings: Optional[str] = None,
    checkpoints: Optional[str] = None,
    loras: Optional[str] = None,
    prompts: Optional[str] = None,
    min_width: Optional[int] = None,
    max_width: Optional[int] = None,
    min_height: Optional[int] = None,
    max_height: Optional[int] = None,
    aspect_ratio: Optional[str] = None,
    folders: Optional[str] = None
):
    """Start a manual sort session."""
    global sort_session
    
    gen_list = generators.split(",") if generators else None
    tag_list = tags.split(",") if tags else None
    rating_list = ratings.split(",") if ratings else None
    cp_list = checkpoints.split(",") if checkpoints else None
    lr_list = loras.split(",") if loras else None
    prompt_list = prompts.split(",") if prompts else None
    
    if rating_list:
        tag_list = (tag_list or []) + rating_list
    
    images = db.get_images(
        generators=gen_list,
        tags=tag_list,
        ratings=rating_list,
        checkpoints=cp_list,
        loras=lr_list,
        prompt_terms=prompt_list,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        aspect_ratio=aspect_ratio,
        limit=999999
    )
    
    folder_config = {}
    if folders:
        try:
            folder_config = json.loads(folders)
        except:
            pass
    
    sort_session = {
        "active": True,
        "images": images,
        "current_index": 0,
        "folders": folder_config,
        "history": []
    }
    
    return {
        "status": "started",
        "total_images": len(images),
        "current": images[0] if images else None
    }


@router.get("/sort/current")
async def get_current_sort_image():
    """Get the current image in the sort session."""
    if not sort_session["active"]:
        raise HTTPException(status_code=400, detail="No active sort session")
    
    if sort_session["current_index"] >= len(sort_session["images"]):
        return {"done": True, "message": "All images sorted"}
    
    current = sort_session["images"][sort_session["current_index"]]
    tags = db.get_image_tags(current["id"])
    
    return {
        "image": current,
        "tags": tags,
        "index": sort_session["current_index"],
        "total": len(sort_session["images"]),
        "remaining": len(sort_session["images"]) - sort_session["current_index"]
    }


@router.post("/sort/action")
async def sort_action(action: str, folder_key: Optional[str] = None):
    """
    Perform a sort action.
    Actions: 'move' (with folder_key), 'skip', 'undo'
    """
    global sort_session
    
    print(f"[sort/action] Action: {action}, folder_key: {folder_key}, current_index: {sort_session['current_index']}")
    
    if not sort_session["active"]:
        raise HTTPException(status_code=400, detail="No active sort session")
    
    if action == "undo":
        print(f"[sort/action] Undo requested. History length: {len(sort_session['history'])}")
        if sort_session["history"]:
            last = sort_session["history"].pop()
            print(f"[sort/action] Undoing: {last}")
            if last["action"] == "move":
                image = db.get_image_by_id(last["image_id"])
                if image:
                    try:
                        move_image(last["image_id"], os.path.dirname(last["original_path"]), image["path"])
                        print(f"[sort/action] Moved image back to {os.path.dirname(last['original_path'])}")
                    except Exception as e:
                        print(f"[sort/action] Error moving image back: {e}")
            # Decrement index to go back to the previous image
            sort_session["current_index"] = max(0, sort_session["current_index"] - 1)
            print(f"[sort/action] New index after undo: {sort_session['current_index']}")
        else:
            print(f"[sort/action] No history to undo")
            return {"status": "no_history", "message": "Nothing to undo"}
        
        # Return current image info for the undone position - get FRESH data from DB
        if sort_session["current_index"] < len(sort_session["images"]):
            old_image = sort_session["images"][sort_session["current_index"]]
            # Fetch fresh image data from database to get updated path
            fresh_image = db.get_image_by_id(old_image["id"])
            if fresh_image:
                # Update the session's images array with fresh data
                sort_session["images"][sort_session["current_index"]] = fresh_image
                current = fresh_image
            else:
                current = old_image
            current_tags = db.get_image_tags(current["id"])
            return {
                "status": "undone",
                "image": current,
                "tags": current_tags,
                "index": sort_session["current_index"],
                "total": len(sort_session["images"]),
                "remaining": len(sort_session["images"]) - sort_session["current_index"]
            }
        return {"status": "undone", "current_index": sort_session["current_index"]}
    
    if sort_session["current_index"] >= len(sort_session["images"]):
        return {"done": True}
    
    current = sort_session["images"][sort_session["current_index"]]
    
    if action == "move" and folder_key:
        folder = sort_session["folders"].get(folder_key)
        print(f"[sort/action] Move to folder: {folder}, image path: {current['path']}")
        if folder and os.path.exists(current["path"]):
            original_path = current["path"]
            try:
                new_path = move_image(current["id"], folder, current["path"])
                print(f"[sort/action] Moved to: {new_path}")
                sort_session["history"].append({
                    "action": "move",
                    "image_id": current["id"],
                    "original_path": original_path,
                    "new_path": new_path,
                    "folder_key": folder_key
                })
            except Exception as e:
                print(f"[sort/action] Error moving: {e}")
                return {"error": str(e)}
        else:
            print(f"[sort/action] Folder not found or image doesn't exist. Folder: {folder}, Exists: {os.path.exists(current['path']) if current.get('path') else 'no path'}")
    elif action == "skip":
        sort_session["history"].append({
            "action": "skip",
            "image_id": current["id"]
        })
    
    sort_session["current_index"] += 1
    print(f"[sort/action] New index: {sort_session['current_index']}")
    
    if sort_session["current_index"] >= len(sort_session["images"]):
        return {"done": True, "message": "All images sorted"}
    
    next_image = sort_session["images"][sort_session["current_index"]]
    next_tags = db.get_image_tags(next_image["id"])
    
    return {
        "image": next_image,
        "tags": next_tags,
        "index": sort_session["current_index"],
        "total": len(sort_session["images"]),
        "remaining": len(sort_session["images"]) - sort_session["current_index"]
    }


@router.post("/sort/set-folders")
async def set_sort_folders(config: FolderConfig):
    """Set folder destinations for sort keys."""
    global sort_session
    
    for key, path in config.folders.items():
        if path:
            os.makedirs(path, exist_ok=True)
    
    sort_session["folders"] = config.folders
    return {"status": "ok", "folders": sort_session["folders"]}


@router.get("/sort/folders")
async def get_sort_folders():
    """Get current folder configuration."""
    return {"folders": sort_session["folders"]}


@router.delete("/clear-gallery")
async def clear_gallery():
    """Clear all image records from the database."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM images")
        cursor.execute("DELETE FROM tags")
    return {"status": "ok", "message": "Gallery cleared"}


@router.get("/analytics")
async def get_analytics():
    """Get popular tags, checkpoints, and loras."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        
        # Checkpoints - exact match count (matches filter logic)
        cursor.execute("""
            SELECT checkpoint, COUNT(*) as count 
            FROM images 
            WHERE checkpoint IS NOT NULL AND checkpoint != ''
            GROUP BY checkpoint 
            ORDER BY count DESC 
            LIMIT 50
        """)
        checkpoints = [dict(row) for row in cursor.fetchall()]
        
        # Loras - use same extraction logic as the filter for count consistency
        # Extract from both loras JSON column AND prompt <lora:name:weight> patterns
        cursor.execute("""
            SELECT id, loras, prompt 
            FROM images 
            WHERE (loras IS NOT NULL AND loras != '[]' AND loras != '')
               OR (prompt IS NOT NULL AND prompt LIKE '%<lora:%')
        """)
        all_loras_rows = cursor.fetchall()
        lora_counts = {}
        for row in all_loras_rows:
            # Use same extraction logic as filter for consistency
            image_loras = db.extract_lora_names(row["loras"] or "", row["prompt"] or "")
            for lora_name in image_loras:
                lora_counts[lora_name] = lora_counts.get(lora_name, 0) + 1
        
        sorted_loras = sorted(lora_counts.items(), key=lambda x: x[1], reverse=True)[:50]
        loras = [{"lora": l, "count": c} for l, c in sorted_loras]
        
        tags = db.get_all_tags()[:20]
        
    return {
        "checkpoints": checkpoints,
        "loras": loras,
        "top_tags": tags
    }


@router.get("/stats")
async def get_stats():
    """Get database statistics."""
    analytics_data = await get_analytics()
    return {
        "total_images": db.get_image_count(),
        "generators": db.get_all_generators(),
        "top_tags": analytics_data["top_tags"],
        "checkpoints": analytics_data["checkpoints"],
        "loras": analytics_data["loras"]
    }


@router.post("/export-tags-batch")
async def export_tags_batch(request: BatchTagExportRequest):
    """
    Export tags for each image to individual .txt files.
    Each file is named {image_basename}.txt with comma-separated tags.
    """
    from utils.path_validation import validate_folder_path
    
    is_valid, error = validate_folder_path(request.output_folder, allow_create=True)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error or "Invalid output folder")
    
    os.makedirs(request.output_folder, exist_ok=True)
    
    blacklist = set(tag.strip().lower() for tag in (request.blacklist or []))
    prefix = request.prefix or ""
    
    exported = 0
    errors = []
    
    for image_id in request.image_ids:
        image = db.get_image_by_id(image_id)
        if not image:
            errors.append(f"Image {image_id} not found")
            continue
        
        tags = db.get_image_tags(image_id)
        filtered_tags = [t["tag"] for t in tags if t["tag"].lower() not in blacklist]
        tag_string = prefix + ", ".join(filtered_tags) if filtered_tags else prefix.rstrip(", ")
        
        image_basename = os.path.splitext(image["filename"])[0]
        output_path = os.path.join(request.output_folder, f"{image_basename}.txt")
        
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(tag_string)
            exported += 1
        except Exception as e:
            errors.append(f"Error writing {output_path}: {e}")
    
    return {
        "status": "ok",
        "exported": exported,
        "total": len(request.image_ids),
        "errors": errors if errors else None
    }
