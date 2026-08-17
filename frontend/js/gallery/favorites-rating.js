/**
 * gallery/favorites-rating.js — gallery.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/gallery.js pre-cut
 * lines 199-375 (of 4,708): generator colors + favorites (hearts) + user star rating.
 * Classic script: joins the ONE unsealed window.Gallery object declared in
 * gallery/base.js, which loads FIRST (the shared consts + module helpers
 * live there); index.html lists the family in original line order.
 */
Object.assign(window.Gallery, {
    /**
     * Get generator color map (uses global override if available)
     * @returns {Object} Generator color mapping
     */
    _getGenColors() {
        return window.GENERATOR_COLORS || DEFAULT_GENERATOR_COLORS;
    },

    // v3.3.0 FEAT-COLLECTIONS: favorite (heart) state + toggle.
    async hydrateFavorites() {
        try {
            const result = await window.App?.API?.getFavoriteIds?.();
            const ids = Array.isArray(result?.image_ids) ? result.image_ids : [];
            this.favoriteIds = new Set(ids.map((id) => Number(id)).filter((id) => Number.isFinite(id)));
            this._applyFavoriteStateToDom();
        } catch (error) {
            window.App?.Logger?.warn?.('Failed to hydrate favorites:', error);
        }
    },

    isFavorited(imageId) {
        return this.favoriteIds.has(Number(imageId));
    },

    _favoriteButtonHtml(image) {
        const id = Number(image?.id);
        const on = this.favoriteIds.has(id);
        const title = this._t('collections.favoriteToggle', null, 'Favorite');
        return `<button type="button" class="gallery-item-fav${on ? ' is-favorited' : ''}" `
            + `data-fav-id="${id}" aria-pressed="${on ? 'true' : 'false'}" `
            + `title="${this._escapeHtml(title)}" aria-label="${this._escapeHtml(title)}">`
            + `<span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-heart"/></svg></span></button>`;
    },

    _applyFavoriteStateToDom() {
        document.querySelectorAll('#gallery-grid .gallery-item[data-id]').forEach((item) => {
            const id = Number(item.dataset.id);
            const btn = item.querySelector('.gallery-item-fav');
            if (btn) {
                const on = this.favoriteIds.has(id);
                btn.classList.toggle('is-favorited', on);
                btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            }
        });
    },

    async toggleFavorite(imageId) {
        const app = window.App || {};
        const id = Number(imageId);
        const next = !this.favoriteIds.has(id);
        // Optimistic update with rollback on failure.
        if (next) this.favoriteIds.add(id); else this.favoriteIds.delete(id);
        this._applyFavoriteStateToDom();
        try {
            const result = await app.API?.setFavorite?.(id, next);
            const favorited = Boolean(result?.favorited);
            if (favorited) this.favoriteIds.add(id); else this.favoriteIds.delete(id);
            this._applyFavoriteStateToDom();
            // v3.3.1 FEAT-COLLECTIONS: refresh the sidebar Favorites count.
            window.CollectionsUI?.notifyChanged?.();
        } catch (error) {
            // Roll back.
            if (next) this.favoriteIds.delete(id); else this.favoriteIds.add(id);
            this._applyFavoriteStateToDom();
            app.showToast?.(
                app.appT?.('collections.favoriteFailed', 'Could not update favorite') || 'Could not update favorite',
                'error'
            );
        }
    },

    // v3.3.3 WIRING-01: user star rating (0-5). The backend was fully built
    // (POST /api/images/{id}/rating, min_user_rating filter, user_rating sort,
    // migration 015) but the frontend surfaced none of it. The value rides on
    // each image object (image.user_rating), so unlike favorites there is no
    // separate id-set to hydrate — we read/patch it in place.
    _userRatingOf(image) {
        const n = Number(image?.user_rating);
        return Number.isFinite(n) && n >= 0 && n <= 5 ? Math.round(n) : 0;
    },

    _ratingBadgeHtml(image) {
        const stars = this._userRatingOf(image);
        if (stars <= 0) return '';
        const label = this._t('rating.cardLabel', { stars }, `${stars}/5 stars`);
        return `<span class="gallery-item-stars" data-rating-badge `
            + `title="${this._escapeHtml(label)}" aria-label="${this._escapeHtml(label)}">`
            + `${'★'.repeat(stars)}</span>`;
    },

    _renderModalRating(image) {
        const container = document.getElementById('modal-user-rating');
        if (!container) return;
        const stars = this._userRatingOf(image);
        const id = Number(image?.id);
        container.dataset.imageId = Number.isFinite(id) ? String(id) : '';
        container.dataset.rating = String(stars);
        const starLabel = (n) => this._t('rating.setStars', { stars: n }, `Rate ${n}/5`);
        let html = '';
        for (let n = 1; n <= 5; n++) {
            html += `<button type="button" class="star${n <= stars ? ' is-filled' : ''}" data-star="${n}" `
                + `role="radio" aria-checked="${n === stars ? 'true' : 'false'}" `
                + `title="${this._escapeHtml(starLabel(n))}" aria-label="${this._escapeHtml(starLabel(n))}">★</button>`;
        }
        const clearLabel = this._t('rating.clear', null, 'Clear rating');
        html += `<button type="button" class="star-clear${stars === 0 ? ' is-hidden' : ''}" data-star="0" `
            + `title="${this._escapeHtml(clearLabel)}" aria-label="${this._escapeHtml(clearLabel)}">✕</button>`;
        container.innerHTML = html;
        if (!container.dataset.bound) {
            container.dataset.bound = '1';
            container.addEventListener('click', (e) => {
                const btn = e.target.closest('[data-star]');
                if (!btn) return;
                e.preventDefault();
                e.stopPropagation();
                const value = Number(btn.dataset.star);
                const imageId = Number(container.dataset.imageId);
                if (Number.isFinite(imageId)) this.setUserRating(imageId, value);
            });
        }
    },

    _applyRatingToState(imageId, value) {
        const id = Number(imageId);
        let prev = 0;
        const apply = (list) => {
            if (!Array.isArray(list)) return;
            const img = list.find((image) => Number(image?.id) === id);
            if (img) { prev = this._userRatingOf(img); img.user_rating = value; }
        };
        apply(this.images);
        apply(window.App?.AppState?.images);
        return prev;
    },

    _renderRatingEverywhere(imageId, value) {
        const id = Number(imageId);
        const modal = document.getElementById('modal-user-rating');
        if (modal && Number(modal.dataset.imageId) === id) {
            this._renderModalRating({ id, user_rating: value });
        }
        const item = document.querySelector(`#gallery-grid .gallery-item[data-id="${id}"]`);
        if (item) {
            const existing = item.querySelector('[data-rating-badge]');
            const html = this._ratingBadgeHtml({ id, user_rating: value });
            if (existing) {
                if (html) existing.outerHTML = html; else existing.remove();
            } else if (html) {
                const anchor = item.querySelector('.gallery-item-media') || item;
                anchor.insertAdjacentHTML('beforeend', html);
            }
        }
    },

    async setUserRating(imageId, stars) {
        const app = window.App || {};
        const id = Number(imageId);
        const value = Math.max(0, Math.min(5, Math.round(Number(stars) || 0)));
        // Optimistic update with rollback on failure (mirrors toggleFavorite).
        const prev = this._applyRatingToState(id, value);
        this._renderRatingEverywhere(id, value);
        try {
            const result = await app.API?.setRating?.(id, value);
            const saved = Number(result?.user_rating);
            const finalVal = Number.isFinite(saved) ? saved : value;
            this._applyRatingToState(id, finalVal);
            this._renderRatingEverywhere(id, finalVal);
        } catch (error) {
            this._applyRatingToState(id, prev);
            this._renderRatingEverywhere(id, prev);
            app.showToast?.(
                app.appT?.('rating.failed', 'Could not update rating') || 'Could not update rating',
                'error'
            );
        }
    },

});
