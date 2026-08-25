# =============================================================================
# metadata_parser.comfyui.extract - metadata_parser decomposition stages 1+2 (2026-07-13).
# Extracted VERBATIM from backend/metadata_parser.py @ c06d374 (4,912 lines).
# Source line ranges (original file): 2597-2948.
# Mixin: ComfyUI graph extraction entry: prompt nodes, multi-LoRA, civitai resources.
# self.* calls and class-constant lookups resolve via MRO exactly as before.
# Patched seams (Image / open / _MAX_* / _sidecar_directory_cache): the readers
# live in metadata_parser/_runtime.py behind the package get/set proxy in
# __init__.py (stage 3); see tests/test_metadata_parser_pins.py.
import json
import logging
import re
from typing import Optional, Dict, Any, Tuple, List, Set
from civitai_extractor import extract_civitai_resources

logger = logging.getLogger(__name__)

class ComfyUIExtractMixin:
    """ComfyUI graph extraction entry: prompt nodes, multi-LoRA, civitai resources."""

    def _extract_comfyui_data(self, prompt_data: Any) -> Tuple[Optional[str], Optional[str], Optional[str], List[str]]:
        """
        Extract positive/negative prompts, checkpoint, and loras from ComfyUI workflow.

        Uses graph traversal to follow KSampler positive/negative connections
        back to their source text nodes, rather than guessing based on order.
        """
        positive_text, negative_text, checkpoint, loras, _, _, _, _, _ = self._extract_comfyui_data_extended(prompt_data)
        return (positive_text, negative_text, checkpoint, loras)

    def _extract_comfyui_data_extended(self, prompt_data: Any, workflow_data: Any = None) -> Tuple[Optional[str], Optional[str], Optional[str], List[str], Optional[Dict], Optional[List], Optional[Dict], Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
        """
        Extended ComfyUI extraction: returns
        (pos, neg, checkpoint, loras, gen_params, prompt_nodes, img2img_info, model_assets, civitai_resources).
        """
        if not isinstance(prompt_data, dict):
            try:
                prompt_data = json.loads(prompt_data) if isinstance(prompt_data, str) else {}
            except Exception as e:
                logger.debug('Failed to parse ComfyUI prompt_data (extended): %s', e)
                prompt_data = {}

        if not prompt_data:
            converted = self._workflow_ui_to_prompt_data(workflow_data)
            if converted:
                prompt_data = converted
            else:
                return (None, None, None, [], None, None, None, None, None)

        checkpoint = None
        loras = []
        gen_params: Dict[str, Any] = {}
        prompt_nodes = []
        img2img_info = None
        model_assets = None

        # Build lookup
        nodes = {}
        for node_id, node in prompt_data.items():
            if isinstance(node, dict):
                nodes[str(node_id)] = node

        # Extract checkpoint, loras, and generation params from nodes
        has_load_image = False
        for node_id, node in nodes.items():
            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})

            # Checkpoint
            if any(ct in class_type for ct in ["CheckpointLoader", "CheckPointLoader", "UNETLoader", "DiffusionModelLoader"]):
                cp = inputs.get("ckpt_name", inputs.get("unet_name", inputs.get("model_name", "")))
                if cp and isinstance(cp, str):
                    checkpoint = cp

            # LoRAs (standard single-lora nodes)
            if any(ct in class_type for ct in ["LoraLoader", "LoRALoader"]):
                lr = inputs.get("lora_name", "")
                if lr and isinstance(lr, str):
                    loras.append(lr)
                    strength_model = inputs.get("strength_model")
                    strength_clip = inputs.get("strength_clip")
                    lora_detail = {"name": lr}
                    if isinstance(strength_model, (int, float)):
                        lora_detail["strength_model"] = round(float(strength_model), 4)
                    if isinstance(strength_clip, (int, float)):
                        lora_detail["strength_clip"] = round(float(strength_clip), 4)
                    if "lora_details" not in gen_params:
                        gen_params["lora_details"] = []
                    gen_params["lora_details"].append(lora_detail)

            # LoRAs (multi-lora nodes like rgthree Power Lora Loader)
            if any(ct in class_type for ct in self.COMFYUI_MULTI_LORA_NODE_TYPES):
                loras.extend(self._extract_multi_lora_inputs(inputs))
                multi_details = self._extract_multi_lora_details(inputs)
                if multi_details:
                    if "lora_details" not in gen_params:
                        gen_params["lora_details"] = []
                    gen_params["lora_details"].extend(multi_details)

            # KSampler params (and custom *Sampler nodes with the same fields)
            if self._is_comfyui_sampler_node(class_type, inputs):
                if "seed" in inputs:
                    seed_val = inputs["seed"]
                    if isinstance(seed_val, (int, float)):
                        gen_params["seed"] = int(seed_val)
                if "steps" in inputs:
                    steps_val = inputs["steps"]
                    if isinstance(steps_val, (int, float)):
                        gen_params["steps"] = int(steps_val)
                if "cfg" in inputs:
                    cfg_val = inputs["cfg"]
                    if isinstance(cfg_val, (int, float)):
                        gen_params["cfg_scale"] = float(cfg_val)
                if "sampler_name" in inputs:
                    gen_params["sampler"] = inputs["sampler_name"]
                if "sampler" in inputs and "sampler" not in gen_params:
                    gen_params["sampler"] = inputs["sampler"]
                if "scheduler" in inputs:
                    gen_params["scheduler"] = inputs["scheduler"]
                if "denoise" in inputs:
                    denoise_val = inputs["denoise"]
                    if isinstance(denoise_val, (int, float)):
                        gen_params["denoising_strength"] = float(denoise_val)
                if "noise_seed" in inputs and isinstance(inputs["noise_seed"], (int, float)):
                    gen_params["noise_seed"] = int(inputs["noise_seed"])
                if "add_noise" in inputs:
                    gen_params["add_noise"] = inputs["add_noise"]
                if "start_at_step" in inputs and isinstance(inputs["start_at_step"], (int, float)):
                    gen_params["start_at_step"] = int(inputs["start_at_step"])
                if "end_at_step" in inputs and isinstance(inputs["end_at_step"], (int, float)):
                    gen_params["end_at_step"] = int(inputs["end_at_step"])
                if "return_with_leftover_noise" in inputs:
                    gen_params["return_with_leftover_noise"] = inputs["return_with_leftover_noise"]

            if class_type in ("EmptyLatentImage", "EmptySD3LatentImage", "EmptyHunyuanLatentVideo"):
                width = inputs.get("width")
                height = inputs.get("height")
                if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                    gen_params["size"] = f"{int(width)}x{int(height)}"

            # img2img detection: LoadImage node presence
            if class_type in ("LoadImage", "LoadImageMask"):
                has_load_image = True

        # Determine img2img
        denoise = gen_params.get("denoising_strength")
        if has_load_image and denoise is not None and denoise < 1.0:
            img2img_info = {
                "denoising_strength": denoise,
                "source": "img2img",
            }
        elif denoise is not None and denoise < 1.0 and not has_load_image:
            # Likely hires fix or latent upscale — still record it
            img2img_info = {
                "denoising_strength": denoise,
                "source": "latent upscale",
            }

        # Trace prompts via KSampler graph
        positive_text, negative_text = self._trace_sampler_prompts(nodes)

        # Build prompt_nodes list (multi-node breakdown)
        prompt_nodes = self._collect_prompt_nodes(nodes)
        if not prompt_nodes:
            fallback = self._collect_text_from_nodes_as_nodes(nodes)
            if fallback:
                prompt_nodes = fallback

        # Fallback — fill ONLY the missing side. The old unconditional
        # unpack overwrote a traced negative with the fallback's (possibly
        # None) value whenever the positive was missing.
        # Also upgrade a traced fragment to a longer completed form found
        # elsewhere (ShowText executed cache, concat result).
        fallback_pos, fallback_neg = self._collect_text_from_nodes(nodes)
        if not positive_text:
            positive_text = fallback_pos
        elif fallback_pos and self._is_completed_prompt_form(fallback_pos, positive_text):
            positive_text = fallback_pos
        if not negative_text:
            negative_text = fallback_neg
        elif fallback_neg and self._is_completed_prompt_form(fallback_neg, negative_text):
            negative_text = fallback_neg

        # Saved WebP/JPEG often embed a *subgraph* API ``prompt`` (upscale /
        # SaveImage only) plus the full UI ``workflow`` that still has CLIP
        # widgets. Tracing the subgraph yields nothing; fill from the UI graph.
        if (not positive_text or not negative_text) and workflow_data:
            converted = self._workflow_ui_to_prompt_data(workflow_data)
            if converted:
                ui_pos, ui_neg = self._trace_sampler_prompts(converted)
                if not ui_pos or not ui_neg:
                    harvest_pos, harvest_neg = self._collect_text_from_nodes(converted)
                    if not ui_pos:
                        ui_pos = harvest_pos
                    elif harvest_pos and self._is_completed_prompt_form(harvest_pos, ui_pos):
                        ui_pos = harvest_pos
                    if not ui_neg:
                        ui_neg = harvest_neg
                    elif harvest_neg and self._is_completed_prompt_form(harvest_neg, ui_neg):
                        ui_neg = harvest_neg
                if not positive_text:
                    positive_text = ui_pos
                elif ui_pos and self._is_completed_prompt_form(ui_pos, positive_text):
                    positive_text = ui_pos
                if not negative_text:
                    negative_text = ui_neg
                elif ui_neg and self._is_completed_prompt_form(ui_neg, negative_text):
                    negative_text = ui_neg

        workflow_assets = self._extract_comfyui_model_assets_from_workflow_widgets(workflow_data)

        if checkpoint is None or not loras:
            model_assets = self._extract_comfyui_model_assets_from_active_graph(nodes)
            if checkpoint is None:
                checkpoint = model_assets.get("primary_model_name")
            if not loras:
                loras = list(model_assets.get("loras", []))

            global_lora_candidates = self._extract_comfyui_global_lora_candidates(nodes)
            if global_lora_candidates:
                existing_loras = {
                    str(name).strip()
                    for name in model_assets.get("loras", [])
                    if str(name).strip()
                }
                existing_loras.update(
                    str(item.get("name", "")).strip()
                    for item in model_assets.get("lora_candidates", [])
                    if str(item.get("name", "")).strip()
                )
                filtered_global_candidates = [
                    item for item in global_lora_candidates
                    if item["name"] not in existing_loras
                ]
                if filtered_global_candidates:
                    model_assets["global_lora_candidates"] = filtered_global_candidates
        else:
            model_assets = self._build_explicit_model_assets(
                source="fast_path",
                checkpoint=checkpoint,
                loras=loras,
            )

        model_assets = self._merge_model_assets(model_assets, workflow_assets)
        model_assets = self._merge_model_assets(model_assets, self._extract_comfyui_yolo_assets_from_full_graph(nodes))

        # Collect disabled LoRA names from rgthree-style nodes so workflow
        # widget data (which lacks the on/off flag) doesn't re-introduce them.
        disabled_loras: Set[str] = set()
        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            for key, val in node.get("inputs", {}).items():
                if isinstance(val, dict) and val.get("on") is False:
                    lr_name = val.get("lora", val.get("lora_name", ""))
                    if lr_name and isinstance(lr_name, str):
                        disabled_loras.add(lr_name)

        if checkpoint is None and model_assets:
            checkpoint = model_assets.get("primary_model_name")
        loras = self._normalize_lora_names([
            *loras,
            *((model_assets or {}).get("loras") or []),
        ])
        if disabled_loras:
            loras = [lr for lr in loras if lr not in disabled_loras]
        if model_assets is not None:
            model_assets["loras"] = list(loras)
            if disabled_loras:
                for key in ("lora_candidates", "global_lora_candidates"):
                    candidates = model_assets.get(key)
                    if candidates:
                        model_assets[key] = [c for c in candidates if c.get("name") not in disabled_loras]

        # Extract Civitai resources from prompt and workflow JSON
        civitai_resources = None
        try:
            resources = extract_civitai_resources(prompt_data, workflow_data)
            if resources:
                civitai_resources = resources
        except Exception as e:
            logger.debug('Failed to extract Civitai resources: %s', e)

        return (positive_text, negative_text, checkpoint, loras,
                gen_params if gen_params else None,
                prompt_nodes if prompt_nodes else None,
                img2img_info,
                model_assets,
                civitai_resources)

    def _collect_prompt_nodes(self, nodes: Dict[str, dict]) -> List[Dict[str, Any]]:
        """Collect all text-bearing nodes for multi-node prompt breakdown."""
        result = []
        seen_texts = set()

        for node_id, node in nodes.items():
            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})

            # Only collect from text encoder nodes
            if not any(ct in class_type for ct in ["CLIPTextEncode", "NewBieCLIPTextEncode", "TextEncode", "PromptBuilder", "PromptComposer"]):
                continue

            text = inputs.get("text", inputs.get("prompt", inputs.get("user_prompt", "")))
            source_node_id = node_id
            source_class_type = class_type
            source_key = "text" if "text" in inputs else ("prompt" if "prompt" in inputs else "user_prompt")

            if isinstance(text, (list, tuple)):
                traced_info = self._trace_to_text_with_source(text, nodes, set())
                traced_texts = [item["text"] for item in traced_info if item.get("text")]
                text = "\n".join(traced_texts) if traced_texts else None
                if traced_info:
                    source_node_id = traced_info[0]["source_node_id"]
                    source_class_type = traced_info[0]["source_class_type"]
                    source_key = traced_info[0]["source_key"]

            if isinstance(text, str) and text.strip() and len(text.strip()) > 3:
                # Deduplicate
                if text.strip() not in seen_texts:
                    seen_texts.add(text.strip())
                    role = "negative" if self._looks_like_negative_prompt(text) else "positive"
                    result.append({
                        "node_id": node_id,
                        "class_type": class_type,
                        "text": text.strip(),
                        "role": role,
                        "resolved_from": source_node_id,
                        "source_class_type": source_class_type,
                        "source_key": source_key,
                    })
                    extra_source_id = source_node_id if source_node_id in nodes else node_id
                    if role == "positive" and extra_source_id in nodes:
                        source_node = nodes[extra_source_id]
                        source_inputs = source_node.get("inputs", {})
                        for extra_key in ["text_b", "text_c", "prompt_b", "prompt_c", "string_b", "string_c"]:
                            extra_text = source_inputs.get(extra_key)
                            if isinstance(extra_text, str) and extra_text.strip() and extra_text.strip() not in seen_texts:
                                seen_texts.add(extra_text.strip())
                                result.append({
                                    "node_id": extra_source_id,
                                    "class_type": source_node.get("class_type", source_class_type),
                                    "text": extra_text.strip(),
                                    "role": role,
                                    "resolved_from": extra_source_id,
                                    "source_class_type": source_node.get("class_type", source_class_type),
                                    "source_key": extra_key,
                                })

        return result

    @staticmethod
    def _extract_multi_lora_inputs(inputs: dict) -> List[str]:
        """Extract LoRA names from multi-lora nodes (e.g. rgthree Power Lora Loader).

        These nodes have inputs like lora_1, lora_2, ... lora_N.
        Each can be:
        - A dict with {on: bool, lora: str, strength: float}
        - A string (lora name directly)
        """
        loras = []
        for key, value in inputs.items():
            key_lower = str(key).lower()
            if not key_lower.startswith("lora_"):
                continue
            if not (
                re.match(r"^lora_\d+$", key_lower)
                or key_lower.endswith("_name")
                or key_lower.endswith("_lora")
                or key_lower.endswith("_lora_name")
            ):
                continue
            if isinstance(value, dict):
                if value.get("on") is False:
                    continue
                lora_name = value.get("lora", value.get("lora_name", ""))
                if lora_name and isinstance(lora_name, str) and lora_name != "None":
                    loras.append(lora_name)
            elif isinstance(value, str) and value and value != "None":
                loras.append(value)
        return loras

    @staticmethod
    def _extract_multi_lora_details(inputs: dict) -> List[Dict[str, Any]]:
        """Like _extract_multi_lora_inputs but returns structured details with weights."""
        details = []
        for key, value in inputs.items():
            key_lower = str(key).lower()
            if not key_lower.startswith("lora_"):
                continue
            if not (
                re.match(r"^lora_\d+$", key_lower)
                or key_lower.endswith("_name")
                or key_lower.endswith("_lora")
                or key_lower.endswith("_lora_name")
            ):
                continue
            if isinstance(value, dict):
                if value.get("on") is False:
                    continue
                lora_name = value.get("lora", value.get("lora_name", ""))
                if lora_name and isinstance(lora_name, str) and lora_name != "None":
                    detail: Dict[str, Any] = {"name": lora_name}
                    strength = value.get("strength")
                    if isinstance(strength, (int, float)):
                        detail["strength_model"] = round(float(strength), 4)
                    details.append(detail)
        return details

