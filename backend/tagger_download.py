"""HuggingFace download mixin for WD14Tagger (split from tagger.py, 2026-07).

Methods moved from tagger.py:
_download_model / _download_with_fallback. Manifested lines (the ONLY
non-verbatim edits): the three not-None assert guards and the
``hf_hub_download(**kwargs)`` call resolve ``hf_hub`` through _svc()
at call time, because the reader suites patch ``tagger.hf_hub`` on the
facade module object -- a bare module-global read here would silently miss
those patches (the lazy-import family stays DEFINED on the facade). The
logger keeps the original "tagger" channel.
"""

import logging
import os
from pathlib import Path
from typing import Mapping, Optional, Tuple

from config import TAGGER_MODELS as MODELS
from model_download_sources import (
    endpoint_label,
    get_hf_endpoint_order,
    log_model_artifact_status,
)

logger = logging.getLogger("tagger")


def _svc():
    """Resolve facade-owned lazy-import globals through ``tagger`` at call time.

    Tests patch ``tagger.hf_hub`` on the facade; a from-import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import tagger

    return tagger


def _validated_model_revision(
    model_name: str,
    config: Mapping[str, object],
) -> Optional[str]:
    """Return one optional commit pin and reject ambiguous revisions."""
    revision = config.get("revision")
    if revision is None:
        return None
    if not isinstance(revision, str):
        raise TypeError(
            f"Tagger model {model_name!r} revision must be a 40-character commit hash"
        )
    normalized = revision.strip().lower()
    if len(normalized) != 40 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(
            f"Tagger model {model_name!r} revision must be a 40-character commit hash: "
            f"received {revision!r}"
        )
    return normalized


class _DownloadMixin:
    """Endpoint-fallback HF downloads (network; never exercised by unit suites)."""

    def _download_model(self) -> Tuple[str, str]:
        """Download model from HuggingFace if not present."""
        if self.model_name not in MODELS:
            raise ValueError(
                f"Unknown model: {self.model_name}. Available: {list(MODELS.keys())}"
            )

        config = MODELS[self.model_name]
        repo_id = config["repo_id"]
        revision = _validated_model_revision(self.model_name, config)

        model_path = os.path.join(self.model_dir, self.model_name, config["model_file"])
        tags_path = os.path.join(self.model_dir, self.model_name, config["tags_file"])

        # Check if model exists and is valid
        needs_download = False
        if not os.path.exists(model_path):
            needs_download = True
        elif not self._validate_model_file(model_path):
            logger.warning(
                f"Model file {model_path} appears corrupted. Re-downloading..."
            )
            needs_download = True
            # Delete corrupted file
            try:
                os.remove(model_path)
            except Exception as e:
                logger.warning(f"Could not delete corrupted model file: {e}")

        # Download if needed
        if needs_download:
            logger.info(f"Downloading model {self.model_name}...")
            os.makedirs(os.path.dirname(model_path), exist_ok=True)

            try:
                assert _svc().hf_hub is not None
                model_path = self._download_with_fallback(
                    repo_id=repo_id,
                    filename=config["model_file"],
                    local_dir=os.path.join(self.model_dir, self.model_name),
                    revision=revision,
                )

                # Validate after download
                if not self._validate_model_file(model_path):
                    raise ValueError(
                        "Downloaded model file is invalid. Please check your internet connection and try again."
                    )
            except Exception as e:
                logger.error(f"Error downloading model: {e}")
                raise

        if not os.path.exists(tags_path) or not self._validate_tag_file(tags_path):
            logger.info("Downloading tags file...")
            assert _svc().hf_hub is not None
            tags_path = self._download_with_fallback(
                repo_id=repo_id,
                filename=config["tags_file"],
                local_dir=os.path.join(self.model_dir, self.model_name),
                revision=revision,
            )

        local_root = Path(self.model_dir) / self.model_name
        missing = log_model_artifact_status(
            logger,
            model_id=self.model_name,
            revision=revision,
            endpoint="local",
            model_dir=local_root,
            required_files=(config["model_file"], config["tags_file"]),
        )
        if missing:
            raise RuntimeError(
                f"WD14 model {self.model_name!r} is incomplete after download: "
                + ", ".join(missing)
                + ". Re-run Prepare / Download."
            )

        return model_path, tags_path

    @staticmethod
    def _validate_tag_file(path: str) -> bool:
        """Return whether a WD14 tag table is a non-empty regular file."""
        try:
            return Path(path).is_file() and Path(path).stat().st_size > 0
        except OSError:
            return False

    def _download_with_fallback(
        self,
        repo_id: str,
        filename: str,
        local_dir: str,
        revision: Optional[str],
    ) -> str:
        assert _svc().hf_hub is not None
        endpoints = get_hf_endpoint_order(model_name=f"WD14 {self.model_name}")

        seen = set()
        last_error: Optional[Exception] = None
        for endpoint in endpoints:
            key = endpoint.lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                logger.info(
                    "Downloading %s from %s via %s",
                    filename,
                    repo_id,
                    endpoint_label(endpoint),
                )
                kwargs = {
                    "repo_id": repo_id,
                    "filename": filename,
                    "local_dir": local_dir,
                    "endpoint": endpoint,
                }
                if revision is not None:
                    kwargs["revision"] = revision
                resolved_path = _svc().hf_hub.hf_hub_download(**kwargs)
                log_model_artifact_status(
                    logger,
                    model_id=self.model_name,
                    revision=revision,
                    endpoint=endpoint,
                    model_dir=Path(local_dir),
                    required_files=(filename,),
                )
                return resolved_path
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Download failed for %s via %s: %s",
                    filename,
                    endpoint_label(endpoint),
                    exc,
                )

        if last_error is None:
            raise RuntimeError(f"Failed to download {filename} from {repo_id}")
        raise last_error
