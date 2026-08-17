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

            const cards = [
                [this.tText('Total Images', '总图片数'), Number(result.total_images) || 0],
                [this.tText('Identified', '已识别'), Number(result.identified_images) || 0],
                [this.tText('Undefined', '未定义'), Number(result.undefined_count) || 0],
                [this.tText('Artists Found', '发现画师'), Object.keys(result.artist_counts || {}).length],
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

        if (entries.length === 0) {
            const identifiedImages = Number(this.stats?.identified_images || 0);
            const undefinedCount = Number(this.stats?.undefined_count || 0);
            const currentThreshold = this.getThresholdValue().toFixed(2);
            const allUndefined = identifiedImages > 0 && undefinedCount >= identifiedImages;
            const emptyTitle = allUndefined
                ? this.tText('Kaloscope finished, but every result fell below the threshold.', 'Kaloscope 已经跑完了，但结果全部低于当前阈值。')
                : this.tText('No artists identified yet.', '还没有识别到画师。');
            const emptyHint = allUndefined
                ? this.tText(
                    `Lower the threshold from ${currentThreshold} to around ${this.thresholdDefaults.suggestedLow.toFixed(2)}-${this.thresholdDefaults.suggestedHigh.toFixed(2)} and run again.`,
                    `把阈值从 ${currentThreshold} 降到大约 ${this.thresholdDefaults.suggestedLow.toFixed(2)}-${this.thresholdDefaults.suggestedHigh.toFixed(2)}，再跑一次。`
                )
                : this.tText('Click "Identify All Images" to start.', '点击“识别所有图片”开始。');
            grid.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon"><svg class="icon" aria-hidden="true"><use href="#i-palette"/></svg></div>
                    <p>${emptyTitle}</p>
                    <p class="empty-hint">${emptyHint}</p>
                </div>
            `;
            return;
        }

        const escapeHtml = this._escapeHtml.bind(this);
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


});
