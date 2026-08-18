/**
 * Target base-model profiles for LoRA dataset prep (standing optimize
 * directive: evidence-based model support, frontend-first).
 *
 * The model you train FOR decides how captions should look, so the choice
 * lives at the top of the LoRA-setup card and drives:
 *   - the recommended per-image caption type (booru / both / nl), offered
 *     as an explicit one-click apply — never silently rewritten;
 *   - the token budget the Separation Console counter warns against.
 *
 * Evidence per profile (primary sources):
 *   sdxl  — CLIP text encoders take 77 tokens (75 usable): the budget the
 *           QW-1 counter always used for SD1.5/SDXL-family models.
 *   flux  — FLUX.1 conditions on T5-XXL with max_sequence_length 512
 *           (black-forest-labs/flux reference inference + diffusers
 *           FluxPipeline default); booru tags work but NL adds detail.
 *   krea2 — Krea 2 uses Qwen3-VL-4B-Instruct with a 512-token maximum
 *           and was trained predominantly on long natural-language captions
 *           (krea.ai/blog/krea-2-technical-report; krea-ai/krea-2 encoder.py
 *           and inference.py). The official repository directs LoRA users
 *           to train on krea/Krea-2-Raw and run on Turbo for inference.
 *   anima — Qwen3-class encoder with booru-tag-native vocabulary; the
 *           app's Anima export presets (@-prefixed triggers, category
 *           sections) already encode its conventions.
 */
(function () {
    'use strict';

    const DEFAULT_CAPTION_HELP_KEYS = Object.freeze([
        ['dataset-caption-help-intro', 'dataset.helpIntro'],
        ['dataset-caption-help-rule-1', 'dataset.helpRule1'],
        ['dataset-caption-help-rule-2', 'dataset.helpRule2'],
        ['dataset-caption-help-rule-3', 'dataset.helpRule3'],
        ['dataset-caption-help-rule-4', 'dataset.helpRule4'],
        ['dataset-caption-help-shortcut', 'dataset.helpShortcut'],
    ]);
    const KREA2_CAPTION_HELP_KEYS = Object.freeze([
        ['dataset-caption-help-intro', 'dataset.krea2HelpIntro'],
        ['dataset-caption-help-rule-1', 'dataset.krea2HelpRule1'],
        ['dataset-caption-help-rule-2', 'dataset.krea2HelpRule2'],
        ['dataset-caption-help-rule-3', 'dataset.krea2HelpRule3'],
        ['dataset-caption-help-rule-4', 'dataset.krea2HelpRule4'],
        ['dataset-caption-help-shortcut', 'dataset.krea2HelpShortcut'],
    ]);

    function refreshCaptionHelp(keys) {
        for (const [elementId, translationKey] of keys) {
            const element = document.getElementById(elementId);
            if (!element) continue;
            element.setAttribute('data-i18n', translationKey);
            element.textContent = window.I18n?.t?.(translationKey) || translationKey;
        }
    }

    function t(en, zh) {
        try {
            const lang = window.I18n?.getLang?.() || document.documentElement.lang || '';
            return String(lang).toLowerCase().startsWith('zh') ? zh : en;
        } catch (_) { return en; }
    }

    // Mirrors backend/services/caption_dialect.py CAPTION_DIALECT_TARGET_MODELS.
    // Only krea2 and anima have first-party evidence for a caption dialect;
    // sdxl and flux are deliberately un-opinionated there, so they carry no
    // `dialect` and every reader must treat a missing one as "no opinion"
    // rather than guessing. backend/tests/test_frontend_contract.py pins this
    // map to the backend's so the two cannot drift apart.
    const PROFILES = {
        sdxl: {
            captionType: 'booru',
            tokenBudget: 75,
            hint: () => t(
                'CLIP encoder: 75-token budget — concise booru tags, most important first.',
                'CLIP 编码器：75-token 预算 — 精简 booru 标签，重要的放前面。'),
            applyLabel: () => t('Set every image to Booru tags', '全部图片设为 Booru 标签'),
        },
        flux: {
            captionType: 'both',
            tokenBudget: 512,
            hint: () => t(
                'T5 encoder (512 tokens): tags + a natural-language sentence give the densest signal.',
                'T5 编码器（512 tokens）：标签 + 自然语言句子的组合信息最充分。'),
            applyLabel: () => t('Set every image to Both (tags + NL)', '全部图片设为 Both（标签+自然语言）'),
        },
        krea2: {
            captionType: 'nl',
            dialect: 'natural',
            captionProfile: 'krea2_long_nl',
            tokenBudget: 512,
            captionHelpKeys: KREA2_CAPTION_HELP_KEYS,
            hint: () => t(
                'Train LoRAs on krea/Krea-2-Raw, then run them on Krea 2 Turbo for inference. Its Qwen3-VL encoder supports 512 tokens, and training used predominantly long natural-language captions. Use Smart Tag\'s Krea 2 Long NL profile; tags remain useful for library search and review, not as a Krea 2 training tag-count target.',
                'LoRA 请在 krea/Krea-2-Raw 上训练，再用于 Krea 2 Turbo 推理。其 Qwen3-VL 编码器支持 512 tokens，训练数据主要采用自然语言长 caption。请使用 Smart Tag 的 Krea 2 长 NL 配置；标签仍适合图库检索与审核，但不是 Krea 2 训练的标签数量目标。'),
            applyLabel: () => t('Set every image to NL captions', '全部图片设为自然语言 caption'),
        },
        anima: {
            captionType: 'booru',
            dialect: 'tags',
            tokenBudget: 512,
            hint: () => t(
                'Booru-native vocabulary (Qwen3 encoder) — rich tag lists work; use the Anima export presets for @-triggers and sections.',
                'Booru 原生词表（Qwen3 编码器）— 标签列表友好；导出请配合 Anima 预设（@ 触发词与分区）。'),
            applyLabel: () => t('Set every image to Booru tags', '全部图片设为 Booru 标签'),
        },
    };

    const TargetModel = {
        get dm() { return window.DatasetMaker || null; },

        current() {
            return document.getElementById('dataset-target-model')?.value || '';
        },

        profile() {
            return PROFILES[this.current()] || null;
        },

        /** Token budget for caption counters; null = no target chosen (CLIP default applies). */
        tokenBudget() {
            const profile = this.profile();
            return profile ? profile.tokenBudget : null;
        },

        /** Caption dialect this target is documented to want, or null for
         *  "no evidenced opinion" (sdxl, flux, and no target chosen). */
        captionDialect() {
            return this.profile()?.dialect || null;
        },

        /** Smart Tag caption profile; null means use its normal purpose preset. */
        captionProfile() {
            const profile = this.profile();
            return profile?.captionProfile || null;
        },

        /**
         * The same two answers for an ARBITRARY target id, so a surface that is
         * not the Dataset Maker project (the Reverse Prompt page picks its own
         * target) can ask without reading `#dataset-target-model`. Applying the
         * project's setting to text it does not govern would be a claim about
         * text the setting never reaches, which a contract test forbids.
         */
        dialectFor(model) {
            return PROFILES[String(model || '')]?.dialect || null;
        },

        captionProfileFor(model) {
            return PROFILES[String(model || '')]?.captionProfile || null;
        },

        init() {
            const select = document.getElementById('dataset-target-model');
            if (!select) return;
            select.addEventListener('change', () => {
                this.refresh();
            });
            document.getElementById('btn-dataset-target-model-apply')
                ?.addEventListener('click', () => this.applyRecommendation());
            this.refresh();
        },

        refresh() {
            const hint = document.getElementById('dataset-target-model-hint');
            const apply = document.getElementById('btn-dataset-target-model-apply');
            const profile = this.profile();
            if (hint) hint.textContent = profile ? profile.hint() : '';
            refreshCaptionHelp(profile?.captionHelpKeys || DEFAULT_CAPTION_HELP_KEYS);
            if (apply) {
                apply.hidden = !profile;
                if (profile) apply.textContent = profile.applyLabel();
            }
        },

        applyRecommendation() {
            const dm = this.dm;
            const profile = this.profile();
            if (!dm || !profile) return;
            if (typeof dm._applyCaptionTypeToScope !== 'function') {
                window.App?.showToast?.(t('Load a dataset queue first', '请先建立数据集队列'), 'info');
                return;
            }
            dm._applyCaptionTypeToScope(profile.captionType, 'all');
        },
    };

    window.TargetModel = TargetModel;
    document.addEventListener('languageChanged', () => TargetModel.refresh());
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => TargetModel.init());
    } else {
        TargetModel.init();
    }
})();
