"""
FastAPI backend for SD Image Sorter.
Provides REST API for image management, tagging, and sorting.

This is the main application entry point. Endpoints are organized into routers:
- routers/images.py - Image retrieval and serving
- routers/tags.py - Tag management and tagging
- routers/sorting.py - Scanning, moving, and manual sorting
- routers/censor.py - NSFW detection and censoring
"""
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

# Add current dir to path for imports
sys.path.insert(0, os.path.dirname(__file__))

import database as db

# Import routers
from routers import images, tags, sorting, censor


# Lazy import tagger to avoid loading model at startup
_tagger = None
_tagger_settings = {}


def get_tagger(
    model_name: str = None,
    model_path: str = None,
    tags_path: str = None,
    threshold: float = 0.35,
    character_threshold: float = 0.85,
    use_gpu: bool = True
):
    """Get or create the tagger instance with given settings."""
    global _tagger, _tagger_settings
    from tagger import get_tagger as _get_tagger, DEFAULT_MODEL
    
    model_name = model_name or DEFAULT_MODEL
    
    return _get_tagger(
        model_name=model_name,
        model_path=model_path,
        tags_path=tags_path,
        threshold=threshold,
        character_threshold=character_threshold,
        use_gpu=use_gpu
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown handler."""
    # Startup
    print("SD Image Sorter backend starting...")
    db.init_db()
    
    # Initialize the tags router with the tagger getter
    tags.set_tagger_getter(get_tagger)
    
    yield
    # Shutdown
    print("Shutting down...")


app = FastAPI(
    title="SD Image Sorter",
    description="Image management API for Stable Diffusion generated images",
    version="2.0.0",
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


# Include routers
app.include_router(images.router)
app.include_router(tags.router)
app.include_router(sorting.router)
app.include_router(censor.router)


@app.get("/")
async def root():
    """Serve the main frontend page."""
    index_path = os.path.join(frontend_path, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "SD Image Sorter API", "docs": "/docs"}


# ============== Run Server ==============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
