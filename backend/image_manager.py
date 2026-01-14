"""
Image manager for file operations (scanning, moving, copying).
"""
import os
import shutil
from typing import List, Dict, Any, Optional, Generator
from datetime import datetime
from pathlib import Path
import json

from database import add_image, update_image_path, get_images, add_tags
from metadata_parser import parse_image


# Supported image extensions
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}


def scan_folder(
    folder_path: str,
    recursive: bool = True,
    progress_callback: Optional[callable] = None
) -> Dict[str, Any]:
    """
    Scan a folder for images and add them to the database.
    
    Args:
        folder_path: Path to scan
        recursive: Whether to scan subdirectories
        progress_callback: Optional callback(current, total, filename)
    
    Returns:
        {
            "total": int,
            "new": int,
            "updated": int,
            "errors": int,
            "by_generator": {generator: count}
        }
    """
    result = {
        "total": 0,
        "new": 0,
        "updated": 0,
        "errors": 0,
        "by_generator": {}
    }
    
    # Collect all image files
    image_files = []
    folder = Path(folder_path)
    
    pattern = "**/*" if recursive else "*"
    for file_path in folder.glob(pattern):
        if file_path.is_file() and file_path.suffix.lower() in IMAGE_EXTENSIONS:
            image_files.append(str(file_path))
    
    result["total"] = len(image_files)
    
    # Process each image
    for i, image_path in enumerate(image_files):
        try:
            if progress_callback:
                progress_callback(i + 1, result["total"], os.path.basename(image_path))
            
            # Parse metadata
            metadata = parse_image(image_path)
            
            # Get file timestamps
            stat = os.stat(image_path)
            created_at = datetime.fromtimestamp(stat.st_mtime)
            
            # Serialize metadata safely
            try:
                metadata_json = json.dumps(metadata["metadata"])
            except (TypeError, ValueError) as e:
                print(f"Warning: Could not serialize metadata for {image_path}: {e}")
                metadata_json = "{}"
            
            # Add to database
            add_image(
                path=image_path,
                filename=os.path.basename(image_path),
                generator=metadata["generator"],
                prompt=metadata["prompt"],
                negative_prompt=metadata["negative_prompt"],
                metadata_json=metadata_json,
                width=metadata["width"],
                height=metadata["height"],
                file_size=metadata["file_size"],
                checkpoint=metadata["checkpoint"],
                loras=metadata["loras"],
                created_at=created_at
            )
            
            result["new"] += 1
            
            # Track by generator
            gen = metadata["generator"]
            result["by_generator"][gen] = result["by_generator"].get(gen, 0) + 1
            
        except Exception as e:
            print(f"Error processing {image_path}: {e}")
            import traceback
            traceback.print_exc()
            result["errors"] += 1
    
    return result


def move_image(image_id: int, destination_folder: str, image_path: str) -> str:
    """
    Move an image to a new folder.
    
    Args:
        image_id: Database ID of the image
        destination_folder: Target folder path
        image_path: Current path of the image
    
    Returns:
        New path of the image
    """
    os.makedirs(destination_folder, exist_ok=True)
    
    filename = os.path.basename(image_path)
    new_path = os.path.join(destination_folder, filename)
    
    # Handle filename conflicts
    if os.path.exists(new_path) and new_path != image_path:
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(new_path):
            new_filename = f"{base}_{counter}{ext}"
            new_path = os.path.join(destination_folder, new_filename)
            counter += 1
    
    # Move file
    shutil.move(image_path, new_path)
    
    # Update database
    update_image_path(image_id, new_path)
    
    return new_path


def copy_image(image_path: str, destination_folder: str) -> str:
    """
    Copy an image to a new folder.
    
    Args:
        image_path: Path of the image to copy
        destination_folder: Target folder path
    
    Returns:
        Path of the copied image
    """
    os.makedirs(destination_folder, exist_ok=True)
    
    filename = os.path.basename(image_path)
    new_path = os.path.join(destination_folder, filename)
    
    # Handle filename conflicts
    if os.path.exists(new_path):
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(new_path):
            new_filename = f"{base}_{counter}{ext}"
            new_path = os.path.join(destination_folder, new_filename)
            counter += 1
    
    shutil.copy2(image_path, new_path)
    return new_path


def batch_move(
    image_ids: List[int],
    image_paths: List[str],
    destination_folder: str,
    progress_callback: Optional[callable] = None
) -> Dict[str, Any]:
    """
    Move multiple images to a folder.
    
    Returns:
        {
            "total": int,
            "moved": int,
            "errors": int,
            "new_paths": [str]
        }
    """
    result = {
        "total": len(image_ids),
        "moved": 0,
        "errors": 0,
        "new_paths": []
    }
    
    for i, (img_id, img_path) in enumerate(zip(image_ids, image_paths)):
        try:
            if progress_callback:
                progress_callback(i + 1, result["total"], os.path.basename(img_path))
            
            new_path = move_image(img_id, destination_folder, img_path)
            result["new_paths"].append(new_path)
            result["moved"] += 1
        except Exception as e:
            print(f"Error moving {img_path}: {e}")
            result["errors"] += 1
    
    return result


def get_folder_stats(folder_path: str) -> Dict[str, Any]:
    """Get statistics about a folder's images."""
    folder = Path(folder_path)
    
    stats = {
        "total_files": 0,
        "total_size": 0,
        "by_extension": {}
    }
    
    for file_path in folder.rglob("*"):
        if file_path.is_file():
            ext = file_path.suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                stats["total_files"] += 1
                stats["total_size"] += file_path.stat().st_size
                stats["by_extension"][ext] = stats["by_extension"].get(ext, 0) + 1
    
    return stats
