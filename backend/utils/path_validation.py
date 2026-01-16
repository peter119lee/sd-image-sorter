"""
Path validation utilities for secure file operations.
Prevents directory traversal attacks and validates file paths.
"""
import os
import re
from pathlib import Path
from typing import Optional, Tuple


# Allowed file extensions for images
ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}

# Allowed file extensions for models
ALLOWED_MODEL_EXTENSIONS = {'.onnx', '.pt', '.pth', '.safetensors'}


def is_safe_path(base_path: str, user_path: str) -> bool:
    """
    Check if a user-provided path is safely within the base path.
    Prevents directory traversal attacks like '../../../etc/passwd'.
    
    Args:
        base_path: The allowed base directory
        user_path: The user-provided path to validate
    
    Returns:
        True if path is safe, False otherwise
    """
    try:
        # Resolve both paths to absolute paths
        base = Path(base_path).resolve()
        target = Path(user_path).resolve()
        
        # Check if target is within base
        return str(target).startswith(str(base))
    except (ValueError, OSError):
        return False


def validate_folder_path(path: str, allow_create: bool = False) -> Tuple[bool, Optional[str]]:
    """
    Validate that a folder path is safe and exists (or can be created).
    
    Args:
        path: The folder path to validate
        allow_create: If True, the folder doesn't need to exist
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not path or not isinstance(path, str):
        return False, "Path cannot be empty"
    
    # Check for null bytes (path injection)
    if '\x00' in path:
        return False, "Invalid characters in path"
    
    # Resolve to absolute path
    try:
        resolved = Path(path).resolve()
    except (ValueError, OSError) as e:
        return False, f"Invalid path: {str(e)}"
    
    # Check if it's a reasonable file system path
    if len(str(resolved)) > 260:  # Windows MAX_PATH limit
        return False, "Path too long"
    
    if allow_create:
        # Check if parent directory exists or can be created
        parent = resolved.parent
        if not parent.exists():
            # Try to check if we can eventually create it
            root = resolved.anchor
            if not root or not Path(root).exists():
                return False, "Drive or root path does not exist"
        return True, None
    else:
        if not resolved.exists():
            return False, "Path does not exist"
        if not resolved.is_dir():
            return False, "Path is not a directory"
        return True, None


def validate_file_path(path: str, allowed_extensions: set = None) -> Tuple[bool, Optional[str]]:
    """
    Validate that a file path is safe and exists.
    
    Args:
        path: The file path to validate
        allowed_extensions: Set of allowed file extensions (e.g., {'.png', '.jpg'})
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not path or not isinstance(path, str):
        return False, "Path cannot be empty"
    
    # Check for null bytes
    if '\x00' in path:
        return False, "Invalid characters in path"
    
    try:
        resolved = Path(path).resolve()
    except (ValueError, OSError) as e:
        return False, f"Invalid path: {str(e)}"
    
    if not resolved.exists():
        return False, "File does not exist"
    
    if not resolved.is_file():
        return False, "Path is not a file"
    
    if allowed_extensions:
        ext = resolved.suffix.lower()
        if ext not in allowed_extensions:
            return False, f"File extension '{ext}' not allowed"
    
    return True, None


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to remove potentially dangerous characters.
    
    Args:
        filename: The filename to sanitize
    
    Returns:
        Sanitized filename
    """
    if not filename:
        return "unnamed"
    
    # Remove path separators
    filename = os.path.basename(filename)
    
    # Remove or replace dangerous characters
    # Keep alphanumeric, spaces, dots, underscores, hyphens
    sanitized = re.sub(r'[^\w\s\.\-]', '_', filename, flags=re.UNICODE)
    
    # Remove leading/trailing dots and spaces
    sanitized = sanitized.strip('. ')
    
    # Ensure we don't end up with an empty string
    if not sanitized:
        return "unnamed"
    
    # Limit length
    if len(sanitized) > 200:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:200 - len(ext)] + ext
    
    return sanitized


def validate_output_path(path: str, filename: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validate an output path and filename, creating directory if needed.
    
    Args:
        path: The output directory path
        filename: The desired filename
    
    Returns:
        Tuple of (is_valid, error_message, full_output_path)
    """
    is_valid, error = validate_folder_path(path, allow_create=True)
    if not is_valid:
        return False, error, None
    
    safe_filename = sanitize_filename(filename)
    
    try:
        resolved_dir = Path(path).resolve()
        full_path = resolved_dir / safe_filename
        
        # Verify the full path is still within the target directory
        if not str(full_path.resolve()).startswith(str(resolved_dir)):
            return False, "Invalid filename", None
        
        return True, None, str(full_path)
    except (ValueError, OSError) as e:
        return False, f"Invalid path: {str(e)}", None
