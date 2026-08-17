"""Download engine + integrity guards for artist model assets.

Moved verbatim from backend/artist_identifier.py (decomposition 2026-07) except the
manifest lines: reads of the facade-patched seam family (the _MAX_* caps,
_EXPECTED_ARTIST_FILE_SHA256, ARTIST_KALOSCOPE_*/ARTIST_HF_MODEL_ID config binds,
get_hf_endpoint_order/endpoint_label, and every cross-function call) resolve
through _facade() at call time. ``urllib.request.urlretrieve`` stays a bare
module-attr access: tests patch the shared urllib.request module singleton, so
it is split-safe. ARTIST_MODELSCOPE_REVISION/_ARTIST_USER_AGENT
are unpatched same-module constants (def-time default-arg bind preserved).
"""

import hashlib
import logging
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import List, Optional

logger = logging.getLogger("sd-image-sorter.artist")


def _facade():
    """Resolve facade-owned seams/constants through artist_identifier at call time.

    Tests patch module attributes on the facade: ~10 of these free functions
    are monkeypatched on
    ``artist_identifier`` and called by the others, and the diagnostics/pin
    suites patch the facade ``__file__`` and its config bindings. A from-import
    here would freeze an independent binding those patches — and the
    ``importlib.reload(artist_identifier)`` config re-reads — silently miss.
    The lazy import avoids a facade<->submodule load cycle.
    """
    import artist_identifier

    return artist_identifier

def _materialize_existing_file(source: Path, dest: Path) -> bool:
    if not source.exists() or dest.exists():
        return dest.exists()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)
    return True


def _copy_existing_tree(source: Path, dest: Path, marker_name: str) -> bool:
    if not (source / marker_name).exists():
        return False
    if (dest / marker_name).exists():
        return True
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    return True


def _artist_override_url(filename: str) -> Optional[str]:
    if filename == _facade().ARTIST_KALOSCOPE_CLASS_MAPPING:
        return os.environ.get("SD_IMAGE_SORTER_ARTIST_CLASS_MAPPING_URL") or None
    if filename == _facade().ARTIST_KALOSCOPE_CHECKPOINT:
        return os.environ.get("SD_IMAGE_SORTER_ARTIST_CHECKPOINT_URL") or None
    return None


def _candidate_hf_endpoints() -> List[str]:
    return _facade().get_hf_endpoint_order(model_name="Artist ID / Kaloscope")


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_artist_file_digest(filename: str, file_path: Path) -> None:
    """Reject a freshly-downloaded artist file if its SHA-256 is pinned and wrong.

    No-op for files without a pinned digest (see _EXPECTED_ARTIST_FILE_SHA256).
    A file matching ANY of the pinned digest variants for its name is accepted.

    The end-to-end suite stages tiny fixture checkpoints whose digests cannot
    match the pinned production artifacts, so the same explicit test-only flag
    that opts into ``file://`` fixture downloads also skips digest verification.
    Production never sets this flag, so real downloads stay strictly verified.
    """
    if os.environ.get("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    expected = _facade()._EXPECTED_ARTIST_FILE_SHA256.get(filename)
    if not expected:
        return
    actual = _facade()._sha256_file(file_path)
    accepted = {digest.lower() for digest in expected}
    if actual.lower() not in accepted:
        raise RuntimeError(
            f"SHA-256 mismatch for downloaded artist file '{filename}': expected "
            f"one of {sorted(accepted)}, got {actual}. Refusing to use a tampered "
            f"or version-mismatched artifact."
        )


def _assert_http_download_url(url: str) -> None:
    """Reject non-http(s) artist download URLs (defense in depth).

    Artist file URLs come from app-controlled constants, but three are
    env-var-overridable for tests/self-hosted mirrors
    (SD_IMAGE_SORTER_ARTIST_MODELSCOPE_BASE_URL,
    SD_IMAGE_SORTER_ARTIST_CHECKPOINT_URL / _CLASS_MAPPING_URL). Without a
    scheme guard a stray ``file://`` override would coerce urllib into reading
    local files. Only ``https``/``http`` are accepted — plus ``file`` when the
    explicit test flag opts in (the E2E suite stages fixtures as ``file://``
    URLs), mirroring ``model_service.urlopen_with_ua``. Production never sets
    that flag, so real downloads stay http(s)-only.
    """
    from urllib.parse import urlparse

    allowed = {"https", "http"}
    if os.environ.get("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", "").strip().lower() in {"1", "true", "yes", "on"}:
        allowed.add("file")
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in allowed:
        raise ValueError(
            f"Refusing to download artist file from scheme {scheme!r}; only {sorted(allowed)} are allowed."
        )


def _hf_download_with_fallback(repo_id: str, filename: str, local_dir: str) -> str:
    # Downloads are verified against pinned SHA-256 digests where available
    # (_EXPECTED_ARTIST_FILE_SHA256, checked just before each tmp_path.replace).
    # The Kaloscope checkpoint and class mapping are pinned; the LSNet runtime
    # zip is not yet — add its digest to that table to make it tamper-evident.
    override_url = _facade()._artist_override_url(filename)
    if override_url:
        _facade()._assert_http_download_url(override_url)
        destination = Path(local_dir) / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = destination.with_suffix(destination.suffix + ".tmp")
        logger.info("Downloading %s from explicit artist override URL", filename)
        try:
            request = urllib.request.Request(override_url, headers={"User-Agent": "sd-image-sorter/3.2.1"})
            with urllib.request.urlopen(request, timeout=600) as src, tmp_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            _facade()._verify_artist_file_digest(filename, tmp_path)
            tmp_path.replace(destination)
            return str(destination.resolve())
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    last_error: Optional[Exception] = None
    for endpoint in _facade()._candidate_hf_endpoints():
        try:
            url = f"{endpoint.rstrip('/')}/{repo_id}/resolve/main/{filename}"
            destination = Path(local_dir) / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = destination.with_suffix(destination.suffix + ".tmp")
            logger.info("Downloading %s from %s via %s", filename, repo_id, _facade().endpoint_label(endpoint))
            request = urllib.request.Request(url, headers={"User-Agent": "sd-image-sorter/3.1.1"})
            with urllib.request.urlopen(request, timeout=600) as src, tmp_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            _facade()._verify_artist_file_digest(filename, tmp_path)
            tmp_path.replace(destination)
            return str(destination.resolve())
        except Exception as exc:
            last_error = exc
            logger.warning("Download failed for %s via %s: %s", filename, _facade().endpoint_label(endpoint), exc)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                logger.debug("Could not remove partial download %s: %s", tmp_path, cleanup_exc)

    if last_error is None:
        raise RuntimeError(f"Failed to download {filename} from {repo_id}")
    raise last_error


def _bounded_download_reporthook(max_bytes: int):
    """Abort a runtime download once it exceeds ``max_bytes``.

    urlretrieve streams straight to disk, so the extraction-phase caps only run
    after the whole response has landed. Enforcing the ceiling from urlretrieve's
    own reporthook keeps ``urllib.request.urlretrieve`` as the mockable seam
    while still stopping mid-stream: the advertised Content-Length is checked on
    the first callback, and the running byte count on every callback so a
    missing or dishonest header is bounded too.
    """

    def _reporthook(block_num: int, block_size: int, total_size: int) -> None:
        advertised = int(total_size or 0)
        if advertised > max_bytes:
            raise ValueError(
                f"Runtime download advertises {advertised} bytes, above the "
                f"{max_bytes}-byte safe download limit"
            )
        if int(block_num) * int(block_size) > max_bytes:
            raise ValueError(
                f"Runtime download exceeded the {max_bytes}-byte safe download limit"
            )

    return _reporthook


def _download_and_extract_github_zip(zip_url: str, target_dir: Path) -> Path:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kaloscope-runtime-") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        zip_path = tmp_dir_path / "repo.zip"
        # Three layers: this download-size ceiling, then the entry-count and
        # total-uncompressed-bytes caps during extraction below.
        urllib.request.urlretrieve(
            zip_url,
            zip_path,
            _facade()._bounded_download_reporthook(
                _facade()._MAX_ARTIST_RUNTIME_ZIP_BYTES
            ),
        )
        extract_dir = tmp_dir_path / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        extract_root = extract_dir.resolve()
        total_uncompressed_bytes = 0
        with zipfile.ZipFile(zip_path, "r") as archive:
            members = archive.infolist()
            if len(members) > _facade()._MAX_ARTIST_RUNTIME_ZIP_ENTRIES:
                raise ValueError("Zip contains too many entries to extract safely")
            for member in members:
                normalized_name = str(member.filename or "").replace("\\", "/").strip()
                relative_name = PurePosixPath(normalized_name)
                if (
                    not normalized_name
                    or relative_name.is_absolute()
                    or normalized_name[:2].endswith(":")
                    or ".." in relative_name.parts
                ):
                    raise ValueError(f"Zip contains path traversal: {member.filename}")
                member_path = (extract_root / relative_name).resolve()
                try:
                    member_path.relative_to(extract_root)
                except ValueError as exc:
                    raise ValueError(f"Zip contains path traversal: {member.filename}") from exc
                if not member.is_dir():
                    total_uncompressed_bytes += member.file_size
                    if total_uncompressed_bytes > _facade()._MAX_ARTIST_RUNTIME_UNCOMPRESSED_BYTES:
                        raise ValueError("Zip uncompressed size exceeds the safe extraction limit")
            for member in members:
                normalized_name = str(member.filename or "").replace("\\", "/").strip()
                member_path = (extract_root / PurePosixPath(normalized_name)).resolve()
                if member.is_dir():
                    member_path.mkdir(parents=True, exist_ok=True)
                    continue
                member_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as src, member_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

        extracted_roots = [path for path in extract_dir.iterdir() if path.is_dir()]
        if len(extracted_roots) != 1:
            raise ValueError("Zip must contain exactly one runtime root directory")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(str(extracted_roots[0]), str(target_dir))
    return target_dir


ARTIST_MODELSCOPE_REVISION = "master"
_ARTIST_USER_AGENT = "sd-image-sorter/3.3"


def _modelscope_resolve_url(repo_id: str, filename: str, *, revision: str = ARTIST_MODELSCOPE_REVISION) -> str:
    """Build a direct ModelScope resolve URL for a repo-relative file.

    ``SD_IMAGE_SORTER_ARTIST_MODELSCOPE_BASE_URL`` overrides the base so the
    E2E suite (and self-hosted mirrors) can point at a local/alternate host.
    """
    base = os.environ.get("SD_IMAGE_SORTER_ARTIST_MODELSCOPE_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/{filename}"
    return f"https://modelscope.cn/models/{repo_id}/resolve/{revision}/{filename}"


def _fetch_artist_file(url: str, destination: Path, filename: str) -> str:
    """Download one artist file to ``destination`` with pinned-digest verification.

    Mirrors ``_hf_download_with_fallback``'s integrity guarantees: validates the
    URL scheme, follows redirects (ModelScope LFS blobs 302 to a CDN), writes
    atomically through a ``.tmp`` sibling, and verifies the bytes against the
    pinned digest for ``filename`` (the *remote* name, which is how the digest
    table is keyed) before moving them into place.
    """
    _facade()._assert_http_download_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_suffix(destination.suffix + ".tmp")
    logger.info("Downloading %s from %s", filename, url)
    request = urllib.request.Request(url, headers={"User-Agent": _ARTIST_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=600) as src, tmp_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        _facade()._verify_artist_file_digest(filename, tmp_path)
        tmp_path.replace(destination)
        return str(destination.resolve())
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
