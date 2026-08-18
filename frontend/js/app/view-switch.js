/**
 * app/view-switch.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 1368-1491 (of 10,152): switchView routing + per-view cleanup.
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
function switchView(viewName) {
    const previousView = AppState.currentView;

    // Cleanup previous view
    if (previousView === 'gallery' && viewName !== 'gallery') {
        if (window.Gallery && typeof window.Gallery.destroy === 'function') {
            window.Gallery.destroy();
        }
        if (window.VirtualGallery && typeof window.VirtualGallery.destroy === 'function') {
            window.VirtualGallery.destroy();
        }
        detachGalleryPaginationListener();
        cancelGalleryImageLoad();
        // Stop hidden-gallery thumbnail downloads from occupying the browser's
        // per-host connection pool. This keeps Dataset Maker's folder browser
        // responsive even when the gallery was still loading many thumbnails.
        // The cached ``AppState.images`` array survives, so when the user
        // returns to the gallery the ``Gallery.setImages`` path on line ~3729
        // re-renders the DOM (and re-attaches img.src) from cache without a
        // network refetch. Setting ``galleryNeedsRefresh = true`` here would
        // force a full reload on every nav-out and break callers that
        // explicitly DO NOT want a refresh after their action (e.g. Reader
        // save-as-new to a path outside the indexed library).
        const galleryGrid = $('#gallery-grid');
        if (galleryGrid) {
            galleryGrid.querySelectorAll('img').forEach((img) => {
                img.removeAttribute('srcset');
                img.removeAttribute('src');
            });
        }
    }

    // Cleanup censor view listeners when leaving
    if (previousView === 'censor' && viewName !== 'censor') {
        if (typeof window.cleanupCensorView === 'function') {
            window.cleanupCensorView();
        }
    }

    AppState.currentView = viewName;

    // Update nav tabs
    $$('.nav-tab').forEach(tab => {
        const isActive = tab.dataset.view === viewName;
        tab.classList.toggle('active', isActive);
        tab.setAttribute('aria-selected', String(isActive));
    });
    // v3.3.3 WS1: mirror active state onto the Tools toggle (its menu items are
    // hidden in the dropdown) and collapse the menu after a choice is made.
    updateNavToolsActive(viewName);
    window._closeNavToolsMenu?.();

    // Update mobile nav items
    $$('.mobile-nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.view === viewName);
    });

    // Update views
    $$('.view').forEach(view => {
        view.classList.toggle('active', view.id === `view-${viewName}`);
    });
    scheduleViewScrollReset();

    // Hide selection FAB when not in Gallery view
    if (viewName !== 'gallery') {
        const selActions = $('#selection-actions');
        if (selActions) selActions.style.display = 'none';
        collapseSelectionMoreActions();
    } else if (AppState.selectionMode && getSelectedGalleryCount() > 0) {
        // Show FAB if we have selections and are returning to gallery
        const selActions = $('#selection-actions');
        if (selActions) selActions.style.display = 'grid';
    }

    // View-specific initialization
    if (viewName === 'gallery') {
        let suppressInitialGalleryAutoLoadMore = false;
        setGalleryViewMode(AppState.viewMode);
        // Re-render existing images immediately, only reload from API if needed
        if (AppState.galleryNeedsRefresh) {
            suppressInitialGalleryAutoLoadMore = AppState.gallerySuppressNextAutoLoadMore;
            loadImages(false, {
                silent: AppState.images.length > 0,
                preserveExisting: AppState.images.length > 0,
                coalesce: true,
                suppressAutoLoadMore: suppressInitialGalleryAutoLoadMore,
            });
            AppState.galleryNeedsRefresh = false;
            AppState.gallerySuppressNextAutoLoadMore = false;
        } else if (AppState.images.length > 0 && window.Gallery) {
            Gallery.setImages(AppState.images);
        } else {
            loadImages();
        }
        requestAnimationFrame(() => {
            attachGalleryPaginationListener();
            if (!suppressInitialGalleryAutoLoadMore) {
                _onGalleryScroll();
            }
        });
        // Re-check whether unreadable rows exist when returning to gallery.
        // Cached for 60s inside UnreadableBanner so this is cheap.
        window.UnreadableBanner?.refresh?.(false);
    } else if (viewName === 'similar') {
        if (typeof window.initSimilar === 'function') window.initSimilar();
    } else if (viewName === 'promptlab') {
        if (typeof window.initPromptLab === 'function') window.initPromptLab();
    } else if (viewName === 'reverse') {
        if (typeof window.initReversePrompt === 'function') window.initReversePrompt();
    } else if (viewName === 'artist') {
        if (window.ArtistIdent && typeof window.ArtistIdent.init === 'function') {
            window.ArtistIdent.init();
        }
    } else if (viewName === 'censor') {
        if (typeof window.initCensorEdit === 'function') window.initCensorEdit();
    } else if (viewName === 'sorting') {
        const activeSortingSub = document.querySelector('.sorting-sub-tab.active')?.getAttribute('data-sorting-sub') || 'autosep';
        if (typeof window._switchSortingSub === 'function') {
            window._switchSortingSub(activeSortingSub);
        }
    }

    updateSelectionUI();
    scheduleViewScrollReset();
}

