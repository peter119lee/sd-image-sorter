# Why SD Image Sorter?

## Positioning

**SD Image Sorter — a local image manager built for Stable Diffusion workflows**

Unlike general-purpose image managers that treat AI-generated images like photos, SD Image Sorter is designed from the ground up for Stable Diffusion workflows. It understands your metadata, speaks your language, and provides tools that match how AI artists actually work. Eagle and Billfish are general asset managers; they are not in the SD-workflow comparison below.

## What Makes Us Different

### 1. Deep SD Metadata Understanding

**SD Image Sorter:**
- Natively reads ComfyUI, NovelAI, WebUI, Forge metadata without conversion
- Extracts prompts, negative prompts, seeds, steps, CFG, samplers, models, LoRAs, VAEs automatically
- Preserves metadata when editing and re-exporting images
- Filters by checkpoint, LoRA, aspect ratio, generation parameters

**Allusion / TagStudio / DigiKam / Hydrus:**
- Allusion can show PNG Parameters in the inspector; it does not index ComfyUI / NAI / WebUI / Forge fields for filtering
- TagStudio, DigiKam, and Hydrus treat SD images as generic files unless you add plugins or scripts

### 2. AI-First Feature Set

**SD Image Sorter:**
- WD14 family auto-tagging (9 local taggers + ToriiGate captioner: ViT, ViT-Large, SwinV2, ConvNeXt, EVA02, Camie, PixAI, OppaiOracle, CL Tagger v2; ToriiGate is NL captioning)
- CLIP similarity search for finding duplicates and near-matches
- VLM captioning via OpenAI-compatible (incl. Ollama), Anthropic, and Gemini (optional Vertex)
- Prompt Helper: reverse-engineer prompts from your own library
- Artist identification: Kaloscope style recognition
- Aesthetic scoring: local beauty ranking
- LoRA training export with template engine (7 presets, 17 variables)

**Allusion:**
- Hierarchical tags and watched folders
- Inspector can show PNG Parameters; no built-in WD14 / VLM / CLIP tagging

**TagStudio:**
- Manual tagging only
- No built-in WD14 / VLM tagger

**DigiKam:**
- Face detection (photo-centric)
- No SD-specific AI features

**Hydrus:**
- Manual tags
- Some third-party AI integrations exist but not first-party

### 3. Workflow Speed

**SD Image Sorter:**
- WASD keyboard-driven manual sorting (4-way split + skip + undo)
- Auto-Separate: filter + preview + action in one 3-pane view
- Manual Sort multi-mode: Slot Mode (4-way), Bracket Mode (ranking), Cull Mode (keep/delete)
- Background job queue with live progress tracking
- Batch operations on thousands of images

**Allusion:**
- Click-and-drag sorting
- Slower for large batches

**TagStudio:**
- Manual organization
- No keyboard-first workflows

**DigiKam:**
- Traditional photo manager UI
- Slower for bulk operations

**Hydrus:**
- Powerful but complex UI
- Steep learning curve

### 4. Deployment & Privacy

**SD Image Sorter:**
- Windows/Linux portable archives with a launcher (not a single-file binary)
- Source installs need Python 3.12 or 3.13 and a venv; no account
- Local-first: gallery, tagging, and models stay on disk. Optional cloud VLM captioning runs only with a user-supplied API key and does upload images to that provider
- Local models run on your machine

**Allusion:**
- Traditional installer
- Local-first

**TagStudio:**
- Python required
- Local-first

**DigiKam:**
- Full KDE stack required
- Local-first

**Hydrus:**
- Complex setup
- Local-first but heavy

### 5. SD-Specific Tools

**SD Image Sorter:**
- Censor Editor: YOLO/NudeNet detection + brush tools + batch queue
- Image Reader: drag-drop metadata extraction without importing
- Image Obfuscate: password-protected sharing
- Collections system with folder tree navigation
- Star ratings (1-5 stars)
- Library roots: multiple folder hierarchies

**Allusion / TagStudio / DigiKam / Hydrus:**
- Allusion can display PNG Parameters; the others are general libraries or taggers
- None of them ship an SD-native censor / LoRA export / WASD sort workbench

## Competitive Comparison

| Feature | SD Image Sorter | Allusion | TagStudio | DigiKam | Hydrus |
|---------|----------------|----------|-----------|---------|--------|
| **SD Metadata** | Native ComfyUI/NAI/WebUI/Forge | PNG Parameters view | ❌ | ❌ | ❌ |
| **AI Auto-Tagging** | 9 local taggers + ToriiGate captioner | ❌ | ❌ | Face detect only | Via plugins |
| **VLM Captioning** | OpenAI-compat / Anthropic / Gemini | ❌ | ❌ | ❌ | ❌ |
| **CLIP Similarity** | ✅ | ❌ | ❌ | ❌ | ✅ (third-party) |
| **Keyboard Sorting** | WASD 4-way + multi-mode | ❌ | ❌ | ❌ | ❌ |
| **Censor Tools** | YOLO + brush + batch | ❌ | ❌ | ❌ | ❌ |
| **Prompt Helper** | ✅ Reverse-engineer prompts | ❌ | ❌ | ❌ | ❌ |
| **LoRA Export** | Template engine + presets | ❌ | ❌ | ❌ | ❌ |
| **Deployment** | Portable zip/tarball + launcher | Installer | Python required | Full KDE stack | Complex setup |
| **Learning Curve** | Low-Medium | Low | Medium | Medium | High |
| **Large Libraries** | 50k+ tested | Unknown | Slower | Good | Good |
| **Privacy** | Local-first; optional cloud VLM uploads | Local | Local | Local | Local |

## When to Choose SD Image Sorter

✅ **Choose SD Image Sorter if you:**
- Generate images with Stable Diffusion, ComfyUI, NovelAI, or similar tools
- Have thousands to tens of thousands of AI-generated images
- Want fast keyboard-driven sorting workflows
- Need AI auto-tagging, similarity search, and metadata filtering
- Want a portable, local-first tool (optional cloud VLM is opt-in)
- Need to prepare datasets for LoRA training
- Want to batch-censor images for sharing

❌ **Consider alternatives if you:**
- Primarily work with photos (not AI art) → DigiKam
- Need a simple, minimal UI with no AI features → Allusion
- Want maximum control and complexity → Hydrus
- Need a lightweight tagging-only tool → TagStudio

## Real-World Use Cases

### Dataset Curation
- Scan 50k generations
- Auto-tag with WD14
- Filter by tags + rating + model
- Export to LoRA training folders with template captions

### Portfolio Sorting
- Import all outputs from last month
- WASD manual sort: best / portfolio / archive / delete
- CLIP similarity to find duplicates
- Star-rate favorites

### Safe Sharing
- Select explicit images
- Censor Editor: auto-detect + manual brush
- Batch queue processing
- Export censored versions to share folder

### Prompt Mining
- Scan your best 1000 images
- Prompt Helper reverse-engineers common patterns
- Copy reusable prompt snippets
- Apply to new generations

## Bottom Line

**SD Image Sorter is not trying to be a universal image manager. It is purpose-built for people who generate AI art and need a fast, local, metadata-aware tool to manage thousands of outputs without clicking through menus or learning arcane tag syntax.**

If you generate AI images and your folder is chaos, this tool exists to fix that problem specifically.
