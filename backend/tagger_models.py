"""WD14/ONNX tagger-model catalog (split from config.py, 2026-07).

``TAGGER_MODELS`` moved VERBATIM from config.py lines 455-637
(split leaf #1): a pure literal with
zero config dependencies. config.py re-exports it BY REFERENCE
(``from tagger_models import TAGGER_MODELS``) so the ~20 historical
``from config import TAGGER_MODELS`` consumers (three alias it as ``MODELS``)
and the ``monkeypatch.setitem`` seam (tests/test_tagging_pins_service.py)
keep sharing the SAME dict object at clean import -- pinned by
tests/test_config_pins.py Groups H and I. After an ``importlib.reload(config)``
(tests/test_artist_gpu_toggle.py, tests/test_main_logging.py) the facade now
rebinds to this module's unchanged object, so the pre-split reload divergence
(report finding F1) is reduced, not worsened.

Every entry pins ``revision`` to a 40-character commit hash. An unpinned entry
resolves the repo's ``main``, which means an upstream re-upload silently changes
what a user's tagger produces and can invalidate the measured operating points
recorded in the comments below. The 2026-08-17 pinning pass took each hash from
the installed copy where one exists (the commit is recorded in
``data/models/**/.cache/huggingface/download/*.metadata``) and cross-checked it
against the HuggingFace model API; entries with no local copy were pinned to the
upstream head, i.e. exactly what ``main`` already resolved to, so no model
version changed. When bumping a model, benchmark the new revision before
changing the hash - the thresholds here were tuned against these weights.
"""

TAGGER_MODELS: dict = {
    "wd-eva02-large-tagger-v3": {
        "writer_family": "wd14",
        "repo_id": "SmilingWolf/wd-eva02-large-tagger-v3",
        "revision": "b25b82a03f7282e41aa2f257a52c7583b710bd1c",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv",
        "runtime_safety_tier": "heavy",
        "default_threshold": 0.35,
        "default_character_threshold": 0.85,
        "default_copyright_threshold": 0.35,
        "default_max_tags_per_image": 60,
    },
    "wd-swinv2-tagger-v3": {
        "writer_family": "wd14",
        "repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
        "revision": "627aef95638667ddcaa3ac8ae625e88ea5b02f51",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv",
        "runtime_safety_tier": "balanced",
        "default_threshold": 0.35,
        "default_character_threshold": 0.85,
        "default_copyright_threshold": 0.35,
        "default_max_tags_per_image": 50,
    },
    "wd-convnext-tagger-v3": {
        "writer_family": "wd14",
        "repo_id": "SmilingWolf/wd-convnext-tagger-v3",
        "revision": "d39e46de298d27340111b64965e20b8185c407e6",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv",
        "runtime_safety_tier": "balanced",
        "default_threshold": 0.35,
        "default_character_threshold": 0.85,
        "default_copyright_threshold": 0.35,
        "default_max_tags_per_image": 50,
    },
    "wd-vit-tagger-v3": {
        "writer_family": "wd14",
        "repo_id": "SmilingWolf/wd-vit-tagger-v3",
        "revision": "7f6b584d0bd3f55c4531f14ba3d4761b2bccdc0f",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv",
        "runtime_safety_tier": "light",
        "default_threshold": 0.35,
        "default_character_threshold": 0.85,
        "default_copyright_threshold": 0.35,
        "default_max_tags_per_image": 40,
    },
    "wd-vit-large-tagger-v3": {
        "writer_family": "wd14",
        "repo_id": "SmilingWolf/wd-vit-large-tagger-v3",
        "revision": "ae469aa2e4706a3af08d3673cf73a11d1add314c",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv",
        "runtime_safety_tier": "balanced",
        "default_threshold": 0.35,
        "default_character_threshold": 0.85,
        "default_copyright_threshold": 0.35,
        "default_max_tags_per_image": 55,
    },
    "camie-tagger-v2": {
        "writer_family": "camie",
        "repo_id": "Camais03/camie-tagger-v2",
        "revision": "7d40c1b85b86ab4f607b2caf26b1b50c99db743e",
        "model_file": "camie-tagger-v2.onnx",
        "tags_file": "camie-tagger-v2-metadata.json",
        "runtime_safety_tier": "heavy",
        "metadata_format": "camie_v2",
        "input_layout": "nchw",
        "input_normalization": "imagenet",
        "output_activation": "sigmoid",
        # Camie v2 ONNX has 3 outputs: initial_predictions(70527),
        # refined_predictions(70527), selected_candidates(256). The refined
        # head is the model's real output; index 0 (initial) is a coarse
        # intermediate that misses characters/halo/guitar-level content and
        # emits contradictions (open_mouth + closed_mouth). A/B on a real
        # image: initial had no character and guitar at 0.79; refined gave
        # kayoko_(blue_archive) 0.99, 1girl 1.00, halo 0.88.
        "output_index": 1,
        "pad_color": [124, 116, 104],
        "default_threshold": 0.62,
        "default_character_threshold": 0.78,
        "default_copyright_threshold": 0.62,
        "default_max_tags_per_image": 65,
        "supports_rating": True
    },
    "pixai-tagger-v0.9": {
        "writer_family": "pixai",
        "repo_id": "deepghs/pixai-tagger-v0.9-onnx",
        "revision": "d8cf666911a2c3d10d586d7823259192313c7eb7",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv",
        "runtime_safety_tier": "heavy",
        "input_layout": "nchw",
        "input_normalization": "minus_one_to_one",
        "resize_mode": "stretch",
        # PixAI v0.9 ONNX has 3 outputs: embedding(1024), logits(13461),
        # prediction(13461). prediction = sigmoid(logits) and is the correct
        # probability vector for thresholding. We must use output index 2
        # (prediction), NOT index 0 (embedding). output_activation stays
        # identity because prediction is already in [0, 1].
        "output_index": 2,
        "output_activation": "identity",
        "default_threshold": 0.45,
        "default_character_threshold": 0.85,
        "default_copyright_threshold": 0.45,
        "default_max_tags_per_image": 65,
        "supports_rating": False,
        "rating_fallback_mode": "derive_from_tags"
    },
    "toriigate-0.5": {
        "repo_id": "Minthy/ToriiGate-0.5",
        # ToriiGate downloads via snapshot_download in toriigate_tagger.py
        # rather than the shared tagger download path, so the constant
        # TORIIGATE_COMMIT_HASH reads this value back instead of duplicating
        # it. Deliberately NOT the upstream head: trust_remote_code=True means
        # the repo can execute code on load, so the pin is a reviewed commit.
        "revision": "667e771497abcfa38637e1d308cb495beb68d803",
        "model_file": "config.json",
        "tags_file": "",
        "runtime_backend": "toriigate",
        "runtime_safety_tier": "vlm",
        # Owner decision (2026-07-06): ToriiGate is a captioner, not a
        # tagger. As a gallery tagger it produced 5-7 tags/image with
        # non-danbooru words ("buttocks") and invented anatomy — measured
        # unusable. It stays registered here for model download/prepare and
        # for Smart Tag's natural-language stage, but /api/tag rejects it
        # and the gallery tagger dropdown hides it.
        "captioner_only": True,
        # Hardware floors are calibrated to the actual ToriiGate-0.5
        # checkpoint (Qwen3.5-VL, ~9.6 GB BF16 weights, image capped to
        # 1 MP via TORIIGATE_MAX_IMAGE_PIXELS).
        #
        # Empirical measurement on RTX 3090 (24 GB): peak GPU memory
        # consumption hit 22.7 GB during a real inference (model weights
        # 9.6 GB + PyTorch caching allocator + KV cache + activations).
        # That puts the realistic floor at ~16 GB total VRAM and ~14 GB
        # free VRAM - 12 GB cards (3060, 4070) WILL OOM and must be
        # rejected, but 16 GB cards (4060 Ti 16 GB, A4000) and above
        # work after closing other GPU apps.
        #
        # Host RAM peak during load: ~3-5 GB (transformers uses
        # low_cpu_mem_usage=True which streams safetensors directly to
        # the GPU). The previous 48 GB / 12 GB-free numbers blocked any
        # 32 GB workstation - including the user's 32 GB / RTX 3090
        # setup that successfully tagged a real image at this revision.
        # Those numbers were never re-tuned for ToriiGate's BF16 + GPU
        # streaming loader.
        #
        # CPU mode peak: ~19.3 GB FP32 weights + ~3-5 GB working set =
        # ~24 GB. Keep a safety margin to 32 GB total / 20 GB free so
        # the OS and other apps don't get evicted to swap.
        "minimum_total_ram_gb": 16,
        "minimum_available_ram_gb": 4,
        "minimum_gpu_vram_mb": 16384,
        "minimum_gpu_available_vram_mb": 14000,
        "minimum_cpu_total_ram_gb": 32,
        "minimum_cpu_available_ram_gb": 20,
        "default_threshold": 1.0,
        "default_character_threshold": 1.0,
        "default_copyright_threshold": 1.0,
        "default_max_tags_per_image": 120,
        "supports_rating": True,
    },
    "oppai-oracle-v1.1": {
        # OppaiOracle is a from-scratch ViT (~247M params) anime tagger by
        # Grio43 with a 19,294-tag general-only vocabulary. The V1.1 ONNX
        # bundle lives in the V1.1_onnx/ subfolder of the HF repo and ships
        # two ONNX inputs (pixel_values + padding_mask) instead of the
        # WD14-style single input, so we route it through a dedicated
        # OppaiOracleTagger class via runtime_backend = "oppai-oracle".
        "repo_id": "Grio43/OppaiOracle",
        "revision": "96992fa30568c386e9fe7c8a1a68f798a3202c09",
        "repo_subfolder": "V1.1_onnx",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv",
        "extra_files": ["preprocessing.json", "pr_thresholds.json", "config.json"],
        "runtime_backend": "oppai-oracle",
        "runtime_safety_tier": "heavy",
        "input_layout": "nchw",
        "input_normalization": "minus_one_to_one",
        "resize_mode": "letterbox",
        "pad_color": [114, 114, 114],
        "image_size": 448,
        # Output is already sigmoid'd inside the graph, so we read it as a
        # probability vector. Indices 0/1 are <PAD>/<UNK> and the last 4
        # entries are the rating:* tags. The tagger handles both quirks.
        "output_activation": "identity",
        "supports_rating": True,
        # 0.7927 is the model's published P=R global threshold (precision ==
        # recall == 0.699 on the held-out 296k val split). At this operating
        # point the model emits ~35-50 tags per image which matches our
        # smoke-test density on real anime images.
        "default_threshold": 0.7927,
        "default_copyright_threshold": 0.7927,
        "default_max_tags_per_image": 60,
        # OppaiOracle's vocabulary is general-only — there is no dedicated
        # character category. Setting the character threshold to 1.0 keeps
        # the existing tagging service shape working without forcing
        # character-tag splits that the model cannot supply.
        "default_character_threshold": 1.0,
    },
    "cl-tagger-v2": {
        "writer_family": "cl_tagger_v2",
        "repo_id": "cella110n/cl_tagger_v2",
        "revision": "b57909b8e9c63f71e208a26473e7aabdf45ed6b6",
        "model_file": "v2_00/model.onnx",
        "tags_file": "v2_00/model_vocabulary.json",
        "external_data_files": [
            "v2_00/model.onnx.data",
            "v2_00/model_metadata.json",
            "v2_00/model_tag_metrics.npz",
        ],
        "runtime_backend": "cl-tagger-v2",
        "runtime_safety_tier": "heavy",
        "metadata_format": "cl_tagger_v2",
        "input_layout": "nchw",
        "input_normalization": "minus_one_to_one",
        "resize_mode": "stretch",
        "image_size": 384,
        "output_activation": "sigmoid",
        "default_threshold": 0.55,
        "default_character_threshold": 0.55,
        "default_copyright_threshold": 0.55,
        "default_max_tags_per_image": 80,
        "supports_rating": True,
        "gated_download": True,
        "official_download_only": True,
    },
}
