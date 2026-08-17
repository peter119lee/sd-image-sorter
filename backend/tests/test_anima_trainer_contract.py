from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import TextIO

import pytest
from pydantic import ValidationError

from services.dataset_export.models import DatasetExportRequest


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts" / "verify_anima_trainer_contract.py"
FIXTURE_ROOT = (
    ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "anima"
    / "v1.14.2.hotfix"
)
VALID_FIXTURE = FIXTURE_ROOT / "valid-dataset-config.toml"
MASKED_FIXTURE = FIXTURE_ROOT / "valid-masked-dataset-config.toml"
REJECTED_FIXTURE = FIXTURE_ROOT / "upstream-rejected-dataset-config.toml"
PINNED_COMMIT = "13eaf97a3903405baa939d7cb4a524f8f3e11303"


def _contract_module() -> ModuleType:
    return importlib.import_module("services.dataset_export.anima_contract")


def _verifier_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("anima_contract_verifier_test", VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load verifier module: {VERIFIER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request(tmp_path: Path, **updates: object) -> DatasetExportRequest:
    values: dict[str, object] = {
        "image_paths": [str(tmp_path / "source.png")],
        "output_folder": str(tmp_path),
        "trainer_config": "anima_lora_toml",
    }
    values.update(updates)
    return DatasetExportRequest.model_validate(values)


def _write_image_caption_pair(root: Path, stem: str, caption: str) -> None:
    (root / f"{stem}.png").write_bytes(b"not-decoded-by-contract")
    (root / f"{stem}.txt").write_text(caption, encoding="utf-8")


def _init_git_checkout(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Contract Test"],
        cwd=root,
        check=True,
    )
    tracked = root / "tracked.txt"
    tracked.write_text("clean", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "test"], cwd=root, check=True)


def test_anima_contract_is_strict_and_pinned() -> None:
    contract_module = _contract_module()
    contract = contract_module.get_anima_trainer_contract()

    assert contract.model_dump(mode="json") == {
        "id": "anima_lora",
        "display_name": "Anima LoRA",
        "wire_value": "anima_lora_toml",
        "contract_version": "1.0.0",
        "verified": True,
        "mask_export_modes": ["none", "anima_lora"],
        "upstream": {
            "repository": "https://github.com/sorryhyun/anima_lora",
            "tag": "v1.14.2.hotfix",
            "commit": PINNED_COMMIT,
            "license": "MIT",
            "python_requirement": "==3.13.*",
        },
        "capabilities": {
            "caption_extensions": [".txt"],
            "separate_loss_masks": True,
            "loss_mask_suffix": "_mask.png",
            "class_tokens_behavior": "forbidden",
        },
        "option_bounds": {
            "repeats": {"minimum": 1, "maximum": 1000, "default": 10},
            "batch_size": {"minimum": 1, "maximum": 64, "default": 2},
            "resolution": {"minimum": 1024, "maximum": 1024, "default": 1024},
            "keep_tokens": {"minimum": 0, "maximum": 0, "default": 0},
        },
        "generated_artifacts": {
            "dataset_config": "dataset_config.toml",
            "caption_sidecar": "<image-stem>.txt",
            "loss_mask": "<relative-path>/<image-stem>_mask.png",
            "mask_directory": "mask",
        },
        "verification_boundary": {
            "module": "library.config.loader",
            "required_flags": ["--support_dropout"],
            "validates_upstream_schema": True,
            "validates_artifact_completeness": False,
            "requires_module_path_match": True,
            "artifact_completeness_gate": (
                "all_captions_and_requested_masks_before_generation"
            ),
            "starts_training": False,
        },
    }
    payload = contract.model_dump(mode="json")
    with pytest.raises(ValidationError):
        contract_module.AnimaTrainerContract.model_validate({**payload, "extra": True})


def test_anima_options_are_strict_and_require_all_fields(tmp_path: Path) -> None:
    contract_module = _contract_module()
    valid = {
        "image_dir": tmp_path,
        "caption_extension": ".txt",
        "num_repeats": 10,
        "batch_size": 2,
        "mask_dir": None,
    }

    options = contract_module.AnimaDatasetConfigOptions.model_validate(valid)

    assert options.image_dir == tmp_path
    with pytest.raises(ValidationError):
        contract_module.AnimaDatasetConfigOptions.model_validate(
            {**valid, "class_tokens": "forbidden"}
        )
    with pytest.raises(ValidationError):
        contract_module.AnimaDatasetConfigOptions.model_validate(
            {key: value for key, value in valid.items() if key != "batch_size"}
        )
    with pytest.raises(ValidationError):
        contract_module.AnimaDatasetConfigOptions.model_validate(
            {**valid, "num_repeats": True}
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"trainer_config": "kohya_toml"}, "trainer_config='anima_lora_toml'"),
        ({"content_mode": "json"}, "requires text captions"),
        ({"trainer_keep_tokens": 1}, "trainer_keep_tokens=0"),
        ({"trainer_resolution": 768}, "trainer_resolution=1024"),
        ({"mask_export": "kohya"}, "mask_export='none' or 'anima_lora'"),
    ],
)
def test_anima_request_rejects_incompatible_options(
    tmp_path: Path,
    updates: dict[str, object],
    message: str,
) -> None:
    contract_module = _contract_module()

    with pytest.raises(contract_module.AnimaTrainerContractError, match=message):
        contract_module.validate_anima_request(_request(tmp_path, **updates))


@pytest.mark.parametrize("mask_export", ["none", "anima_lora"])
def test_anima_request_accepts_supported_mask_modes(
    tmp_path: Path,
    mask_export: str,
) -> None:
    contract_module = _contract_module()

    contract_module.validate_anima_request(_request(tmp_path, mask_export=mask_export))


def test_anima_versioned_fixtures_validate_with_distinct_schema() -> None:
    contract_module = _contract_module()

    unmasked = contract_module.validate_anima_toml_text(
        VALID_FIXTURE.read_text(encoding="utf-8")
    )
    masked = contract_module.validate_anima_toml_text(
        MASKED_FIXTURE.read_text(encoding="utf-8")
    )

    assert unmasked.caption_extension == ".txt"
    assert unmasked.num_repeats == 10
    assert unmasked.batch_size == 2
    assert unmasked.mask_dir is None
    assert masked.mask_dir == Path("./mask")


@pytest.mark.parametrize(
    "content",
    [
        "unsupported_root = true\n"
        "[[datasets]]\nbatch_size = 2\n[[datasets.subsets]]\n"
        "image_dir = './images'\ncaption_extension = '.txt'\nnum_repeats = 10\n",
        "[general]\nenable_bucket = true\n"
        "[[datasets]]\nbatch_size = 2\n[[datasets.subsets]]\n"
        "image_dir = './images'\ncaption_extension = '.txt'\nnum_repeats = 10\n",
        "[[datasets]]\nbatch_size = 2\nresolution = 1024\n"
        "[[datasets.subsets]]\nimage_dir = './images'\n"
        "caption_extension = '.txt'\nnum_repeats = 10\n",
        "[[datasets]]\nbatch_size = 2\n[[datasets.subsets]]\n"
        "image_dir = './images'\ncaption_extension = '.txt'\n"
        "num_repeats = 10\nclass_tokens = 'forbidden'\n",
        "[[datasets]]\nbatch_size = 2\n[[datasets.subsets]]\n"
        "image_dir = './images'\ncaption_extension = '.txt'\nnum_repeats = 10\n"
        "conditioning_data_dir = './mask'\n",
        "[[datasets]]\nbatch_size = 2\n[[datasets.subsets]]\n"
        "image_dir = './images'\ncaption_extension = '.txt'\n"
        "num_repeats = 10\nshuffle_caption = true\n",
        "[[datasets]]\nbatch_size = 2\n[[datasets.subsets]]\n"
        "image_dir = './images'\ncaption_extension = '.txt'\n"
        "num_repeats = 10\nkeep_tokens = 1\n",
    ],
)
def test_anima_local_schema_rejects_unknown_or_kohya_only_fields(content: str) -> None:
    contract_module = _contract_module()

    with pytest.raises(
        contract_module.AnimaTrainerContractError,
        match="unsupported fields",
    ):
        contract_module.validate_anima_toml_text(content)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not = [valid", "not parseable"),
        (
            "[[datasets]]\nbatch_size = 2\n[[datasets.subsets]]\n"
            "image_dir = './images'\ncaption_extension = '.caption'\n"
            "num_repeats = 10\n",
            "strict option",
        ),
        (
            "[[datasets]]\nbatch_size = true\n[[datasets.subsets]]\n"
            "image_dir = './images'\ncaption_extension = '.txt'\n"
            "num_repeats = 10\n",
            "strict option",
        ),
        (
            "[[datasets]]\nbatch_size = 2\n[[datasets.subsets]]\n"
            "caption_extension = '.txt'\nnum_repeats = 10\n",
            "strict option",
        ),
    ],
)
def test_anima_local_schema_rejects_malformed_or_incomplete_documents(
    content: str,
    message: str,
) -> None:
    contract_module = _contract_module()

    with pytest.raises(contract_module.AnimaTrainerContractError, match=message):
        contract_module.validate_anima_toml_text(content)


def test_anima_renderer_escapes_paths_and_emits_only_approved_fields(
    tmp_path: Path,
) -> None:
    contract_module = _contract_module()
    image_dir = tmp_path / 'images "quoted"'
    mask_dir = tmp_path / 'masks "quoted"'
    options = contract_module.AnimaDatasetConfigOptions(
        image_dir=image_dir,
        caption_extension=".txt",
        num_repeats=7,
        batch_size=3,
        mask_dir=mask_dir,
    )

    content = contract_module.render_anima_dataset_config(options)
    parsed = tomllib.loads(content)

    assert parsed == {
        "datasets": [
            {
                "batch_size": 3,
                "subsets": [
                    {
                        "image_dir": str(image_dir).replace("\\", "/"),
                        "caption_extension": ".txt",
                        "num_repeats": 7,
                        "mask_dir": str(mask_dir).replace("\\", "/"),
                    }
                ],
            }
        ],
    }
    for forbidden in (
        "class_tokens",
        "enable_bucket",
        "resolution",
        "conditioning_data_dir",
        "shuffle_caption",
        "keep_tokens",
    ):
        assert forbidden not in content


def test_anima_artifact_completeness_accepts_nonempty_captions_and_loss_masks(
    tmp_path: Path,
) -> None:
    contract_module = _contract_module()
    _write_image_caption_pair(tmp_path, "sample", "trigger, blue hair")
    mask_dir = tmp_path / "mask"
    mask_dir.mkdir()
    (mask_dir / "sample_mask.png").write_bytes(b"mask")
    options = contract_module.AnimaDatasetConfigOptions(
        image_dir=tmp_path,
        caption_extension=".txt",
        num_repeats=10,
        batch_size=2,
        mask_dir=mask_dir,
    )

    result = contract_module.validate_anima_artifact_completeness(options)

    assert result.model_dump() == {
        "image_count": 1,
        "caption_count": 1,
        "mask_count": 1,
    }


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("empty", "no training images"),
        ("missing-caption", "caption is missing"),
        ("empty-caption", "caption is empty"),
        ("duplicate-stem", "duplicate image stem"),
        ("missing-mask", "Anima loss masks require"),
    ],
)
def test_anima_artifact_completeness_fails_closed(
    tmp_path: Path,
    setup: str,
    message: str,
) -> None:
    contract_module = _contract_module()
    mask_dir: Path | None = None
    if setup != "empty":
        (tmp_path / "sample.png").write_bytes(b"image")
    if setup not in {"empty", "missing-caption"}:
        caption = "   " if setup == "empty-caption" else "caption"
        (tmp_path / "sample.txt").write_text(caption, encoding="utf-8")
    if setup == "duplicate-stem":
        (tmp_path / "sample.jpg").write_bytes(b"second-image")
    if setup == "missing-mask":
        mask_dir = tmp_path / "mask"
        mask_dir.mkdir()
    options = contract_module.AnimaDatasetConfigOptions(
        image_dir=tmp_path,
        caption_extension=".txt",
        num_repeats=10,
        batch_size=2,
        mask_dir=mask_dir,
    )

    with pytest.raises(contract_module.AnimaTrainerContractError, match=message):
        contract_module.validate_anima_artifact_completeness(options)


def test_anima_loss_mask_does_not_fall_back_to_flat_path(tmp_path: Path) -> None:
    contract_module = _contract_module()
    image_dir = tmp_path / "images"
    image = image_dir / "character" / "sample.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    mask_dir = tmp_path / "mask"
    mask_dir.mkdir()
    (mask_dir / "sample_mask.png").write_bytes(b"flat-mask")

    with pytest.raises(
        contract_module.AnimaTrainerContractError,
        match="Anima loss masks require",
    ):
        contract_module._require_loss_mask(image, image_dir, mask_dir)

    expected = mask_dir / "character" / "sample_mask.png"
    expected.parent.mkdir()
    expected.write_bytes(b"relative-mask")
    assert contract_module._require_loss_mask(image, image_dir, mask_dir) == expected


def test_anima_writer_validates_real_artifacts_before_emitting_config(
    tmp_path: Path,
) -> None:
    contract_module = _contract_module()
    _write_image_caption_pair(tmp_path, "sample", "trigger")
    mask_dir = tmp_path / "mask"
    mask_dir.mkdir()
    (mask_dir / "sample_mask.png").write_bytes(b"mask")
    request = _request(tmp_path, mask_export="anima_lora")

    target = contract_module.write_anima_dataset_config(
        tmp_path,
        request,
        masks_written=1,
        masks_missing=0,
    )

    assert target == str(tmp_path / "dataset_config.toml")
    options = contract_module.validate_anima_toml_text(
        Path(target).read_text(encoding="utf-8")
    )
    assert options.mask_dir == mask_dir


@pytest.mark.parametrize(
    ("updates", "masks_written", "masks_missing", "message"),
    [
        ({"mask_export": "anima_lora"}, 0, 1, "Anima loss masks require"),
        ({"mask_export": "anima_lora"}, 2, 0, "mask count mismatch"),
        ({"mask_export": "none"}, 1, 0, "mask counts must be zero"),
        ({"mask_export": "none"}, True, 0, "non-negative integer"),
    ],
)
def test_anima_writer_rejects_false_artifact_counts_without_config(
    tmp_path: Path,
    updates: dict[str, object],
    masks_written: object,
    masks_missing: object,
    message: str,
) -> None:
    contract_module = _contract_module()
    _write_image_caption_pair(tmp_path, "sample", "caption")
    if updates.get("mask_export") == "anima_lora" and masks_missing == 0:
        mask_dir = tmp_path / "mask"
        mask_dir.mkdir()
        (mask_dir / "sample_mask.png").write_bytes(b"mask")
    request = _request(tmp_path, **updates)

    with pytest.raises(contract_module.AnimaTrainerContractError, match=message):
        contract_module.write_anima_dataset_config(
            tmp_path,
            request,
            masks_written=masks_written,
            masks_missing=masks_missing,
        )

    assert (tmp_path / "dataset_config.toml").exists() is False


def test_anima_writer_reports_config_write_failure(tmp_path: Path) -> None:
    contract_module = _contract_module()
    _write_image_caption_pair(tmp_path, "sample", "caption")
    target = tmp_path / "dataset_config.toml"
    target.mkdir()

    with pytest.raises(
        contract_module.AnimaTrainerContractError,
        match="Anima dataset config could not be written",
    ) as exc:
        contract_module.write_anima_dataset_config(
            tmp_path,
            _request(tmp_path),
            masks_written=0,
            masks_missing=0,
        )

    assert str(target).casefold() in str(exc.value).casefold()


def test_anima_writer_short_write_preserves_target_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_module = _contract_module()
    _write_image_caption_pair(tmp_path, "sample", "caption")
    target = tmp_path / "dataset_config.toml"
    original = "previous valid config\n"
    target.write_text(original, encoding="utf-8")
    real_open = Path.open

    class ShortWriteHandle:
        def __init__(self, handle: TextIO) -> None:
            self._handle = handle

        def __enter__(self) -> "ShortWriteHandle":
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            self._handle.close()

        def write(self, content: str) -> int:
            short_length = max(1, len(content) // 2)
            return self._handle.write(content[:short_length])

        def flush(self) -> None:
            self._handle.flush()

        def fileno(self) -> int:
            return self._handle.fileno()

    def short_open(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> TextIO | ShortWriteHandle:
        handle = real_open(path, *args, **kwargs)  # type: ignore[arg-type]
        mode = kwargs.get("mode", args[0] if args else "r")
        if mode == "w" and path.parent == tmp_path:
            return ShortWriteHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", short_open)

    observed_error: Exception | None = None
    try:
        contract_module.write_anima_dataset_config(
            tmp_path,
            _request(tmp_path),
            masks_written=0,
            masks_missing=0,
        )
    except contract_module.AnimaTrainerContractError as exc:
        observed_error = exc

    assert observed_error is not None, (
        "Anima writer accepted a short write and left target content: "
        f"{target.read_text(encoding='utf-8')!r}"
    )
    assert "short write" in str(observed_error)
    assert str(target).casefold() in str(observed_error).casefold()
    assert target.read_text(encoding="utf-8") == original
    # Any dot-prefixed sibling of the target, whatever the staging name: pinning
    # the old ``.<name>.<random>.tmp`` shape only pinned tempfile's random name.
    assert tuple(tmp_path.glob(".dataset_config.toml*")) == ()


def test_anima_writer_replace_failure_preserves_target_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_module = _contract_module()
    _write_image_caption_pair(tmp_path, "sample", "caption")
    target = tmp_path / "dataset_config.toml"
    original = "previous valid config\n"
    target.write_text(original, encoding="utf-8")

    def fail_replace(source: str, destination: str) -> None:
        raise OSError(f"replace denied: source={source}, destination={destination}")

    monkeypatch.setattr(contract_module.os, "replace", fail_replace)

    with pytest.raises(
        contract_module.AnimaTrainerContractError,
        match="replace denied",
    ) as exc:
        contract_module.write_anima_dataset_config(
            tmp_path,
            _request(tmp_path),
            masks_written=0,
            masks_missing=0,
        )

    assert str(target).casefold() in str(exc.value).casefold()
    assert target.read_text(encoding="utf-8") == original
    assert tuple(tmp_path.glob(".dataset_config.toml*")) == ()


def test_verifier_requires_explicit_existing_environment(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--anima-lora-root",
            str(tmp_path / "missing-anima-lora"),
            "--anima-lora-python",
            str(tmp_path / "missing-python"),
            str(tmp_path / "missing-config.toml"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "anima_lora root does not exist" in result.stderr
    assert str(tmp_path / "missing-anima-lora").casefold() in result.stderr.casefold()


def test_verifier_rejects_local_contract_before_upstream(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--anima-lora-root",
            str(tmp_path),
            "--anima-lora-python",
            sys.executable,
            str(REJECTED_FIXTURE),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "failed local schema validation" in result.stderr
    assert "unsupported_contract_probe" in result.stderr


def test_verifier_rejects_upstream_commit_drift(tmp_path: Path) -> None:
    checkout = tmp_path / "anima_lora"
    _init_git_checkout(checkout)

    result = subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--anima-lora-root",
            str(checkout),
            "--anima-lora-python",
            sys.executable,
            str(VALID_FIXTURE),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "anima_lora commit mismatch" in result.stderr
    assert PINNED_COMMIT in result.stderr


def test_verifier_rejects_dirty_tracked_checkout(tmp_path: Path) -> None:
    verifier = _verifier_module()
    checkout = tmp_path / "anima_lora"
    _init_git_checkout(checkout)
    (checkout / "tracked.txt").write_text("dirty", encoding="utf-8")

    with pytest.raises(verifier.AnimaVerifierError) as exc:
        verifier._require_clean_checkout(checkout.resolve(), "before module path probe")

    message = str(exc.value)
    assert "anima_lora tracked checkout is dirty" in message
    assert "before module path probe" in message
    assert "tracked.txt" in message


def test_verifier_rejects_module_import_outside_pinned_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    checkout = tmp_path / "anima_lora"
    expected_module = checkout / "library" / "config" / "loader.py"
    expected_module.parent.mkdir(parents=True)
    expected_module.write_text("# pinned module", encoding="utf-8")
    foreign_module = tmp_path / "vendor" / "library" / "config" / "loader.py"
    foreign_module.parent.mkdir(parents=True)
    foreign_module.write_text("# foreign module", encoding="utf-8")
    monkeypatch.setattr(verifier, "_checkout_commit", lambda _root: PINNED_COMMIT)
    monkeypatch.setattr(verifier, "_require_clean_checkout", lambda _root, _phase: None)

    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"SD_IMAGE_SORTER_ANIMA_MODULE={foreign_module.resolve()}\n",
            stderr="",
        )

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    with pytest.raises(verifier.AnimaVerifierError, match="module path mismatch"):
        verifier.verify_contract(checkout, Path(sys.executable), VALID_FIXTURE)

    assert len(calls) == 1


def test_verifier_uses_native_importer_same_runtime_and_clean_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    checkout = tmp_path / "anima_lora"
    expected_module = checkout / "library" / "config" / "loader.py"
    expected_module.parent.mkdir(parents=True)
    expected_module.write_text("# pinned module", encoding="utf-8")
    monkeypatch.setattr(verifier, "_checkout_commit", lambda _root: PINNED_COMMIT)
    phases: list[str] = []
    monkeypatch.setattr(
        verifier,
        "_require_clean_checkout",
        lambda _root, phase: phases.append(phase),
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        if command[1] == "-c":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"SD_IMAGE_SORTER_ANIMA_MODULE={expected_module.resolve()}\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="accepted", stderr="")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    result = verifier.verify_contract(checkout, Path(sys.executable), VALID_FIXTURE)

    assert result["module_path"] == str(expected_module.resolve())
    assert result["artifact_completeness_validated"] is False
    assert len(calls) == 2
    probe_command, probe_kwargs = calls[0]
    import_command, import_kwargs = calls[1]
    assert probe_command[:2] == [str(Path(sys.executable).resolve()), "-c"]
    assert import_command[1:4] == ["-m", "library.config.loader", "--support_dropout"]
    assert import_command[-1] == str(VALID_FIXTURE.resolve())
    assert probe_kwargs["cwd"] == import_kwargs["cwd"] == checkout.resolve()
    assert probe_kwargs["env"] is import_kwargs["env"]
    assert probe_kwargs["timeout"] == import_kwargs["timeout"] == 120
    assert phases == [
        "before module path probe",
        "after module path probe",
        "before upstream config import",
        "after upstream config import",
    ]


def test_verifier_timeout_and_import_rejection_are_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    checkout = tmp_path / "anima_lora"
    expected_module = checkout / "library" / "config" / "loader.py"
    expected_module.parent.mkdir(parents=True)
    expected_module.write_text("# pinned module", encoding="utf-8")
    monkeypatch.setattr(verifier, "_checkout_commit", lambda _root: PINNED_COMMIT)
    phases: list[str] = []
    monkeypatch.setattr(
        verifier,
        "_require_clean_checkout",
        lambda _root, phase: phases.append(phase),
    )
    call_count = 0

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"SD_IMAGE_SORTER_ANIMA_MODULE={expected_module.resolve()}\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            command,
            7,
            stdout="native stdout",
            stderr="native stderr",
        )

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    with pytest.raises(verifier.AnimaVerifierError) as exc:
        verifier.verify_contract(checkout, Path(sys.executable), VALID_FIXTURE)

    message = str(exc.value)
    assert "native importer rejected" in message
    assert "returncode=7" in message
    assert "native stdout" in message
    assert "native stderr" in message
    assert "library.config.loader" in message
    assert phases == [
        "before module path probe",
        "after module path probe",
        "before upstream config import",
        "after upstream config import",
    ]


def test_verifier_module_probe_timeout_is_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    checkout = tmp_path / "anima_lora"
    expected_module = checkout / "library" / "config" / "loader.py"
    expected_module.parent.mkdir(parents=True)
    expected_module.write_text("# pinned module", encoding="utf-8")
    monkeypatch.setattr(verifier, "_checkout_commit", lambda _root: PINNED_COMMIT)
    phases: list[str] = []
    monkeypatch.setattr(
        verifier,
        "_require_clean_checkout",
        lambda _root, phase: phases.append(phase),
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout=120)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    with pytest.raises(verifier.AnimaVerifierError) as exc:
        verifier.verify_contract(checkout, Path(sys.executable), VALID_FIXTURE)

    message = str(exc.value)
    assert "module path probe timed out after 120 seconds" in message
    assert str(checkout.resolve()).casefold() in message.casefold()
    assert phases == ["before module path probe", "after module path probe"]
