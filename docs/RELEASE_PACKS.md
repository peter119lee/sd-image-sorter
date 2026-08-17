# Release Pack Guide

This document explains the release assets produced for the public build.

## Fastest Path For Normal Users

Download:

- `sd-image-sorter-vX.X.X-windows-portable.zip`

Then:

1. Extract it to any normal folder.
2. Double-click `run-portable.bat`.
3. Wait for dependency install on first run.
4. Open `http://localhost:8487`.

This package includes an embedded Python runtime — **no system Python install needed**.

On NVIDIA machines, the first ONNX Runtime check may install CUDA / cuDNN runtime wheels after the normal dependency install. The launcher prints the actual pip progress during that step; do not close it just because it is still working under `Checking Windows ONNX Runtime package state...`.

That package is meant to cover the common workflows:

- Gallery
- WD14 tagging with the default `wd-swinv2` model
- Censor Edit with Wenaka privacy YOLO + NudeNet
- Similar search with local CLIP

## All Release Packages

| Package | Python Included | Models Included | Best For |
|:--------|:---------------:|:---------------:|:---------|
| `sd-image-sorter-vX.X.X-windows-portable.zip` | Yes | None (auto-download) | **Most Windows users** — no system Python install |
| `sd-image-sorter-vX.X.X-linux-portable-x86_64.tar.gz` | Yes (cpython 3.13, x86_64) | None (auto-download) | **Most Linux users on PCs / laptops / x86 servers** — works on any distro, including ones whose system Python is 3.14 (where heavy AI wheels are not yet available) |
| `sd-image-sorter-vX.X.X-linux-portable-aarch64.tar.gz` | Yes (cpython 3.13, aarch64) | None (auto-download) | **Linux users on ARM** — Raspberry Pi 4 / 5, ARM Linux servers, AWS Graviton, Apple Silicon running Linux |
| `sd-image-sorter-vX.X.X-linux.tar.gz` | No | None (auto-download) | Advanced Linux users with Python 3.12+ already installed and managed |
| `sd-image-sorter-vX.X.X-app-patch.zip` | No | None | In-app updater payload; not the recommended manual first install |
| `sd-image-sorter-vX.X.X-release-manifest.json` | No | No | SHA256/size manifest used by the updater and release checks |

### Linux Portable Notes

- The bundled Python is `cpython-3.13.13` from [astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone), built against an old enough glibc (2.17 on x86_64, 2.28 on aarch64) to run on every modern Linux distro on either architecture.
- Both `x86_64` and `aarch64` ship in this release line. Pick the tarball that matches your CPU; the runtime experience is identical (same Python, same first-run install flow, same `Setup Now` UX for heavy AI features).
- The `python/` directory inside the archive is the bundled interpreter. `run.sh` automatically detects it and forwards to `run-portable.sh`, so users only need to chmod once and double-click.
- First launch installs the lightweight core dependencies (~120 MB extra after install). Heavy AI features (CLIP, SAM3, NudeNet, Aesthetic Score, Artist ID, ToriiGate) install on demand via **Setup Now → Prepare**.

### macOS: Source-Install Only (No Portable Bundle Yet)

There is **no `macos-portable-*.tar.gz` asset** by design. macOS users should clone the repository and run `./run.sh` against system Python (Homebrew / pyenv / asdf / `uv` all work; the app's lockfile supports Python 3.12 and 3.13). The published Linux archive is a Linux release package and intentionally refuses macOS.

Intel Mac supports the **core runtime and core-only development/test lock**. `backend/requirements-dev.txt` extends `requirements-core.txt`, excludes Torch/CUDA packages, and remains installable for Intel Mac CI and contributors. Do not install the separate full-AI `backend/requirements.txt` there because upstream publishes no current macOS x86_64 Torch wheel. Full-AI development requires macOS 14+ Apple Silicon, Windows, or Linux; ordinary Intel Mac users should keep the default core install.


The reasons are documented in [`docs/AI_DECISION_LOG.md`](AI_DECISION_LOG.md) under **ADR-2026-05-24: macOS portable bundle deferred**, summarised here:

1. **Gatekeeper friction without notarization.** A macOS portable would need [Apple Developer notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) (\$99 / yr Apple Developer Program) to launch without `"developer cannot be verified"` warnings on every fresh download. Without notarization, every user has to right-click → Open or `xattr -dr com.apple.quarantine sd-image-sorter/` before launching — a much worse first-run experience than the Windows / Linux portable double-click flow.
2. **macOS users almost always already have Python.** Homebrew, `pyenv`, `asdf`, and `uv` are standard on macOS dev machines, so the "no system Python" pain point that drove the Linux portable basically does not exist on macOS. The existing `./run.sh` source path already creates a venv and installs deps; PR #12 fixed Darwin-clone detection so this works on the source bundle.
3. **macOS Intel is core-only for a reason.** PyTorch dropped Intel Mac wheels after `2.2.2`, and that legacy branch now carries many known advisories. The app no longer silently pins it: Intel Mac keeps the core gallery, sorting, metadata, and ONNX features, while Torch-backed Prepare fails before download with an actionable platform message.
4. **Safe Apple Silicon Torch has a clear floor.** The current security-supported Torch 2.13 wheel targets macOS 14. Apple Silicon on macOS 14+ can prepare Torch-backed features from source; older macOS stays on the core/ONNX feature set rather than downgrading to an advisory-bearing runtime.

This decision will be revisited if any of the following becomes true:

- An Apple Developer account becomes available for notarization, removing the Gatekeeper friction.
- A real macOS user reports the source path is broken in a way `./run.sh` cannot fix.
- PyTorch ships a renewed macOS Intel wheel line that justifies a fresh look at the legacy pin.

Until then, macOS users should:

```bash
git clone https://github.com/Rinne414/sd-image-sorter.git
cd sd-image-sorter
./run.sh
```

Do not use `sd-image-sorter-vX.X.X-linux.tar.gz` on macOS; its package manifest marks it as a Linux release and the launcher fails closed on Darwin.

## Model Download Sources

Every model is downloaded only after an explicit **Model Manager → Prepare /
Download** action. No source, portable, patch, or Linux archive contains model
weights, tokenizer files, CSV files, ONNX external data, or checkpoint files.

- **Default**: official [Hugging Face](https://huggingface.co), with the
  configured `hf-mirror` fallback.
- **Gated models**: the UI links to the official page and reports the required
  terms/token step; it does not silently retry with an unauthenticated mirror.
- **ModelScope**: used only by model-specific Artist/SAM3 download paths.
- **Bulk selection**: the Model Manager preselects one complete model for each
  core feature. Users can clear the defaults, select optional models, or select
  all visible missing models in one confirmation.

The launcher console stays open while downloads run. It prints per-file
validation, endpoint, revision, size, failures, and an explicit restart notice
when installing Python packages. After restarting, click Prepare / Download
again to resume verification and any remaining files.

## Package Manifest Model Policy

Every app package writes `update/package-manifest.json` with a
`model_artifact_policy` block whose delivery mode is
`application_prepare_only`.

- `models/` may contain documentation only; all model payload paths are
  excluded from every archive and updater manifest.
- `data/models/` is user state and is never copied, deleted, or managed by a
  release package or the in-app updater.
- The manifest lists model IDs that the application can prepare, plus explicit
  forbidden model prefixes. It contains no “optional model asset” entries.
- The release builder rejects an explicit request to include model payloads,
  so a future packaging call cannot accidentally turn into redistribution.

## Manual App Updates

The app only checks for updates when the user clicks the update button.

- Default channel: GitHub Releases
- If GitHub is unreachable, the app will suggest setting up an update proxy
- Default user guidance: if GitHub is unreachable, enable VPN and retry the manual update check
- Asset selection rule: prefer `app-patch`, but automatically fall back to the platform full package when no patch asset exists
- Safety rule: the updater only replaces release-managed app files and never touches protected runtime paths

## Why The Updater Never Touches `data/`

This is intentional and must stay that way.

- `data/` is package-local user state: database, favorites, downloaded models, cache, thumbnails, temp files, and other long-lived runtime data
- `update/backups`, `update/downloads`, `update/logs`, `update/state`, and `update/worker` are updater runtime workspaces, not release payload content
- Protected runtime prefixes are: `data`, `update/backups`, `update/downloads`, `update/logs`, `update/state`, `update/worker`
- The in-app updater is meant to behave like "replace the app code in place", not "reinstall the whole environment from scratch"
- Release packaging already excludes runtime folders, but the worker also hard-blocks them so a future packaging mistake cannot silently overwrite or delete user state
- If a new release manifest ever tries to manage protected runtime paths, the worker aborts the update before copying or deleting installed files
- If an old installed manifest contains dirty entries for protected paths, the worker ignores those entries instead of treating user data as obsolete app files

## Why Models Are Not Included In The Repository Or Packages

1. **Copyright**: Some models have specific redistribution terms
2. **Size**: Models range from 12 MB to 3.3 GB — too large for git
3. **Application download**: Model Manager downloads and verifies only what the
   user selects.
4. **User choice**: The bulk selector makes multi-model setup convenient
   without forcing optional or gated models.

## After Extraction

The app itself will tell you what is ready:

- `Similar` tab banner: local CLIP readiness
- `Censor Edit` banner: recommended detection mode and default privacy model
- `Artist ID` banner: Kaloscope runtime readiness
- `Smart Tag` / `Dataset Maker`: Florence-2 and Lucida readiness, with their
  exact missing-file guidance
- `CL Tagger v2`: gated authorization guidance until the official terms/token
  requirement is satisfied
