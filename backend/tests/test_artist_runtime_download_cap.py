"""The LSNet runtime download must be bounded in size (audit F8, TODO resolved).

``_download_and_extract_github_zip`` has good extraction-phase caps (entry count
and total uncompressed bytes), but those only run after ``urlretrieve`` has
already streamed the whole response to disk. A hostile or misconfigured server
could therefore fill the disk before any guard fired - which is exactly what the
TODO at artist/downloads.py deferred.

The bound is enforced through urlretrieve's own reporthook, so the mockable
``urllib.request.urlretrieve`` seam the TODO asked to preserve is preserved: the
advertised Content-Length is rejected on the first callback, and the running
byte count is rejected on every callback so an absent or lying header is still
bounded.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

import artist_identifier as ai


def _zip_bytes(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _streaming_urlretrieve(payload: bytes, *, advertised_size: int, block_size: int = 8192):
    """A urlretrieve stand-in that drives the reporthook like the real one."""
    written = {"bytes": 0}

    def fake(url, destination, reporthook=None):
        with open(destination, "wb") as handle:
            for block_num, offset in enumerate(range(0, max(len(payload), 1), block_size)):
                if reporthook is not None:
                    reporthook(block_num, block_size, advertised_size)
                chunk = payload[offset:offset + block_size]
                handle.write(chunk)
                written["bytes"] += len(chunk)
        return str(destination), None

    fake.written = written
    return fake


def test_oversized_content_length_is_rejected_before_streaming(monkeypatch, tmp_path):
    payload = _zip_bytes({"comfyui-lsnet-main/lsnet_model/__init__.py": b"ok"})
    fake = _streaming_urlretrieve(payload, advertised_size=512 * 1024 * 1024)
    monkeypatch.setattr(ai.urllib.request, "urlretrieve", fake)

    with pytest.raises(ValueError, match="safe download limit"):
        ai._download_and_extract_github_zip(
            "https://example.test/runtime.zip", tmp_path / "runtime"
        )

    assert fake.written["bytes"] == 0, "bytes were written despite an oversized header"


def test_running_byte_cap_stops_a_server_that_lies_about_its_size(monkeypatch, tmp_path):
    payload = _zip_bytes({"comfyui-lsnet-main/lsnet_model/big.bin": b"x" * 200_000})
    # advertised_size <= 0 is what urlretrieve reports when there is no
    # Content-Length header at all.
    fake = _streaming_urlretrieve(payload, advertised_size=-1, block_size=1024)
    monkeypatch.setattr(ai.urllib.request, "urlretrieve", fake)
    monkeypatch.setattr(ai, "_MAX_ARTIST_RUNTIME_ZIP_BYTES", 4096)

    with pytest.raises(ValueError, match="safe download limit"):
        ai._download_and_extract_github_zip(
            "https://example.test/runtime.zip", tmp_path / "runtime"
        )

    assert fake.written["bytes"] <= 4096 + 1024, (
        "the download kept going well past the cap"
    )


def test_a_normal_runtime_zip_still_downloads_and_extracts(monkeypatch, tmp_path):
    payload = _zip_bytes({"comfyui-lsnet-main/lsnet_model/__init__.py": b"ok"})
    fake = _streaming_urlretrieve(payload, advertised_size=len(payload))
    monkeypatch.setattr(ai.urllib.request, "urlretrieve", fake)

    target = tmp_path / "models" / "artist" / "runtime"
    result = ai._download_and_extract_github_zip("https://example.test/runtime.zip", target)

    assert result == target
    assert Path(target / "lsnet_model" / "__init__.py").read_bytes() == b"ok"


def test_cap_is_a_real_ceiling_not_a_placeholder():
    assert isinstance(ai._MAX_ARTIST_RUNTIME_ZIP_BYTES, int)
    assert 0 < ai._MAX_ARTIST_RUNTIME_ZIP_BYTES <= 512 * 1024 * 1024
