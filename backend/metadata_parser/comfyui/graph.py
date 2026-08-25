# =============================================================================
# metadata_parser.comfyui.graph - metadata_parser decomposition stages 1+2 (2026-07-13).
# Extracted VERBATIM from backend/metadata_parser.py @ c06d374 (4,912 lines).
# Source line ranges (original file): 3168-3186, 3472-3485, 3486-3494, 3495-3550.
# Mixin: ComfyUI graph walk: activity roots, upstream distances, input-ref/key-path helpers.
# self.* calls and class-constant lookups resolve via MRO exactly as before.
# Patched seams (Image / open / _MAX_* / _sidecar_directory_cache): the readers
# live in metadata_parser/_runtime.py behind the package get/set proxy in
# __init__.py (stage 3); see tests/test_metadata_parser_pins.py.
import json
import re
from typing import Dict, Any, Tuple, List, Optional

class ComfyUIGraphMixin:
    """ComfyUI graph walk: activity roots, upstream distances, input-ref/key-path helpers."""

    def _iter_workflow_widget_strings(self, value: Any, path: str = "") -> List[Tuple[str, str]]:
        """Collect string widget values from workflow nodes with stable paths."""
        results: List[Tuple[str, str]] = []
        if isinstance(value, str):
            text = value.strip()
            if text:
                results.append((path or "0", text))
            return results
        if isinstance(value, list):
            for index, item in enumerate(value):
                next_path = f"{path}.{index}" if path else str(index)
                results.extend(self._iter_workflow_widget_strings(item, next_path))
            return results
        if isinstance(value, dict):
            for key, item in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                results.extend(self._iter_workflow_widget_strings(item, next_path))
        return results

    def _is_explicit_comfyui_lora_key(self, key_path: str) -> bool:
        """Return True only for genuinely lora-shaped keys, not UI flags/noise."""
        leaf_key = key_path.split(".")[-1].lower()
        if re.match(r"^lora(_\d+)?$", leaf_key):
            return True
        if leaf_key in {"lora_name", "lora_path", "lora_file", "lora_str", "temp_lora_str"}:
            return True
        return (
            leaf_key.endswith("_lora")
            or leaf_key.endswith("_lora_name")
            or leaf_key.endswith("_lora_str")
            or leaf_key.endswith("_lora_stack")
        )

    @staticmethod
    def _join_comfyui_key_path(base: str, suffix: str) -> str:
        """Join serialized key suffixes onto an existing input key path."""
        if not suffix:
            return base
        if suffix.startswith("["):
            return f"{base}{suffix}"
        return f"{base}.{suffix}"

    def _find_comfyui_activity_roots(self, nodes: Dict[str, dict]) -> List[str]:
        """Find likely sampler/output roots for the active ComfyUI branch."""
        roots: List[str] = []
        for node_id, node in nodes.items():
            class_type = str(node.get("class_type", ""))
            class_type_lower = class_type.lower()
            inputs = node.get("inputs", {})

            if any(token.lower() in class_type_lower for token in self.COMFYUI_SAMPLER_NODE_TYPES):
                roots.append(node_id)
                continue

            if "ksampler" in class_type_lower or (
                "model" in inputs and ("positive" in inputs or "negative" in inputs)
            ):
                roots.append(node_id)

        return roots or list(nodes.keys())

    def _collect_comfyui_upstream_distances(self, nodes: Dict[str, dict], root_ids: List[str]) -> Dict[str, int]:
        """Breadth-first walk from active roots to upstream nodes."""
        distances: Dict[str, int] = {}
        queue: List[Tuple[str, int]] = [(root_id, 0) for root_id in root_ids if root_id in nodes]

        while queue:
            node_id, distance = queue.pop(0)
            previous = distances.get(node_id)
            if previous is not None and previous <= distance:
                continue
            distances[node_id] = distance

            node = nodes.get(node_id, {})
            for ref_id in self._iter_comfyui_input_refs(node.get("inputs", {})):
                if ref_id in nodes:
                    queue.append((ref_id, distance + 1))

        return distances

    def _iter_comfyui_input_refs(self, value: Any) -> List[str]:
        """Collect node references from nested ComfyUI input values."""
        refs: List[str] = []

        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and isinstance(value[0], (str, int)):
                refs.append(str(value[0]))
                return refs
            for item in value:
                refs.extend(self._iter_comfyui_input_refs(item))
            return refs

        if isinstance(value, dict):
            for nested in value.values():
                refs.extend(self._iter_comfyui_input_refs(nested))

        return refs

    _TEXT_WIDGET_INPUT_NAMES = {
        "text",
        "prompt",
        "positive",
        "negative",
        "string",
        "STRING",
        "text_0",
        "t5xxl",
        "text_g",
        "text_l",
        "clip_l",
        "clip_g",
        "user_prompt",
        "user_text",
        "value",
        "result",
        "prompt1",
        "prompt2",
        "prompt3",
        "text1",
        "text2",
        "part1",
        "part2",
        "part3",
        "part4",
    }

    @staticmethod
    def _unwrap_comfyui_widget_text(value: Any) -> Optional[str]:
        """Flatten ShowText-style list widgets and skip empty/non-text values."""
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, (list, tuple)) and value:
            return ComfyUIGraphMixin._unwrap_comfyui_widget_text(value[0])
        return None

    def _comfyui_bus_name(self, node: Dict[str, Any]) -> Optional[str]:
        """kjnodes SetNode/GetNode bus name from widgets, stamped field, or title."""
        if not isinstance(node, dict):
            return None
        stamped = node.get("_bus_name")
        if isinstance(stamped, str) and stamped.strip():
            return stamped.strip()
        widgets = node.get("widgets_values")
        if isinstance(widgets, list) and widgets:
            name = self._unwrap_comfyui_widget_text(widgets[0])
            if name:
                return name
        title = str(node.get("title") or "").strip()
        for prefix in ("Set_", "Get_"):
            if title.startswith(prefix) and title[len(prefix):]:
                return title[len(prefix):]
        return None

    def _find_comfyui_set_node(self, nodes: Dict[str, dict], bus_name: Optional[str]) -> Optional[str]:
        """Return the SetNode id that publishes ``bus_name``."""
        if not bus_name:
            return None
        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type") or node.get("type") or "")
            if class_type not in ("SetNode", "Set"):
                continue
            if self._comfyui_bus_name(node) == bus_name:
                return str(node_id)
        return None

    def _is_comfyui_sampler_node(self, class_type: str, inputs: Optional[Dict[str, Any]] = None) -> bool:
        """True for KSampler-family nodes and custom samplers with pos/neg inputs."""
        ct = str(class_type or "")
        if any(token in ct for token in self.COMFYUI_SAMPLER_NODE_TYPES):
            return True
        if "Sampler" not in ct:
            return False
        if inputs is None:
            return True
        return "positive" in inputs or "negative" in inputs

    def _workflow_ui_to_prompt_data(self, workflow_data: Any) -> Optional[Dict[str, dict]]:
        """Convert a ComfyUI frontend workflow (nodes+links) into API prompt_data.

        Saved PNGs often omit the API ``prompt`` chunk and only embed the UI
        ``workflow``. CLIPTextEncode widgets are empty; text rides GetNode/SetNode
        buses and ShowText caches. The existing tracer speaks API-format
        ``{node_id: {class_type, inputs}}``, so this adapter is the bridge.
        """
        if isinstance(workflow_data, str):
            try:
                workflow_data = json.loads(workflow_data)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        if not isinstance(workflow_data, dict):
            return None

        nodes_list = workflow_data.get("nodes")
        if not isinstance(nodes_list, list) or not nodes_list:
            # Already API-shaped? Leave it to the caller.
            return None

        raw_links = workflow_data.get("links") or []
        link_by_id: Dict[Any, Any] = {}
        for item in raw_links:
            if isinstance(item, (list, tuple)) and item:
                link_by_id[item[0]] = item
            elif isinstance(item, dict) and "id" in item:
                link_by_id[item["id"]] = item

        node_by_id: Dict[Any, dict] = {}
        for node in nodes_list:
            if isinstance(node, dict) and node.get("id") is not None:
                node_by_id[node["id"]] = node

        set_by_name: Dict[str, Any] = {}
        for node in nodes_list:
            if not isinstance(node, dict):
                continue
            if str(node.get("type") or "") not in ("SetNode", "Set"):
                continue
            bus = self._comfyui_bus_name(node)
            if bus and bus not in set_by_name:
                set_by_name[bus] = node.get("id")

        prompt_data: Dict[str, dict] = {}
        for node in nodes_list:
            if not isinstance(node, dict) or node.get("id") is None:
                continue
            node_id = str(node["id"])
            class_type = str(node.get("type") or node.get("class_type") or "")
            inputs: Dict[str, Any] = {}

            for inp in node.get("inputs") or []:
                if not isinstance(inp, dict):
                    continue
                name = inp.get("name")
                link_id = inp.get("link")
                if link_id is None:
                    continue
                if not name:
                    name = f"_{link_id}"
                link = link_by_id.get(link_id)
                if link is None:
                    continue
                if isinstance(link, dict):
                    src_id = link.get("origin_id", link.get("src"))
                    src_slot = link.get("origin_slot", link.get("src_slot", 0))
                else:
                    src_id = link[1] if len(link) > 1 else None
                    src_slot = link[2] if len(link) > 2 else 0
                if src_id is None:
                    continue
                src_id, src_slot = self._resolve_ui_link_source(
                    src_id, src_slot, node_by_id, link_by_id, set_by_name, set()
                )
                inputs[name] = [str(src_id), src_slot]

            widgets = node.get("widgets_values")
            widget_inputs = [
                inp
                for inp in (node.get("inputs") or [])
                if isinstance(inp, dict) and inp.get("widget") and inp.get("name")
            ]
            text_widget_inputs = [
                inp
                for inp in widget_inputs
                if str(inp.get("name")) in self._TEXT_WIDGET_INPUT_NAMES
                and inp.get("name") not in inputs
            ]
            string_widgets: List[str] = []
            if isinstance(widgets, list):
                for item in widgets:
                    text = self._unwrap_comfyui_widget_text(item)
                    if text:
                        string_widgets.append(text)
            title = str(node.get("title") or "")
            looks_negative = (
                "负面" in title
                or "negative" in title.lower()
                or "negative" in class_type.lower()
                or class_type == "easy negative"
            )
            if text_widget_inputs and string_widgets:
                if len(text_widget_inputs) == len(widgets):
                    for inp, raw in zip(text_widget_inputs, widgets):
                        text = self._unwrap_comfyui_widget_text(raw)
                        if text:
                            inputs[inp["name"]] = text
                else:
                    inputs[text_widget_inputs[0]["name"]] = string_widgets[0]

            if "ShowText" in class_type:
                cache = self._unwrap_comfyui_widget_text(widgets)
                if cache:
                    inputs.setdefault("text_0", cache)

            if not any(isinstance(value, str) and value.strip() for value in inputs.values()):
                prompt_widget = self._first_prompt_like_widget(string_widgets)
                if prompt_widget:
                    inputs.setdefault("text", prompt_widget)
                    inputs.setdefault("negative" if looks_negative else "positive", prompt_widget)

            if looks_negative and "positive" in inputs and "negative" not in inputs:
                inputs["negative"] = inputs["positive"]

            prompt_data[node_id] = {
                "class_type": class_type,
                "inputs": inputs,
                "widgets_values": widgets,
                "_bus_name": self._comfyui_bus_name(node),
            }

        return prompt_data or None

    def _first_prompt_like_widget(self, string_widgets: List[str]) -> Optional[str]:
        """Pick the first widget string that reads as a prompt, any node class."""
        if not string_widgets:
            return None
        try:
            from prompt_text_scorer import (
                PROMPT_SCORE_FLOOR,
                looks_like_non_prompt_value,
                score_prompt_likeness,
            )
        except Exception:
            return None
        for text in string_widgets:
            if looks_like_non_prompt_value(text):
                continue
            if score_prompt_likeness(text)["score"] >= PROMPT_SCORE_FLOOR:
                return text
        return None

    def _resolve_ui_link_source(
        self,
        src_id: Any,
        src_slot: Any,
        node_by_id: Dict[Any, dict],
        link_by_id: Dict[Any, Any],
        set_by_name: Dict[str, Any],
        seen: set,
    ) -> Tuple[Any, Any]:
        """Walk Get/Set buses and Reroute nodes to the real upstream source."""
        if src_id in seen:
            return src_id, src_slot
        seen.add(src_id)
        src_node = node_by_id.get(src_id)
        if not isinstance(src_node, dict):
            return src_id, src_slot
        ntype = str(src_node.get("type") or src_node.get("class_type") or "")
        if ntype in ("GetNode", "Get"):
            bus = self._comfyui_bus_name(src_node)
            set_id = set_by_name.get(bus) if bus else None
            if set_id is not None:
                return self._resolve_ui_link_source(
                    set_id, 0, node_by_id, link_by_id, set_by_name, seen
                )
            return src_id, src_slot
        if ntype in ("Reroute", "ReroutePrimitive"):
            for inp in src_node.get("inputs") or []:
                if not isinstance(inp, dict) or inp.get("link") is None:
                    continue
                link = link_by_id.get(inp.get("link"))
                if link is None:
                    continue
                if isinstance(link, dict):
                    next_id = link.get("origin_id", link.get("src"))
                    next_slot = link.get("origin_slot", link.get("src_slot", 0))
                else:
                    next_id = link[1] if len(link) > 1 else None
                    next_slot = link[2] if len(link) > 2 else 0
                if next_id is None:
                    continue
                return self._resolve_ui_link_source(
                    next_id, next_slot, node_by_id, link_by_id, set_by_name, seen
                )
        return src_id, src_slot

