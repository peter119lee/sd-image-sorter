from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

import artist_identifier


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)


def _fake_urlretrieve(source_zip: Path):
    def fake_urlretrieve(url: str, destination: Path, reporthook=None):
        # urlretrieve drives a reporthook; the download-size ceiling is enforced
        # there, so the stub has to drive it too or the cap goes untested.
        payload_size = source_zip.stat().st_size
        if reporthook is not None:
            reporthook(0, payload_size, payload_size)
        shutil.copyfile(source_zip, destination)
        return str(destination), None

    return fake_urlretrieve


def test_artist_runtime_zip_uses_pinned_commit_url():
    assert artist_identifier.ARTIST_LSNET_RUNTIME_REVISION in artist_identifier.ARTIST_LSNET_RUNTIME_ZIP_URL
    assert "refs/heads/main" not in artist_identifier.ARTIST_LSNET_RUNTIME_ZIP_URL
    assert artist_identifier.ARTIST_LSNET_RUNTIME_REVISION != "main"


def test_download_and_extract_github_zip_extracts_single_safe_root(monkeypatch, tmp_path: Path):
    source_zip = tmp_path / "runtime.zip"
    _write_zip(source_zip, {"comfyui-lsnet-main/lsnet_model/__init__.py": b"ok"})
    target_dir = tmp_path / "models" / "artist" / "runtime"

    monkeypatch.setattr(artist_identifier.urllib.request, "urlretrieve", _fake_urlretrieve(source_zip))

    result = artist_identifier._download_and_extract_github_zip("https://example.test/runtime.zip", target_dir)

    assert result == target_dir
    assert (target_dir / "lsnet_model" / "__init__.py").read_bytes() == b"ok"


@pytest.mark.parametrize("member_name", ["../escape.py", "..\\escape.py", "/tmp/escape.py", "C:/escape.py"])
def test_download_and_extract_github_zip_rejects_path_traversal(monkeypatch, tmp_path: Path, member_name: str):
    source_zip = tmp_path / "runtime.zip"
    _write_zip(source_zip, {member_name: b"bad"})
    target_dir = tmp_path / "models" / "artist" / "runtime"

    monkeypatch.setattr(artist_identifier.urllib.request, "urlretrieve", _fake_urlretrieve(source_zip))

    with pytest.raises(ValueError, match="path traversal"):
        artist_identifier._download_and_extract_github_zip("https://example.test/runtime.zip", target_dir)

    assert not (tmp_path / "escape.py").exists()


def test_download_and_extract_github_zip_rejects_empty_zip(monkeypatch, tmp_path: Path):
    source_zip = tmp_path / "runtime.zip"
    _write_zip(source_zip, {})
    target_dir = tmp_path / "models" / "artist" / "runtime"

    monkeypatch.setattr(artist_identifier.urllib.request, "urlretrieve", _fake_urlretrieve(source_zip))

    with pytest.raises(ValueError, match="exactly one runtime root"):
        artist_identifier._download_and_extract_github_zip("https://example.test/runtime.zip", target_dir)


def test_download_and_extract_github_zip_rejects_oversized_zip(monkeypatch, tmp_path: Path):
    source_zip = tmp_path / "runtime.zip"
    _write_zip(source_zip, {"comfyui-lsnet-main/lsnet_model/__init__.py": b"12345"})
    target_dir = tmp_path / "models" / "artist" / "runtime"

    monkeypatch.setattr(artist_identifier, "_MAX_ARTIST_RUNTIME_UNCOMPRESSED_BYTES", 4)
    monkeypatch.setattr(artist_identifier.urllib.request, "urlretrieve", _fake_urlretrieve(source_zip))

    with pytest.raises(ValueError, match="safe extraction limit"):
        artist_identifier._download_and_extract_github_zip("https://example.test/runtime.zip", target_dir)

    assert not (target_dir / "lsnet_model" / "__init__.py").exists()


def test_prepare_artist_assets_prefers_modelscope_when_configured(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    checkpoint = tmp_path / "ms" / "best_checkpoint.pth"
    mapping = tmp_path / "ms" / "class_mapping.csv"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"ckpt")
    mapping.write_text("class\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(artist_identifier, "_resolve_lsnet_runtime_path", lambda: str(runtime_dir))
    monkeypatch.setattr(artist_identifier, "ARTIST_MODELSCOPE_MODEL_ID", "owner/kaloscope")
    monkeypatch.setattr(
        artist_identifier,
        "_ensure_kaloscope_modelscope_files",
        lambda: calls.append("modelscope") or (str(checkpoint), str(mapping)),
    )
    monkeypatch.setattr(
        artist_identifier,
        "_ensure_kaloscope_hf_files",
        lambda: (_ for _ in ()).throw(AssertionError("HF fallback should not be first when ModelScope is configured")),
    )

    result = artist_identifier.prepare_artist_assets("modelscope")

    assert calls == ["modelscope"]
    assert result["source"] == "modelscope"
    assert result["checkpoint_path"] == str(checkpoint)


def test_prepare_artist_assets_modelscope_without_repo_uses_hf_endpoint_order(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    checkpoint = tmp_path / "hf" / "best_checkpoint.pth"
    mapping = tmp_path / "hf" / "class_mapping.csv"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"ckpt")
    mapping.write_text("class\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(artist_identifier, "_resolve_lsnet_runtime_path", lambda: str(runtime_dir))
    monkeypatch.setattr(artist_identifier, "ARTIST_MODELSCOPE_MODEL_ID", "")
    monkeypatch.setattr(
        artist_identifier,
        "_ensure_kaloscope_hf_files",
        lambda: calls.append("huggingface") or (str(checkpoint), str(mapping)),
    )

    result = artist_identifier.prepare_artist_assets("modelscope")

    assert calls == ["huggingface"]
    assert result["source"] == "huggingface"
    assert result["checkpoint_path"] == str(checkpoint)


def test_verify_artist_file_digest_rejects_pinned_mismatch(tmp_path: Path):
    target = tmp_path / "checkpoint.pth"
    target.write_bytes(b"tampered bytes that will not match the pinned digest")
    pinned_name = next(iter(artist_identifier._EXPECTED_ARTIST_FILE_SHA256))
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        artist_identifier._verify_artist_file_digest(pinned_name, target)


def test_verify_artist_file_digest_skips_unpinned_file(tmp_path: Path):
    target = tmp_path / "unpinned.bin"
    target.write_bytes(b"anything goes when no digest is pinned")
    # No pinned digest for this name -> no-op, must not raise.
    artist_identifier._verify_artist_file_digest("unpinned.bin", target)


def test_class_mapping_pin_lists_both_hf_and_modelscope_digests():
    """HuggingFace serves class_mapping.csv with CRLF, ModelScope with LF.

    The rows are byte-identical apart from line endings, so both digests are
    legitimate and must be pinned (a single-digest pin would reject the real
    ModelScope download).
    """
    digests = artist_identifier._EXPECTED_ARTIST_FILE_SHA256["class_mapping.csv"]
    assert isinstance(digests, tuple)
    assert len(digests) >= 2


def test_verify_artist_file_digest_accepts_any_pinned_variant(monkeypatch, tmp_path: Path):
    target = tmp_path / "class_mapping.csv"
    target.write_bytes(b"class_id,class_name\n0,a\n")
    real_digest = artist_identifier._sha256_file(target)
    # Pin two acceptable digests; the file matches the second one.
    monkeypatch.setitem(
        artist_identifier._EXPECTED_ARTIST_FILE_SHA256,
        "class_mapping.csv",
        ("de" * 32, real_digest),
    )
    # Matches one of the accepted digests -> must not raise.
    artist_identifier._verify_artist_file_digest("class_mapping.csv", target)

    # Matches neither -> rejected.
    target.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        artist_identifier._verify_artist_file_digest("class_mapping.csv", target)


def test_ensure_kaloscope_modelscope_files_uses_direct_url_not_sdk(monkeypatch, tmp_path: Path):
    """The ModelScope route must download via direct resolve URLs and must NOT
    require the modelscope SDK (real users don't have it installed)."""
    import sys

    artist_root = tmp_path / "artist"
    monkeypatch.setattr(artist_identifier, "_get_artist_model_root", lambda: artist_root)
    monkeypatch.setattr(artist_identifier, "ARTIST_MODELSCOPE_MODEL_ID", "Owner/Kaloscope-2.0")
    monkeypatch.setenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", "1")
    # Simulate modelscope SDK being absent: importing it would fail.
    monkeypatch.setitem(sys.modules, "modelscope", None)

    fetched: list[str] = []

    def fake_fetch(url: str, destination: Path, filename: str) -> str:
        fetched.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x")
        return str(destination.resolve())

    monkeypatch.setattr(artist_identifier, "_fetch_artist_file", fake_fetch)

    checkpoint_path, mapping_path = artist_identifier._ensure_kaloscope_modelscope_files()

    # Files land in the canonical kaloscope2.0 layout that health detection expects.
    assert checkpoint_path.endswith(
        str(Path("kaloscope2.0") / "448-90.13" / "best_checkpoint.pth")
    )
    assert mapping_path.endswith(str(Path("kaloscope2.0") / "class_mapping.csv"))
    # Direct modelscope.cn URLs; the flat basename is tried for the checkpoint.
    assert fetched, "expected at least one direct download URL"
    assert all("modelscope.cn" in url for url in fetched)
    assert any(url.endswith("/resolve/master/best_checkpoint.pth") for url in fetched)


def test_ensure_kaloscope_modelscope_files_requires_repo_id(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(artist_identifier, "_get_artist_model_root", lambda: tmp_path)
    monkeypatch.setattr(artist_identifier, "ARTIST_MODELSCOPE_MODEL_ID", "")
    with pytest.raises(RuntimeError, match="No compatible ModelScope"):
        artist_identifier._ensure_kaloscope_modelscope_files()


def test_assert_http_download_url_rejects_file_scheme_by_default(monkeypatch):
    monkeypatch.delenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", raising=False)
    with pytest.raises(ValueError, match="Refusing to download"):
        artist_identifier._assert_http_download_url("file:///etc/passwd")
    # http(s) always allowed.
    artist_identifier._assert_http_download_url("https://modelscope.cn/x")
    artist_identifier._assert_http_download_url("http://localhost:8000/x")


def test_assert_http_download_url_allows_file_scheme_only_with_test_flag(monkeypatch):
    monkeypatch.setenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", "1")
    # With the explicit E2E fixture flag, file:// is permitted.
    artist_identifier._assert_http_download_url("file:///tmp/fixture.pth")


def test_modelscope_base_url_override_blocks_file_scheme(monkeypatch, tmp_path: Path):
    """A stray file:// base-URL override must not coerce urllib into local reads."""
    monkeypatch.delenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", raising=False)
    monkeypatch.setattr(artist_identifier, "_get_artist_model_root", lambda: tmp_path / "artist")
    monkeypatch.setattr(artist_identifier, "ARTIST_MODELSCOPE_MODEL_ID", "Owner/Kaloscope-2.0")
    monkeypatch.setenv("SD_IMAGE_SORTER_ARTIST_MODELSCOPE_BASE_URL", "file:///C:/Windows/secret")
    with pytest.raises(RuntimeError, match="Could not download the Kaloscope checkpoint"):
        artist_identifier._ensure_kaloscope_modelscope_files()


def test_ensure_kaloscope_modelscope_files_short_circuits_when_present(monkeypatch, tmp_path: Path):
    """Already-downloaded files are returned without any network fetch."""
    artist_root = tmp_path / "artist"
    local_dir = artist_root / "kaloscope2.0"
    (local_dir / "448-90.13").mkdir(parents=True)
    (local_dir / "448-90.13" / "best_checkpoint.pth").write_bytes(b"ckpt")
    (local_dir / "class_mapping.csv").write_text("class_id,class_name\n", encoding="utf-8")

    monkeypatch.setattr(artist_identifier, "_get_artist_model_root", lambda: artist_root)
    monkeypatch.setattr(artist_identifier, "ARTIST_MODELSCOPE_MODEL_ID", "Owner/Kaloscope-2.0")

    def boom(*args, **kwargs):
        raise AssertionError("must not download when files already exist")

    monkeypatch.setattr(artist_identifier, "_fetch_artist_file", boom)

    checkpoint_path, mapping_path = artist_identifier._ensure_kaloscope_modelscope_files()
    assert Path(checkpoint_path).exists()
    assert Path(mapping_path).exists()
