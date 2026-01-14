"""
FastAPI backend for SD Image Sorter.
Provides REST API for image management, tagging, and sorting.
"""
import os
import sys
import json
import asyncio
from typing import Optional, List
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add current dir to path for imports
sys.path.insert(0, os.path.dirname(__file__))

import database as db
from metadata_parser import parse_image
from image_manager import scan_folder, move_image, batch_move, get_folder_stats

# Lazy import tagger to avoid loading model at startup
_tagger = None
_tagger_settings = {}

def get_tagger(
    model_name: str = None,
    model_path: str = None,
    tags_path: str = None,
    threshold: float = 0.35,
    character_threshold: float = 0.85
):
    global _tagger, _tagger_settings
    from tagger import get_tagger as _get_tagger, DEFAULT_MODEL
    
    model_name = model_name or DEFAULT_MODEL
    
    return _get_tagger(
        model_name=model_name,
        model_path=model_path,
        tags_path=tags_path,
        threshold=threshold,
        character_threshold=character_threshold
    )


# Pydantic models
class ScanRequest(BaseModel):
    folder_path: str
    recursive: bool = True


class TagRequest(BaseModel):
    image_ids: Optional[List[int]] = None
    threshold: float = 0.35
    character_threshold: float = 0.85
    retag_all: bool = False
    model_name: Optional[str] = None
    model_path: Optional[str] = None  # Custom local model path
    tags_path: Optional[str] = None   # Custom tags file path


class MoveRequest(BaseModel):
    image_ids: List[int]
    destination_folder: str


class BatchMoveRequest(BaseModel):
    generators: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    ratings: Optional[List[str]] = None
    checkpoints: Optional[List[str]] = None
    loras: Optional[List[str]] = None
    destination_folder: str


class FolderConfig(BaseModel):
    folders: dict  # {"w": "path", "a": "path", "s": "path", "d": "path"}


class BatchTagExportRequest(BaseModel):
    image_ids: List[int]
    output_folder: str
    blacklist: Optional[List[str]] = []
    prefix: Optional[str] = ""


class CensorDetectRequest(BaseModel):
    image_id: int
    model_path: str
    confidence_threshold: float = 0.5


class CensorApplyRequest(BaseModel):
    image_id: int
    regions: List[List[int]]  # [[x1, y1, x2, y2], ...]
    style: str = "mosaic"  # mosaic, black_bar, white_bar, blur, sticker
    block_size: int = 16
    blur_radius: int = 20
    sticker_path: Optional[str] = None


class CensorSaveRequest(BaseModel):
    image_id: int
    regions: List[List[int]]
    style: str = "mosaic"
    block_size: int = 16
    blur_radius: int = 20
    sticker_path: Optional[str] = None
    output_folder: str
    filename_suffix: str = "_censored"


# Background task tracking
scan_progress = {"status": "idle", "current": 0, "total": 0, "message": ""}
tag_progress = {"status": "idle", "current": 0, "total": 0, "message": ""}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("SD Image Sorter backend starting...")
    db.init_db()
    yield
    # Shutdown
    print("Shutting down...")


app = FastAPI(
    title="SD Image Sorter",
    description="Image management API for Stable Diffusion generated images",
    version="1.0.0",
    lifespan=lifespan
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")


@app.get("/")
async def root():
    """Serve the main frontend page."""
    index_path = os.path.join(frontend_path, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "SD Image Sorter API", "docs": "/docs"}


# ============== Image Endpoints ==============

@app.get("/api/images")
async def get_images(
    generators: Optional[str] = None,
    tags: Optional[str] = None,
    ratings: Optional[str] = None,
    checkpoints: Optional[str] = None,
    loras: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = Query(default="newest", description="Sort by: newest, oldest, name_asc, name_desc, generator, prompt_length, tag_count, rating, character_count, random"),
    limit: int = Query(default=0, description="0 = no limit, returns all images"),
    offset: int = 0
):
    """
    Get images with optional filters.
    - generators: Comma-separated list of generators (comfyui, nai, webui, forge)
    - tags: Comma-separated list of tags (AND logic)
    - ratings: Comma-separated ratings (general, sensitive, questionable, explicit)
    - search: Search in prompts
    - sort_by: Sorting method
    - limit: 0 for all images
    """
    gen_list = generators.split(",") if generators else None
    tag_list = tags.split(",") if tags else None
    rating_list = ratings.split(",") if ratings else None
    cp_list = checkpoints.split(",") if checkpoints else None
    lr_list = loras.split(",") if loras else None
    
    # Use very high limit when 0 (all images)
    actual_limit = limit if limit > 0 else 999999
    
    images = db.get_images(
        generators=gen_list,
        tags=tag_list,
        ratings=rating_list,
        checkpoints=cp_list,
        loras=lr_list,
        search_query=search,
        sort_by=sort_by,
        limit=actual_limit,
        offset=offset
    )
    
    return {"images": images, "count": len(images)}



@app.get("/api/images/{image_id}")
async def get_image(image_id: int):
    """Get a single image with its tags."""
    image = db.get_image_by_id(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    tags = db.get_image_tags(image_id)
    return {"image": image, "tags": tags}


@app.get("/api/image-file/{image_id}")
async def get_image_file(image_id: int):
    """Serve the actual image file."""
    image = db.get_image_by_id(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    return FileResponse(image["path"])


@app.get("/api/image-thumbnail/{image_id}")
async def get_image_thumbnail(image_id: int, size: int = 256):
    """Get a thumbnail of the image (returns full image for now, frontend can resize)."""
    image = db.get_image_by_id(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    return FileResponse(image["path"])


# ============== Tag Endpoints ==============

@app.get("/api/tags")
async def get_all_tags(limit: int = 500):
    """Get all unique tags with counts."""
    tags = db.get_all_tags()
    return {"tags": tags[:limit]}


@app.get("/api/generators")
async def get_generators():
    """Get all generators with counts."""
    generators = db.get_all_generators()
    return {"generators": generators}


@app.get("/api/tagger/models")
async def get_tagger_models():
    """Get available tagger models."""
    from tagger import get_available_models, DEFAULT_MODEL
    return {
        "models": get_available_models(),
        "default": DEFAULT_MODEL
    }


# ============== Scan Endpoints ==============

@app.post("/api/scan")
async def start_scan(request: ScanRequest, background_tasks: BackgroundTasks):
    """Start scanning a folder for images."""
    global scan_progress
    
    if not os.path.exists(request.folder_path):
        raise HTTPException(status_code=400, detail="Folder does not exist")
    
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


@app.get("/api/scan/progress")
async def get_scan_progress():
    """Get current scan progress."""
    return scan_progress


# ============== Tagging Endpoints ==============

@app.post("/api/tag/start")
@app.post("/api/tag")
async def start_tagging(request: TagRequest, background_tasks: BackgroundTasks):
    """Start tagging images with WD14 tagger."""
    global tag_progress
    
    if tag_progress["status"] == "running":
        raise HTTPException(status_code=400, detail="Tagging already in progress")
    
    def run_tagging():
        global tag_progress
        tag_progress = {"status": "running", "current": 0, "total": 0, "message": "Loading model..."}
        
        try:
            tagger = get_tagger(
                model_name=request.model_name,
                model_path=request.model_path,
                tags_path=request.tags_path,
                threshold=request.threshold,
                character_threshold=request.character_threshold
            )
            
            # Get untagged images or specific IDs
            if request.image_ids:
                images = [db.get_image_by_id(id) for id in request.image_ids]
                images = [img for img in images if img]
            elif request.retag_all:
                # Get ALL images
                images = db.get_images(limit=999999)
            else:
                # Get untagged images
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


@app.get("/api/tag/progress")
async def get_tag_progress():
    """Get current tagging progress."""
    return tag_progress


# ============== Move/Sort Endpoints ==============

@app.post("/api/move")
async def move_images(request: MoveRequest):
    """Move specific images to a folder."""
    if not os.path.exists(request.destination_folder):
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


@app.post("/api/batch-move")
async def batch_move_images(request: BatchMoveRequest, background_tasks: BackgroundTasks):
    """Move all images matching filters to a folder."""
    # Combine ratings into tags
    tag_list = request.tags or []
    if request.ratings:
        tag_list = tag_list + request.ratings
    
    # Get matching images (no limit)
    images = db.get_images(
        generators=request.generators,
        tags=tag_list if tag_list else None,
        checkpoints=request.checkpoints,
        loras=request.loras,
        limit=999999
    )
    
    if not images:
        return {"message": "No images match the filters", "count": 0}
    
    os.makedirs(request.destination_folder, exist_ok=True)
    
    # Move all matching images
    moved = 0
    for image in images:
        if os.path.exists(image["path"]):
            try:
                move_image(image["id"], request.destination_folder, image["path"])
                moved += 1
            except Exception as e:
                print(f"Error moving {image['path']}: {e}")
    
    return {"message": f"Moved {moved} images", "count": moved}


# ============== Manual Sort Endpoints ==============

# Store for manual sort session
sort_session = {
    "active": False,
    "images": [],
    "current_index": 0,
    "folders": {},
    "history": []
}


@app.post("/api/sort/start")
async def start_sort_session(
    generators: Optional[str] = None,
    tags: Optional[str] = None,
    ratings: Optional[str] = None,
    checkpoints: Optional[str] = None,
    loras: Optional[str] = None,
    folders: Optional[str] = None  # JSON string of folder config
):
    """Start a manual sort session."""
    global sort_session
    
    gen_list = generators.split(",") if generators else None
    tag_list = tags.split(",") if tags else None
    rating_list = ratings.split(",") if ratings else None
    cp_list = checkpoints.split(",") if checkpoints else None
    lr_list = loras.split(",") if loras else None
    
    # Combine ratings into tags
    if rating_list:
        tag_list = (tag_list or []) + rating_list
    
    images = db.get_images(
        generators=gen_list,
        tags=tag_list,
        ratings=rating_list,
        checkpoints=cp_list,
        loras=lr_list,
        limit=999999  # No limit
    )
    
    # Parse folder config
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


@app.get("/api/sort/current")
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


@app.post("/api/sort/action")
async def sort_action(action: str, folder_key: Optional[str] = None):
    """
    Perform a sort action.
    Actions: 'move' (with folder_key), 'skip', 'undo'
    """
    global sort_session
    
    if not sort_session["active"]:
        raise HTTPException(status_code=400, detail="No active sort session")
    
    if action == "undo" and sort_session["history"]:
        # Undo last action
        last = sort_session["history"].pop()
        if last["action"] == "move":
            # Move back
            image = db.get_image_by_id(last["image_id"])
            if image:
                move_image(last["image_id"], os.path.dirname(last["original_path"]), image["path"])
        sort_session["current_index"] = max(0, sort_session["current_index"] - 1)
        return {"status": "undone", "current_index": sort_session["current_index"]}
    
    if sort_session["current_index"] >= len(sort_session["images"]):
        return {"done": True}
    
    current = sort_session["images"][sort_session["current_index"]]
    
    if action == "move" and folder_key:
        folder = sort_session["folders"].get(folder_key)
        if folder and os.path.exists(current["path"]):
            original_path = current["path"]
            new_path = move_image(current["id"], folder, current["path"])
            sort_session["history"].append({
                "action": "move",
                "image_id": current["id"],
                "original_path": original_path,
                "new_path": new_path,
                "folder_key": folder_key
            })
    elif action == "skip":
        sort_session["history"].append({
            "action": "skip",
            "image_id": current["id"]
        })
    
    sort_session["current_index"] += 1
    
    # Get next image
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


@app.post("/api/sort/set-folders")
async def set_sort_folders(config: FolderConfig):
    """Set folder destinations for sort keys."""
    global sort_session
    
    # Validate and create folders
    for key, path in config.folders.items():
        if path:
            os.makedirs(path, exist_ok=True)
    
    sort_session["folders"] = config.folders
    return {"status": "ok", "folders": sort_session["folders"]}


@app.get("/api/sort/folders")
async def get_sort_folders():
    """Get current folder configuration."""
    return {"folders": sort_session["folders"]}


# ============== New Endpoints ==============

@app.delete("/api/clear-gallery")
async def clear_gallery():
    """Clear all image records from the database."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM images")
        cursor.execute("DELETE FROM tags")
    return {"status": "ok", "message": "Gallery cleared"}


@app.get("/api/analytics")
async def get_analytics():
    """Get popular tags, checkpoints, and loras."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        
        # Top Checkpoints
        cursor.execute("""
            SELECT checkpoint, COUNT(*) as count 
            FROM images 
            WHERE checkpoint IS NOT NULL 
            GROUP BY checkpoint 
            ORDER BY count DESC 
            LIMIT 20
        """)
        checkpoints = [dict(row) for row in cursor.fetchall()]
        
        # Top Loras (stored as JSON array)
        # We need to parse all loras and count them
        cursor.execute("SELECT loras FROM images WHERE loras IS NOT NULL AND loras != '[]'")
        all_loras_json = cursor.fetchall()
        lora_counts = {}
        for row in all_loras_json:
            try:
                ls = json.loads(row[0])
                for l in ls:
                    lora_counts[l] = lora_counts.get(l, 0) + 1
            except:
                pass
        
        sorted_loras = sorted(lora_counts.items(), key=lambda x: x[1], reverse=True)[:20]
        loras = [{"lora": l, "count": c} for l, c in sorted_loras]
        
        # Top Tags (already in db)
        tags = db.get_all_tags()[:20]
        
    return {
        "checkpoints": checkpoints,
        "loras": loras,
        "top_tags": tags
    }


# ============== Stats Endpoints ==============

@app.get("/api/stats")
async def get_stats():
    """Get database statistics."""
    analytics = await get_analytics()
    return {
        "total_images": db.get_image_count(),
        "generators": db.get_all_generators(),
        "top_tags": analytics["top_tags"],
        "checkpoints": analytics["checkpoints"],
        "loras": analytics["loras"]
    }


# ============== Export Endpoints ==============

@app.post("/api/export-tags-batch")
async def export_tags_batch(request: BatchTagExportRequest):
    """
    Export tags for each image to individual .txt files.
    Each file is named {image_basename}.txt with comma-separated tags.
    """
    if not os.path.exists(request.output_folder):
        try:
            os.makedirs(request.output_folder, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot create output folder: {e}")
    
    # Normalize blacklist (lowercase, strip)
    blacklist = set(tag.strip().lower() for tag in (request.blacklist or []))
    prefix = request.prefix or ""
    
    exported = 0
    errors = []
    
    for image_id in request.image_ids:
        image = db.get_image_by_id(image_id)
        if not image:
            errors.append(f"Image {image_id} not found")
            continue
        
        # Get image tags
        tags = db.get_image_tags(image_id)
        
        # Filter out blacklisted tags
        filtered_tags = [t["tag"] for t in tags if t["tag"].lower() not in blacklist]
        
        # Build tag string with prefix
        tag_string = prefix + ", ".join(filtered_tags) if filtered_tags else prefix.rstrip(", ")
        
        # Get output filename (same as image but .txt)
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


# ============== Censor Endpoints ==============

# Lazy load censor module
_censor_detector = None


@app.post("/api/censor/detect")
async def censor_detect(request: CensorDetectRequest):
    """
    Run detection on an image to find regions to censor.
    Returns list of detected regions with class names and confidence.
    """
    global _censor_detector
    
    import traceback
    from censor import CensorDetector
    
    # Get image path
    image = db.get_image_by_id(request.image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    if not os.path.exists(request.model_path):
        raise HTTPException(status_code=400, detail=f"Model file not found: {request.model_path}")
    
    try:
        # Load or reuse detector
        if _censor_detector is None or _censor_detector.model_path != request.model_path or _censor_detector.session is None:
            if _censor_detector is not None and _censor_detector.session is None:
                print("Censor detector exists but session is None, re-loading...")
            
            print(f"Loading censor model: {request.model_path}")
            _censor_detector = CensorDetector(request.model_path)
            _censor_detector.load()
            print("Model loaded successfully")
        
        print(f"Running detection on: {image['path']}")
        detections = _censor_detector.detect(image["path"], request.confidence_threshold)
        print(f"Found {len(detections)} detections")
        
        return {
            "status": "ok",
            "image_id": request.image_id,
            "detections": detections
        }
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Detection error:\n{error_trace}")
        
        # Provide more helpful error for common issues
        msg = str(e)
        if "Protobuf" in msg:
            msg = "Model format error. If using a .pt file, ensure 'ultralytics' is installed. If using .onnx, the file may be corrupted."
            
        raise HTTPException(status_code=500, detail=f"Detection failed: {msg}")


@app.post("/api/censor/preview")
async def censor_preview(request: CensorApplyRequest):
    """
    Apply censoring and return base64 preview image.
    """
    from censor import Censor
    from PIL import Image
    import base64
    from io import BytesIO
    
    image_data = db.get_image_by_id(request.image_id)
    if not image_data:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image_data["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    try:
        # Load image
        image = Image.open(image_data["path"]).convert('RGB')
        
        # Convert regions to tuples
        regions = [tuple(r) for r in request.regions]
        
        # Apply censoring
        censored = Censor.apply_censoring(
            image,
            regions,
            style=request.style,
            block_size=request.block_size,
            blur_radius=request.blur_radius,
            sticker_path=request.sticker_path
        )
        
        # Convert to base64
        buffer = BytesIO()
        censored.save(buffer, format='JPEG', quality=90)
        buffer.seek(0)
        b64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        return {
            "status": "ok",
            "preview": f"data:image/jpeg;base64,{b64_image}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {str(e)}")


@app.post("/api/censor/save")
async def censor_save(request: CensorSaveRequest):
    """
    Apply censoring and save to output folder.
    """
    from censor import Censor
    from PIL import Image
    
    image_data = db.get_image_by_id(request.image_id)
    if not image_data:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image_data["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    # Create output folder if needed
    if not os.path.exists(request.output_folder):
        try:
            os.makedirs(request.output_folder, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot create output folder: {e}")
    
    try:
        # Load image
        image = Image.open(image_data["path"]).convert('RGB')
        
        # Convert regions to tuples
        regions = [tuple(r) for r in request.regions]
        
        # Apply censoring
        censored = Censor.apply_censoring(
            image,
            regions,
            style=request.style,
            block_size=request.block_size,
            blur_radius=request.blur_radius,
            sticker_path=request.sticker_path
        )
        
        # Generate output filename
        base_name = os.path.splitext(image_data["filename"])[0]
        ext = os.path.splitext(image_data["filename"])[1] or ".png"
        output_filename = f"{base_name}{request.filename_suffix}{ext}"
        output_path = os.path.join(request.output_folder, output_filename)
        
        # Save
        if ext.lower() in ['.jpg', '.jpeg']:
            censored.save(output_path, format='JPEG', quality=95)
        else:
            censored.save(output_path, format='PNG')
        
        return {
            "status": "ok",
            "output_path": output_path,
            "filename": output_filename
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")


class CensorSaveDataRequest(BaseModel):
    """Request to save base64 image data directly."""
    image_data: str  # Base64 data URL
    filename: str
    output_folder: str
    metadata_option: str = "keep"  # 'keep' to preserve metadata, 'wash' to strip all metadata
    original_image_id: Optional[int] = None  # ID of original image to copy metadata from


@app.post("/api/censor/save-data")
async def censor_save_data(request: CensorSaveDataRequest):
    """
    Save base64 image data directly to disk.
    Used for saving canvas-edited images.
    Supports metadata handling: 'keep' preserves original metadata, 'wash' strips all metadata.
    """
    from PIL import Image, PngImagePlugin
    from io import BytesIO
    
    try:
        # Create output folder if needed
        if not os.path.exists(request.output_folder):
            os.makedirs(request.output_folder, exist_ok=True)
        
        # Parse base64 data URL
        if ',' in request.image_data:
            header, data = request.image_data.split(',', 1)
        else:
            data = request.image_data
        
        # Decode base64
        image_bytes = base64.b64decode(data)
        image = Image.open(BytesIO(image_bytes))
        
        # Generate output filename
        base_name = os.path.splitext(request.filename)[0]
        ext = os.path.splitext(request.filename)[1] or ".png"
        output_filename = f"{base_name}_censored{ext}"
        output_path = os.path.join(request.output_folder, output_filename)
        
        # Handle metadata based on option
        save_kwargs = {}
        if request.metadata_option == "keep" and request.original_image_id:
            # Try to copy metadata from original image
            original_image_data = db.get_image_by_id(request.original_image_id)
            if original_image_data and os.path.exists(original_image_data["path"]):
                try:
                    original_img = Image.open(original_image_data["path"])
                    # Copy EXIF data if present
                    if hasattr(original_img, 'info'):
                        for key in ['exif', 'icc_profile', 'dpi']:
                            if key in original_img.info:
                                save_kwargs[key] = original_img.info[key]
                    # For PNG, copy text chunks (including generation parameters)
                    if ext.lower() == '.png' and hasattr(original_img, 'info'):
                        pnginfo = PngImagePlugin.PngInfo()
                        for key, value in original_img.info.items():
                            if isinstance(value, str):
                                pnginfo.add_text(key, value)
                        save_kwargs['pnginfo'] = pnginfo
                except Exception as e:
                    print(f"Warning: Could not copy metadata from original: {e}")
        # For 'wash' option, we simply don't include any metadata (default behavior)
        
        # Save with appropriate format
        if ext.lower() in ['.jpg', '.jpeg']:
            if image.mode == 'RGBA':
                image = image.convert('RGB')
            # Filter out non-JPEG compatible kwargs
            jpeg_kwargs = {k: v for k, v in save_kwargs.items() if k in ['exif', 'icc_profile', 'dpi']}
            image.save(output_path, format='JPEG', quality=95, **jpeg_kwargs)
        elif ext.lower() == '.webp':
            # WebP supports EXIF
            webp_kwargs = {k: v for k, v in save_kwargs.items() if k in ['exif', 'icc_profile']}
            image.save(output_path, format='WEBP', quality=95, **webp_kwargs)
        else:
            # PNG
            png_kwargs = {k: v for k, v in save_kwargs.items() if k in ['pnginfo', 'dpi']}
            image.save(output_path, format='PNG', **png_kwargs)
        
        return {
            "status": "ok",
            "output_path": output_path,
            "filename": output_filename
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")


# ============== Run Server ==============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
