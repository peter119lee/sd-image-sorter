# Third-Party Models

SD Image Sorter is local-first, but model weights and large runtime assets are
user data. The application source and every portable/archive package contain
no model payloads. The Model Manager downloads a selected model from its
official source, verifies the required files, and stores them under the user's
`data/models/` directory. The aesthetic linear head is an exception: it lives
in `models/aesthetic/` at the project root.

## Delivery Rules

- Models are never redistributed, mirrored, or packed into a release archive.
- Model Manager **Prepare / Download** is the supported installation path.
- The bulk downloader lets users select several missing features in one run.
- Download progress, per-file validation, errors, and restart requirements are
  printed in the launcher console and shown in the UI.
- A Python dependency installation can require closing and restarting the app;
  the next Prepare run resumes file verification/download.
- Existing `data/` is user state. Updates and package extraction must preserve it.

## Supported Model Matrix

| Feature | Model/source | Delivery | Notes |
|---|---|---|---|
| Default tagging | `SmilingWolf/wd-swinv2-tagger-v3` | Model Manager | WD14 ONNX + `selected_tags.csv`; the default revision is pinned. |
| Other WD14 taggers | EVA02, ConvNeXt, ViT, ViT-Large | Model Manager | Existing optional catalog entries; no weights are shipped. |
| Modern optional tagger | `cella110n/cl_tagger_v2` (`v2_00`) | Explicit opt-in Model Manager download | Gated Hugging Face repository; the user must accept the terms and configure access. |
| Natural-language captioning | `florence-community/Florence-2-base` | Model Manager | Commit-pinned native Transformers checkpoint; all processor/tokenizer files are required. |
| Censor detection | NudeNet, legacy YOLO, SAM3 | Model Manager / existing source paths | Detector semantics stay separate from matting. |
| Training masks | `egeorcun/lucida` | Model Manager | Commit-pinned BiRefNet matte; opt-in, MIT weights, remote code loaded only from the local verified snapshot. |
| Similarity search | `Qdrant/clip-ViT-B-32-vision` + text tower | Model Manager | Both vision and text FastEmbed directories must be complete. |
| Aesthetic score | LAION head + CLIP ViT-L/14 | Model Manager | Head and backbone/runtime must both be ready. |
| Artist identification | Kaloscope 2.0 + LSNet runtime | Model Manager | Downloaded on demand; not part of a release asset. |
| OppaiOracle / ToriiGate | Existing optional catalog entries | Individual Prepare | Alternatives, not required for the recommended feature set. |

## Required Download Completeness

The downloader fails explicitly if any required file is absent or zero bytes.
The following companion files are part of the runtime contract:

- WD14: `model.onnx` and `selected_tags.csv`.
- Florence-2 Base: `model.safetensors`, `config.json`, generation and
  processor configuration, merges/vocabulary, and all tokenizer JSON files.
- Lucida: `model.safetensors`, `config.json`, `BiRefNet_config.py`, and
  `birefnet.py`.
- CL Tagger v2: `v2_00/model.onnx`, its external `.onnx.data`,
  `model_vocabulary.json`, `model_metadata.json`, and
  `model_tag_metrics.npz`.
- CLIP vision: `config.json`, `model.onnx`, and
  `preprocessor_config.json`; CLIP text additionally requires the tokenizer
  JSON files and `special_tokens_map.json`.

The starter console emits one structured `[MODEL] file_ready` or
`[MODEL] file_missing` line for every required artifact, including model ID,
revision, endpoint, and size. A successful Prepare response is therefore not
reported until the complete set is present.

## Licensing and Access

The app links to the upstream model page in the Model Manager where relevant.
Lucida's weights are MIT-licensed, while its README lists research-only
training datasets; commercial users must make their own assessment. CL Tagger
v2 is gated by Hugging Face and remains unchecked in the recommended bulk
selection until the user has authorized access.

## Download Sources

- Hugging Face is the default source, with the configured `hf-mirror` fallback.
- ModelScope is used only where the model-specific implementation has a real
  ModelScope source (currently Artist/SAM3 paths). Hugging Face-only models do
  not pretend to be available on ModelScope.
- `HF_ENDPOINT` and the in-app Download Source setting may change endpoint
  order, but never change the pinned model revision or required-file contract.

## Model Roles

Lucida produces a full-image soft alpha matte. It is not a semantic detector
and must not be offered as a YOLO/NudeNet/SAM3 censor detector. NudeNet, YOLO,
or SAM3 decides *where* a censor region is; Lucida is used for persistent
training masks and subject-aware dataset geometry.

## Verification Boundary

Model files are downloaded only after an explicit user action. The repository
tests use fake Hub responses for download contracts; real runtime checks are
performed separately on isolated data and never alter a user's gallery.
