"""Loader / file-resolution mixin for OppaiOracleTagger (split 2026-07).

Methods moved from oppai_oracle_tagger.py: _model_config / _expected_local_paths / _validate_model_file /
_download_with_fallback / _download_model / _get_model_paths /
_build_session_options / _create_session / _session_uses_gpu /
set_session_refresh_interval / _load_tags / load. Manifested lines (the
ONLY non-verbatim edits): every ``ort`` / ``hf_hub`` read resolves through
_svc() at call time (the pin suite patches ``oppai_oracle_tagger.ort`` /
``.hf_hub`` on the facade module object), the PAD/UNK/RATING tag constants
resolve through _svc() (they stay DEFINED on the facade), and the three
local ``config = self._model_config()`` shadows are renamed ``model_cfg``
(the locals shadowed the module-level ``config``
import). TAGGER_MODELS is origin-imported from config (the same dict
object the facade binds; no suite patches it on the oppai module), as are
endpoint_label / get_hf_endpoint_order from model_download_sources. The
logger keeps the original "oppai_oracle_tagger" channel.
"""

import csv
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-only
    import onnxruntime as ort  # type: ignore

from config import TAGGER_MODELS
from model_download_sources import (
    endpoint_label,
    get_hf_endpoint_order,
    log_model_artifact_status,
)

logger = logging.getLogger("oppai_oracle_tagger")


def _svc():
    """Resolve facade-owned globals through ``oppai_oracle_tagger`` at call time.

    The pin suite patches ``oppai_oracle_tagger.ort`` / ``.hf_hub`` and reads
    the PAD/UNK/RATING constants on the facade module object; a from-import
    here would freeze independent bindings those patches silently miss. The
    lazy import avoids a facade<->mixin load cycle.
    """
    import oppai_oracle_tagger

    return oppai_oracle_tagger


class _LoaderMixin:
    """Model/tag file resolution, HF downloads, ONNX session build, load."""

    # ----- file resolution ------------------------------------------------

    def _model_config(self) -> Dict[str, Any]:
        return TAGGER_MODELS.get(self.model_name, {})

    def _expected_local_paths(self) -> Tuple[str, str]:
        """Return ``(model_path, tags_path)`` under the canonical layout.

        Layout: ``<model_dir>/<model_name>/<repo_subfolder>/<file>``. Keeping
        the HF repo subfolder (e.g. ``V1.1_onnx``) means future variants like
        ``V1.1_safetensors`` can sit beside the ONNX one without collisions.
        """
        model_cfg = self._model_config()
        subfolder = str(model_cfg.get("repo_subfolder") or "").strip("/\\")
        model_file = model_cfg.get("model_file") or "model.onnx"
        tags_file = model_cfg.get("tags_file") or "selected_tags.csv"
        base = Path(self.model_dir) / self.model_name
        if subfolder:
            base = base / subfolder
        return str(base / model_file), str(base / tags_file)

    def _validate_model_file(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            return os.path.getsize(path) > 1024 * 1024
        except OSError:
            return False

    def _download_with_fallback(
        self, *, repo_id: str, filename: str, local_dir: str
    ) -> str:
        assert _svc().hf_hub is not None
        endpoints = get_hf_endpoint_order(model_name=f"OppaiOracle {self.model_name}")
        seen: set = set()
        last_error: Optional[Exception] = None
        for endpoint in endpoints:
            key = endpoint.lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                logger.info(
                    "Downloading %s from %s via %s",
                    filename, repo_id, endpoint_label(endpoint),
                )
                resolved_path = _svc().hf_hub.hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    local_dir=local_dir,
                    endpoint=endpoint,
                )
                log_model_artifact_status(
                    logger,
                    model_id=self.model_name,
                    revision=None,
                    endpoint=endpoint,
                    model_dir=Path(local_dir),
                    required_files=(filename,),
                )
                return resolved_path
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Download failed for %s via %s: %s",
                    filename, endpoint_label(endpoint), exc,
                )
        if last_error is None:  # pragma: no cover - defensive
            raise RuntimeError(f"Failed to download {filename} from {repo_id}")
        raise last_error

    def _download_model(self) -> Tuple[str, str]:
        model_cfg = self._model_config()
        if not model_cfg:
            raise ValueError(
                f"Unknown OppaiOracle model: {self.model_name}. "
                f"Available: {[n for n,c in TAGGER_MODELS.items() if c.get('runtime_backend') == 'oppai-oracle']}"
            )
        repo_id = model_cfg["repo_id"]
        subfolder = str(model_cfg.get("repo_subfolder") or "").strip("/\\")
        model_file = model_cfg["model_file"]
        tags_file = model_cfg["tags_file"]
        local_dir = str(Path(self.model_dir) / self.model_name)
        Path(local_dir).mkdir(parents=True, exist_ok=True)

        def _hf_filename(name: str) -> str:
            return f"{subfolder}/{name}" if subfolder else name

        model_path, tags_path = self._expected_local_paths()
        if not self._validate_model_file(model_path):
            if os.path.exists(model_path):
                try:
                    os.remove(model_path)
                except OSError as exc:
                    logger.warning("Could not remove invalid model file: %s", exc)
            logger.info("Downloading OppaiOracle model %s ...", self.model_name)
            model_path = self._download_with_fallback(
                repo_id=repo_id, filename=_hf_filename(model_file), local_dir=local_dir,
            )
            if not self._validate_model_file(model_path):
                raise RuntimeError("Downloaded OppaiOracle model file is invalid.")

        if not os.path.isfile(tags_path) or os.path.getsize(tags_path) <= 0:
            tags_path = self._download_with_fallback(
                repo_id=repo_id, filename=_hf_filename(tags_file), local_dir=local_dir,
            )

        required_files = (_hf_filename(model_file), _hf_filename(tags_file))
        missing = log_model_artifact_status(
            logger,
            model_id=self.model_name,
            revision=None,
            endpoint="local",
            model_dir=Path(local_dir),
            required_files=required_files,
        )
        if missing:
            raise RuntimeError(
                "OppaiOracle download completed without required runtime files: "
                + ", ".join(missing)
                + ". Re-run Prepare / Download."
            )

        # Pull the small companion files too so health / debug pages can show
        # the real preprocessing config and threshold table without needing
        # network access on every check.
        for extra in model_cfg.get("extra_files") or []:
            extra_path = str(Path(local_dir) / (subfolder or "") / extra)
            if os.path.exists(extra_path):
                continue
            try:
                self._download_with_fallback(
                    repo_id=repo_id, filename=_hf_filename(extra), local_dir=local_dir,
                )
            except Exception as exc:
                logger.warning("Optional file %s not available: %s", extra, exc)
        return model_path, tags_path

    def _get_model_paths(self) -> Tuple[str, str]:
        if self.model_path:
            if not os.path.exists(self.model_path):
                raise FileNotFoundError(f"Custom OppaiOracle model file not found: {self.model_path}")
            if self.tags_path:
                if not os.path.exists(self.tags_path):
                    raise FileNotFoundError(f"Custom tags file not found: {self.tags_path}")
                return self.model_path, self.tags_path
            sibling = Path(self.model_path).parent / "selected_tags.csv"
            if sibling.exists():
                return self.model_path, str(sibling)
            raise ValueError(
                "OppaiOracle requires selected_tags.csv next to the model or via tags_path."
            )
        return self._download_model()



    # ----- session management --------------------------------------------

    def _build_session_options(self, gpu_enabled: bool) -> "ort.SessionOptions":
        import multiprocessing
        opts = _svc().ort.SessionOptions()
        cpu_count = max(1, multiprocessing.cpu_count())
        opts.intra_op_num_threads = 2 if gpu_enabled else min(cpu_count, max(2, cpu_count // 2))
        opts.inter_op_num_threads = max(1, opts.intra_op_num_threads // 2)
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        opts.execution_mode = _svc().ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = _svc().ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.enable_cpu_mem_arena = not gpu_enabled
        opts.enable_mem_pattern = not gpu_enabled
        return opts

    def _create_session(
        self, model_path: str, sess_options: "ort.SessionOptions", providers: List[str]
    ) -> "ort.InferenceSession":
        try:
            return _svc().ort.InferenceSession(model_path, sess_options=sess_options, providers=providers)
        except Exception as exc:
            error_msg = str(exc)
            if not self.model_path and (
                "INVALID_PROTOBUF" in error_msg or "Protobuf parsing failed" in error_msg
            ):
                logger.error("OppaiOracle model file is corrupted: %s", model_path)
                try:
                    os.remove(model_path)
                except Exception as del_exc:  # pragma: no cover
                    logger.warning("Could not delete corrupted file: %s", del_exc)
                model_path, _ = self._download_model()
                return _svc().ort.InferenceSession(model_path, sess_options=sess_options, providers=providers)
            raise RuntimeError(f"Failed to load OppaiOracle ONNX model: {error_msg}") from exc

    def _session_uses_gpu(self) -> bool:
        if self.session is None:
            return False
        providers = self.session.get_providers()
        return "CUDAExecutionProvider" in providers or "DmlExecutionProvider" in providers

    def set_session_refresh_interval(self, interval: int) -> None:
        self._session_refresh_interval = max(0, int(interval))

    # ----- tag table loading ---------------------------------------------

    def _load_tags(self, tags_path: str) -> None:
        """Parse OppaiOracle's selected_tags.csv (header tag_id,name,category).

        All 19,294 entries are nominally category 0. Indices 0-1 are
        ``<PAD>`` / ``<UNK>`` and must be skipped during inference. The
        last 4 entries are ``rating:general/sensitive/questionable/explicit``;
        we route those into the rating split so the existing UI / DB schema
        continue to work.
        """
        self.tags = []
        self.general_tags = []
        self.character_tags = []
        self.rating_tags = []
        self.rating_indices = {}

        with open(tags_path, "r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if not rows:
            raise ValueError(f"Empty tag file: {tags_path}")

        header = [str(part or "").strip().lower() for part in rows[0]]
        try:
            id_index = header.index("tag_id")
            name_index = header.index("name")
        except ValueError as exc:
            raise ValueError(
                f"Unexpected OppaiOracle tag header {header}; expected 'tag_id,name,category'."
            ) from exc
        data_rows = rows[1:]

        for parts in data_rows:
            if len(parts) <= max(id_index, name_index):
                continue
            try:
                tag_id = int(parts[id_index])
            except ValueError:
                continue
            tag_name = parts[name_index]
            self.tags.append(tag_name)
            if tag_id in (_svc().PAD_TAG_INDEX, _svc().UNK_TAG_INDEX):
                continue
            if tag_name.startswith(_svc().RATING_TAG_PREFIX):
                rating_name = tag_name[len(_svc().RATING_TAG_PREFIX):]
                self.rating_tags.append((tag_id, rating_name))
                self.rating_indices[rating_name] = tag_id
            else:
                self.general_tags.append((tag_id, tag_name))

    # ----- public API ----------------------------------------------------

    def load(self) -> None:
        if self._loaded:
            return
        model_path, tags_path = self._get_model_paths()
        self._resolved_model_path = model_path
        self._resolved_tags_path = tags_path

        model_cfg = self._model_config()
        self._target = int(model_cfg.get("image_size", 448))
        pad = model_cfg.get("pad_color") or [114, 114, 114]
        self._pad_color = (int(pad[0]), int(pad[1]), int(pad[2]))

        if self.use_gpu:
            providers = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
        available = _svc().ort.get_available_providers()
        providers = [p for p in providers if p in available]
        gpu_attempted = self.use_gpu and any(
            p in providers for p in ("CUDAExecutionProvider", "DmlExecutionProvider")
        )

        sess_options = self._build_session_options(gpu_enabled=gpu_attempted)
        try:
            self.session = self._create_session(model_path, sess_options, providers)
        except RuntimeError as exc:
            if gpu_attempted:
                logger.warning("OppaiOracle GPU load failed (%s); falling back to CPU.", exc)
                self.session = self._create_session(
                    model_path,
                    self._build_session_options(gpu_enabled=False),
                    ["CPUExecutionProvider"],
                )
                self.use_gpu = False
            else:
                raise

        if not self._session_uses_gpu():
            self.use_gpu = False

        self._load_tags(tags_path)

        # Sanity-check that the ONNX graph actually has the inputs we expect.
        input_names = {i.name for i in self.session.get_inputs()}
        missing = {"pixel_values", "padding_mask"} - input_names
        if missing:
            raise RuntimeError(
                f"OppaiOracle ONNX is missing required input(s) {sorted(missing)}; "
                f"got {sorted(input_names)}. Re-download or check the model file."
            )

        self._loaded = True
        logger.info(
            "OppaiOracle model loaded (providers=%s, tags=%d, ratings=%s).",
            self.session.get_providers(), len(self.tags), list(self.rating_indices.keys()),
        )
