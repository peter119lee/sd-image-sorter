/**
 * artist/events.js — artist-ident.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/artist-ident.js
 * pre-cut lines 138-154 + 160-170 + 1081-1164 (of 1,171):
 * dismissFirstUseCard, refreshFirstUseCard, init (the app/view-switch.js
 * seam), the "Event Binding" section comment, bindEvents (delegated
 * document input/change/click + selection-state-changed +
 * languageChanged handlers behind the eventsBound guard), the "First
 * Use Guide" section comment and showFirstUseGuide. Classic non-strict
 * script: joins the ONE unsealed window.ArtistIdent object declared in
 * artist/core.js, which loads FIRST; artist/boot.js runs the
 * DOMContentLoaded tail LAST.
 */
Object.assign(window.ArtistIdent, {
    dismissFirstUseCard() {
        localStorage.setItem('artist-guide-seen', 'true');
        const card = document.getElementById('artist-start-card');
        if (card) card.hidden = true;
    },

    refreshFirstUseCard() {
        const card = document.getElementById('artist-start-card');
        const dismissBtn = document.getElementById('artist-start-dismiss');
        if (!card) return;
        if (dismissBtn && dismissBtn.dataset.bound !== 'true') {
            dismissBtn.addEventListener('click', () => this.dismissFirstUseCard());
            dismissBtn.dataset.bound = 'true';
        }
        // Quiet by default — Help on this view unhides the card.
        card.hidden = true;
        const help = document.getElementById('btn-help');
        if (help && help.dataset.artistGuideBound !== 'true') {
            help.dataset.artistGuideBound = 'true';
            help.addEventListener('click', () => {
                if (window.App?.AppState?.currentView === 'artist') {
                    card.hidden = false;
                }
            });
        }
    },

    init() {
        this.bindEvents();
        this.applySavedPreferences();
        this._syncControls();
        this.refreshAvailabilityState();
        this.loadDiagnostics();
        this.loadStats();
        this.refreshVocabularyState();
        this.resumeBatchProgress();
        this.showFirstUseGuide();
    },

    // ============== Event Binding ==============

    bindEvents() {
        if (this.eventsBound) return;
        this.eventsBound = true;

        document.addEventListener('input', (event) => {
            if (event.target?.id === 'artist-threshold') {
                this.syncThresholdDisplay();
                this.savePreferences();
                return;
            }
            if (event.target?.id === 'artist-model-path') {
                this.savePreferences();
            }
        });

        document.addEventListener('change', (event) => {
            if (event.target?.id === 'artist-model-source') {
                const localModelGroup = document.getElementById('artist-local-model-group');
                if (localModelGroup) {
                    localModelGroup.style.display = event.target.value === 'local' ? 'block' : 'none';
                }
                this.savePreferences();
                return;
            }
            if (event.target?.id === 'artist-use-gpu') {
                this.savePreferences();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.target?.id === 'artist-vocabulary-input' && event.key === 'Enter') {
                event.preventDefault();
                this.checkArtistVocabulary();
            }
        });

        document.addEventListener('click', (event) => {
            const actionButton = event.target?.closest?.(
                '#btn-identify-all, #btn-identify-selected, #btn-refresh-artist-stats, #btn-clear-artist-data, #btn-artist-load-more, #btn-artist-vocabulary-check'
            );
            const id = actionButton?.id;
            switch (id) {
                case 'btn-artist-vocabulary-check':
                    this.checkArtistVocabulary();
                    return;
                case 'btn-identify-all':
                    this.identifyAll();
                    return;
                case 'btn-identify-selected':
                    this.identifySelected();
                    return;
                case 'btn-refresh-artist-stats':
                    this.loadStats();
                    return;
                case 'btn-clear-artist-data':
                    this.clearAllData();
                    return;
                case 'btn-artist-load-more':
                    if (this.selectedArtist && this.selectedArtistHasMore) {
                        this.selectArtist(this.selectedArtist, { append: true });
                    }
                    return;
                default:
                    break;
            }

            const toggleBtn = event.target.closest?.('.view-toggle .toggle-btn');
            if (toggleBtn) {
                const nextMode = toggleBtn.dataset.view || 'grid';
                document.querySelectorAll('.view-toggle .toggle-btn').forEach(btn => {
                    btn.classList.toggle('active', btn === toggleBtn);
                });
                this.renderArtistGrid(this.stats.artist_counts || {}, nextMode);
            }
        });

        document.addEventListener('selection-state-changed', () => {
            this.refreshAvailabilityState();
        });

        document.addEventListener('languageChanged', () => {
            requestAnimationFrame(() => this.refreshAvailabilityState());
        });
    },


    // ============== First Use Guide ==============

    showFirstUseGuide() {
        this.refreshFirstUseCard();
    },

});
