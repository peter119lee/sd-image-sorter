# SD Image Sorter

## Project Overview

A local web application for managing, tagging, sorting, and censoring Stable Diffusion generated images. Runs as a FastAPI backend serving a vanilla HTML/JS/CSS frontend on `127.0.0.1:8487` by default (configurable via `SD_IMAGE_SORTER_PORT`).

## Target Platform & Supported Resolutions — READ FIRST

**This is desktop/laptop computer software. ONLY support laptop and desktop screen resolutions. Do NOT spend any effort on mobile or tablet.**

- ✅ **In scope:** laptop + desktop viewports, roughly **1280px wide and up** — e.g. 1280×720/800 (small laptop), 1366×768 (most common laptop), 1440×900, 1536×864, 1920×1080, 2560×1440, 3440×1440 (ultrawide), 3840×2160 (4K). Test, audit, and optimize here.
- ❌ **OUT OF SCOPE — do not test, audit, optimize, screenshot, or "fix" anything here:** phones and tablets — any width below ~1280px (320 / 375 / 390 / 414 / 768 / 1024). Do not add new mobile/tablet responsive work. Do not run responsive sweeps at these widths.
- The global rule `~/.Codex/rules/web/testing.md` lists breakpoints `320 / 375 / 768 / 1024 / 1440 / 1920` and "screenshot key breakpoints 320/768/1024/1440". **For THIS project that is overridden** — ignore everything below ~1280px; use desktop/laptop widths only (1366, 1440, 1920, 2560, 3840).
- Existing mobile/tablet responsive CSS already in the tree (e.g. censor stacking ≤960px) is harmless — **leave it as-is; do not invest more in it and do not rip it out** (removing it is itself wasted mobile effort and risks regressions). Width-agnostic fixes that also improve desktop (e.g. empty-state cleanups) are fine.

> Owner directive (2026-06-05, stated emphatically and repeatedly): "We are computer software. Just take care of laptop and desktop resolutions." Auditing phone/tablet widths was explicitly called out as wasted effort. Honor this for ALL UI/UX, layout, testing, and review work.

## Architecture

```
Browser (127.0.0.1:8487 default)
  |
  | HTTP REST API
  v
FastAPI (backend/main.py)
  - application assembly, service initialization, router mounting
  |
  +-- app_static.py       → GET /, /static/*, JS/CSS cache busting
  +-- app_security.py     → CORS, localhost-only guard, rate limit, security headers
  +-- app_diagnostics.py  → support log diagnostics and file-manager open flow
  |
  +-- /api/*              → REST endpoints via routers
  |
  +-- routers/images.py      → image retrieval, thumbnails
  +-- routers/tags.py       → AI tagging (WD14 ONNX), tag CRUD
  +-- routers/sorting.py    → scan folders, move/batch-move, WASD manual sort
  +-- routers/censor.py     → YOLOv8 detection + Pillow censoring
  +-- routers/prompts.py    → prompt generation endpoints
  +-- routers/similarity.py → CLIP embedding similarity search
  +-- routers/artists.py    → artist identification
  |
  +-- database.py         → SQLite (raw SQL, no ORM)
  +-- metadata_parser.py  → SD metadata extraction (ComfyUI/NAI/WebUI/Forge)
  +-- image_manager.py    → file operations (scan, move, copy)
  +-- tagger.py           → WD14 tagger via ONNX Runtime
  +-- censor.py           → YOLOv8 ONNX + Pillow
  +-- services/           → business logic and feature orchestration
```

## Tech Stack

- **Backend**: Python 3.12+, FastAPI, Uvicorn, SQLite, Pillow, ONNX Runtime
- **Frontend**: Vanilla HTML5 / CSS3 / JavaScript (no framework, no build step)
- **AI Models**: WD14 Tagger (ONNX, from HuggingFace), YOLOv8 (segmentation, .pt/.onnx)
- **UI Style**: Graphite (flat neutral surfaces, one amber accent; `tokens.css` last)

## Quick Start

```bash
# Windows
run.bat

# Linux/Mac
./run.sh
```

Both scripts auto-create a Python venv in `backend/venv/` and install dependencies on first run.

## Project Structure

```
sd-image-sorter/
├── backend/
│   ├── main.py               # FastAPI app entry point
│   ├── app_security.py       # Security middleware wiring
│   ├── app_static.py         # Static frontend serving and cache busting
│   ├── app_diagnostics.py    # Support diagnostics and log opening
│   ├── database.py           # SQLite layer (raw SQL)
│   ├── metadata_parser.py    # SD image metadata extraction
│   ├── image_manager.py      # File operations (scan, move, copy)
│   ├── tagger.py             # WD14 AI tagger (ONNX Runtime)
│   ├── censor.py             # YOLOv8 detection + censoring
│   ├── requirements.txt      # Python dependencies
│   ├── routers/
│   │   ├── images.py         # GET /api/images, /api/image-file, /api/image-thumbnail
│   │   ├── tags.py           # Tag CRUD, tagging pipeline, library endpoints
│   │   ├── sorting.py        # Scan, move, batch-move, manual sort (WASD)
│   │   ├── censor.py         # YOLO detect, preview, save endpoints
│   │   ├── prompts.py        # Prompt generation endpoints
│   │   ├── similarity.py     # CLIP embedding similarity search
│   │   ├── artists.py        # Artist identification
│   ├── services/
│   │   ├── image_service.py          # Image workflows
│   │   ├── image_metadata_writer.py  # Reader metadata save helpers
│   │   ├── sorting_service.py        # Sorting workflow orchestration
│   │   ├── sorting_models.py         # Sorting API request models
│   │   └── sorting_session_store.py  # Manual sort session file persistence
│   └── utils/
│       └── path_validation.py  # Path traversal prevention
├── frontend/
│   ├── index.html            # Single-page app (all views in one file)
│   ├── css/
│   │   ├── styles.css        # Base layout and component styles
│   │   └── censor-v2.css     # Censor editor styles
│   └── js/
│       ├── modules/core/     # earliest prerequisites (storage, request manager)
│       ├── stores/           # FilterStore / SelectionStore (key-by-key allowlists —
│       │                     #   a new filter field MUST be added to BOTH functions)
│       ├── app/              # the former app.js god file, decomposed 2026-07 into
│       │                     #   41 modules (state-core, api, filters, flows, binders);
│       │                     #   static script tags, dependency order, one shared
│       │                     #   classic-script global scope
│       ├── app.js            # boot remainder only (~240 lines): initEventListeners
│       │                     #   composer + DOMContentLoaded + buildAppContext/seal
│       ├── gallery.js        # servable shim; gallery grid lives in gallery/
│       ├── dataset/          # Dataset Maker family (27 by-feature modules, dynamic
│       │                     #   _appendOrderedScript loader in dataset/core.js)
│       ├── censor/           # Censor editor family (16 ordered classic scripts)
│       ├── autosep.js        # servable shim; Auto-Separate lives in autosep/
│       ├── manual-sort.js    # servable shim; WASD session lives in manual-sort/
│       └── audio.js          # Sound effects for sorting
├── models/
│   └── yolo/                 # YOLOv8 segmentation model
├── docs/                     # Screenshots and docs
├── run.bat                   # Windows launcher
├── run.sh                    # Linux/Mac launcher
└── README.md                 # Bilingual EN/ZH documentation
```

## Key Features

1. **Gallery**: Scan folders for images, grid view with generator tabs, advanced filtering
2. **Metadata Parsing**: Auto-detect ComfyUI, NovelAI, WebUI, Forge generators; extract prompts, checkpoints, LoRAs
3. **AI Tagging**: WD14 tagger (default `wd-swinv2-tagger-v3`, the balanced/recommended model; EVA02-Large is the opt-in max-quality choice) with general/character/rating tags
4. **Sorting**: Auto-separate by filter + WASD manual keyboard sort with undo
5. **Censor Editor**: Canvas-based with brush/pen/eraser/clone, AI auto-detection, batch processing

## Supported Metadata Formats

- **ComfyUI**: JSON workflow in PNG `prompt`/`workflow` text chunks
- **NovelAI**: JSON in `Comment` PNG text chunk (has `prompt` + `uc` keys)
- **WebUI/A1111**: `parameters` PNG text chunk (`prompt\nNegative prompt: neg\nSteps: ...`)
- **Forge**: Same as WebUI but with "Forge" in parameters string
- **WebP**: EXIF + XMP chunk parsing for SD metadata

## Development Notes

### Running the Backend Directly

```bash
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python main.py
```

Server runs at `http://127.0.0.1:8487` by default. API docs at `/docs`.

### Database

SQLite database defaults to `data/images.db` (override `SD_IMAGE_SORTER_DB_PATH`). Schema migrates on startup via `database.init_db()`.

### Important Patterns

- **Singleton models**: Tagger and censor detector are singletons to avoid reloading 500MB+ models
- **Lazy imports**: Heavy dependencies (ONNX, HuggingFace) imported on first use
- **Background tasks**: Scanning and tagging run as FastAPI BackgroundTasks, polled via progress endpoints
- **Path validation**: All file-accepting endpoints use `utils/path_validation.py` to prevent traversal attacks

### Known Limitations

- Manual Sort supports one persisted active session at a time and restores it from disk on startup
- No authentication (local-only tool)
- Thumbnail endpoint serves cached, downsized thumbnails (`thumbnail_cache.py`, LANCZOS); full-resolution pixels are served only by the separate image-file endpoint
- Gallery pagination is cursor-based (`next_cursor` / `has_more`); random sort cannot use a stable cursor
- CORS is restricted by regex to `localhost`, `127.0.0.1`, and `[::1]` (any port). `localhost_only_middleware` in `backend/app_security.py` rejects non-loopback client IPs even if the bind host is widened.

---

# Release SOP

## CRITICAL: GitHub Release Notes Structure

The in-app "Check Update" popup (`frontend/js/app/update-popup.js:_showUpdatePopup`) truncates `release_notes` to the **first 200 characters** and displays it as "更新说明". The release body is fetched raw from `release.body` via the GitHub API (`update_service.py:355`).

**The first 200 characters MUST be a useful changelog summary, NOT download instructions.**

### Required Section Order

```
1. Title line (## vX.Y.Z — 中文摘要 / English summary)
2. 2-3 sentence bilingual changelog summary (this is what users see in-app)
3. ---
4. ## Fixed / 修复  (detailed bilingual changelog)
5. ---
6. ## Upgrading / 升级注意  (migration notes for old users)
7. ---
8. ## Validation / 验证  (CI results, one line)
9. ---
10. ## ⬇️ Download guide  (last explanatory section; only Checksums follows)
11. ---
12. ## Checksums
```

### Title Line Format

```
## vX.Y.Z — 中文关键词 + 关键词 / English Keywords + Keywords
```

Keep under 80 chars. This becomes the "更新说明" heading.

### First 200 Characters Rule

The 2-3 sentence summary immediately after the title MUST:
- Be bilingual (Chinese first, English second)
- Summarize the most important user-visible changes
- NOT contain markdown links, download URLs, or file names
- NOT start with "Which file should I download" or similar

Example:
```
## v3.1.3 — 大图库稳定性 + 存储优化 / Large Library Stability + Storage Optimization

扫描 8 万+图片更安全（metadata 超时跳过、卡住诊断）；images.db 存储瘦身；首次启动只装核心依赖，重型 AI 包按需 Prepare。

Large folder scans (80k+) safer with bounded workers. Metadata compaction shrinks images.db. First launch lightweight.
```

### Fixed Section Format

Each bullet:
```
- **English feature name**: English description.
  - 中文描述。
```

### Download Guide (LAST EXPLANATORY SECTION; CHECKSUMS FOLLOW)

```
## ⬇️ Which file should I download? / 我该下载哪一个？

**Windows → windows-portable.zip** — extract, run run-portable.bat
**Linux → linux.tar.gz** — extract, run ./run.sh

**Do NOT download / 不要下载：**
- app-patch.zip — in-app updater only / 仅供更新器
- release-manifest.json — updater metadata / 更新器元数据
```

## Release Build Steps

```bash
# 1. Ensure version in backend/app_info.py matches target
# 2. Ensure CHANGELOG.md has the version entry
# 3. Run full CI
python scripts/run_ci.py
# 4. Build packages
python scripts/build_release_packages.py --version X.Y.Z
# 5. Release QA gate (asset completeness + SHA256-vs-manifest verification)
#    Validates artifacts/release/ against the manifest. --skip-server keeps it
#    fast (archive integrity only; omit it to also boot the backend for a smoke run).
python scripts/lazy_release_qa.py --skip-server
# 6. Commit and push
git add . && git commit -m "release: prepare vX.Y.Z" && git push
# 7. Create GitHub release with ALL 6 assets (glob uploads every built artifact)
gh release create vX.Y.Z artifacts/release/sd-image-sorter-vX.Y.Z-* \
  --title "vX.Y.Z" --notes "$(cat release-notes.md)"
# 8. Verify: 6 assets (windows-portable, app-patch, linux, linux-portable x86_64, linux-portable aarch64, manifest)
gh release view vX.Y.Z --json assets --jq '.assets[].name'
```

### Required Assets (always 6)

| Asset | Purpose | Who uses it |
|-------|---------|-------------|
| `windows-portable.zip` | Full Windows package with embedded Python | New Windows users |
| `linux.tar.gz` | Linux source package (uses system Python) | New Linux users with Python 3.12+ |
| `linux-portable-x86_64.tar.gz` | Linux package with bundled CPython (x86_64) | Linux users without Python 3.12+ |
| `linux-portable-aarch64.tar.gz` | Linux package with bundled CPython (aarch64/ARM) | ARM Linux (Pi 5, Graviton) |
| `app-patch.zip` | In-app updater payload | Existing users via "Check Update" |
| `release-manifest.json` | Version + SHA256 metadata | In-app updater version detection + release QA gate |

### Pre-Release Checklist

- [ ] `backend/app_info.py` version matches
- [ ] `CHANGELOG.md` entry exists with bilingual notes
- [ ] Full CI green (backend + E2E)
- [ ] `build_release_packages.py` completes without errors
- [ ] `python scripts/lazy_release_qa.py --skip-server` passes (asset completeness + SHA256-vs-manifest gate)
- [ ] All 6 assets uploaded to GitHub release
- [ ] Release notes first 200 chars are a useful bilingual summary (NOT download guide)
- [ ] Release notes contain download guide section (at bottom)
- [ ] Checksums table present

---

# Team Operations — sd-image-sorter-release

## Operating Model

- Team-lead = the main conversation.
- Plans and durable evidence live in `.plans/sd-image-sorter-release/`; transient chat is not a source of truth.
- Default team = lead plus one implementer. Add one read-only reviewer only after the diff is frozen.
- Add a researcher only when an external evidence gap blocks a decision. Maximum active team size is three, including the lead.
- Give agents a minimal task brief and only the recent turns they need. Do not fork the full conversation by default.
- Never assign overlapping file edits. Parallel work should normally be read-only research, review, or non-conflicting verification.
- The lead must not repeat work already assigned to an agent.

## Sources of Truth

- Current plan and workflow evidence matrix: `.plans/sd-image-sorter-release/task_plan.md`
- Evidence-backed decisions: `.plans/sd-image-sorter-release/decisions.md`
- Concise cross-slice progress: `.plans/sd-image-sorter-release/progress.md`
- Findings index: `.plans/sd-image-sorter-release/findings.md`
- Architecture, API, and invariant contracts:
  - `.plans/sd-image-sorter-release/docs/architecture.md`
  - `.plans/sd-image-sorter-release/docs/api-contracts.md`
  - `.plans/sd-image-sorter-release/docs/invariants.md`

Read only the source needed for the active slice. Do not reread historical reports unless the current task depends on them.

## Priority Order

1. Data loss, corruption, security, crashes, hangs, false success, or unusable workflows.
2. High-impact desktop UX problems in core user journeys.
3. Incorrect or obsolete AI/model behavior.
4. Performance and pipeline bottlenecks proven by measurement.
5. Architectural debt that materially causes defects or blocks safe work.
6. Optional polish.

Weight routine choices approximately 40% user-visible value, 30% correctness and stability, 20% competitive gap, and 10% maintainability. Do not optimize something merely because it is easy to measure.

## One-Slice Contract

Work on one independently committable outcome at a time. Before implementation, record in one active task entry:

For a long-running product goal, the goal is the execution envelope and each slice is the unit of implementation, review, verification, and commit. Finishing one slice does not narrow or complete the wider goal.

- user-visible outcome;
- exact scope and likely files;
- acceptance criteria;
- required evidence;
- focused test command;
- stop condition.

Do not create separate plan, findings, progress, handoff, and report files for the same slice. Update the existing source of truth only when durable state changes.

Target an ordinary slice to finish within 60–90 minutes. Split larger work before implementation. If the same hypothesis fails twice, reassess the design instead of adding retries, agents, or brute-force runs.

## Test Ladder

For every code slice:

1. Inspect existing behavior and related tests.
2. Add the smallest meaningful RED test when behavior changes.
3. Implement the complete fix.
4. Run the focused test.
5. Run related module or integration tests.
6. Freeze the diff.
7. Obtain independent read-only reviewer approval.
8. Run the full relevant CI/E2E gate once.
9. Review the exact staged diff and commit.

Additional rules:

- Do not run full CI before the diff is stable, and do not rerun an unchanged full suite.
- Use the repaired sharded desktop Playwright runner for full E2E after its reliability is proven; use sequential E2E only for diagnosis.
- Never add retries, skips, fallbacks, mocks, or relaxed assertions merely to hide a real failure.
- Documentation-only and workflow-only slices do not require unrelated product CI.
- UI changes require rendered interaction proof at 1366×768, 1920×1080, and 2560×1440, plus console, HTTP, overlap, clipping, and horizontal-overflow checks. Do not test mobile or tablet widths.

## Evidence and Context Discipline

- Never infer a model capability, compatible dependency set, performance result, or competitive feature from naming or marketing alone.
- Prefer official papers, vendor documentation, release metadata, security advisories, upstream repositories, installed dependency source, and real product benchmarks.
- Benchmark a replacement model against the current model on representative product data before changing the default or removing the current option.
- Treat Krea 2 as a natural-language-first target. Do not reintroduce Booru-only prompt assumptions into its workflow without contrary first-party evidence.
- Record a decision once and reuse it until the relevant version or evidence changes.
- Store large logs and artifacts by path and hash. Do not paste full web pages, lockfiles, JSON reports, diffs, or test logs into task context.
- Use isolated databases and copied data for destructive tests; never mutate a user's external library for validation.
- After compaction, recover from the active task entry and only its referenced current-state files.

Maintain evidence for these core workflows without duplicating documentation: Scan/Gallery, metadata parsing, filtering/selection, WD14/Mass Tag, Smart Tag and natural-language targets, Dataset Maker/export, Auto-Separate, Manual Sort/undo, Censor/output integrity, similarity/duplicates, Model Setup/runtime repair, and update/build/CI without publishing.

## Team-Lead Protocols

- Inspect the worktree and preserve unrelated user or parallel changes before every slice.
- Decide routine priorities and implementation approaches autonomously. Escalate only for credentials, irreversible external actions, destructive changes outside isolated data, new authority, or an evidence-intractable product decision.
- Keep the active plan aligned with actual state; never mark a risk complete by omission.
- Freeze implementation before reviewer dispatch. Reviewer agents are read-only and receive only the diff, acceptance criteria, and required evidence.
- Commit each independently useful, reviewed, verified slice with a conventional local commit.
- Never stage temporary reports, artifacts, caches, databases, `.plans` noise, or unrelated files.
- Do not push, publish a release, or change the application version without explicit user authority.
- After a commit, report only the user-visible result, evidence/tests, commit hash, and next highest-priority slice.

## Goal Completion Standard

Passing tests for one change is not proof that a long-running product goal is complete. Completion requires current evidence that:

- every core workflow has a verified positive path and an important failure or recovery path;
- supported desktop layouts have no critical clipping, overlap, hidden primary action, or horizontal overflow;
- no known P0/P1 correctness, security, data-integrity, stability, or UX defect remains;
- confirmed architectural debt is fixed, disproven as a defect, or remains explicitly open rather than disappearing from the plan;
- model and dependency choices are current, secure, platform-compatible, and evidence-backed;
- major competitive gaps have been compared with currently maintained similar software through official evidence and real testing;
- backend, frontend, coverage, and complete desktop E2E gates pass on the final frozen worktree;
- the repository is clean except for recorded user-owned files; and
- no release, external push, tag, or application-version change was made without explicit authority.

Do not redefine success around already-finished work, elapsed effort, or the available budget. Reuse committed and independently proven slice evidence instead of repeating it, while preserving every still-open requirement of the wider goal.

## Known Pitfalls

- Open-ended "everything before publish" scope causes repeated audits and context growth. Always enforce the one-slice contract and priority order.
- Full-context agent forks multiply token use. Send minimal briefs and keep durable evidence in the existing source of truth.
- Running full CI before review wastes time when the diff changes afterward. Run it once after reviewer approval.
- Shared canonical test artifacts can be stale. Trust only artifacts tied to the current run id and terminal status.
- A passing test is not proof of product quality unless it covers the stated user outcome and failure path.
