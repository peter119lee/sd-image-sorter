/**
 * smart-tag/ollama-banner.js — smart-tag.js decomposition.
 * Extracted VERBATIM from frontend/js/smart-tag.js pre-split lines
 * 200-306: ensureSmartTagStyles (lazy inline stylesheet),
 * ensureOllamaWarningBanner and refreshOllamaWarning (cloud-endpoint-
 * skips-banner bugfix). The open-VLM-settings click handler here is
 * intentionally duplicated with the one in smart-tag/boot.js
 * (pre-split 253-259 vs 1145-1151) — do NOT DRY it as part of a
 * verbatim split. Classic script; family renames applied.
 */
'use strict';
    function ensureSmartTagStyles() {
        if (document.getElementById('smart-tag-inline-styles')) return;
        const style = document.createElement('style');
        style.id = 'smart-tag-inline-styles';
        style.textContent = `
.smart-tag-ollama-warning {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.6rem 0.85rem;
    margin: 0 0 0.75rem 0;
    background: rgba(var(--accent-rgb), 0.12);
    border: 1px solid rgba(var(--accent-rgb), 0.35);
    border-radius: 8px;
    color: var(--text-primary, #f0f0f0);
    font-size: 0.9rem;
    line-height: 1.35;
}
.smart-tag-ollama-warning .smart-tag-ollama-icon { font-size: 1.1rem; flex: 0 0 auto; }
.smart-tag-ollama-warning .smart-tag-ollama-text { flex: 1 1 auto; }
.smart-tag-ollama-warning .smart-tag-ollama-action { flex: 0 0 auto; }
.smart-tag-tagger-help {
    display: block;
    margin-top: 0.25rem;
    font-size: 0.8rem;
    font-style: italic;
    color: var(--text-muted, rgba(255, 255, 255, 0.6));
}
`;
        document.head.appendChild(style);
    }

    function ensureOllamaWarningBanner() {
        let banner = document.getElementById('smart-tag-ollama-warning');
        if (banner) return banner;
        const naturalSection = document.getElementById('smart-tag-natural-section');
        if (!naturalSection || !naturalSection.parentNode) return null;
        ensureSmartTagStyles();
        banner = document.createElement('div');
        banner.id = 'smart-tag-ollama-warning';
        banner.className = 'smart-tag-ollama-warning';
        banner.hidden = true;
        banner.innerHTML = `
            <span class="smart-tag-ollama-icon" aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-alert"/></svg></span>
            <span class="smart-tag-ollama-text">
                自然语言描述器尚未配置 — 请打开 VLM 设置，填入云端 API 端点（如 OpenAI / OpenRouter / Gemini），或确认本地 Ollama 正在运行。<br>
                No natural-language captioner configured — open VLM Settings to add a cloud API endpoint (OpenAI / OpenRouter / Gemini, etc.), or start a local Ollama.
            </span>
            <button type="button" class="btn btn-small btn-primary smart-tag-ollama-action" id="btn-smart-tag-open-vlm-from-warning">
                Open VLM Settings
            </button>
        `;
        naturalSection.parentNode.insertBefore(banner, naturalSection);
        banner.querySelector('#btn-smart-tag-open-vlm-from-warning')?.addEventListener('click', () => {
            if (typeof window.App?.openVlmSettings === 'function') {
                window.App.openVlmSettings();
            } else {
                document.getElementById('btn-vlm-settings')?.click();
            }
        });
        return banner;
    }

    async function refreshOllamaWarning() {
        const banner = ensureOllamaWarningBanner();
        if (!banner) return;
        const naturalEnabled = !!smartTag$('#smart-tag-enable-vlm')?.checked;
        const nlMode = smartTag$('#smart-tag-nl-mode')?.value || 'vlm';
        // Only relevant when the user actually plans to use the
        // VLM-via-endpoint path. ToriiGate runs in-process and doesn't
        // care about the Ollama daemon.
        if (!naturalEnabled || nlMode !== 'vlm') {
            banner.hidden = true;
            return;
        }
        // A configured VLM endpoint (cloud API such as OpenAI / OpenRouter /
        // aihubmix / Anthropic / Gemini, or any local server) OR Vertex means
        // the captioner does NOT depend on Ollama — so the "Ollama required"
        // banner must not fire. The banner is only for the truly-unconfigured
        // case where the implicit default would be a local Ollama daemon.
        //
        // Bug fix: this used to query ONLY /api/vlm/local-models/recommended,
        // so a user who had pointed Smart Tag at a cloud API was still nagged
        // to install / start Ollama (the API had in fact tagged their images).
        try {
            const settings = await getJson('/api/vlm/settings');
            const endpoint = String(settings?.endpoint || '').trim();
            if (endpoint.length > 0 || settings?.use_vertex === true) {
                banner.hidden = true;
                return;
            }
        } catch (_err) {
            // Couldn't read settings — fall through to the Ollama probe rather
            // than assuming a cloud captioner is configured.
        }
        try {
            const data = await getJson('/api/vlm/local-models/recommended');
            const unavailable = !data?.ollama_installed || !data?.ollama_running;
            banner.hidden = !unavailable;
        } catch (_err) {
            // No configured endpoint AND the Ollama probe failed — we can't
            // confirm any captioner is reachable, so show the banner with a
            // path to fix it.
            banner.hidden = false;
        }
    }

