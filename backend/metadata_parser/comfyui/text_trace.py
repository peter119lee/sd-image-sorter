# =============================================================================
# metadata_parser.comfyui.text_trace - metadata_parser decomposition stages 1+2 (2026-07-13).
# Extracted VERBATIM from backend/metadata_parser.py @ c06d374 (4,912 lines).
# Source line ranges (original file): 3819-4374.
# Mixin: ComfyUI text tracing: sampler prompts, node text extraction, workflow text.
# self.* calls and class-constant lookups resolve via MRO exactly as before.
# Patched seams (Image / open / _MAX_* / _sidecar_directory_cache): the readers
# live in metadata_parser/_runtime.py behind the package get/set proxy in
# __init__.py (stage 3); see tests/test_metadata_parser_pins.py.
import json
import logging
from typing import Optional, Dict, Any, Tuple, List, Set

logger = logging.getLogger(__name__)

class ComfyUITextTraceMixin:
    """ComfyUI text tracing: sampler prompts, node text extraction, workflow text."""

    # Encoder input names used by stock ComfyUI CLIP/T5/SDXL nodes.
    # Infinite Image Browser reads `text` or `t5xxl`; sd-prompt-reader also
    # reads SDXL `text_g`/`text_l`. Graph position decides this is a prompt;
    # the string format (booru vs natural language) does not.
    _ENCODER_TEXT_KEYS = (
        # executed caches first (IIB ImpactWildcard populated_text / ShowText)
        "populated_text", "text_0", "result",
        "text", "t5xxl", "text_g", "text_l", "clip_l", "clip_g",
        "prompt", "user_prompt",
    )

    def _trace_sampler_prompts(self, nodes: Dict[str, dict]) -> Tuple[Optional[str], Optional[str]]:
        """
        Trace KSampler positive/negative inputs back through the node graph
        to find the actual text content.

        Fallback strategy when no sampler is found:
        1. If exactly 2 CLIPTextEncode nodes → first=positive, second=negative
        2. If 1 CLIPTextEncode node → positive only
        3. If 3+ CLIPTextEncode nodes → all as positive
        """
        positive_texts = []
        negative_texts = []

        # Find sampler nodes (KSampler family + custom *Sampler with pos/neg)
        sampler_nodes = []
        for node_id, node in nodes.items():
            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {}) if isinstance(node.get("inputs"), dict) else {}
            if self._is_comfyui_sampler_node(class_type, inputs):
                sampler_nodes.append((node_id, node))

        # For each sampler, trace its positive and negative inputs
        for sampler_id, sampler_node in sampler_nodes:
            inputs = sampler_node.get("inputs", {})

            pos_ref = inputs.get("positive")
            neg_ref = inputs.get("negative")

            # SamplerCustomAdvanced uses a guider node instead of direct
            # positive/negative.  Follow the guider reference to find them.
            if pos_ref is None and neg_ref is None:
                guider_ref = inputs.get("guider")
                if isinstance(guider_ref, (list, tuple)) and len(guider_ref) >= 2:
                    guider_node = nodes.get(str(guider_ref[0]), {})
                    guider_inputs = guider_node.get("inputs", {})
                    pos_ref = guider_inputs.get("positive")
                    neg_ref = guider_inputs.get("negative")
                    if pos_ref is None:
                        pos_ref = guider_inputs.get("cond")

            # Trace positive conditioning
            if pos_ref:
                texts = self._trace_to_text(pos_ref, nodes, set(), side="positive")
                positive_texts.extend(texts)

            # Trace negative conditioning
            if neg_ref:
                texts = self._trace_to_text(neg_ref, nodes, set(), side="negative")
                negative_texts.extend(texts)

        pos_result = "\n".join(positive_texts) if positive_texts else None
        neg_result = "\n".join(negative_texts) if negative_texts else None
        if pos_result or neg_result or sampler_nodes:
            # Empty-but-sampler-present is better than guessing CLIP widgets
            # (custom samplers behind pipes yield junk like "否 (false)").
            # Harvest in _extract_comfyui_data_extended fills the gap.
            return (pos_result, neg_result)

        return self._trace_clip_encode_prompts(nodes)

    def _trace_clip_encode_prompts(self, nodes: Dict[str, dict]) -> Tuple[Optional[str], Optional[str]]:
        """Fallback: read CLIPTextEncode nodes when sampler tracing is empty."""
        clip_nodes = []
        for node_id, node in nodes.items():
            class_type = node.get("class_type", "")
            if any(clip_type in class_type for clip_type in self.COMFYUI_TEXT_NODE_TYPES):
                clip_nodes.append((node_id, node))

        classified_pos: List[str] = []
        classified_neg: List[str] = []
        for node_id, _node in clip_nodes:
            texts = self._trace_to_text(node_id, nodes, set())
            for text in texts:
                if self._looks_like_negative_prompt(text):
                    classified_neg.append(text)
                else:
                    classified_pos.append(text)

        positive_texts: List[str] = []
        negative_texts: List[str] = []
        # UI graphs often list the negative CLIP node first. If both sides
        # classify, trust the classifier instead of node order.
        if classified_neg and classified_pos:
            positive_texts.extend(classified_pos)
            negative_texts.extend(classified_neg)
        elif len(clip_nodes) == 2 and classified_neg and not classified_pos:
            negative_texts.extend(classified_neg)
        elif len(clip_nodes) == 2 and not classified_neg:
            positive_texts.extend(self._trace_to_text(clip_nodes[0][0], nodes, set()))
            negative_texts.extend(self._trace_to_text(clip_nodes[1][0], nodes, set()))
        elif len(clip_nodes) == 1:
            if classified_neg and not classified_pos:
                negative_texts.extend(classified_neg)
            else:
                positive_texts.extend(
                    classified_pos or self._trace_to_text(clip_nodes[0][0], nodes, set())
                )
        elif len(clip_nodes) > 2:
            positive_texts.extend(classified_pos)
            negative_texts.extend(classified_neg)

        pos_result = "\n".join(positive_texts) if positive_texts else None
        neg_result = "\n".join(negative_texts) if negative_texts else None
        return (pos_result, neg_result)

    @staticmethod
    def _extract_danbooru_gallery_text(inputs: dict) -> Optional[str]:
        """Parse DanbooruGallery ``selection_data`` into a prompt string.

        ``selection_data`` is serialized at QUEUE time, so it reflects the
        CURRENT run: ``{"selections": [{"post_id": ..., "prompt": ...}]}``.
        Multiple selections are joined with ", ". Malformed payloads yield
        ``None`` (never raise).
        """
        raw = inputs.get("selection_data")
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        selections = data.get("selections")
        if not isinstance(selections, list):
            return None
        prompts = []
        for item in selections:
            if not isinstance(item, dict):
                continue
            prompt = item.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                prompts.append(prompt.strip())
        return ", ".join(prompts) if prompts else None

    def _trace_to_text(self, ref: Any, nodes: Dict[str, dict], visited: Set[str], depth: int = 0,
                       side: Optional[str] = None) -> List[str]:
        """
        Recursively trace a node reference back to find text content.
        Handles node connections (lists like [node_id, output_index])
        and direct string values. ``side`` ("positive"/"negative") keeps a
        trace from resolving through the OTHER side's input on
        dual-conditioning nodes (ControlNet, guiders).
        """
        traced = self._trace_to_text_with_source(ref, nodes, visited, depth, side=side)
        return [item["text"] for item in traced if item.get("text")]

    def _trace_to_text_with_source(self, ref: Any, nodes: Dict[str, dict], visited: Set[str], depth: int = 0,
                                   side: Optional[str] = None) -> List[Dict[str, Any]]:
        """Trace text and keep source node metadata."""
        if depth > 20:
            return []

        if isinstance(ref, str):
            if ref in nodes:
                return self._extract_text_from_node_with_source(ref, nodes, visited, depth, side=side)
            return [{
                "text": ref,
                "source_node_id": None,
                "source_class_type": "literal",
                "source_key": "literal",
            }] if ref.strip() else []

        if isinstance(ref, list) and len(ref) >= 2:
            target_id = str(ref[0])
            return self._extract_text_from_node_with_source(target_id, nodes, visited, depth, side=side)

        return []

    def _extract_text_from_node(self, node_id: str, nodes: Dict[str, dict], visited: Set[str], depth: int = 0) -> List[str]:
        """Extract text from a specific node, following connections as needed."""
        if node_id in visited:
            return []
        visited.add(node_id)

        node = nodes.get(node_id)
        if not node:
            return []

        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {})
        texts = []

        # FluxGuidance / similar: the prompt is on the conditioning input.
        # Infinite Image Browser skips through this node explicitly.
        if "Guidance" in class_type:
            cond = inputs.get("conditioning") if isinstance(inputs, dict) else None
            if isinstance(cond, (list, tuple)):
                return self._trace_to_text(cond, nodes, visited, depth + 1)

        bus_texts = self._extract_set_get_bus_text(node_id, node, nodes, visited, depth)
        if bus_texts is not None:
            return bus_texts

        # Text encoder nodes — graph position is the classifier (IIB /
        # sd-prompt-reader): whatever string sits on an encoder input is the
        # prompt, tags or natural language. Flux/SD3 use t5xxl; SDXL uses
        # text_g/text_l. Do not require a `text` key.
        if any(ct in class_type for ct in ["CLIPTextEncode", "NewBieCLIPTextEncode", "TextEncodeQwen"]):
            for key in self._ENCODER_TEXT_KEYS:
                text_val = inputs.get(key)
                if isinstance(text_val, str) and text_val.strip():
                    texts.append(text_val)
                elif isinstance(text_val, (list, tuple)):
                    texts.extend(self._trace_to_text(text_val, nodes, visited, depth + 1))

            # Also check system_prompt for some custom nodes
            sys_prompt = inputs.get("system_prompt", "")
            if isinstance(sys_prompt, (list, tuple)):
                # Follow connection but don't include system prompts in output
                pass

        # String/text concatenation/join nodes (CR Text Concatenate, StringConcatenate, JoinStrings, easy promptConcat, etc.)
        # MUST be before StringConstant/Text check since "CR Text Concatenate" contains "Text"
        elif any(kw in class_type for kw in ["Concatenate", "Concat", "JoinString", "Join"]):
            # Prioritize known text input keys first (preserves order)
            priority_keys = ["string_a", "string_b", "string1", "string2", "text1", "text2",
                             "text_a", "text_b", "prompt1", "prompt2", "prompt3",
                             "string_1", "string_2"]
            # Then dynamically match numbered keys (string_3, string_4, ..., string_N)
            numbered_keys = [key for key in inputs.keys() if self._is_numbered_text_key(key) and key not in priority_keys]
            # Combine: fixed keys first, then numbered keys sorted naturally
            all_keys = priority_keys + sorted(numbered_keys, key=lambda k: (k.rsplit("_", 1)[0], int(k.rsplit("_", 1)[1])))

            for key in all_keys:
                val = inputs.get(key)
                if val is None:
                    continue
                if isinstance(val, str) and val.strip():
                    texts.append(val)
                elif isinstance(val, (list, tuple)):
                    sub_texts = self._trace_to_text(val, nodes, visited, depth + 1)
                    texts.extend(sub_texts)
            # Also follow delimiter/separator connections (they might chain to text)
            for key in ["delimiter", "separator"]:
                val = inputs.get(key)
                if val and isinstance(val, (list, tuple)):
                    sub_texts = self._trace_to_text(val, nodes, visited, depth + 1)
                    texts.extend(sub_texts)

        # Conditioning combine/concat - follow both conditioning inputs
        # MUST be before generic "Prompt" check since ConditioningConcat contains no text
        elif "ConditioningCombine" in class_type or "ConditioningConcat" in class_type:
            for key in ["conditioning_1", "conditioning_2", "cond1", "cond2"]:
                val = inputs.get(key)
                if val:
                    sub_texts = self._trace_to_text(val, nodes, visited, depth + 1)
                    texts.extend(sub_texts)

        # ControlNet nodes - follow the positive/negative conditioning through
        elif "ControlNet" in class_type:
            for key in ["positive", "negative", "conditioning"]:
                val = inputs.get(key)
                if val and isinstance(val, (list, tuple)):
                    sub_texts = self._trace_to_text(val, nodes, visited, depth + 1)
                    texts.extend(sub_texts)

        # ShowText nodes (pysssss etc.) - text_0 is a display cache serialized
        # at QUEUE time, so it can hold STALE output from a PREVIOUS run when
        # the text is generated at runtime (e.g. by a VLM). When the live
        # text input is a link, trace upstream FIRST; fall back to the cached
        # literal only when upstream derivation yields nothing. If the cache
        # is a longer completed form of the upstream fragments (concat of
        # Get/Set buses), keep the cache — that is the executed prompt.
        elif "ShowText" in class_type:
            texts.extend(self._resolve_showtext_texts(inputs, nodes, visited, depth))

        # DanbooruGallery nodes - selection_data is a QUEUE-TIME literal, so
        # it reflects the CURRENT run's selected post(s) (unlike ShowText
        # display caches).
        elif "DanbooruGallery" in class_type:
            danbooru_text = self._extract_danbooru_gallery_text(inputs)
            if danbooru_text:
                texts.append(danbooru_text)

        # StringFunction nodes (pysssss) - have text_a/text_b/text_c inputs
        # and a 'result' cached output. Prefer result if available, else trace inputs.
        elif "StringFunction" in class_type:
            result_val = inputs.get("result", "")
            if isinstance(result_val, str) and result_val.strip():
                texts.append(result_val)
            else:
                # Follow text_a, text_b, text_c inputs
                for key in ["text_a", "text_b", "text_c"]:
                    val = inputs.get(key)
                    if val is None:
                        continue
                    if isinstance(val, str) and val.strip():
                        texts.append(val)
                    elif isinstance(val, (list, tuple)):
                        sub_texts = self._trace_to_text(val, nodes, visited, depth + 1)
                        texts.extend(sub_texts)

        # LLM/AI prompt formatter nodes - extract user_text as the prompt
        elif any(kw in class_type for kw in ["LLM", "Formatter", "ChatGPT"]):
            for key in ["user_text", "text", "prompt", "user_prompt", "input_text"]:
                val = inputs.get(key)
                if val is None:
                    continue
                if isinstance(val, str) and val.strip():
                    texts.append(val)
                elif isinstance(val, (list, tuple)):
                    sub_texts = self._trace_to_text(val, nodes, visited, depth + 1)
                    texts.extend(sub_texts)

        # Prompt text nodes (CR Prompt Text, WeiLin prompt nodes, etc.)
        elif any(kw in class_type for kw in ["Prompt", "prompt"]):
            for key in ["prompt", "positive", "negative", "text", "string",
                         "user_text", "user_prompt"]:
                val = inputs.get(key)
                if val is None:
                    continue
                if isinstance(val, str) and val.strip():
                    texts.append(val)
                elif isinstance(val, (list, tuple)):
                    sub_texts = self._trace_to_text(val, nodes, visited, depth + 1)
                    texts.extend(sub_texts)

        # String constant nodes - return the string value
        # This is intentionally AFTER Concatenate/Prompt checks since those class_types
        # can contain substrings like "Text" or "String" (e.g. "CR Text Concatenate")
        elif any(ct in class_type for ct in ["StringConstant", "String", "Text", "Note", "PrimitiveNode"]):
            text_val = inputs.get("string", inputs.get("String", inputs.get("text", inputs.get("value", ""))))
            if isinstance(text_val, str) and text_val.strip():
                texts.append(text_val)
            elif isinstance(text_val, (list, tuple)):
                sub_texts = self._trace_to_text(text_val, nodes, visited, depth + 1)
                texts.extend(sub_texts)

        # Generic fallback: check for any text-like input or cached result
        # Also handles FluxKontextMultiReferenceLatentMethod (follow conditioning ref)
        else:
            for key in ["populated_text", "text_0", "result",
                         "text", "t5xxl", "text_g", "text_l", "clip_l", "clip_g",
                         "string", "STRING", "prompt", "user_prompt",
                         "positive", "negative", "conditioning", "text1", "text2",
                         "string_a", "string_b", "user_text", "value"]:
                val = inputs.get(key)
                if val is None:
                    continue
                if isinstance(val, str) and val.strip():
                    texts.append(val)
                elif isinstance(val, (list, tuple)):
                    sub_texts = self._trace_to_text(val, nodes, visited, depth + 1)
                    texts.extend(sub_texts)

        # VLM/image-inference dead-end bridging: nodes whose text output is
        # generated at RUNTIME (e.g. QwenTE_ImageInfer) carry no recoverable
        # text in the serialized graph. Follow their image input upstream —
        # it can reach a node whose queue-time literal IS recoverable (e.g.
        # DanbooruGallery). Only image-typed links are followed here; VLM
        # instruction ("提示词") and system ("系统提示词"/"system") inputs are
        # never extracted on this bridging path.
        if not texts:
            for key in self.COMFYUI_IMAGE_BRIDGE_KEYS:
                val = inputs.get(key)
                if isinstance(val, (list, tuple)) and len(val) >= 2:
                    sub_texts = self._trace_to_text(val, nodes, visited, depth + 1)
                    if sub_texts:
                        texts.extend(sub_texts)
                        break

        return texts

    def _extract_text_from_node_with_source(self, node_id: str, nodes: Dict[str, dict], visited: Set[str], depth: int = 0,
                                             side: Optional[str] = None) -> List[Dict[str, Any]]:
        """Extract text plus source metadata from a node.

        ``side`` ("positive"/"negative"/None) marks which sampler input the
        trace started from, so dual-conditioning nodes (ControlNetApply,
        guiders) resolve through THEIR side's link and never the other one.
        """
        if node_id in visited:
            return []

        node = nodes.get(node_id)
        if not node:
            return []

        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {})

        if "Guidance" in class_type:
            nested_visited = set(visited)
            nested_visited.add(node_id)
            cond = inputs.get("conditioning") if isinstance(inputs, dict) else None
            if isinstance(cond, (list, tuple)):
                traced = self._trace_to_text_with_source(
                    cond, nodes, nested_visited, depth + 1, side=side
                )
                if traced:
                    return traced

        bus_results = self._extract_set_get_bus_text_with_source(
            node_id, node, nodes, visited, depth, side
        )
        if bus_results is not None:
            return bus_results

        # DanbooruGallery nodes - selection_data is a QUEUE-TIME literal that
        # reflects the CURRENT run's selected post(s).
        if "DanbooruGallery" in class_type:
            danbooru_text = self._extract_danbooru_gallery_text(inputs)
            if danbooru_text:
                return [{
                    "text": danbooru_text,
                    "source_node_id": node_id,
                    "source_class_type": class_type,
                    "source_key": "selection_data",
                }]

        # ShowText display caches (text_0) are serialized at QUEUE time and
        # can be STALE; prefer the live upstream link, cache as fallback only.
        # A cache that is a completed form of the upstream fragments wins.
        if "ShowText" in class_type:
            nested_visited = set(visited)
            nested_visited.add(node_id)
            upstream: List[Dict[str, Any]] = []
            for key in ["text", "string"]:
                val = inputs.get(key)
                if isinstance(val, (list, tuple)):
                    upstream.extend(
                        self._trace_to_text_with_source(
                            val, nodes, nested_visited, depth + 1, side=side
                        )
                    )
            cache_text = None
            cache_key = "text_0"
            for key in ["text_0", "text", "string"]:
                val = inputs.get(key)
                if isinstance(val, str) and val.strip():
                    cache_text = val.strip()
                    cache_key = key
                    break
            if upstream:
                joined = "\n".join(item["text"] for item in upstream if item.get("text"))
                if cache_text and self._is_completed_prompt_form(cache_text, joined):
                    return [{
                        "text": cache_text,
                        "source_node_id": node_id,
                        "source_class_type": class_type,
                        "source_key": cache_key,
                    }]
                return upstream
            if cache_text:
                return [{
                    "text": cache_text,
                    "source_node_id": node_id,
                    "source_class_type": class_type,
                    "source_key": cache_key,
                }]
            return []

        # Join/Concat nodes use numbered keys (string_1, string_2, …)
        if any(kw in class_type for kw in ["Concatenate", "Concat", "JoinString", "Join"]):
            nested_visited = set(visited)
            nested_visited.add(node_id)
            results: List[Dict[str, Any]] = []
            # Prioritize known text input keys first (preserves order)
            priority_keys = ["string_a", "string_b", "string1", "string2",
                             "text1", "text2", "text_a", "text_b",
                             "prompt1", "prompt2", "prompt3",
                             "string_1", "string_2", "string_3", "string_4"]
            # Then dynamically match numbered keys (string_5, string_6, ..., string_N)
            numbered_keys = [key for key in inputs.keys() if self._is_numbered_text_key(key) and key not in priority_keys]
            # Combine: fixed keys first, then numbered keys sorted naturally
            all_keys = priority_keys + sorted(numbered_keys, key=lambda k: (k.rsplit("_", 1)[0], int(k.rsplit("_", 1)[1])))

            for key in all_keys:
                val = inputs.get(key)
                if val is None:
                    continue
                if isinstance(val, str) and val.strip():
                    results.append({
                        "text": val,
                        "source_node_id": node_id,
                        "source_class_type": class_type,
                        "source_key": key,
                    })
                elif isinstance(val, (list, tuple)):
                    traced = self._trace_to_text_with_source(val, nodes, nested_visited, depth + 1, side=side)
                    results.extend(traced)
            if results:
                return results

        # WeiLin / CR Prompt / similar: widget text lives on `positive` even
        # for negative prompt UIs. Read both literals; do not follow the
        # opposite conditioning link.
        if any(kw in class_type for kw in ["Prompt", "prompt", "WeiLin"]):
            nested_visited = set(visited)
            nested_visited.add(node_id)
            results: List[Dict[str, Any]] = []
            literal_keys = ["negative", "positive", "prompt", "text", "string",
                            "user_text", "user_prompt"]
            for key in literal_keys:
                val = inputs.get(key)
                if isinstance(val, str) and val.strip():
                    results.append({
                        "text": val.strip(),
                        "source_node_id": node_id,
                        "source_class_type": class_type,
                        "source_key": key,
                    })
                    continue
                if not isinstance(val, (list, tuple)):
                    continue
                if key == "positive" and side == "negative":
                    continue
                if key == "negative" and side == "positive":
                    continue
                traced = self._trace_to_text_with_source(
                    val, nodes, nested_visited, depth + 1, side=side
                )
                results.extend(traced)
            if results:
                return results

        # "base_prompt" (AnimaArtistPack) and the SAME-side conditioning
        # channel (ControlNet/guider-style processors) carry the prompt
        # through custom conditioning nodes. The OTHER side's channel is
        # excluded: tracing KSampler.negative through ControlNetApply must
        # ride its "negative" input, never resolve via "positive" (corpus
        # case controlnet-apply-chain caught exactly that).
        side_channel = "negative" if side == "negative" else "positive"
        for key in ["populated_text", "text_0", "result",
                    "text", "t5xxl", "text_g", "text_l", "clip_l", "clip_g",
                    "prompt", "base_prompt", "user_prompt",
                    side_channel, "string", "String", "STRING", "value",
                    "conditioning"]:
            value = inputs.get(key)
            if isinstance(value, str) and value.strip():
                return [{
                    "text": value,
                    "source_node_id": node_id,
                    "source_class_type": class_type,
                    "source_key": key,
                }]
            if isinstance(value, (list, tuple)):
                nested_visited = set(visited)
                nested_visited.add(node_id)
                traced = self._trace_to_text_with_source(value, nodes, nested_visited, depth + 1, side=side)
                if traced:
                    return traced

        # VLM/image-inference dead-end bridging: follow image-typed links only
        # (see _extract_text_from_node for rationale); instruction/system
        # inputs are never followed here.
        bridge_visited = set(visited)
        bridge_visited.add(node_id)
        for key in self.COMFYUI_IMAGE_BRIDGE_KEYS:
            val = inputs.get(key)
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                traced = self._trace_to_text_with_source(val, nodes, bridge_visited, depth + 1, side=side)
                if traced:
                    return traced

        # Conditioning bridge (v3.5.0): custom conditioning processors hide
        # the prompt behind node-specific link keys (e.g. AnimaArtistCrossAttn
        # → artist_pack → AnimaArtistPack → base_prompt). After every known
        # text key missed, follow the remaining links except known non-text
        # plumbing — the first chain that yields text wins. Only reached when
        # the node would otherwise dead-end, so already-parsing workflows are
        # unaffected. The opposite side's channel stays off-limits here too.
        opposite_channel = "positive" if side == "negative" else "negative"
        for key, val in inputs.items():
            if not isinstance(val, (list, tuple)) or len(val) < 2:
                continue
            lowered = str(key).lower()
            if lowered in self.COMFYUI_COND_BRIDGE_EXCLUDE_KEYS:
                continue
            if lowered in self.COMFYUI_IMAGE_BRIDGE_KEYS:
                continue
            if lowered == opposite_channel:
                continue
            traced = self._trace_to_text_with_source(val, nodes, bridge_visited, depth + 1, side=side)
            if traced:
                return traced

        return []

    def _collect_text_from_nodes(self, nodes: Dict[str, dict]) -> Tuple[Optional[str], Optional[str]]:
        """Generic last-resort harvest: score EVERY string in the graph.

        v3.5.0 L2 rewrite. The old version had two stages where a partial
        hit in stage 1 (e.g. only the negative CLIPTextEncode is a literal,
        the positive rides custom links) returned early and PERMANENTLY
        shadowed the whole-graph scan — one of the two reasons an owner
        folder of 657 images parsed with empty positives. Now every node's
        every string value is scored for prompt-likeness (danbooru-vocab
        hit ratio OR comma structure — see prompt_text_scorer) in a single
        pass; encoder-ish nodes only add a prior bonus, never a monopoly.
        """
        try:
            from prompt_text_scorer import harvest_prompt_candidates, pick_positive_negative

            candidates = harvest_prompt_candidates(nodes, self.COMFYUI_TEXT_NODE_TYPES)
            return pick_positive_negative(candidates)
        except Exception as exc:  # scorer must never take the parser down
            logger.debug("prompt text harvest failed: %s", exc)
            return (None, None)

    def _is_completed_prompt_form(self, candidate: Optional[str], current: Optional[str]) -> bool:
        """True when ``candidate`` is a longer assembled form of ``current``.

        Used to prefer an executed ShowText/parameters blob over the fragments
        a Get/Set concat chain can reconstruct, without letting a stale VLM
        cache replace a live upstream prompt (token overlap must be high).
        """
        if not candidate or not current:
            return False
        cand = candidate.strip()
        cur = current.strip()
        if not cand or not cur or len(cand) <= len(cur):
            return False
        if cur in cand:
            return True
        # Concat/Get-Set fragments: every comma token of current must appear
        # as an exact substring of candidate. Do NOT use fuzzy token-in-string
        # matching ("day" vs "daylight") — that lets a longer stale ShowText
        # cache beat a live Danbooru/VLM prompt that shares generic tags.
        fragments = [
            token.strip()
            for token in cur.split(",")
            if token.strip() and len(token.strip()) > 2
        ]
        if len(fragments) < 2:
            return False
        return all(fragment in cand for fragment in fragments)

    def _resolve_showtext_texts(
        self,
        inputs: dict,
        nodes: Dict[str, dict],
        visited: Set[str],
        depth: int,
        side: Optional[str] = None,
    ) -> List[str]:
        """Live upstream first; cache wins only as fallback or completed form."""
        upstream_texts: List[str] = []
        for key in ["text", "string"]:
            val = inputs.get(key)
            if isinstance(val, (list, tuple)):
                upstream_texts.extend(
                    self._trace_to_text(val, nodes, visited, depth + 1, side=side)
                )
        cache_text = None
        for key in ["text_0", "text", "string"]:
            val = inputs.get(key)
            if isinstance(val, str) and val.strip():
                cache_text = val.strip()
                break
        if upstream_texts:
            joined = "\n".join(upstream_texts)
            if cache_text and self._is_completed_prompt_form(cache_text, joined):
                return [cache_text]
            return upstream_texts
        return [cache_text] if cache_text else []

    def _extract_set_get_bus_text(
        self,
        node_id: str,
        node: dict,
        nodes: Dict[str, dict],
        visited: Set[str],
        depth: int,
    ) -> Optional[List[str]]:
        """Follow kjnodes GetNode → matching SetNode → STRING input.

        Returns None when this node is not a bus node, so callers fall through
        to the normal extractors.
        """
        class_type = str(node.get("class_type") or "")
        if class_type not in ("GetNode", "Get", "SetNode", "Set", "Reroute", "ReroutePrimitive"):
            return None
        inputs = node.get("inputs", {}) if isinstance(node.get("inputs"), dict) else {}
        if class_type in ("Reroute", "ReroutePrimitive"):
            for val in inputs.values():
                if isinstance(val, (list, tuple)):
                    return self._trace_to_text(val, nodes, visited, depth + 1)
            return []
        if class_type in ("GetNode", "Get"):
            target = self._find_comfyui_set_node(nodes, self._comfyui_bus_name(node))
            if not target or target == node_id:
                return []
            return self._trace_to_text([target, 0], nodes, visited, depth + 1)
        for key in ("STRING", "string", "value", "text"):
            val = inputs.get(key)
            if isinstance(val, str) and val.strip():
                return [val.strip()]
            if isinstance(val, (list, tuple)):
                return self._trace_to_text(val, nodes, visited, depth + 1)
        return []

    def _extract_set_get_bus_text_with_source(
        self,
        node_id: str,
        node: dict,
        nodes: Dict[str, dict],
        visited: Set[str],
        depth: int,
        side: Optional[str],
    ) -> Optional[List[Dict[str, Any]]]:
        class_type = str(node.get("class_type") or "")
        if class_type not in ("GetNode", "Get", "SetNode", "Set", "Reroute", "ReroutePrimitive"):
            return None
        nested = set(visited)
        nested.add(node_id)
        inputs = node.get("inputs", {}) if isinstance(node.get("inputs"), dict) else {}
        if class_type in ("Reroute", "ReroutePrimitive"):
            for val in inputs.values():
                if isinstance(val, (list, tuple)):
                    return self._trace_to_text_with_source(
                        val, nodes, nested, depth + 1, side=side
                    )
            return []
        if class_type in ("GetNode", "Get"):
            target = self._find_comfyui_set_node(nodes, self._comfyui_bus_name(node))
            if not target or target == node_id:
                return []
            return self._trace_to_text_with_source([target, 0], nodes, nested, depth + 1, side=side)
        for key in ("STRING", "string", "value", "text"):
            val = inputs.get(key)
            if isinstance(val, str) and val.strip():
                return [{
                    "text": val.strip(),
                    "source_node_id": node_id,
                    "source_class_type": class_type,
                    "source_key": key,
                }]
            if isinstance(val, (list, tuple)):
                return self._trace_to_text_with_source(val, nodes, nested, depth + 1, side=side)
        return []

    def _looks_like_negative_prompt(self, text: str) -> bool:
        """Heuristic to detect if a text is a negative prompt."""
        lower = text.lower().strip()
        negative_indicators = [
            "worst quality", "low quality", "bad quality", "lowres",
            "bad anatomy", "worst hands", "deformed", "blurry",
            "low_resolution", "medium_resolution", "low_score",
            "pixelated", "compression artifacts", "jpeg artifacts",
            "bad_anatomy", "worst_hands",
        ]
        # Count how many negative indicators are present
        matches = sum(1 for indicator in negative_indicators if indicator in lower)
        # If 3+ negative quality indicators, likely a negative prompt
        return matches >= 3

    def _collect_text_from_nodes_as_nodes(self, nodes: Dict[str, dict]) -> Optional[List[Dict[str, Any]]]:
        """Collect text-bearing nodes in a frontend-friendly structure."""
        prompt_nodes = self._collect_prompt_nodes(nodes)
        return prompt_nodes if prompt_nodes else None

    def _extract_from_workflow(self, workflow: dict) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract prompts from ComfyUI workflow format (nodes with widgets_values).
        This is a fallback when prompt data is missing or empty.
        """
        converted = self._workflow_ui_to_prompt_data(workflow)
        if converted:
            pos, neg = self._trace_sampler_prompts(converted)
            if not pos or not neg:
                harvest_pos, harvest_neg = self._collect_text_from_nodes(converted)
                if not pos:
                    pos = harvest_pos
                elif harvest_pos and self._is_completed_prompt_form(harvest_pos, pos):
                    pos = harvest_pos
                if not neg:
                    neg = harvest_neg
                elif harvest_neg and self._is_completed_prompt_form(harvest_neg, neg):
                    neg = harvest_neg
            if pos or neg:
                return (pos, neg)

        nodes = workflow.get("nodes", [])
        if not isinstance(nodes, list):
            return (None, None)

        synthetic: Dict[str, dict] = {}
        for node in nodes:
            if not isinstance(node, dict) or node.get("id") is None:
                continue
            synthetic[str(node["id"])] = {
                "class_type": str(node.get("type") or node.get("class_type") or ""),
                "inputs": {},
                "widgets_values": node.get("widgets_values"),
            }
        return self._collect_text_from_nodes(synthetic)

