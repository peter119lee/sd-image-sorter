"""Device/provider toggles for artist identification (pure helpers).

Moved verbatim from backend/artist_identifier.py (decomposition 2026-07). Zero facade-owned reads: both functions
take everything as arguments and probe torch lazily at call time. The facade
re-exports them; the gpu-toggle suite calls them as facade attributes.
"""

import logging
from typing import Any, List, Optional

logger = logging.getLogger("sd-image-sorter.artist")


def _resolve_artist_device(*, use_gpu: bool = True, cuda_available: Optional[bool] = None) -> str:
    """Pick the torch device for Kaloscope, honoring the use_gpu opt-out.

    Returns ``"cuda"`` only when GPU use is requested AND CUDA is actually
    available; otherwise ``"cpu"``. Mirrors the WD14 tagger's use_gpu toggle so
    users whose GPU stack freezes under CUDA load (e.g. NVIDIA + Wayland) can run
    artist identification on CPU instead of having no escape from the hardcoded
    ``cuda`` path. ``cuda_available`` is injectable for tests; left None it is
    probed via ``torch.cuda.is_available()``.
    """
    if not use_gpu:
        return "cpu"
    if cuda_available is None:
        try:
            import torch

            cuda_available = bool(torch.cuda.is_available())
        except Exception as exc:  # torch import / CUDA probe failure -> CPU
            logger.debug("CUDA probe failed; using CPU for artist ID: %s", exc)
            cuda_available = False
    return "cuda" if cuda_available else "cpu"


def _onnx_providers_for(ort_module: Any, *, use_gpu: bool) -> List[str]:
    """ONNX Runtime provider list honoring the use_gpu opt-out.

    ``InferenceSession(path)`` with no providers defaults to CUDA-first when
    onnxruntime-gpu is installed, which silently ignored the Style Finder's
    "use GPU if available" toggle on the .onnx path (owner report 2026-07-05:
    the toggle "did nothing"). Intersect with the actually available
    providers so passing CUDA on a CPU-only install never raises.
    """
    try:
        available = list(ort_module.get_available_providers())
    except Exception:
        available = ["CPUExecutionProvider"]
    if not use_gpu:
        return ["CPUExecutionProvider"]
    preferred = [p for p in available if p != "CPUExecutionProvider"]
    return [*preferred, "CPUExecutionProvider"]
