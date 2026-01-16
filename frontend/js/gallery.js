/**
 * SD Image Sorter - Gallery Module
 * Handles image grid display, preview modal, multi-selection and drag-and-drop
 */

const Gallery = {
    images: [],
    loading: false,

    setImages(images) {
        this.images = images;
        this.render();
    },

    render() {
        const { $, API, AppState } = window.App || { $: (s) => document.querySelector(s), API: window.API, AppState: window.AppState };
        const grid = $('#gallery-grid');
        if (!grid) return;

        grid.innerHTML = '';
        const fragment = document.createDocumentFragment();

        const genColors = {
            comfyui: '#22c55e',
            nai: '#f97316',
            webui: '#3b82f6',
            forge: '#8b5cf6',
            unknown: '#64748b'
        };

        // Use IntersectionObserver for lazy loading - all images accessible, but loads on scroll
        const lazyLoadObserver = new IntersectionObserver((entries, observer) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target.querySelector('img');
                    if (img && img.dataset.src) {
                        img.src = img.dataset.src;
                        delete img.dataset.src;
                    }
                    observer.unobserve(entry.target);
                }
            });
        }, { rootMargin: '200px' }); // Pre-load 200px before visible

        this.images.forEach(image => {
            const item = document.createElement('div');
            item.className = 'gallery-item';
            if (AppState.selectedIds.has(image.id)) {
                item.classList.add('selected');
            }
            item.dataset.id = image.id;
            item.draggable = true;

            // Use data-src for lazy loading, with a placeholder
            item.innerHTML = `
                <img data-src="${API.getThumbnailUrl(image.id)}" alt="${image.filename}" loading="lazy" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">
                <div class="gallery-item-overlay">
                    <span class="gallery-item-generator" style="background: ${genColors[image.generator] || genColors.unknown}">
                        ${image.generator}
                    </span>
                </div>
            `;

            // Click handling
            item.addEventListener('click', (e) => {
                if (AppState.selectionMode) {
                    this.toggleSelection(image.id);
                } else {
                    this.openPreview(image.id);
                }
            });

            // Drag navigation support - enables dragging to WebUI/Forge/ComfyUI
            item.addEventListener('dragstart', (e) => {
                // Get the full image URL (not thumbnail) for metadata access
                const imgUrl = API.getImageUrl(image.id);
                const absoluteUrl = new URL(imgUrl, window.location.origin).href;

                // Set multiple data formats for maximum compatibility
                // - text/uri-list: Standard URL format for browsers
                // - text/plain: Fallback with prompt text
                // - DownloadURL: Chrome-specific for file download triggers
                e.dataTransfer.setData('text/uri-list', absoluteUrl);
                e.dataTransfer.setData('text/plain', absoluteUrl);

                // DownloadURL format: MIME:filename:URL (Chrome specific, triggers download on drop)
                const mimeType = image.filename.toLowerCase().endsWith('.png') ? 'image/png' :
                    image.filename.toLowerCase().endsWith('.webp') ? 'image/webp' :
                        'image/jpeg';
                e.dataTransfer.setData('DownloadURL', `${mimeType}:${image.filename}:${absoluteUrl}`);

                // Set drag image to the actual thumbnail
                const img = item.querySelector('img');
                if (img && img.src) {
                    e.dataTransfer.setDragImage(img, 50, 50);
                }

                // Mark as dragging
                item.classList.add('dragging');

                // Store image data for potential same-app drops
                e.dataTransfer.effectAllowed = 'copyMove';
            });

            item.addEventListener('dragend', () => {
                item.classList.remove('dragging');
            });

            fragment.appendChild(item);

            // Observe for lazy loading
            lazyLoadObserver.observe(item);
        });

        grid.appendChild(fragment);

        if (this.images.length === 0) {
            grid.innerHTML = `
                <div style="grid-column: 1/-1; text-align: center; padding: 60px; color: var(--text-secondary);">
                    <div style="font-size: 48px; margin-bottom: 16px;">📷</div>
                    <p>No images found. Click "Scan Folder" to add images.</p>
                </div>
            `;
        }
    },

    toggleSelection(imageId) {
        const { $, AppState, updateSelectionUI } = window.App || { $: (s) => document.querySelector(s), AppState: window.AppState, updateSelectionUI: window.updateSelectionUI };

        const isNowSelected = !AppState.selectedIds.has(imageId);

        if (isNowSelected) {
            AppState.selectedIds.add(imageId);
        } else {
            AppState.selectedIds.delete(imageId);
        }

        // Update only the specific item instead of full re-render (prevents flash)
        const item = document.querySelector(`.gallery-item[data-id="${imageId}"]`);
        if (item) {
            item.classList.toggle('selected', isNowSelected);
        }

        if (updateSelectionUI) updateSelectionUI();
    },

    async openPreview(imageId) {
        const { $, API, showModal, formatSize } = window.App || { $: (s) => document.querySelector(s), API: window.API, showModal: window.showModal, formatSize: window.formatSize };

        try {
            const result = await API.getImage(imageId);
            const image = result.image;
            const tags = result.tags;

            $('#modal-image').src = API.getImageUrl(imageId);
            $('#modal-filename').textContent = image.filename;
            $('#modal-generator').textContent = image.generator.toUpperCase();
            $('#modal-size').textContent = `${image.width}×${image.height} • ${formatSize(image.file_size)}`;
            $('#modal-prompt-text').textContent = image.prompt || 'No prompt data';

            const tagsList = $('#modal-tags-list');
            if (tags.length > 0) {
                const ratingTags = ['general', 'sensitive', 'questionable', 'explicit'];
                const ratings = tags.filter(t => ratingTags.includes(t.tag));
                const otherTags = tags.filter(t => !ratingTags.includes(t.tag));

                let html = '';
                if (ratings.length > 0) {
                    const rating = ratings.reduce((a, b) => a.confidence > b.confidence ? a : b);
                    const ratingColors = {
                        general: '#22c55e',
                        sensitive: '#eab308',
                        questionable: '#f97316',
                        explicit: '#ef4444'
                    };
                    html += `<span class="tag" style="background: ${ratingColors[rating.tag]}; color: white; font-weight: 600;">${rating.tag}</span>`;
                }

                html += otherTags.slice(0, 40).map(t => `<span class="tag">${t.tag}</span>`).join('');
                tagsList.innerHTML = html;
            } else {
                tagsList.innerHTML = '<span style="color: var(--text-muted)">No tags (run WD14 tagger)</span>';
            }

            if (window.showModal) window.showModal('image-modal');
        } catch (error) {
            console.error('Failed to load image details:', error);
            if (window.showToast) window.showToast('Failed to load image details', 'error');
        }
    }
};

window.Gallery = Gallery;
