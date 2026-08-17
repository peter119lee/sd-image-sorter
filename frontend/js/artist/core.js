/**
 * artist/core.js — artist-ident.js decomposition (family base; MUST LOAD FIRST).
 * Moved BYTE-IDENTICAL from frontend/js/artist-ident.js pre-cut lines 1-61 +
 * 134-137 + 155-159 + 321-349 + 496-508 + 1165-1168 (of 1,171): the file
 * header, `const ArtistIdent = {` + every state field + thresholdDefaults,
 * tText/tKey/localizeDiagnosticsMessage, getArtistStat,
 * formatConfidencePercent, getInitials/formatArtistName,
 * _escapeHtml/_decodeArtistValue, the object-literal `};` closer and the
 * `window.ArtistIdent = ArtistIdent;` publish. Declares the ONE unsealed
 * object every other artist/*.js file Object.assign()s onto — this file
 * must load before the rest of the family; artist/boot.js runs the
 * DOMContentLoaded tail LAST. No 'use strict' anywhere in the family: the
 * original was a non-strict classic script (similar.js precedent); bare
 * `Logger` / `formatUserError` globals resolve via the shared
 * classic-script scope.
 */
/**
 * SD Image Sorter - Artist Identification Module
 * Identifies artist/style of images using LSNet-style classification.
 */

const ArtistIdent = {
    isIdentifying: false,
    selectedArtist: null,
    selectedArtistPageSize: 120,
    selectedArtistOffset: 0,
    selectedArtistHasMore: false,
    selectedArtistImages: [],
    artistRequestToken: 0,
    statsRequestToken: 0,
    viewMode: 'grid',
    stats: {},
    diagnostics: null,
    eventsBound: false,
    progressTracker: null,
    // `value` is the slider default (ARTIST_THRESHOLD_DEFAULT). `confident` is
    // ARTIST_CONFIDENT_THRESHOLD: the score at or above which the backend is
    // willing to write a name. The slider can only tighten, never reach below
    // `confident` to have a guess asserted, so the old suggested 0.02-0.08
    // "try a lower threshold" band no longer describes anything the app does.
    thresholdDefaults: {
        value: 0.03,
        confident: 0.20,
    },
    vocabulary: {
        size: 0,
        loaded: false,
    },

    tText(enText, zhText) {
        return window.I18n?.getLang?.() === 'zh-CN' ? zhText : enText;
    },

    tKey(key, enText, zhText = enText, params = null) {
        const translated = window.I18n?.t?.(key, params || undefined);
        if (translated && translated !== key) return translated;
        let fallback = this.tText(enText, zhText);
        if (params && typeof params === 'object') {
            Object.entries(params).forEach(([token, value]) => {
                fallback = fallback.replaceAll(`{${token}}`, String(value));
            });
        }
        return fallback;
    },

    localizeDiagnosticsMessage(message) {
        const raw = String(message || '').trim();
        if (!raw) return '';

        if (raw === 'Kaloscope runtime is ready.') {
            return this.tText(raw, 'Kaloscope 运行环境已就绪。');
        }
        if (raw === 'Artist identification still needs the LSNet runtime, Kaloscope files, or Python dependencies.') {
            return this.tText(raw, '还缺少 LSNet / Kaloscope / Python 依赖。');
        }
        if (raw === "On Windows, comfyui-lsnet may log 'SkaFn failed; falling back to PyTorchSkaFn'. That fallback is usually okay if artist predictions still appear.") {
            return this.tText(
                raw,
                'Windows 下若出现 “SkaFn failed; falling back to PyTorchSkaFn”，但结果仍能出来，通常可以先忽略。'
            );
        }

        return raw;
    },

    _getExplicitGallerySelectionIds() {
        const selectedIds = window.AppFilterAccess?.getSelectedImageIds?.();
        return Array.isArray(selectedIds)
            ? selectedIds
                .map((id) => Number(id))
                .filter((id) => Number.isFinite(id) && id > 0)
            : [];
    },

    getArtistStat(artist) {
        return this.stats?.artist_stats?.[artist] || { count: 0, avg_confidence: 0, max_confidence: 0 };
    },

    formatConfidencePercent(value) {
        const numeric = Number(value || 0);
        return `${(numeric * 100).toFixed(1)}%`;
    },

    getInitials(name) {
        const safeName = String(name ?? '').trim();
        if (!safeName || safeName === 'undefined') return '?';

        const parts = safeName
            .replace(/_/g, ' ')
            .split(/\s+/)
            .filter(Boolean);

        if (parts.length === 0) return '?';
        if (parts.length === 1) {
            return parts[0].substring(0, 2).toUpperCase();
        }

        return parts.slice(0, 2).map(p => p[0].toUpperCase()).join('');
    },

    formatArtistName(name) {
        const safeName = String(name ?? '').trim();
        // "undefined" is the backend's no-name sentinel, not an artist. Echoing
        // it (even title-cased) is how a refusal to answer used to read as an
        // answer, so it renders as the localized no-match label instead.
        if (!safeName || safeName === 'undefined') {
            return this.tKey('artist.noMatch', 'No match', '没有匹配');
        }

        return safeName
            .replace(/_/g, ' ')
            .split(/\s+/)
            .filter(Boolean)
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ');
    },

    _isUndefinedSentinel(value) {
        const normalized = String(value ?? '').trim().toLowerCase();
        return !normalized || normalized === 'undefined';
    },

    /**
     * Backend advisories ship both languages in one "EN / ZH" string so the
     * API stays language-agnostic. Show the half that matches the UI.
     */
    _pickAdvisoryLanguage(text) {
        const raw = String(text ?? '').trim();
        if (!raw) return '';
        const parts = raw.split(/\s\/\s(?=[\u4e00-\u9fff])/);
        if (parts.length < 2) return raw;
        return this.tText(parts[0].trim(), parts.slice(1).join(' / ').trim());
    },

    /**
     * Local mirror of artist_identifier.artist_confidence_advisory. Batch
     * results carry only the tier, not the sentence, so the batch UI would
     * otherwise have nothing to explain the tier with.
     */
    _fallbackAdvisory(level, vocabularySize) {
        const size = Number(vocabularySize || this.vocabulary?.size || 0);
        const vocabEn = size ? `${size.toLocaleString()}-artist vocabulary` : 'artist vocabulary';
        const vocabZh = size ? `${size.toLocaleString()} 位画师的词表` : '画师词表';
        if (level === 'high') {
            return this.tText(
                'Confident match. Measured on a ground-truth sample, about 1 in 13 matches at this confidence is still wrong.',
                '高置信度匹配。在实测样本中，这一档仍约有 1/13 是错的。'
            );
        }
        if (level === 'low') {
            return this.tText(
                `Unconfirmed suggestion, not an identification. Most results at this confidence are wrong, usually because the real artist is not in the model's ${vocabEn}. Confirm it yourself before trusting it.`,
                `这只是低置信度候选，不是识别结果。这一档大多数是错的，通常是因为真实画师不在模型的${vocabZh}里。请自行确认后再使用。`
            );
        }
        return this.tText(
            `No match. The artist is probably not in this model's ${vocabEn}, so no name is offered rather than naming the closest wrong one.`,
            `没有匹配。该画师大概率不在此模型的${vocabZh}内，因此不提供任何名字，而不是给出最接近的错误名字。`
        );
    },

    /**
     * The ONE place a /api/artists/identify or batch result becomes display
     * text. `artist` is the sentinel "undefined" unless confidence_level is
     * "high", so every caller that reads `artist` directly puts the sentinel
     * on screen for the two lower tiers.
     *
     *   high (>=0.20, 92% precision)  -> a name, plus the score.
     *   low  (0.03-0.20, 28% precision, 65% out-of-vocabulary)
     *                                 -> candidate_artist as an explicitly
     *                                    unconfirmed suggestion, never "the artist".
     *   none (<0.03, 2% precision, 97% out-of-vocabulary)
     *                                 -> the advisory, no name at all.
     */
    describeArtistResult(result = {}) {
        const source = result && typeof result === 'object' ? result : {};
        const confidence = Number(source.confidence || 0);
        const rawLevel = String(source.confidence_level || '').trim().toLowerCase();
        const level = ['high', 'low', 'none'].includes(rawLevel)
            ? rawLevel
            : (confidence >= this.thresholdDefaults.confident
                ? 'high'
                : (confidence >= this.thresholdDefaults.value ? 'low' : 'none'));

        const artistRaw = source.artist;
        const candidateRaw = this._isUndefinedSentinel(source.candidate_artist)
            ? (this._isUndefinedSentinel(artistRaw) ? '' : String(artistRaw).trim())
            : String(source.candidate_artist).trim();

        const confirmedName = level === 'high' && !this._isUndefinedSentinel(artistRaw)
            ? this.formatArtistName(artistRaw)
            : null;
        const candidateName = level === 'low' && candidateRaw
            ? this.formatArtistName(candidateRaw)
            : null;

        const vocabularySize = Number(source.vocabulary_size || 0) || Number(this.vocabulary?.size || 0);
        const advisory = this._pickAdvisoryLanguage(source.advisory)
            || this._fallbackAdvisory(level, vocabularySize);
        const percent = this.formatConfidencePercent(confidence);

        let headline;
        if (level === 'high' && confirmedName) {
            headline = this.tKey(
                'artist.tierHighHeadline',
                '{artist} · {percent} confident',
                '{artist} · 置信度 {percent}',
                { artist: confirmedName, percent }
            );
        } else if (level === 'low' && candidateName) {
            headline = this.tKey(
                'artist.tierLowHeadline',
                'Unconfirmed candidate: {artist} · {percent}',
                '低置信度候选：{artist} · {percent}',
                { artist: candidateName, percent }
            );
        } else {
            headline = this.tKey('artist.noMatch', 'No match', '没有匹配');
        }

        return {
            level,
            confidence,
            percent,
            artistName: confirmedName,
            candidateName,
            displayName: confirmedName || candidateName || null,
            advisory,
            vocabularySize,
            outOfVocabularyLikely: source.out_of_vocabulary_likely === undefined
                ? level !== 'high'
                : !!source.out_of_vocabulary_likely,
            tierLabel: this.confidenceTierLabel(level),
            headline,
        };
    },

    confidenceTierLabel(level) {
        if (level === 'high') return this.tKey('artist.tierHigh', 'Confident', '高置信度');
        if (level === 'low') return this.tKey('artist.tierLow', 'Unconfirmed', '未确认');
        return this.tKey('artist.tierNone', 'No match', '没有匹配');
    },

    _escapeHtml(value) {
        // Delegate to global escapeHtml from modules/utils/escape.js
        return window.escapeHtml(value);
    },

    _decodeArtistValue(value) {
        try {
            return decodeURIComponent(String(value ?? ''));
        } catch (e) {
            return String(value ?? '');
        }
    },

};

// Export
window.ArtistIdent = ArtistIdent;
