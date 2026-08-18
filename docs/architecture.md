# SD Image Sorter Architecture

## System Overview

SD Image Sorter is a local web application for managing, tagging, sorting, and censoring Stable Diffusion generated images. It runs as a FastAPI backend serving a vanilla HTML/JS/CSS frontend on `127.0.0.1:8487` by default (configurable via `SD_IMAGE_SORTER_PORT`).

## Architecture Diagram

```
+-------------------+     HTTP REST API     +-------------------+
|                   | <-------------------> |                   |
|    Browser UI     |                       |   FastAPI Backend |
| (HTML/JS/CSS)     |                       |  (Python 3.12+)   |
|                   |                       |                   |
+-------------------+                       +--------+----------+
                                                     |
                    +----------------+---------------+----------------+
                    |                |               |                |
             +------v------+  +------v------+ +------v------+ +------v------+
             |   SQLite    |  |  AI Models  | |   File      | |  Thumbnail  |
             |  Database   |  |  (ONNX)     | |   System    | |   Cache     |
             +-------------+  +-------------+ +-------------+ +-------------+
```

## Core Components

### 1. Backend (`backend/`)

#### Entry Point
- **`main.py`**: FastAPI application assembly, service initialization, router mounting, exception handlers, and process startup.
- **`app_security.py`**: CORS, localhost-only enforcement, in-memory API rate limiting, and security response headers.
- **`app_static.py`**: `/static` mounting, no-cache static responses, and `GET /` cache-bust injection for frontend JS/CSS.
- **`app_diagnostics.py`**: Bounded support diagnostics, support-log redaction, and file-manager opening for the support log.

#### Routers (`routers/`)
Each router handles a specific domain:
- **`images.py`**: Image retrieval, filtering, file serving, thumbnail generation
- **`tags.py`**: WD14 AI tagging, tag CRUD operations, import/export
- **`sorting.py`**: Folder scanning, batch operations, manual sort sessions
- **`censor.py`**: NSFW detection (YOLOv8/NudeNet/SAM3), censoring preview/save
- **`similarity.py`**: CLIP embedding, similarity search, duplicate detection
- **`prompts.py`**: Prompt generation, tag categorization, exclusion rules
- **`artists.py`**: Artist/style identification (experimental)
- **`models.py`**: Local model/runtime preparation and readiness
- **`obfuscation.py`**: Image obfuscation encode/decode/batch operations
- **`aesthetic.py`**: Local aesthetic scoring (single and batch)
- **`updates.py`**: In-app update status/channel/apply workflow

#### Services (`services/`)
Business logic layer with dependency injection:
- **`image_service.py`**: Image operations, thumbnail caching
- **`image_metadata_writer.py`**: Reader metadata edit/save helpers for PNG/JPEG/WebP output
- **`tagging_service.py`**: WD14 tagger integration, tagging pipeline
- **`sorting_service.py`**: Scan, move, sort session management
- **`sorting_models.py`**: Sorting API request models and validation constants
- **`sorting_session_store.py`**: Manual sort session file persistence helpers
- **`censor_service.py`**: Detection and censoring operations
- **`similarity_service.py`**: CLIP embedding and similarity search

#### Core Modules
- **`database.py`**: SQLite layer with raw SQL (no ORM), connection pooling
- **`metadata_parser.py`**: SD metadata extraction (ComfyUI/NAI/WebUI/Forge)
- **`image_manager.py`**: File operations (scan, move, copy, delete)
- **`tagger.py`**: WD14 tagger via ONNX Runtime
- **`censor.py`**: YOLOv8 ONNX + Pillow censoring
- **`model_health.py`**: Unified local model discovery and readiness reporting
- **`similarity.py`**: CLIP-based image similarity
- **`artist_identifier.py`**: Artist classification (experimental)

#### Utilities (`utils/`)
- **`path_validation.py`**: Path traversal prevention, filename sanitization

### 2. Frontend (`frontend/`)

Single-page application with no build step. Every script is a classic
`<script src>` tag in `index.html` sharing one global lexical scope, so **tag
order is the dependency graph**. The only dynamic loader is
`dataset/core.js:_appendOrderedScript`, which appends the rest of the Dataset
Maker family.

- **`index.html`**: every view, modal and overlay in one file
- **`js/modules/core/`**: the earliest prerequisites, loaded before anything
  else (`storage-utils.js`, `request-manager.js`)
- **`js/stores/`**: `FilterStore` / `SelectionStore`. Both hold key-by-key
  allowlists — a new filter field must be added to **both**, or it is silently
  dropped
- **`js/app.js`**: boot remainder only (~245 lines). The former god file was
  decomposed into **`js/app/`** (41 modules: state-core, api, filters, flows,
  binders)
- **Feature families**, each a directory with an ordered set of modules:
  `gallery/`, `censor/`, `dataset/`, `manual-sort/`, `autosep/`, `similar/`,
  `prompt-lab/`, `artist/`, `image-reader/`, `reverse-prompt/`, `smart-tag/`,
  `vlm-caption/`, `guide/`, `v321/`
- **`js/gallery.js`, `similar.js`, `prompt-lab.js`, `artist-ident.js`**: 8–10
  line compatibility shims that only point at the directory above. Do not add
  behavior to them
- **`js/lang/en.js`, `js/lang/zh-CN.js`**: locale packs with **identical key
  sets** — that symmetry is a contract enforced by a test
- **`js/theme.js`**: the Graphite / Black+Blue palette picker

Stylesheets load `styles.css` first, then the feature sheets, then
**`css/tokens.css` LAST — and that ordering is load-bearing.** `tokens.css` is
an *override layer*, not a cleaned source of truth: `styles.css` and
`ui-refresh.css` still hold their original hardcoded colors, radii and
line-heights, and `tokens.css` wins only because it is last. Deleting or
"tidying" those sheets on the assumption that `tokens.css` is authoritative
will break the design. The current visual language is flat graphite with one
accent — not glassmorphism, which `tokens.css` explicitly retired.

### 3. Runtime Layout

Package-local runtime state is stored outside `backend/` code paths:

- `data/images.db` (default SQLite path)
- `data/thumbnails/` (thumbnail cache)
- `data/models/` (downloaded runtime models)
- `data/state/sort-session.json` (manual sort persisted session)
- `update/` (update downloads/logs/state/worker workspaces)

All runtime paths are configurable by environment variables in `backend/config.py`.

### 4. Database Schema

SQLite database defaults to `data/images.db`:

```sql
-- Core tables
images (id, path, filename, generator, prompt, negative_prompt,
        checkpoint, loras, width, height, file_size, metadata_json,
        created_at, tagged_at)

tags (id, image_id, tag, confidence)

collections (id, slug, name, description)
collection_items (id, collection_id, source_image_id, copied_path, ...)

-- Prompt Lab tables
tag_categories (tag, category, is_user_defined)
tag_sets (id, name, description, category)
tag_set_members (set_id, tag, weight, is_required)
tag_exclusions (id, rule_name, description)
tag_exclusion_conditions (exclusion_id, condition_tag, condition_type)
tag_exclusion_targets (exclusion_id, excluded_tag, excluded_category)
prompt_presets (id, name, config_json, created_at)

-- Similarity tables
image_embeddings (image_id, embedding BLOB)

-- Artist identification tables
artist_predictions (image_id, artist, confidence, top_predictions)
```

### 5. AI Models

Models are loaded lazily on first use:

| Model | Purpose | Format | Typical local size | Notes |
|-------|---------|--------|--------------------|-------|
| WD14 SwinV2 | Default auto-tagging | ONNX | ~446MB | Preferred out-of-box tagger |
| WD14 EVA02 | Optional high-quality tagging | ONNX | ~1.2GB | Heavier optional pack |
| Wenaka privacy YOLO | Privacy-part censor detection | ONNX/.pt | ~46MB / ~23MB | Recommended legacy censor model; app positions it as the fast fixed-class privacy detector |
| YOLO26 / YOLOv8s | Compatibility object segmentation | ONNX/.pt | ~40-45MB / ~22MB | Verified; current packaged files are fixed-class COCO models rather than open-text detectors |
| NudeNet | Body part detection | ONNX | ~12MB | Works without manual path selection |
| Kaloscope2.0 | Experimental artist identification | `.pth` | ~2.8GB | Requires LSNet runtime checkout |
| CLIP | Image similarity | ONNX | ~335MB | Local-first model path now reported in UI |
| SAM3 | Optional mask refinement | `.pt` | ~3.3GB | Current verified setup should be treated as CUDA-only |

## Data Flow

### Image Scan Flow
```
1. User specifies folder path
2. Backend validates path (prevent traversal)
3. Recursive file scan with extension filter
4. For each image:
   a. Extract metadata (generator-specific parser)
   b. Calculate dimensions and file size
   c. Insert/update database record
5. Return scan statistics
```

### Tagging Flow
```
1. User starts tagging (all untagged or specific images)
2. Background task loads WD14 model (singleton)
3. For each image:
   a. Load and resize image
   b. Run ONNX inference
   c. Post-process predictions
   d. Store tags with confidence scores
4. Progress tracked via polling endpoint with step/count metadata
```

### Censor Flow
```
1. User selects image and detection backend
2. Backend runs detection (Legacy YOLO / NudeNet / both)
3. Frontend explains the selected model's real capability profile (fixed privacy labels vs fixed general classes vs SAM3 prompt-guided masks)
4. Detection returns box-first results for the simple flow
5. User adjusts mask manually or uses optional SAM3 text/box-guided refinement when CUDA-ready
6. Preview/apply censoring (mosaic/blur/solid)
7. Save censored image to output folder
```

## Security Architecture

### Path Validation
All file-accepting endpoints use `utils/path_validation.py`:
- Prevents directory traversal (`../`, symlinks)
- Validates file extensions
- Sanitizes filenames
- Enforces path depth limits

### SQL Injection Prevention
- Parameterized queries throughout
- No string concatenation in SQL
- Input validation at API layer

### Local-Only Access
- No authentication (intentional for local tool)
- CORS is restricted to loopback origins by regex, and `app_security.py` rejects non-loopback client IPs even if a future launcher widens the bind host.

## Architecture Guardrails

The project is intentionally a local-first monolith, but individual files should not become unbounded monoliths.

### Backend

- `main.py` is an application composition file. Do not move security middleware, static asset serving, cache busting, support diagnostics, or OS file-manager logic back into it.
- Routers should own HTTP request/response contracts and framework background-task scheduling. Business workflow state belongs in services.
- Sorting request schemas belong in `services/sorting_models.py`; manual-sort JSON file IO belongs in `services/sorting_session_store.py`. Keep `sorting_service.py` focused on workflow orchestration and compatibility state.
- Very large service files are allowed only as temporary refactor waypoints. New cross-feature helpers should be extracted into focused service modules instead of growing `image_service.py`, `sorting_service.py`, or `smart_tag_service.py`.
- Data mutation invariants such as path identity, derived-state invalidation, and selection snapshots must have a single owner or a documented shared helper.

### Frontend

- `app.js` is boot-only. New reusable infrastructure goes under
  `frontend/js/modules/core/` or `frontend/js/modules/utils/`, loaded before
  `app.js` in `index.html`.
- Feature behavior lives in that feature's directory (`gallery/`, `censor/`,
  `dataset/`, `manual-sort/`, …), never in `app.js` or in the 8–10 line
  compatibility shims.
- `tokens.css` owns the palette and current chrome, and must stay the last
  stylesheet. Feature stylesheets own their own surfaces. Avoid competing
  selector ownership for the same layout shell.
- Dynamic text on an element that carries `data-i18n` will be reset by the
  `#app` MutationObserver in `ui-refresh.js`. Either repoint the `data-i18n`
  key or claim `dataset.i18nLocked = '1'`.
- Adding a palette means converting the feature stylesheets first. Only
  `tokens.css` has `data-theme` selectors today; the other sheets still carry
  hardcoded dark values, which is why a light theme is not offered.

## Performance Considerations

### Model Loading
- Singleton pattern for heavy models (WD14, YOLOv8, CLIP)
- Lazy loading on first request
- Models persist in memory for subsequent requests
- `model_health.py` keeps launcher output and frontend health banners aligned with the same local truth

### Thumbnail Caching
- Disk-based cache in `data/thumbnails/`
- WebP format for optimal compression
- Cache invalidation by source file modification time
- Configurable cleanup by age

### Database
- Connection pooling via context manager
- Indexes on frequently queried columns
- Cursor-based pagination for large datasets

## Error Handling

### API Layer
- Structured JSON error responses
- HTTP status codes follow REST conventions
- Detailed error messages for debugging

### Service Layer
- Custom exceptions in `exceptions.py`
- Graceful degradation for optional features
- Background task error tracking

## Extensibility

### Adding New Generators
1. Add detection logic in `metadata_parser.py`
2. Add generator name to `GENERATORS` list
3. UI automatically picks up new generator filter

### Adding New Endpoints
1. Create request/response models in service
2. Implement business logic in service class
3. Add route in appropriate router
4. Update API documentation

### Adding New AI Models
1. Create model loader (singleton pattern)
2. Add configuration in `config.py`
3. Create service methods for inference
4. Add router endpoints for API access
