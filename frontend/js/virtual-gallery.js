/**
 * SD Image Sorter - Virtual Scrolling Gallery (legacy helper)
 * Live gallery virtualization lives in gallery/virtual-layout.js.
 * This file is a no-op when initVirtualScroll already exists; do not treat
 * it as a replacement for Gallery.render().
 */

// Shared generator color map (DRY — also used in gallery.js).
// Stay in sync with backend/metadata_parser.py::MetadataParser.GENERATORS.
const GENERATOR_COLORS = {
    comfyui: '#456852',
    nai: '#685445',
    webui: '#455368',
    forge: '#504568',
    reforge: '#574568',
    fooocus: '#684556',
    'easy-diffusion': '#456864',
    invokeai: '#455d68',
    swarmui: '#686145',
    drawthings: '#684558',
    gemini: '#685e45',
    'gpt-image': '#45685d',
    others: '#455468',
    unknown: '#575759'
};

const VirtualGallery = {
    // Configuration
    BUFFER_ROWS: 3,       // Extra rows above/below viewport
    ITEM_MIN_WIDTH: 200,  // Matches CSS grid minmax
    ROW_GAP: 16,          // Matches CSS gap

    // State
    containerEl: null,
    scrollEl: null,
    itemHeight: 0,
    columns: 0,
    totalRows: 0,
    visibleStart: 0,
    visibleEnd: 0,
    renderedItems: new Map(), // row index -> DOM element
    images: [],
    viewMode: 'grid',
    isLargeGrid: false,
    resizeObserver: null,
    scrollRAF: null,
    resizeDebounceTimer: null,
    initialized: false,
    _scrollHandler: null,
    _origSetImages: null,
    _origRender: null,

    /**
     * Initialize virtual gallery, replacing the default render.
     */
    init() {
        // Clean up before reinitializing to prevent memory leaks
        if (this.initialized) {
            this.destroy();
        }

        const grid = document.getElementById('gallery-grid');
        if (!grid) return;

        this.containerEl = grid;
        this.scrollEl = grid.parentElement; // The scrollable container

        // Observe container resize to recalculate columns (debounced)
        this.resizeObserver = new ResizeObserver(() => {
            clearTimeout(this.resizeDebounceTimer);
            this.resizeDebounceTimer = setTimeout(() => {
                this.recalculate();
            }, 100);
        });
        this.resizeObserver.observe(this.containerEl);

        // Bind scroll handler (store reference for cleanup)
        this._scrollHandler = () => this.onScroll();
        this.scrollEl.addEventListener('scroll', this._scrollHandler, { passive: true });

        // Save original Gallery methods before overriding
        this._origSetImages = Gallery.setImages.bind(Gallery);
        this._origRender = Gallery.render.bind(Gallery);

        // Override Gallery.render with virtual version
        Gallery.setImages = (images) => {
            Gallery.images = images;
            this.setImages(images);
        };

        // Also override direct render calls
        Gallery.render = () => {
            this.setImages(Gallery.images);
        };

        this.initialized = true;
    },

    /**
     * Set images and trigger virtual render.
     */
    setImages(images) {
        this.images = images || [];

        for (const [, rowEl] of this.renderedItems) {
            rowEl.remove();
        }
        this.renderedItems.clear();

        if (this.scrollEl) {
            this.scrollEl.scrollTop = 0;
        }

        if (this.viewMode === 'waterfall') {
            this.renderWaterfall();
            return;
        }

        this.recalculate();
    },

    /**
     * Recalculate layout dimensions.
     */
    recalculate() {
        if (!this.containerEl) return;

        const containerWidth = this.containerEl.clientWidth;
        const minWidth = this.isLargeGrid ? 300 : this.ITEM_MIN_WIDTH;

        // Calculate columns (matches CSS auto-fill behavior)
        this.columns = Math.max(1, Math.floor((containerWidth + this.ROW_GAP) / (minWidth + this.ROW_GAP)));

        // Calculate item height (square aspect ratio)
        const itemWidth = (containerWidth - (this.columns - 1) * this.ROW_GAP) / this.columns;
        this.itemHeight = itemWidth; // aspect-ratio: 1

        // Total rows
        this.totalRows = Math.ceil(this.images.length / this.columns);

        // Calculate total content height
        const totalHeight = this.totalRows * (this.itemHeight + this.ROW_GAP) - this.ROW_GAP;

        // Set container to relative for absolute positioned rows
        // Clear any lingering grid properties first
        this.containerEl.style.gridTemplateColumns = '';
        this.containerEl.style.position = 'relative';
        this.containerEl.style.display = 'block';
        this.containerEl.style.minHeight = Math.max(0, totalHeight) + 'px';

        // Rows bake columns/top/height into inline styles at creation, so a
        // width change must drop them all — renderVisible only adds missing
        // row indices and would leave in-range rows at stale geometry.
        for (const [, rowEl] of this.renderedItems) {
            rowEl.remove();
        }
        this.renderedItems.clear();

        // Re-render visible items
        this.renderVisible();
    },

    /**
     * Handle scroll events.
     */
    onScroll() {
        if (this.scrollRAF) return;
        this.scrollRAF = requestAnimationFrame(() => {
            this.scrollRAF = null;
            this.renderVisible();
        });
    },

    /**
     * Calculate and render only visible rows.
     */
    renderVisible() {
        if (!this.containerEl || !this.scrollEl) return;

        if (this.images.length === 0) {
            this.containerEl.innerHTML = '';
            this.renderedItems.clear();
            this.containerEl.style.display = '';
            this.containerEl.style.position = '';
            this.containerEl.style.minHeight = '';
            this.containerEl.style.gridTemplateColumns = '';
            return;
        }

        const scrollTop = this.scrollEl.scrollTop;
        const viewportHeight = this.scrollEl.clientHeight;
        const containerTop = this.containerEl.offsetTop - this.scrollEl.offsetTop;
        const relativeScroll = scrollTop - containerTop;

        const rowHeight = this.itemHeight + this.ROW_GAP;

        // Calculate visible row range
        const firstVisibleRow = Math.max(0, Math.floor(relativeScroll / rowHeight) - this.BUFFER_ROWS);
        const lastVisibleRow = Math.min(
            this.totalRows - 1,
            Math.ceil((relativeScroll + viewportHeight) / rowHeight) + this.BUFFER_ROWS
        );

        // Remove rows that are no longer visible
        for (const [rowIdx, rowEl] of this.renderedItems) {
            if (rowIdx < firstVisibleRow || rowIdx > lastVisibleRow) {
                rowEl.remove();
                this.renderedItems.delete(rowIdx);
            }
        }

        // Add newly visible rows
        for (let rowIdx = firstVisibleRow; rowIdx <= lastVisibleRow; rowIdx++) {
            if (!this.renderedItems.has(rowIdx)) {
                const rowEl = this.createRow(rowIdx);
                if (rowEl) {
                    this.containerEl.appendChild(rowEl);
                    this.renderedItems.set(rowIdx, rowEl);
                }
            }
        }

        this.visibleStart = firstVisibleRow;
        this.visibleEnd = lastVisibleRow;
    },

    /**
     * Create a row of gallery items.
     */
    createRow(rowIndex) {
        const startIdx = rowIndex * this.columns;
        if (startIdx >= this.images.length) return null;

        const endIdx = Math.min(startIdx + this.columns, this.images.length);
        const rowHeight = this.itemHeight + this.ROW_GAP;
        const topOffset = rowIndex * rowHeight;

        const { API, AppState } = window.App || {};

        const rowEl = document.createElement('div');
        rowEl.className = 'virtual-row';
        rowEl.style.cssText = `
            position: absolute;
            top: ${topOffset}px;
            left: 0;
            right: 0;
            display: grid;
            grid-template-columns: repeat(${this.columns}, 1fr);
            gap: ${this.ROW_GAP}px;
            height: ${this.itemHeight}px;
        `;
        rowEl.dataset.row = rowIndex;

        for (let i = startIdx; i < endIdx; i++) {
            const image = this.images[i];
            const item = Gallery.createGalleryItem(image, i, false);
            item.querySelector('img')?.setAttribute('src', API ? API.getThumbnailUrl(image.id, this.isLargeGrid ? 512 : 256) : `/api/image-thumbnail/${image.id}`);
            item.querySelector('img')?.removeAttribute('data-src');
            rowEl.appendChild(item);
        }

        return rowEl;
    },

    /**
     * Toggle large grid mode.
     */
    setViewMode(mode) {
        this.viewMode = mode || 'grid';
        this.isLargeGrid = this.viewMode === 'large';
        this.ITEM_MIN_WIDTH = this.isLargeGrid ? 300 : 200;
        if (this.viewMode === 'waterfall') {
            this.renderWaterfall();
            return;
        }
        this.recalculate();
    },

    renderWaterfall() {
        if (!this.containerEl) return;
        for (const [, rowEl] of this.renderedItems) {
            rowEl.remove();
        }
        this.renderedItems.clear();
        this.containerEl.innerHTML = '';
        this.containerEl.style.position = '';
        this.containerEl.style.display = 'block';
        this.containerEl.style.minHeight = '';
        this.containerEl.style.gridTemplateColumns = '';

        const fragment = document.createDocumentFragment();
        this.images.forEach((image, index) => {
            fragment.appendChild(Gallery.createGalleryItem(image, index, true));
        });
        this.containerEl.appendChild(fragment);
    },

    /**
     * Clean up observers, listeners, and restore original Gallery methods.
     */
    destroy() {
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
            this.resizeObserver = null;
        }
        if (this.scrollRAF) {
            cancelAnimationFrame(this.scrollRAF);
            this.scrollRAF = null;
        }
        if (this.resizeDebounceTimer) {
            clearTimeout(this.resizeDebounceTimer);
            this.resizeDebounceTimer = null;
        }
        // Remove scroll listener to prevent duplicate bindings on re-init
        if (this._scrollHandler && this.scrollEl) {
            this.scrollEl.removeEventListener('scroll', this._scrollHandler);
            this._scrollHandler = null;
        }
        // Remove all rendered DOM elements
        for (const [, rowEl] of this.renderedItems) {
            rowEl.remove();
        }
        this.renderedItems.clear();
        // Restore original Gallery methods to prevent closure nesting on re-init
        if (this._origSetImages) {
            Gallery.setImages = this._origSetImages;
            this._origSetImages = null;
        }
        if (this._origRender) {
            Gallery.render = this._origRender;
            this._origRender = null;
        }
        this.initialized = false;
    }
};

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    if (window.Gallery && typeof window.Gallery.initVirtualScroll === 'function') {
        return;
    }

    // Delay init slightly to ensure Gallery is available
    setTimeout(() => {
        VirtualGallery.init();
    }, 50);
});

window.VirtualGallery = VirtualGallery;
// Export shared color map for gallery.js
window.GENERATOR_COLORS = GENERATOR_COLORS;
