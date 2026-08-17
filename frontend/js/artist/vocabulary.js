/**
 * artist/vocabulary.js — "is my artist supported?" lookup (GET /api/artists/vocabulary).
 *
 * The model can only ever answer with a name that is in its vocabulary. On a
 * sampled set of this library's artists only 37% were in the 39,261-name
 * vocabulary, which means most identification runs were always going to name
 * somebody else. Nothing in the product said so, and the nearest wrong match is
 * strictly less informative than "this model does not know that name".
 *
 * The lookup deliberately sits in the left control column ABOVE the run
 * buttons: it answers the question that decides whether starting a run on those
 * images can produce anything at all, so it belongs before the run, not in the
 * results panel after it.
 *
 * Classic non-strict script: joins the ONE unsealed window.ArtistIdent object
 * declared in artist/core.js, which loads FIRST; artist/boot.js runs the
 * DOMContentLoaded tail LAST.
 */
Object.assign(window.ArtistIdent, {
    _parseVocabularyQuery(rawValue) {
        return String(rawValue ?? '')
            .split(/[\n,、，]+/)
            .map((name) => name.trim())
            .filter(Boolean)
            .filter((name, index, all) => all.indexOf(name) === index)
            .slice(0, 20);
    },

    _renderVocabularyStatus(nodes) {
        const container = document.getElementById('artist-vocabulary-result');
        if (!container) return;
        container.replaceChildren(...nodes);
    },

    _vocabularySizeLine() {
        const line = document.createElement('p');
        line.className = 'artist-vocabulary-size';
        line.dataset.i18nLocked = '1';
        line.textContent = this.vocabulary.loaded
            ? this.tKey(
                'artist.vocabularySize',
                'This model can name {count} artists.',
                '这个模型总共认识 {count} 位画师。',
                { count: Number(this.vocabulary.size || 0).toLocaleString() }
            )
            : this.tKey(
                'artist.vocabularyNotLoaded',
                'The artist model is not loaded yet, so its vocabulary cannot be read. Run an identification once, then check again.',
                '画师模型还没有载入，读不到它的词表。先跑一次识别，然后再查。'
            );
        return line;
    },

    /**
     * Read the vocabulary size without asking about any name. Safe on view
     * entry: it never loads a model, it only reports whether one is loaded.
     */
    async refreshVocabularyState() {
        try {
            const result = await window.App.API.get('/api/artists/vocabulary');
            this.vocabulary = {
                size: Number(result?.vocabulary_size || 0),
                loaded: !!result?.vocabulary_loaded,
            };
        } catch (e) {
            this.vocabulary = { size: 0, loaded: false };
            Logger.warn('Failed to read the artist vocabulary state:', e);
        }
        this._renderVocabularyStatus([this._vocabularySizeLine()]);
    },

    async checkArtistVocabulary() {
        const input = document.getElementById('artist-vocabulary-input');
        const button = document.getElementById('btn-artist-vocabulary-check');
        if (!input) return;

        const names = this._parseVocabularyQuery(input.value);
        if (names.length === 0) {
            const hint = document.createElement('p');
            hint.className = 'artist-vocabulary-hint';
            hint.dataset.i18nLocked = '1';
            hint.textContent = this.tKey(
                'artist.vocabularyEmptyInput',
                'Type one or more artist names, separated by commas.',
                '请输入一个或多个画师名，用逗号分隔。'
            );
            this._renderVocabularyStatus([hint]);
            return;
        }

        if (button) button.disabled = true;
        try {
            const query = new URLSearchParams();
            names.forEach((name) => query.append('name', name));
            const result = await window.App.API.get(`/api/artists/vocabulary?${query.toString()}`);
            this.vocabulary = {
                size: Number(result?.vocabulary_size || 0),
                loaded: !!result?.vocabulary_loaded,
            };

            const nodes = [this._vocabularySizeLine()];
            const known = (result && typeof result.known === 'object' && result.known) || {};
            names.forEach((name) => {
                const row = document.createElement('p');
                const isKnown = known[name] === true;
                row.className = `artist-vocabulary-row ${isKnown ? 'is-known' : 'is-unknown'}`;
                row.dataset.i18nLocked = '1';
                row.textContent = this.vocabulary.loaded
                    ? this.tKey(
                        isKnown ? 'artist.vocabularyKnown' : 'artist.vocabularyUnknown',
                        isKnown
                            ? '{artist} — in this model\u2019s vocabulary.'
                            : '{artist} — not in this model\u2019s vocabulary, so it can never be predicted.',
                        isKnown
                            ? '{artist} —— 在这个模型的词表里。'
                            : '{artist} —— 不在这个模型的词表里，所以永远不会被预测出来。',
                        { artist: name }
                    )
                    : name;
                nodes.push(row);
            });
            this._renderVocabularyStatus(nodes);
        } catch (e) {
            const failure = document.createElement('p');
            failure.className = 'artist-vocabulary-row is-unknown';
            failure.dataset.i18nLocked = '1';
            failure.textContent = formatUserError(
                e,
                this.tKey('artist.vocabularyFailed', 'Vocabulary lookup failed', '词表查询失败')
            );
            this._renderVocabularyStatus([failure]);
        } finally {
            if (button) button.disabled = false;
        }
    },

});
