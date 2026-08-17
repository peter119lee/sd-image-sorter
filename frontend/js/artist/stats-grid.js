/**
 * artist/stats-grid.js — artist-ident.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/artist-ident.js
 * pre-cut lines 171-320 (of 1,171): the "Data Loading" section comment,
 * loadStats (GET /api/artists/stats -> stat cards; a modal-analysis.js
 * seam) and renderArtistGrid (count-desc artist cards, grid/list mode,
 * empty states, card click/keyboard binding). Classic non-strict script:
 * joins the ONE unsealed window.ArtistIdent object declared in
 * artist/core.js, which loads FIRST; artist/boot.js runs the
 * DOMContentLoaded tail LAST.
 */
Object.assign(window.ArtistIdent, {
    // ============== Data Loading ==============

    async loadStats() {
        const statsEl = document.getElementById('artist-stats');
        if (!statsEl) return;
        const requestToken = ++this.statsRequestToken;

        try {
            const result = await window.App.API.get('/api/artists/stats');
            if (requestToken !== this.statsRequestToken) return;
            this.stats = result;

            // artist_counts / artist_stats are CONFIDENT-ONLY since 2c15c9e, so
            // "identified" would over-report by every unconfirmed row. The three
            // tiers are shown as three separate buckets instead of one number
            // that silently mixes a fact with a guess.
            const cards = [
                [this.tKey('artist.totalImages', 'Total Images', '总图片数'), Number(result.total_images) || 0],
                [this.tKey('artist.confidentMatches', 'Confident Matches', '高置信度匹配'), Number(result.confident_count) || 0],
                [this.tKey('artist.unconfirmed', 'Unconfirmed', '未确认候选'), Number(result.low_confidence_count) || 0],
                [this.tKey('artist.noMatch', 'No match', '没有匹配'), Number(result.undefined_count) || 0],
                [this.tKey('artist.artistsFound', 'Artists Found', '已发现画师'), Object.keys(result.artist_counts || {}).length],
            ].map(([label, value]) => {
                const card = document.createElement('div');
                card.className = 'stat-card';

                const number = document.createElement('span');
                number.className = 'stat-number';
                number.textContent = String(value);

                const text = document.createElement('span');
                text.className = 'stat-label';
                text.textContent = label;

                card.append(number, text);
                return card;
            });

            statsEl.replaceChildren(...cards);

            // Render artist grid
            this.renderArtistGrid(result.artist_counts || {}, this.viewMode);
            this.renderLowConfidenceArtists(result.low_confidence_artist_counts || {});
        } catch (e) {
            if (requestToken !== this.statsRequestToken) return;
            this.stats = {};
            const failureMessage = this.tKey(
                'artist.loadStatsFailed',
                'Failed to load stats',
                '加载统计失败'
            );
            const createFailureState = (containerClass, labelTag, labelClass) => {
                const container = document.createElement('div');
                container.className = containerClass;
                const label = document.createElement(labelTag);
                label.className = labelClass;
                label.textContent = failureMessage;
                label.dataset.i18nLocked = '1';
                container.appendChild(label);
                return container;
            };

            statsEl.replaceChildren(createFailureState('stat-card', 'span', 'stat-label'));
            document.getElementById('artist-results-grid')?.replaceChildren(
                createFailureState('empty-state', 'p', '')
            );
            this.renderLowConfidenceArtists({});
        }
    },


    renderArtistGrid(artistCounts, viewMode = this.viewMode) {
        const grid = document.getElementById('artist-results-grid');
        if (!grid) return;

        const normalizedViewMode = viewMode === 'list' ? 'list' : 'grid';
        this.viewMode = normalizedViewMode;
        grid.classList.toggle('list-mode', normalizedViewMode === 'list');

        document.querySelectorAll('.view-toggle .toggle-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === normalizedViewMode);
        });

        const entries = Object.entries(artistCounts).sort((a, b) => b[1] - a[1]);
        const escapeHtml = this._escapeHtml.bind(this);

        if (entries.length === 0) {
            const identifiedImages = Number(this.stats?.identified_images || 0);
            const hasRun = identifiedImages > 0;
            // Running again at a different slider value cannot produce a name:
            // the confident tier is fixed at 0.20 in the backend and the slider
            // can only tighten. The real cause is a vocabulary miss, so point at
            // the lookup instead of at another 80k-image pass.
            const emptyTitle = hasRun
                ? this.tKey(
                    'artist.emptyNoConfidentTitle',
                    'The run finished, but nothing reached a confident match.',
                    '识别跑完了，但没有任何结果达到高置信度。'
                )
                : this.tKey('artist.emptyNoRunTitle', 'No artists identified yet.', '还没有识别到画师。');
            const emptyHint = hasRun
                ? this.tKey(
                    'artist.emptyNoConfidentHint',
                    'Check whether the artist is in the model\u2019s vocabulary before running again \u2014 a name that is not in it can never be predicted.',
                    '再跑一次之前，先查一下这位画师在不在模型词表里 \u2014 不在词表里的名字永远不可能被预测出来。'
                )
                : this.tKey('artist.emptyNoRunHint', 'Click "Identify All Images" to start.', '点击“识别所有图片”开始。');
            grid.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon"><svg class="icon" aria-hidden="true"><use href="#i-palette"/></svg></div>
                    <p>${escapeHtml(emptyTitle)}</p>
                    <p class="empty-hint">${escapeHtml(emptyHint)}</p>
                </div>
            `;
            return;
        }

        const maxCount = entries[0][1];

        grid.innerHTML = entries.map(([artist, count], index) => {
            const encodedArtist = encodeURIComponent(String(artist ?? ''));
            const displayName = escapeHtml(this.formatArtistName(artist));
            const initials = escapeHtml(this.getInitials(artist));
            const countLabel = escapeHtml(String(count));
            const width = Math.max(0, Math.min(100, (count / maxCount) * 100));
            const stat = this.getArtistStat(artist);
            const avgConfidence = escapeHtml(this.formatConfidencePercent(stat.avg_confidence));
            const maxConfidence = escapeHtml(this.formatConfidencePercent(stat.max_confidence));
            const rankLabel = escapeHtml(`#${index + 1}`);

            if (normalizedViewMode === 'list') {
                return `
                <div class="artist-card artist-card-list" data-artist="${encodedArtist}" role="button" tabindex="0" aria-pressed="false">
                    <div class="artist-rank">${rankLabel}</div>
                    <div class="artist-avatar">${initials}</div>
                    <div class="artist-info">
                        <span class="artist-name">${displayName}</span>
                        <span class="artist-count">${countLabel} images</span>
                    </div>
                    <div class="artist-metrics">
                        <span class="artist-metric"><strong>${escapeHtml(this.tText('Avg', '平均'))}</strong> ${avgConfidence}</span>
                        <span class="artist-metric"><strong>${escapeHtml(this.tText('Peak', '最高'))}</strong> ${maxConfidence}</span>
                    </div>
                    <div class="artist-progress artist-progress-list" aria-hidden="true">
                        <span class="artist-bar" style="width: ${width}%"></span>
                    </div>
                </div>
            `;
            }

            return `
            <div class="artist-card" data-artist="${encodedArtist}" role="button" tabindex="0" aria-pressed="false">
                <div class="artist-avatar">${initials}</div>
                <div class="artist-info">
                    <span class="artist-name">${displayName}</span>
                    <span class="artist-count">${countLabel} images</span>
                    <span class="artist-confidence-summary">${escapeHtml(this.tText('Avg', '平均'))} ${avgConfidence} · ${escapeHtml(this.tText('Peak', '最高'))} ${maxConfidence}</span>
                </div>
                <div class="artist-progress" aria-hidden="true">
                    <span class="artist-bar" style="width: ${width}%"></span>
                </div>
            </div>
        `;
        }).join('');

        grid.querySelectorAll('.artist-card').forEach(card => {
            const activate = () => this.selectArtist(this._decodeArtistValue(card.dataset.artist));

            card.addEventListener('click', activate);
            card.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    activate();
                }
            });
        });
    },


    /**
     * The 0.03-0.20 bucket, kept out of "Top Artists" on purpose.
     *
     * Measured precision here is 28%, and 65% of these rows are an artist the
     * model does not know at all, so folding them back into the confident grid
     * would restore exactly the "guess presented as fact" the backend removed.
     * They are still worth showing: they are the user's own rows, including
     * every label the old un-tiered pipeline wrote.
     */
    renderLowConfidenceArtists(lowConfidenceCounts) {
        const container = document.getElementById('artist-low-confidence');
        if (!container) return;

        const escapeHtml = this._escapeHtml.bind(this);
        const entries = Object.entries(lowConfidenceCounts || {})
            .filter(([artist]) => !this._isUndefinedSentinel(artist))
            .sort((a, b) => b[1] - a[1]);

        if (entries.length === 0) {
            container.hidden = true;
            container.replaceChildren();
            return;
        }

        container.hidden = false;
        const total = entries.reduce((sum, [, count]) => sum + Number(count || 0), 0);
        container.innerHTML = `
            <h4 class="artist-low-confidence-title">
                <svg class="icon" aria-hidden="true"><use href="#i-alert"/></svg>
                <span>${escapeHtml(this.tKey(
                    'artist.unconfirmedSectionTitle',
                    'Unconfirmed candidates ({count})',
                    '低置信度候选（{count}）',
                    { count: total }
                ))}</span>
            </h4>
            <p class="artist-low-confidence-help">${escapeHtml(this.tKey(
                'artist.unconfirmedSectionHelp',
                'Below the confident threshold, so these are suggestions, not identifications. Most are wrong, usually because the real artist is not in the model\u2019s vocabulary.',
                '低于高置信度门槛，因此这些只是候选，不是识别结果。它们大多是错的，通常是因为真实画师不在模型词表里。'
            ))}</p>
            <div class="artist-low-confidence-list">
                ${entries.map(([artist, count]) => `
                    <button type="button" class="artist-candidate-chip" data-artist="${encodeURIComponent(String(artist ?? ''))}">
                        <span class="artist-candidate-name">${escapeHtml(this.formatArtistName(artist))}</span>
                        <span class="artist-candidate-count">${escapeHtml(String(count))}</span>
                    </button>
                `).join('')}
            </div>
        `;

        container.querySelectorAll('.artist-candidate-chip').forEach((chip) => {
            chip.addEventListener('click', () => {
                this.selectArtist(this._decodeArtistValue(chip.dataset.artist));
            });
        });
    },


});
