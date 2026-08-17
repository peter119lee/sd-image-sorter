/**
 * artist/detail.js — artist-ident.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/artist-ident.js
 * pre-cut lines 592-763 (of 1,171): selectArtist (per-artist detail +
 * paged preview cards + the artistRequestToken stale-response guard),
 * filterGalleryByArtist and clearArtistFilter (the sanctioned
 * App.updateFilters -> switchView(gallery) handoff). Classic non-strict
 * script: joins the ONE unsealed window.ArtistIdent object declared in
 * artist/core.js, which loads FIRST; artist/boot.js runs the
 * DOMContentLoaded tail LAST.
 */
Object.assign(window.ArtistIdent, {
    async selectArtist(artist, { append = false } = {}) {
        const safeArtist = String(artist ?? '');
        const escapeHtml = this._escapeHtml.bind(this);
        const isSameArtist = this.selectedArtist === safeArtist;
        if (!append || !isSameArtist) {
            this.selectedArtistOffset = 0;
            this.selectedArtistHasMore = false;
            this.selectedArtistImages = [];
        }
        this.selectedArtist = safeArtist;
        this.artistRequestToken += 1;
        const requestToken = this.artistRequestToken;
        const requestOffset = append && isSameArtist ? this.selectedArtistOffset : 0;

        // Highlight selected card
        document.querySelectorAll('.artist-card').forEach(card => {
            const isSelected = this._decodeArtistValue(card.dataset.artist) === safeArtist;
            card.classList.toggle('selected', isSelected);
            card.setAttribute('aria-pressed', String(isSelected));
        });

        // Load artist's images
        const detailContent = document.getElementById('artist-detail-content');
        const imagesPreview = document.getElementById('artist-images-preview');
        if (!detailContent || !imagesPreview) return;

        detailContent.innerHTML = `<p class="loading">Loading images for ${escapeHtml(this.formatArtistName(safeArtist))}...</p>`;

        try {
            // Get count from stats. artist_counts is confident-only since
            // 2c15c9e, so an unconfirmed candidate opened from the low-
            // confidence list would otherwise report "0 images".
            const confidentCount = Number(this.stats.artist_counts?.[safeArtist] || 0);
            const candidateCount = Number(this.stats.low_confidence_artist_counts?.[safeArtist] || 0);
            const count = confidentCount || candidateCount;
            const isCandidateOnly = confidentCount === 0 && candidateCount > 0;
            const stat = this.getArtistStat(safeArtist);
            const artistLabel = escapeHtml(this.formatArtistName(safeArtist));
            const countLabel = escapeHtml(String(count));
            const avgConfidence = escapeHtml(this.formatConfidencePercent(stat.avg_confidence));
            const maxConfidence = escapeHtml(this.formatConfidencePercent(stat.max_confidence));
            const detailResponse = await window.App.API.get(
                `/api/artists/images/${encodeURIComponent(safeArtist)}?limit=${this.selectedArtistPageSize}&offset=${requestOffset}`
            );
            if (requestToken !== this.artistRequestToken) return;

            const pageImages = Array.isArray(detailResponse.images) ? detailResponse.images : [];
            this.selectedArtistImages = append
                ? [...this.selectedArtistImages, ...pageImages]
                : pageImages;
            this.selectedArtistOffset = requestOffset + pageImages.length;
            this.selectedArtistHasMore = Boolean(detailResponse.has_more);

            const previewCards = this.selectedArtistImages.map((image) => {
                // Rows below the confident threshold keep the artist's name in
                // the database (nothing was rewritten), so without a badge an
                // unconfirmed row is indistinguishable from a confident one.
                const level = String(image.confidence_level || '').toLowerCase();
                const tierBadge = level === 'high'
                    ? ''
                    : `<span class="artist-image-tier artist-image-tier-${level === 'low' ? 'low' : 'none'}">${escapeHtml(this.confidenceTierLabel(level === 'low' ? 'low' : 'none'))}</span>`;
                return `
                <button class="artist-image-card" data-image-id="${image.image_id}" data-filename="${escapeHtml(image.filename)}" type="button" title="${escapeHtml(image.filename)}">
                    <img src="${window.App.API.getThumbnailUrl(image.image_id, 256)}" alt="${escapeHtml(image.filename)}" loading="lazy">
                    <span class="artist-image-confidence">${escapeHtml(String(image.confidence_percent))}%</span>
                    ${tierBadge}
                    <span class="artist-image-name">${escapeHtml(image.filename)}</span>
                    <span class="artist-image-actions">
                        <span class="artist-image-action" data-action="preview">${escapeHtml(this.tText('Preview', '预览'))}</span>
                        <span class="artist-image-action" data-action="reader">${escapeHtml(this.tText('Reader', 'Reader'))}</span>
                        <span class="artist-image-action" data-action="edit">${escapeHtml(this.tText('Edit', '编辑'))}</span>
                        <span class="artist-image-action" data-action="build">${escapeHtml(this.tText('Build', 'Build'))}</span>
                    </span>
                </button>
            `;
            }).join('');

            const candidateNotice = isCandidateOnly
                ? `<p class="artist-detail-advisory">${escapeHtml(this._fallbackAdvisory('low', this.vocabulary?.size))}</p>`
                : '';
            const candidateBadge = isCandidateOnly
                ? ` <span class="artist-tier-badge artist-tier-badge-low">${escapeHtml(this.tKey('artist.candidateBadge', 'Unconfirmed', '未确认'))}</span>`
                : '';

            detailContent.innerHTML = `
                <h4>${artistLabel}${candidateBadge}</h4>
                <p class="artist-stats-detail">${countLabel} ${escapeHtml(isCandidateOnly
                    ? this.tKey('artist.candidateImagesLabel', 'images suggested for this name', '张图被猜成这个名字')
                    : this.tKey('artist.confidentImagesLabel', 'images identified', '张图匹配到该画师'))}</p>
                <p class="artist-stats-detail">${escapeHtml(this.tKey('artist.avgConfidence', 'Average confidence', '平均置信度'))} ${avgConfidence}</p>
                <p class="artist-stats-detail">${escapeHtml(this.tKey('artist.peakConfidence', 'Peak confidence', '最高置信度'))} ${maxConfidence}</p>
                ${candidateNotice}
            `;

            // Show action button
            imagesPreview.innerHTML = `
                <div class="preview-placeholder">
                    <button class="btn btn-primary btn-small" id="btn-filter-by-artist">
                        <svg class="icon" aria-hidden="true"><use href="#i-search"/></svg> ${escapeHtml(this.tText(`View ${countLabel} images in Gallery`, `在图库中查看这 ${countLabel} 张图`))}
                    </button>
                    <button class="btn btn-ghost btn-small" id="btn-clear-artist-filter" style="margin-top: 8px;">
                        <svg class="icon" aria-hidden="true"><use href="#i-close"/></svg> ${escapeHtml(this.tText('Clear Artist Filter', '清除画师筛选'))}
                    </button>
                </div>
                <div class="artist-images-grid">
                    ${previewCards || `<div class="empty-state"><p>${this.tText('No preview images available yet.', '暂时还没有可预览的图片。')}</p></div>`}
                </div>
            `;

            // Bind filter button
            document.getElementById('btn-filter-by-artist')?.addEventListener('click', () => {
                this.filterGalleryByArtist(safeArtist);
            });

            // Bind clear filter button
            document.getElementById('btn-clear-artist-filter')?.addEventListener('click', () => {
                this.clearArtistFilter();
            });

            imagesPreview.querySelectorAll('.artist-image-card').forEach(card => {
                card.addEventListener('click', (event) => {
                    const imageId = Number(card.dataset.imageId);
                    const filename = String(card.dataset.filename || '');
                    if (!Number.isFinite(imageId)) return;

                    const action = event.target?.dataset?.action;
                    if (action === 'reader') {
                        window.App?.openReaderFromImage?.(imageId, filename);
                        return;
                    }
                    if (action === 'edit') {
                        window.App?.addToCensorQueue?.([imageId]);
                        return;
                    }
                    if (action === 'build') {
                        window.App?.openPromptBuildFromImage?.(imageId);
                        return;
                    }

                    if (window.Gallery?.openPreview) {
                        window.Gallery.openPreview(imageId);
                    }
                });
            });

            const loadMoreBtn = document.getElementById('btn-artist-load-more');
            if (loadMoreBtn) {
                loadMoreBtn.style.display = this.selectedArtistHasMore ? 'inline-flex' : 'none';
            }

        } catch (e) {
            detailContent.innerHTML = `<p class="error">${this.tText('Failed to load artist details', '加载画师详情失败')}</p>`;
        }
    },


    filterGalleryByArtist(artist) {
        if (!window.App?.AppState?.filters) return;

        window.App.updateFilters?.((filters) => {
            filters.artist = artist;
        });
        if (typeof window.App.updateFilterSummary === 'function') {
            window.App.updateFilterSummary();
        }
        if (typeof window.App.switchView === 'function') {
            window.App.switchView('gallery');
        }
        if (typeof window.App.loadImages === 'function') {
            window.App.loadImages();
        }

        window.App.showToast(
            this.tText(`Filtering by artist: ${this.formatArtistName(artist)}`, `正在按画师筛选：${this.formatArtistName(artist)}`),
            'success'
        );
    },

    clearArtistFilter() {
        if (!window.App?.AppState?.filters) return;

        window.App.updateFilters?.((filters) => {
            filters.artist = null;
        });
        if (typeof window.App.updateFilterSummary === 'function') {
            window.App.updateFilterSummary();
        }
        if (typeof window.App.switchView === 'function') {
            window.App.switchView('gallery');
        }
        if (typeof window.App.loadImages === 'function') {
            window.App.loadImages();
        }

        window.App.showToast(this.tText('Artist filter cleared', '已清除画师筛选'), 'info');
    },

});
