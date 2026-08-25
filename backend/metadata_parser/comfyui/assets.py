# =============================================================================
# metadata_parser.comfyui.assets - metadata_parser decomposition stages 1+2 (2026-07-13).
# Extracted VERBATIM from backend/metadata_parser.py @ c06d374 (4,912 lines).
# Source line ranges (original file): 2949-3167, 3187-3462, 3463-3471, 3551-3818.
# Mixin: ComfyUI model-asset + global-LoRA candidate scan/classify/score.
# self.* calls and class-constant lookups resolve via MRO exactly as before.
# Patched seams (Image / open / _MAX_* / _sidecar_directory_cache): the readers
# live in metadata_parser/_runtime.py behind the package get/set proxy in
# __init__.py (stage 3); see tests/test_metadata_parser_pins.py.
import json
import re
from typing import Optional, Dict, Any, Tuple, List, Set

class ComfyUIAssetsMixin:
    """ComfyUI model-asset + global-LoRA candidate scan/classify/score."""

    def _extract_comfyui_model_assets_from_active_graph(self, nodes: Dict[str, dict]) -> Dict[str, Any]:
        """Fallback asset extraction that follows the active sampler subgraph.

        This is slower than the old node-class whitelist, so callers should only
        use it when the fast path failed to find checkpoint / LoRA data.
        """
        root_ids = self._find_comfyui_activity_roots(nodes)
        distances = self._collect_comfyui_upstream_distances(nodes, root_ids)
        if not distances:
            distances = {node_id: 999 for node_id in nodes.keys()}

        candidate_map: Dict[str, List[Dict[str, Any]]] = {
            "checkpoint": [],
            "unet": [],
            "diffusion_model": [],
            "model": [],
            "lora": [],
            "vae": [],
            "clip": [],
            "yolo": [],
        }
        seen: Set[Tuple[str, str, str, str]] = set()

        for node_id, distance in distances.items():
            node = nodes.get(node_id, {})
            self._scan_comfyui_asset_candidates(
                value=node.get("inputs", {}),
                key_path="inputs",
                node_id=node_id,
                class_type=str(node.get("class_type", "")),
                node_distance=distance,
                candidate_map=candidate_map,
                seen=seen,
            )

        for asset_type, items in candidate_map.items():
            candidate_map[asset_type] = sorted(
                items,
                key=lambda item: (-item["score"], item["distance"], item["node_id"], item["name"].lower()),
            )

        primary_model_type = None
        primary_model_name = None
        for asset_type in ("checkpoint", "unet", "diffusion_model", "model"):
            if candidate_map[asset_type]:
                primary_model_type = asset_type
                primary_model_name = candidate_map[asset_type][0]["name"]
                break

        lora_names = self._normalize_lora_names([item["name"] for item in candidate_map["lora"]])
        yolo_names = self._dedupe_non_empty_strings([item["name"] for item in candidate_map["yolo"]])

        return {
            "source": "activity_subgraph_fallback",
            "activity_root_ids": root_ids,
            "activity_node_count": len(distances),
            "primary_model_type": primary_model_type,
            "primary_model_name": primary_model_name,
            "checkpoint_candidates": candidate_map["checkpoint"],
            "unet_candidates": candidate_map["unet"],
            "diffusion_model_candidates": candidate_map["diffusion_model"],
            "model_candidates": candidate_map["model"],
            "lora_candidates": candidate_map["lora"],
            "vae_candidates": candidate_map["vae"],
            "clip_candidates": candidate_map["clip"],
            "yolo_candidates": candidate_map["yolo"],
            "loras": lora_names,
            "yolo_models": yolo_names,
        }

    def _extract_comfyui_model_assets_from_workflow_widgets(self, workflow_data: Any) -> Optional[Dict[str, Any]]:
        """Recover explicit asset filenames stored only in workflow widget state."""
        if not isinstance(workflow_data, dict):
            try:
                workflow_data = json.loads(workflow_data) if isinstance(workflow_data, str) else {}
            except Exception:
                return None

        nodes = workflow_data.get("nodes")
        if not isinstance(nodes, list):
            return None

        candidate_map: Dict[str, List[Dict[str, Any]]] = {
            "checkpoint": [],
            "unet": [],
            "diffusion_model": [],
            "lora": [],
            "vae": [],
            "clip": [],
            "yolo": [],
        }
        seen: Set[Tuple[str, str, str, str]] = set()

        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = str(node.get("type", ""))
            widgets = node.get("widgets_values")
            if widgets is None:
                continue

            for path, value in self._iter_workflow_widget_strings(widgets):
                widget_key_path = f"widgets_values[{path}]"
                asset_type = self._classify_comfyui_workflow_widget_asset(node_type, widget_key_path, value)
                if not asset_type:
                    continue
                identity = (asset_type, value, str(node.get("id", "")), widget_key_path)
                if identity in seen:
                    continue
                seen.add(identity)
                candidate_map[asset_type].append({
                    "name": value,
                    "node_id": str(node.get("id", "")),
                    "class_type": node_type,
                    "input_key": widget_key_path,
                    "key_path": widget_key_path,
                    "source_mode": "workflow_widget_fallback",
                    "confidence": "high",
                    "match_type": "workflow_widget_value",
                })

        if not any(candidate_map.values()):
            return None

        primary_model_type = None
        primary_model_name = None
        for asset_type in ("checkpoint", "unet", "diffusion_model"):
            if candidate_map[asset_type]:
                primary_model_type = asset_type
                primary_model_name = candidate_map[asset_type][0]["name"]
                break

        return {
            "source": "workflow_widget_fallback",
            "primary_model_type": primary_model_type,
            "primary_model_name": primary_model_name,
            "checkpoint_candidates": candidate_map["checkpoint"],
            "unet_candidates": candidate_map["unet"],
            "diffusion_model_candidates": candidate_map["diffusion_model"],
            "lora_candidates": candidate_map["lora"],
            "workflow_widget_lora_candidates": candidate_map["lora"],
            "yolo_candidates": candidate_map["yolo"],
            "loras": self._normalize_lora_names([item["name"] for item in candidate_map["lora"]]),
            "yolo_models": self._dedupe_non_empty_strings([item["name"] for item in candidate_map["yolo"]]),
        }

    def _merge_workflow_widget_assets_into_result(self, result: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        """Merge explicit workflow widget assets into an already-detected result."""
        workflow_assets = self._extract_comfyui_model_assets_from_workflow_widgets(metadata.get("workflow"))
        if not workflow_assets:
            return

        if not result.get("checkpoint") and workflow_assets.get("primary_model_name"):
            result["checkpoint"] = workflow_assets.get("primary_model_name")

        result["loras"] = self._normalize_lora_names([
            *(result.get("loras") or []),
            *(workflow_assets.get("loras") or []),
        ])

        result["model_assets"] = self._merge_model_assets(result.get("model_assets"), workflow_assets)

    def _classify_comfyui_workflow_widget_asset(self, node_type: str, key_path: str, value: str) -> Optional[str]:
        """Classify widget-only values where the numeric path carries no semantic meaning."""
        node_type_lower = str(node_type or "").lower()
        text = str(value or "").strip()
        if not text or not self._looks_like_model_filename(text):
            return None

        if self._looks_like_yolo_model_name(text, node_type, key_path):
            return "yolo"
        if "lora" in node_type_lower:
            return "lora"
        if "unet" in node_type_lower:
            return "unet"
        if "diffusion" in node_type_lower:
            return "diffusion_model"
        if "vae" in node_type_lower:
            return "vae"
        if "clip" in node_type_lower and "loader" in node_type_lower:
            return "clip"
        if any(token in node_type_lower for token in (
            "controlnet", "control_net", "ipadapter", "ip-adapter",
            "instantid", "pulid", "gligen",
        )):
            return None
        # Any remaining *Loader / *Checkpoint node holding a weight file is
        # a primary model. Do not require the word "Checkpoint" — custom
        # booster/base loaders use arbitrary class names.
        if any(token in node_type_lower for token in (
            "checkpoint", "ckpt", "loader", "efficient loader", "comfyloader",
        )):
            return "checkpoint"

        return None

    def _extract_comfyui_yolo_assets_from_full_graph(self, nodes: Dict[str, dict]) -> Optional[Dict[str, Any]]:
        """Collect YOLO/detector models from the full graph so optional detailers still surface."""
        candidates: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, str, str, str]] = set()

        candidate_map = {"checkpoint": [], "unet": [], "diffusion_model": [], "model": [], "lora": [], "vae": [], "clip": [], "yolo": []}
        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            self._scan_comfyui_asset_candidates(
                value=node.get("inputs", {}),
                key_path="inputs",
                node_id=node_id,
                class_type=str(node.get("class_type", "")),
                node_distance=50,
                candidate_map=candidate_map,
                seen=seen,
            )

        for item in sorted(
            candidate_map["yolo"],
            key=lambda candidate: (-candidate["score"], candidate["node_id"], candidate["name"].lower()),
        ):
            enriched = dict(item)
            enriched.setdefault("source_mode", "global_graph_fallback")
            candidates.append(enriched)

        if not candidates:
            return None

        return {
            "source": "global_graph_fallback",
            "global_yolo_candidates": candidates,
            "yolo_candidates": candidates,
            "yolo_models": self._dedupe_non_empty_strings([item["name"] for item in candidates]),
        }

    def _extract_comfyui_global_lora_candidates(self, nodes: Dict[str, dict]) -> List[Dict[str, Any]]:
        """Scan the full ComfyUI graph for secondary LoRA hints.

        These candidates are intentionally conservative and stay in model_assets
        only. They are not promoted into the main loras list from the global
        fallback because disconnected helper/UI nodes can easily be stale.
        """
        candidates: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, str, str, str]] = set()

        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            self._scan_comfyui_global_lora_candidates(
                value=node.get("inputs", {}),
                key_path="inputs",
                node_id=node_id,
                class_type=str(node.get("class_type", "")),
                candidates=candidates,
                seen=seen,
            )

        best_by_name: Dict[str, Dict[str, Any]] = {}
        for item in candidates:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            existing = best_by_name.get(name)
            if existing is None or self._is_better_global_lora_candidate(item, existing):
                best_by_name[name] = item

        return sorted(
            best_by_name.values(),
            key=lambda item: (
                self._global_lora_confidence_rank(item.get("confidence")),
                -int(item.get("score", 0)),
                str(item.get("node_id", "")),
                str(item.get("name", "")).lower(),
            ),
        )

    def _scan_comfyui_global_lora_candidates(
        self,
        value: Any,
        key_path: str,
        node_id: str,
        class_type: str,
        candidates: List[Dict[str, Any]],
        seen: Set[Tuple[str, str, str, str]],
    ) -> None:
        """Recursively scan the full graph for secondary LoRA evidence."""
        if isinstance(value, dict):
            if value.get("on") is False:
                return
            for key, nested_value in value.items():
                next_path = f"{key_path}.{key}" if key_path else str(key)
                self._scan_comfyui_global_lora_candidates(
                    nested_value,
                    next_path,
                    node_id,
                    class_type,
                    candidates,
                    seen,
                )
            return

        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and isinstance(value[0], (str, int)):
                return
            for index, nested_value in enumerate(value):
                next_path = f"{key_path}[{index}]"
                self._scan_comfyui_global_lora_candidates(
                    nested_value,
                    next_path,
                    node_id,
                    class_type,
                    candidates,
                    seen,
                )
            return

        if not isinstance(value, str):
            return

        text = value.strip()
        if not text or text.lower() in {"none", "null", "false"}:
            return

        if self._is_explicit_comfyui_lora_key(key_path) and self._looks_like_model_filename(text):
            self._add_comfyui_global_lora_candidate(
                candidates=candidates,
                seen=seen,
                candidate_name=text,
                node_id=node_id,
                class_type=class_type,
                key_path=key_path,
                match_type="explicit_input",
                confidence="high",
            )

        if text[0] in "[{":
            for item in self._extract_comfyui_serialized_lora_candidates(text):
                full_key_path = self._join_comfyui_key_path(key_path, item["key_path_suffix"])
                self._add_comfyui_global_lora_candidate(
                    candidates=candidates,
                    seen=seen,
                    candidate_name=item["name"],
                    node_id=node_id,
                    class_type=class_type,
                    key_path=full_key_path,
                    match_type=item["match_type"],
                    confidence=item["confidence"],
                )
            return

        for lora_name in self._extract_inline_lora_tags(text):
            self._add_comfyui_global_lora_candidate(
                candidates=candidates,
                seen=seen,
                candidate_name=lora_name,
                node_id=node_id,
                class_type=class_type,
                key_path=key_path,
                match_type="inline_lora_tag",
                confidence="low",
            )

    def _extract_comfyui_serialized_lora_candidates(self, text: str) -> List[Dict[str, str]]:
        """Extract LoRA candidates from JSON-serialized strings.

        Only explicit lora/lora_name-style fields and inline <lora:...> tags are
        accepted here to avoid turning arbitrary UI tokens into fake LoRA names.
        """
        text = text.strip()
        if not text or text[0] not in "[{":
            return []

        try:
            payload = json.loads(text)
        except Exception:
            return []

        candidates: List[Dict[str, str]] = []

        def walk(value: Any, key_path: str = "") -> None:
            if isinstance(value, dict):
                for key, nested_value in value.items():
                    next_path = f"{key_path}.{key}" if key_path else str(key)
                    key_lower = str(key).lower()

                    if isinstance(nested_value, str):
                        nested_text = nested_value.strip()
                        if self._is_explicit_comfyui_lora_key(key_lower) and self._looks_like_model_filename(nested_text):
                            candidates.append({
                                "name": nested_text,
                                "key_path_suffix": next_path,
                                "match_type": "serialized_field",
                                "confidence": "high",
                            })

                        for lora_name in self._extract_inline_lora_tags(nested_text):
                            candidates.append({
                                "name": lora_name,
                                "key_path_suffix": next_path,
                                "match_type": "serialized_inline_lora_tag",
                                "confidence": "low",
                            })
                        continue

                    walk(nested_value, next_path)
                return

            if isinstance(value, list):
                for index, item in enumerate(value):
                    next_path = f"{key_path}[{index}]" if key_path else f"[{index}]"
                    walk(item, next_path)
                return

            if isinstance(value, str):
                nested_text = value.strip()
                for lora_name in self._extract_inline_lora_tags(nested_text):
                    candidates.append({
                        "name": lora_name,
                        "key_path_suffix": key_path or "value",
                        "match_type": "serialized_inline_lora_tag",
                        "confidence": "low",
                    })

        walk(payload)
        return candidates

    def _add_comfyui_global_lora_candidate(
        self,
        candidates: List[Dict[str, Any]],
        seen: Set[Tuple[str, str, str, str]],
        candidate_name: str,
        node_id: str,
        class_type: str,
        key_path: str,
        match_type: str,
        confidence: str,
    ) -> None:
        """Add a deduplicated global LoRA candidate with provenance metadata."""
        name = candidate_name.strip()
        if not name or name.lower() in {"none", "null", "false"}:
            return

        dedupe_key = (name, node_id, key_path, match_type)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)

        candidates.append({
            "name": name,
            "asset_type": "lora",
            "node_id": node_id,
            "class_type": class_type,
            "input_key": key_path.split(".")[-1],
            "key_path": key_path,
            "source_mode": "global_candidate_fallback",
            "match_type": match_type,
            "confidence": confidence,
            "score": self._score_comfyui_global_lora_candidate(
                class_type=class_type,
                key_path=key_path,
                candidate_name=name,
                match_type=match_type,
                confidence=confidence,
            ),
        })

    def _score_comfyui_global_lora_candidate(
        self,
        class_type: str,
        key_path: str,
        candidate_name: str,
        match_type: str,
        confidence: str,
    ) -> int:
        """Score full-graph LoRA candidates so the best provenance wins."""
        score = 300 if confidence == "high" else 200 if confidence == "medium" else 100
        class_type_lower = class_type.lower()
        key_path_lower = key_path.lower()

        if match_type == "explicit_input":
            score += 40
        elif match_type == "serialized_field":
            score += 35
        elif match_type == "inline_lora_tag":
            score += 20
        elif match_type == "serialized_inline_lora_tag":
            score += 15

        if "lora" in class_type_lower:
            score += 20
        if "lora" in key_path_lower:
            score += 15
        if self._looks_like_model_filename(candidate_name):
            score += 10

        return score

    def _is_better_global_lora_candidate(self, candidate: Dict[str, Any], existing: Dict[str, Any]) -> bool:
        """Pick the strongest provenance record when the same LoRA appears repeatedly."""
        candidate_rank = self._global_lora_confidence_rank(candidate.get("confidence"))
        existing_rank = self._global_lora_confidence_rank(existing.get("confidence"))
        if candidate_rank != existing_rank:
            return candidate_rank < existing_rank

        candidate_score = int(candidate.get("score", 0))
        existing_score = int(existing.get("score", 0))
        if candidate_score != existing_score:
            return candidate_score > existing_score

        return str(candidate.get("key_path", "")) < str(existing.get("key_path", ""))

    @staticmethod
    def _global_lora_confidence_rank(confidence: Optional[str]) -> int:
        """Stable sort order for candidate confidence labels."""
        return {
            "high": 0,
            "medium": 1,
            "low": 2,
        }.get(str(confidence or "").lower(), 3)

    def _scan_comfyui_asset_candidates(
        self,
        value: Any,
        key_path: str,
        node_id: str,
        class_type: str,
        node_distance: int,
        candidate_map: Dict[str, List[Dict[str, Any]]],
        seen: Set[Tuple[str, str, str, str]],
    ) -> None:
        """Recursively scan a node input tree for model / LoRA asset candidates."""
        if isinstance(value, dict):
            if value.get("on") is False:
                return
            for key, nested_value in value.items():
                next_path = f"{key_path}.{key}" if key_path else str(key)
                self._scan_comfyui_asset_candidates(
                    nested_value,
                    next_path,
                    node_id,
                    class_type,
                    node_distance,
                    candidate_map,
                    seen,
                )
            return

        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and isinstance(value[0], (str, int)):
                return
            for index, nested_value in enumerate(value):
                next_path = f"{key_path}[{index}]"
                self._scan_comfyui_asset_candidates(
                    nested_value,
                    next_path,
                    node_id,
                    class_type,
                    node_distance,
                    candidate_map,
                    seen,
                )
            return

        if not isinstance(value, str):
            return

        asset_name = value.strip()
        if not asset_name or asset_name.lower() in {"none", "null", "false", "baked vae"}:
            return

        inline_loras = self._extract_inline_lora_tags(asset_name)
        if inline_loras:
            leaf_key = key_path.split(".")[-1]
            for inline_lora in inline_loras:
                dedupe_key = ("lora", inline_lora, node_id, leaf_key)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                score = self._score_comfyui_asset_candidate("lora", leaf_key, class_type, inline_lora, node_distance) + 30
                candidate_map["lora"].append({
                    "name": inline_lora,
                    "node_id": node_id,
                    "class_type": class_type,
                    "input_key": leaf_key,
                    "distance": node_distance,
                    "score": score,
                })

        asset_type = self._classify_comfyui_asset_candidate(key_path, class_type, asset_name)
        if not asset_type:
            return

        expanded_asset_names = self._expand_serialized_asset_value(asset_type, asset_name)
        if expanded_asset_names:
            asset_names = expanded_asset_names
        else:
            asset_names = [asset_name]

        leaf_key = key_path.split(".")[-1]
        for candidate_name in asset_names:
            candidate_name = candidate_name.strip()
            if not candidate_name or candidate_name.lower() in {"none", "null", "false"}:
                continue
            dedupe_key = (asset_type, candidate_name, node_id, leaf_key)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            score = self._score_comfyui_asset_candidate(asset_type, leaf_key, class_type, candidate_name, node_distance)
            candidate_map[asset_type].append({
                "name": candidate_name,
                "node_id": node_id,
                "class_type": class_type,
                "input_key": leaf_key,
                "distance": node_distance,
                "score": score,
            })

    def _classify_comfyui_asset_candidate(self, key_path: str, class_type: str, asset_name: str) -> Optional[str]:
        """Guess asset type from input semantics instead of node-name whitelists."""
        leaf_key = key_path.split(".")[-1].lower()
        class_type_lower = class_type.lower()

        if leaf_key in self.COMFYUI_MODEL_KEY_TYPES:
            mapped_type = self.COMFYUI_MODEL_KEY_TYPES[leaf_key]
            if mapped_type == "model" and self._looks_like_yolo_model_name(asset_name, class_type, key_path):
                return "yolo"
            return mapped_type

        if re.match(r"^lora_\d+$", leaf_key):
            return "lora"
        if self._is_explicit_comfyui_lora_key(key_path):
            return "lora"
        if "ckpt" in leaf_key or "checkpoint" in leaf_key:
            return "checkpoint"
        if "unet" in leaf_key:
            return "unet"
        if "vae" in leaf_key:
            return "vae"
        if "clip" in leaf_key and "name" in leaf_key:
            return "clip"
        if "diffusion" in leaf_key and "model" in leaf_key:
            return "diffusion_model"
        if any(token in leaf_key for token in ("yolo", "detector", "bbox", "segm")):
            return "yolo"

        if not self._looks_like_model_filename(asset_name):
            if "lora" in class_type_lower and leaf_key in {"lora", "lora_name"}:
                return "lora"
            if "loramanager" in class_type_lower and leaf_key == "name":
                return "lora"
            return None

        if self._looks_like_yolo_model_name(asset_name, class_type, key_path):
            return "yolo"
        if "lora" in class_type_lower:
            return "lora"
        if "vae" in class_type_lower:
            return "vae"
        if "clip" in class_type_lower and "loader" in class_type_lower:
            return "clip"
        if "unet" in class_type_lower:
            return "unet"
        if "diffusion" in class_type_lower:
            return "diffusion_model"
        if any(token in class_type_lower for token in ("checkpoint", "ckpt", "loader", "model")):
            return "model"

        return None

    def _score_comfyui_asset_candidate(
        self,
        asset_type: str,
        input_key: str,
        class_type: str,
        asset_name: str,
        node_distance: int,
    ) -> int:
        """Score candidates so the closest, most semantically explicit one wins."""
        score = 0
        class_type_lower = class_type.lower()
        input_key_lower = input_key.lower()

        if asset_type == "checkpoint":
            score += 400
        elif asset_type == "unet":
            score += 320
        elif asset_type == "diffusion_model":
            score += 300
        elif asset_type == "vae":
            score += 280
        elif asset_type == "clip":
            score += 270
        elif asset_type == "model":
            score += 260
        elif asset_type == "lora":
            score += 350
        elif asset_type == "yolo":
            score += 240

        if input_key_lower in self.COMFYUI_MODEL_KEY_TYPES:
            score += 120
        elif re.match(r"^lora_\d+$", input_key_lower):
            score += 110

        if "efficient loader" in class_type_lower:
            score += 80
        if "loader" in class_type_lower:
            score += 40
        if asset_type == "yolo":
            if any(token in class_type_lower for token in ("ultralytics", "yolo", "detector", "detailer", "adetailer")):
                score += 100
            if any(token in input_key_lower for token in ("yolo", "detector", "bbox", "segm")):
                score += 90
        if self._looks_like_model_filename(asset_name):
            score += 20

        score -= node_distance * 5
        return score

    def _looks_like_model_filename(self, value: str) -> bool:
        """Return True when a string looks like a model / LoRA filename."""
        value_lower = value.lower().strip()
        return value_lower.endswith(self.COMFYUI_MODEL_FILE_EXTENSIONS)

    @staticmethod
    def _is_numbered_text_key(key: str) -> bool:
        """
        Return True when a key matches numbered text input patterns.

        Matches patterns like:
        - string_1, string_2, string_10, string_99
        - text_1, text_2, text_3
        - prompt_1, prompt_2, prompt_3

        This enables dynamic matching of concatenation node inputs without
        hardcoding a fixed upper limit (e.g. string_1 to string_20).
        """
        key_lower = key.lower().strip()
        return bool(re.fullmatch(r"(string|text|prompt)_\d+", key_lower))

    def _extract_inline_lora_tags(self, text: str) -> List[str]:
        """Extract <lora:name:weight> tags from prompt-like strings."""
        matches = re.findall(r"<lora:([^:>,\r\n]+)(?::[^>\r\n]*)?>", text, flags=re.IGNORECASE)
        names: List[str] = []
        seen = set()
        for match in matches:
            name = match.strip()
            if not name or name.lower() == "none" or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    def _expand_serialized_asset_value(self, asset_type: str, asset_name: str) -> List[str]:
        """Expand JSON-serialized UI stacks into actual asset filenames."""
        asset_name = asset_name.strip()
        if not asset_name or asset_name[0] not in "[{":
            return []

        try:
            payload = json.loads(asset_name)
        except Exception:
            return []

        names: List[str] = []
        allowed_keys = {
            "lora": {"lora", "lora_name", "lora_path", "lora_file"},
            "checkpoint": {"ckpt_name", "checkpoint", "checkpoint_name", "model_name", "name"},
            "unet": {"unet_name", "model_name", "name"},
            "diffusion_model": {"diffusion_model", "diffusion_model_name", "model_name", "name"},
            "model": {"model_name", "ckpt_name", "unet_name", "diffusion_model", "name"},
            "yolo": {"model_name", "yolo_model", "yolo_model_name", "detector_model", "detector_model_name", "bbox_model_name", "segm_model_name", "name"},
        }.get(asset_type, {"name"})

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if isinstance(nested, str) and key.lower() in allowed_keys and self._looks_like_model_filename(nested):
                        names.append(nested)
                    walk(nested)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)
        return names

