/**
 * SD Image Sorter - Main Application
 * Core app logic and API communication
 */

const API_BASE = '';  // Same origin

// App State
const AppState = {
    currentView: 'gallery',
    images: [],
    filters: {
        generators: ['comfyui', 'nai', 'webui', 'forge', 'unknown'],
        ratings: ['general', 'sensitive', 'questionable', 'explicit'],
        tags: [],
        checkpoints: [],
        loras: [],
        prompts: [],  // Multi-prompt filter
        search: '',
        sortBy: 'newest'
    },
    selectedImage: null,
    isLoading: false,

    // Multi-select state
    selectionMode: false,
    selectedIds: new Set(),

    // Analytics data
    analytics: {
        checkpoints: [],
        loras: [],
        top_tags: []
    },

    // Current modal selection state
    modalSelection: {
        type: null, // 'checkpoint' or 'lora'
        tempSelected: new Set(),
        search: ''
    }
};

// ============== API Functions ==============

const API = {
    async get(endpoint) {
        const response = await fetch(`${API_BASE}${endpoint}`);
        if (!response.ok) throw new Error(`API Error: ${response.status}`);
        return response.json();
    },

    async post(endpoint, data = {}) {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!response.ok) throw new Error(`API Error: ${response.status}`);
        return response.json();
    },

    async delete(endpoint) {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            method: 'DELETE'
        });
        if (!response.ok) throw new Error(`API Error: ${response.status}`);
        return response.json();
    },

    // Images - no limit by default (0 = all)
    async getImages(filters = {}) {
        const params = new URLSearchParams();
        if (filters.generators?.length) params.set('generators', filters.generators.join(','));

        // Fix: Always send ratings if they are selected/changed
        // If all 4 selected, we still send them so backend includes untagged
        if (filters.ratings?.length) {
            params.set('ratings', filters.ratings.join(','));
        }

        if (filters.tags?.length) params.set('tags', filters.tags.join(','));
        if (filters.checkpoints?.length) params.set('checkpoints', filters.checkpoints.join(','));
        if (filters.loras?.length) params.set('loras', filters.loras.join(','));
        if (filters.prompts?.length) params.set('prompts', filters.prompts.join(','));
        if (filters.search) params.set('search', filters.search);
        if (filters.sortBy) params.set('sort_by', filters.sortBy);
        params.set('limit', filters.limit || 0);  // 0 = no limit
        if (filters.offset) params.set('offset', filters.offset);

        // Dimension filters
        if (filters.minWidth) params.set('min_width', filters.minWidth);
        if (filters.maxWidth) params.set('max_width', filters.maxWidth);
        if (filters.minHeight) params.set('min_height', filters.minHeight);
        if (filters.maxHeight) params.set('max_height', filters.maxHeight);
        if (filters.aspectRatio) params.set('aspect_ratio', filters.aspectRatio);

        return this.get(`/api/images?${params}`);
    },

    async getAnalytics() {
        return this.get('/api/analytics');
    },

    async clearGallery() {
        return this.delete('/api/clear-gallery');
    },

    async getImage(id) {
        return this.get(`/api/images/${id}`);
    },

    getImageUrl(id) {
        return `${API_BASE}/api/image-file/${id}`;
    },

    getThumbnailUrl(id) {
        return `${API_BASE}/api/image-thumbnail/${id}`;
    },

    // Tags & Generators
    async getTags() {
        return this.get('/api/tags');
    },

    async getTagsLibrary(sortBy = 'frequency', limit = 2000) {
        return this.get(`/api/tags/library?sort_by=${sortBy}&limit=${limit}`);
    },

    async importTags(images, overwrite = false) {
        return this.post('/api/tags/import', { images, overwrite });
    },

    async getPromptsLibrary(limit = 5000) {
        return this.get(`/api/prompts/library?limit=${limit}`);
    },

    async getGenerators() {
        return this.get('/api/generators');
    },

    // Stats
    async getStats() {
        return this.get('/api/stats');
    },

    // Scan
    async startScan(folderPath, recursive = true) {
        return this.post('/api/scan', { folder_path: folderPath, recursive });
    },

    async getScanProgress() {
        return this.get('/api/scan/progress');
    },

    // Tagging - with all new options
    async startTagging(options = {}) {
        return this.post('/api/tag/start', { // Unified with backend endpoint
            threshold: options.threshold || 0.35,
            character_threshold: options.characterThreshold || 0.85,
            model_name: options.modelName || null,
            model_path: options.modelPath || null,
            tags_path: options.tagsPath || null,
            image_ids: options.imageIds || null,
            retag_all: options.retagAll || false,
            use_gpu: options.useGpu ?? true
        });
    },

    async getTagProgress() {
        return this.get('/api/tag/progress');
    },

    // Move
    async moveImages(imageIds, destinationFolder) {
        return this.post('/api/move', { image_ids: imageIds, destination_folder: destinationFolder });
    },

    async batchMove(generators, tags, ratings, destinationFolder, checkpoints = null, loras = null, prompts = null, dimensions = null) {
        return this.post('/api/batch-move', {
            generators,
            tags,
            ratings,
            checkpoints,
            loras,
            prompts,
            min_width: dimensions?.minWidth || null,
            max_width: dimensions?.maxWidth || null,
            min_height: dimensions?.minHeight || null,
            max_height: dimensions?.maxHeight || null,
            aspect_ratio: dimensions?.aspectRatio || null,
            destination_folder: destinationFolder
        });
    },

    // Manual Sort
    async startSortSession(generators, tags, ratings, folders, checkpoints = null, loras = null, prompts = null, dimensions = null) {
        const params = new URLSearchParams();
        if (generators?.length) params.set('generators', generators.join(','));
        if (tags?.length) params.set('tags', tags.join(','));
        if (ratings?.length) params.set('ratings', ratings.join(','));
        if (checkpoints?.length) params.set('checkpoints', checkpoints.join(','));
        if (loras?.length) params.set('loras', loras.join(','));
        if (prompts?.length) params.set('prompts', prompts.join(','));
        if (dimensions?.minWidth) params.set('min_width', dimensions.minWidth);
        if (dimensions?.maxWidth) params.set('max_width', dimensions.maxWidth);
        if (dimensions?.minHeight) params.set('min_height', dimensions.minHeight);
        if (dimensions?.maxHeight) params.set('max_height', dimensions.maxHeight);
        if (dimensions?.aspectRatio) params.set('aspect_ratio', dimensions.aspectRatio);
        if (folders) params.set('folders', JSON.stringify(folders));
        return this.post(`/api/sort/start?${params}`);
    },

    async getCurrentSortImage() {
        return this.get('/api/sort/current');
    },

    async sortAction(action, folderKey = null) {
        const params = new URLSearchParams();
        params.set('action', action);
        if (folderKey) params.set('folder_key', folderKey);
        return this.post(`/api/sort/action?${params}`);
    },

    async setSortFolders(folders) {
        return this.post('/api/sort/set-folders', { folders });
    },

    // Batch Tag Export
    async exportTagsBatch(imageIds, outputFolder, blacklist = [], prefix = '') {
        return this.post('/api/export-tags-batch', {
            image_ids: imageIds,
            output_folder: outputFolder,
            blacklist: blacklist,
            prefix: prefix
        });
    },

    // Prompts Library
    async getPromptsLibrary(limit = 2000) {
        return this.get(`/api/prompts/library?limit=${limit}`);
    }
};

// ============== UI Utilities ==============

function $(selector) {
    return document.querySelector(selector);
}

function $$(selector) {
    return document.querySelectorAll(selector);
}

function showToast(message, type = 'info') {
    const container = $('#toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    const icons = { success: '✓', error: '✕', info: 'ℹ' };
    toast.innerHTML = `
        <span class="toast-icon">${icons[type]}</span>
        <span class="toast-message">${message}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(50px)';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function showModal(modalId) {
    $(`#${modalId}`).classList.add('visible');
}

function hideModal(modalId) {
    $(`#${modalId}`).classList.remove('visible');
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// ============== View Navigation ==============

function switchView(viewName) {
    AppState.currentView = viewName;

    // Update nav tabs
    $$('.nav-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.view === viewName);
    });

    // Update views
    $$('.view').forEach(view => {
        view.classList.toggle('active', view.id === `view-${viewName}`);
    });

    // Hide selection FAB when not in Gallery view
    if (viewName !== 'gallery') {
        $('#selection-actions').style.display = 'none';
    } else if (AppState.selectedIds && AppState.selectedIds.size > 0) {
        // Show FAB if we have selections and are returning to gallery
        $('#selection-actions').style.display = 'flex';
    }

    // View-specific initialization
    if (viewName === 'gallery') {
        loadImages();
    }
}

// ============== Event Listeners ==============

function initEventListeners() {
    // Nav tabs
    $$('.nav-tab').forEach(tab => {
        tab.addEventListener('click', () => switchView(tab.dataset.view));
    });

    // Scan button
    $('#btn-scan').addEventListener('click', () => showModal('scan-modal'));

    // Tag button
    $('#btn-tag').addEventListener('click', () => showModal('tag-modal'));

    // Modal backdrops
    $$('.modal-backdrop').forEach(backdrop => {
        backdrop.addEventListener('click', () => {
            backdrop.parentElement.classList.remove('visible');
        });
    });

    // Scan modal
    $('#btn-cancel-scan').addEventListener('click', () => hideModal('scan-modal'));
    $('#btn-start-scan').addEventListener('click', startScan);

    // Tag modal
    $('#btn-cancel-tag').addEventListener('click', () => hideModal('tag-modal'));
    $('#btn-start-tag').addEventListener('click', startTagging);

    // Tag threshold sliders
    $('#tag-threshold').addEventListener('input', (e) => {
        $('#tag-threshold-value').textContent = e.target.value;
    });
    $('#tag-character-threshold').addEventListener('input', (e) => {
        $('#tag-character-threshold-value').textContent = e.target.value;
    });

    // Model selection toggle for custom model
    $('#tag-model-select').addEventListener('change', (e) => {
        const isCustom = e.target.value === 'custom';
        $('#custom-model-group').style.display = isCustom ? 'block' : 'none';
        $('#custom-tags-group').style.display = isCustom ? 'block' : 'none';
    });

    // Image modal
    $('#modal-close').addEventListener('click', () => hideModal('image-modal'));

    // Clear all filters button (sidebar)
    $('#btn-clear-filters').addEventListener('click', () => {
        resetAllFilters();
        hideModal('filter-modal');  // In case it's open
    });

    // View size buttons
    $$('.view-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            $$('.view-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            $('#gallery-grid').classList.toggle('large', btn.dataset.size === 'large');
        });
    });

    // --- New Features ---

    // Generator quick-filter tabs
    $$('.gen-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            // Update active state
            $$('.gen-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            const gen = tab.dataset.gen;
            if (gen === 'all') {
                // Reset to show all generators
                AppState.filters.generators = ['comfyui', 'nai', 'webui', 'forge', 'unknown'];
            } else {
                // Filter by single generator
                AppState.filters.generators = [gen];
            }

            // Update filter modal checkboxes to stay in sync
            $$('#modal-generator-filters input').forEach(cb => {
                cb.checked = gen === 'all' || cb.value === gen;
            });

            updateFilterSummary();
            loadImages();
        });
    });

    // Gallery sort dropdown
    $('#gallery-sort').addEventListener('change', (e) => {
        AppState.filters.sortBy = e.target.value;
        loadImages();
    });




    // Clear DB button
    $('#btn-clear-db').addEventListener('click', () => {
        showConfirm(
            'Clear Gallery',
            'Are you sure you want to clear all images from the database? This will NOT delete your physical files.',
            async () => {
                try {
                    await API.clearGallery();
                    showToast('Gallery cleared successfully');
                    loadImages();
                    loadStats();
                } catch (e) {
                    showToast('Error clearing gallery: ' + e.message, 'error');
                }
            }
        );
    });

    // Random button
    $('#btn-random').addEventListener('click', showRandomImage);

    // Multi-select toggle
    $('#btn-toggle-select').addEventListener('click', () => {
        AppState.selectionMode = !AppState.selectionMode;
        $('#btn-toggle-select').classList.toggle('active', AppState.selectionMode);

        if (!AppState.selectionMode) {
            AppState.selectedIds.clear();
            updateSelectionUI();
        }

        Gallery.render();
    });

    // Export selected
    $('#btn-export-selected').addEventListener('click', showExportModal);

    // Clear selection
    $('#btn-clear-selection').addEventListener('click', () => {
        AppState.selectedIds.clear();
        updateSelectionUI();
        Gallery.render();
    });

    // Select All - select all currently visible/filtered images
    $('#btn-select-all').addEventListener('click', () => {
        AppState.images.forEach(img => AppState.selectedIds.add(img.id));
        updateSelectionUI();
        Gallery.render();
    });


    // Confirm modal
    $('#btn-confirm-cancel').addEventListener('click', () => hideModal('confirm-modal'));

    // Note: #btn-select-checkpoints and #btn-select-loras removed - now handled in filter modal
    // Model selection modal handlers (for when opened from filter modal)
    $('#btn-cancel-model-select')?.addEventListener('click', () => hideModal('model-select-modal'));
    $('#btn-close-model-select')?.addEventListener('click', () => hideModal('model-select-modal'));
    $('#btn-confirm-model-select')?.addEventListener('click', confirmModelSelection);
    $('#model-select-search')?.addEventListener('input', (e) => {
        AppState.modalSelection.search = e.target.value.toLowerCase();
        renderModelSelectList();
    });

    // --- Export Modal ---
    $('#btn-close-export').addEventListener('click', () => hideModal('export-modal'));
    $('#btn-copy-export').addEventListener('click', () => {
        const text = $('#export-text').value;
        navigator.clipboard.writeText(text).then(() => {
            showToast('Copied to clipboard!', 'success');
        }).catch(() => {
            showToast('Failed to copy', 'error');
        });
    });
    // Toggle between prompts and tags
    $('#btn-export-tags').addEventListener('click', () => {
        const btn = $('#btn-export-tags');
        if (btn.textContent.includes('Tags')) {
            showExportTagsModal();
            btn.innerHTML = '📤 Export Prompts Instead';
        } else {
            showExportModal();
            btn.innerHTML = '🏷️ Export Tags Instead';
        }
    });

    // --- Export Tags from FAB ---
    $('#btn-export-tags-selected').addEventListener('click', () => {
        showExportTagsModal();
        $('#btn-export-tags').innerHTML = '📤 Export Prompts Instead';
    });

    // --- Unified Filter Modal ---
    $('#btn-open-filters').addEventListener('click', openFilterModal);
    $('#btn-close-filter-modal').addEventListener('click', () => hideModal('filter-modal'));
    $('#btn-apply-modal-filters').addEventListener('click', applyModalFilters);
    $('#btn-reset-filters').addEventListener('click', resetAllFilters);

    // Modal tag search
    $('#modal-tag-search').addEventListener('input', (e) => searchModalTags(e.target.value));

    // Tag input Enter key - add comma-separated tags
    $('#modal-tag-search').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const input = e.target.value.trim();
            if (input) {
                const tags = input.split(',').map(t => t.trim()).filter(t => t.length > 0);
                tags.forEach(tag => {
                    if (!AppState.filters.tags.includes(tag)) {
                        AppState.filters.tags.push(tag);
                    }
                });
                renderModalActiveTags();
                e.target.value = '';
                $('#modal-tag-suggestions').innerHTML = '';
            }
        }
    });

    // Prompt input Enter key - add comma-separated prompts
    const promptSearchEl = $('#modal-prompt-search');
    console.log('Prompt search element:', promptSearchEl);
    if (promptSearchEl) {
        // Autocomplete suggestions on input
        promptSearchEl.addEventListener('input', (e) => {
            searchModalPrompts(e.target.value);
        });

        promptSearchEl.addEventListener('keydown', (e) => {
            console.log('Prompt keydown:', e.key);
            if (e.key === 'Enter') {
                e.preventDefault();
                const input = e.target.value.trim();
                console.log('Prompt Enter pressed, input:', input);
                if (input) {
                    const prompts = input.split(',').map(p => p.trim()).filter(p => p.length > 0);
                    prompts.forEach(prompt => {
                        if (!AppState.filters.prompts.includes(prompt)) {
                            AppState.filters.prompts.push(prompt);
                        }
                    });
                    console.log('Prompts after adding:', AppState.filters.prompts);
                    renderModalActivePrompts();
                    e.target.value = '';
                    $('#modal-prompt-suggestions').innerHTML = '';
                }
            }
        });
    }

    // Library buttons
    $('#btn-tags-library')?.addEventListener('click', openTagsLibrary);
    $('#btn-open-library-from-filter')?.addEventListener('click', () => {
        hideModal('filter-modal');
        openTagsLibrary();
    });
    $('#btn-close-tags-library')?.addEventListener('click', () => hideModal('tags-library-modal'));
    $('#btn-close-tags-library-2')?.addEventListener('click', () => hideModal('tags-library-modal'));
    $('#library-search')?.addEventListener('input', filterLibraryContent);
    $('#library-sort')?.addEventListener('change', loadLibraryContent);
    // Library tab switching
    $('#library-tab-tags')?.addEventListener('click', () => switchLibraryTab('tags'));
    $('#library-tab-prompts')?.addEventListener('click', () => switchLibraryTab('prompts'));

    // Checkpoint search in filter modal
    $('#modal-checkpoint-search')?.addEventListener('input', (e) => {
        filterModalList('modal-checkpoint-list', e.target.value);
    });

    // Lora search in filter modal
    $('#modal-lora-search')?.addEventListener('input', (e) => {
        filterModalList('modal-lora-list', e.target.value);
    });

    // --- Batch Tag Export Modal ---
    $('#btn-batch-export-tags').addEventListener('click', showBatchExportModal);
    $('#btn-close-batch-export').addEventListener('click', () => hideModal('batch-export-modal'));
    $('#btn-cancel-batch-export').addEventListener('click', () => hideModal('batch-export-modal'));
    $('#btn-start-batch-export').addEventListener('click', executeBatchExport);

    // --- Import Tags (from Tag Modal) ---
    $('#btn-import-tags')?.addEventListener('click', () => {
        $('#import-tags-file').click();
    });
    $('#import-tags-file')?.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        try {
            const text = await file.text();
            const data = JSON.parse(text);

            // Validate the data structure
            if (!data.images || !Array.isArray(data.images)) {
                throw new Error('Invalid format: expected { images: [...] }');
            }

            // Ask user about overwrite preference
            const overwrite = confirm(
                `Found ${data.images.length} images in file.\n\n` +
                'Do you want to OVERWRITE existing tags?\n\n' +
                'Click OK to overwrite, Cancel to skip already-tagged images.'
            );

            const result = await API.importTags(data.images, overwrite);
            showToast(`Imported tags for ${result.imported} images (${result.skipped} skipped)`, 'success');

            // Refresh the gallery to show new tags
            loadImages();
        } catch (err) {
            showToast('Failed to import tags: ' + err.message, 'error');
        }
        e.target.value = ''; // Reset file input
    });

    // --- Censored Edit ---
    $('#btn-send-to-censor')?.addEventListener('click', (e) => {
        e.stopPropagation();
        if (AppState.selectedIds.size > 0 && typeof window.App.addToCensorQueue === 'function') {
            window.App.addToCensorQueue(Array.from(AppState.selectedIds));
        } else {
            switchView('censor');
            if (typeof window.initCensorEdit === 'function') window.initCensorEdit();
        }
    });
}

function filterCollapsibleList(type, query) {
    const list = document.getElementById(`${type}-list`);
    const items = list.querySelectorAll('.checkbox-label');
    query = query.toLowerCase();

    items.forEach(item => {
        const text = item.querySelector('.checkbox-text').textContent.toLowerCase();
        item.style.display = text.includes(query) ? 'flex' : 'none';
    });
}

function filterModalList(listId, query) {
    const list = document.getElementById(listId);
    if (!list) return;

    const items = list.querySelectorAll('.checkbox-label');
    query = query.toLowerCase();

    items.forEach(item => {
        const textEl = item.querySelector('.checkbox-text');
        if (textEl) {
            const text = textEl.textContent.toLowerCase();
            item.style.display = text.includes(query) ? '' : 'none';
        }
    });
}

function debounce(func, wait) {
    let timeout;
    return function (...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
    };
}

// ============== Scanning ==============

async function startScan() {
    const folderPath = $('#scan-folder-path').value.trim();
    if (!folderPath) {
        showToast('Please enter a folder path', 'error');
        return;
    }

    const recursive = $('#scan-recursive').checked;

    try {
        await API.startScan(folderPath, recursive);

        $('#scan-progress-container').style.display = 'block';
        $('#btn-start-scan').disabled = true;

        pollScanProgress();
    } catch (error) {
        showToast('Failed to start scan: ' + error.message, 'error');
    }
}

async function pollScanProgress(retryCount = 0) {
    try {
        const progress = await API.getScanProgress();
        console.log('Scan progress:', progress);

        const percent = progress.total > 0 ? (progress.current / progress.total) * 100 : 0;
        $('#scan-progress-fill').style.width = percent + '%';
        $('#scan-progress-text').textContent = progress.message || 'Processing...';

        if (progress.status === 'done') {
            showToast(progress.message, 'success');
            hideModal('scan-modal');
            $('#scan-progress-container').style.display = 'none';
            $('#btn-start-scan').disabled = false;
            loadImages();
            loadStats();
        } else if (progress.status === 'running' || progress.status === 'idle') {
            // If idle, the background task might just be starting, keep polling
            setTimeout(() => pollScanProgress(0), 500);
        }
    } catch (error) {
        console.error('Poll error:', error);
        if (retryCount < 3) {
            setTimeout(() => pollScanProgress(retryCount + 1), 1000);
        } else {
            showToast('Error checking scan progress', 'error');
            $('#btn-start-scan').disabled = false;
        }
    }
}

// ============== Tagging ==============

async function startTagging() {
    const threshold = parseFloat($('#tag-threshold').value);
    const characterThreshold = parseFloat($('#tag-character-threshold').value);
    const modelSelect = $('#tag-model-select').value;

    const options = {
        threshold,
        characterThreshold
    };

    // Handle custom model
    if (modelSelect === 'custom') {
        const modelPath = $('#tag-model-path').value.trim();
        const tagsPath = $('#tag-tags-path').value.trim();

        if (!modelPath) {
            showToast('Please enter a model path', 'error');
            return;
        }

        if (!tagsPath) {
            showToast('Please enter a Tags CSV path', 'error');
            return;
        }

        options.modelPath = modelPath;
        options.tagsPath = tagsPath;
    } else {
        options.modelName = modelSelect;
    }

    options.retagAll = $('#tag-retag-all').checked;
    options.useGpu = $('#tag-use-gpu')?.checked ?? true; // Default to GPU if checkbox is checked or missing

    try {
        await API.startTagging(options);

        $('#tag-progress-container').style.display = 'block';
        $('#btn-start-tag').disabled = true;

        pollTagProgress();
    } catch (error) {
        showToast('Failed to start tagging: ' + error.message, 'error');
    }
}

async function pollTagProgress() {
    try {
        const progress = await API.getTagProgress();

        const percent = progress.total > 0 ? (progress.current / progress.total) * 100 : 0;
        $('#tag-progress-fill').style.width = percent + '%';
        $('#tag-progress-text').textContent = progress.message;

        if (progress.status === 'done') {
            showToast(progress.message, 'success');
            hideModal('tag-modal');
            $('#tag-progress-container').style.display = 'none';
            $('#btn-start-tag').disabled = false;
            loadImages();
        } else if (progress.status === 'running') {
            setTimeout(pollTagProgress, 500);
        } else if (progress.status === 'error') {
            showToast(progress.message, 'error');
            $('#tag-progress-container').style.display = 'none';
            $('#btn-start-tag').disabled = false;
        }
    } catch (error) {
        showToast('Error checking tag progress', 'error');
    }
}

// ============== Stats ==============

async function loadStats() {
    try {
        const stats = await API.getStats();

        // Update generator counts in tabs
        let totalCount = 0;
        const genCounts = {};
        stats.generators.forEach(gen => {
            genCounts[gen.generator] = gen.count;
            totalCount += gen.count;

            // Legacy checkbox count update
            const countEl = $(`.checkbox-count[data-generator="${gen.generator}"]`);
            if (countEl) {
                countEl.textContent = gen.count;
            }
        });

        // Update generator tab counts
        const countAll = $('#count-all');
        if (countAll) countAll.textContent = totalCount;

        ['nai', 'comfyui', 'forge', 'webui', 'unknown'].forEach(gen => {
            const countEl = $(`#count-${gen}`);
            if (countEl) countEl.textContent = genCounts[gen] || 0;
        });

        // Store analytics for later use
        AppState.analytics = {
            checkpoints: stats.checkpoints || [],
            loras: stats.loras || [],
            top_tags: stats.top_tags || [],
            generatorCounts: genCounts,
            totalImages: totalCount
        };

        // Update model filters summary UI
        updateModelSelectionSummaries();

    } catch (error) {
        console.error('Failed to load stats:', error);
    }
}

// ============== Image Loading ==============

async function loadImages() {
    if (AppState.isLoading) return;

    AppState.isLoading = true;
    $('#gallery-loading').style.display = 'flex';
    $('#image-count').textContent = 'Loading...';

    try {
        const result = await API.getImages(AppState.filters);
        AppState.images = result.images;
        $('#image-count').textContent = `${result.count} images`;

        if (window.Gallery) {
            Gallery.setImages(AppState.images);
        }
    } catch (error) {
        showToast('Error loading images: ' + error.message, 'error');
    } finally {
        AppState.isLoading = false;
        $('#gallery-loading').style.display = 'none';
    }
}

// ============== UI Components ==============

function openModelSelect(type) {
    AppState.modalSelection.type = type;
    AppState.modalSelection.search = '';
    AppState.modalSelection.tempSelected = new Set(AppState.filters[`${type}s`]);

    $('#model-select-title').textContent = type === 'checkpoint' ? 'Select Checkpoints' : 'Select Loras';
    $('#model-select-search').value = '';

    renderModelSelectList();
    showModal('model-select-modal');
}

function renderModelSelectList() {
    const { type, tempSelected, search } = AppState.modalSelection;
    const items = type === 'checkpoint' ? AppState.analytics.checkpoints : AppState.analytics.loras;
    const list = $('#model-select-list');

    if (!items || items.length === 0) {
        list.innerHTML = '<div class="filter-empty" style="text-align: center; padding: 20px; color: var(--text-muted);">No models found</div>';
        return;
    }

    const filtered = items.filter(item => {
        const val = type === 'checkpoint' ? item.checkpoint : item.lora;
        return val.toLowerCase().includes(search);
    });

    list.innerHTML = filtered.map(item => {
        const value = type === 'checkpoint' ? item.checkpoint : item.lora;
        const isSelected = tempSelected.has(value);

        return `
            <div class="model-select-item ${isSelected ? 'selected' : ''}" data-value="${value}">
                <div class="checkbox-custom" style="background: ${isSelected ? 'var(--accent-primary)' : 'transparent'}; border-color: ${isSelected ? 'var(--accent-primary)' : 'var(--border-color)'}">
                    ${isSelected ? '✓' : ''}
                </div>
                <div class="item-text" title="${value}">${value}</div>
                <div class="item-count">${item.count}</div>
            </div>
        `;
    }).join('');

    // Add click handlers
    list.querySelectorAll('.model-select-item').forEach(el => {
        el.addEventListener('click', () => {
            const val = el.dataset.value;
            if (tempSelected.has(val)) {
                tempSelected.delete(val);
            } else {
                tempSelected.add(val);
            }
            renderModelSelectList();
        });
    });
}

function confirmModelSelection() {
    const { type, tempSelected } = AppState.modalSelection;
    AppState.filters[`${type}s`] = Array.from(tempSelected);

    updateModelSelectionSummaries();
    hideModal('model-select-modal');
}

function updateModelSelectionSummaries() {
    const cpCount = AppState.filters.checkpoints?.length || 0;
    const lrCount = AppState.filters.loras?.length || 0;

    // These elements may not exist in compact sidebar - use optional chaining
    const cpSummary = $('#selection-summary-checkpoints');
    const loraSummary = $('#selection-summary-loras');

    if (cpSummary) {
        cpSummary.textContent = cpCount === 0 ? 'No checkpoints selected' :
            (cpCount === 1 ? AppState.filters.checkpoints[0] : `${cpCount} checkpoints selected`);
    }

    if (loraSummary) {
        loraSummary.textContent = lrCount === 0 ? 'No Loras selected' :
            (lrCount === 1 ? AppState.filters.loras[0] : `${lrCount} Loras selected`);
    }
}

function updateCollapsibleFilterUI(type, items) {
    // Legacy support, now using summaries
    updateModelSelectionSummaries();
}

// ============== Tags & Prompts Library ==============

const libraryData = {
    currentTab: 'tags',
    tags: [],
    prompts: []
};

function openTagsLibrary() {
    showModal('tags-library-modal');
    loadLibraryContent();
}

function switchLibraryTab(tab) {
    libraryData.currentTab = tab;
    // Update tab button active states
    const tagsTab = $('#library-tab-tags');
    const promptsTab = $('#library-tab-prompts');
    if (tagsTab) {
        tagsTab.classList.toggle('active', tab === 'tags');
        tagsTab.classList.toggle('btn-secondary', tab === 'tags');
        tagsTab.classList.toggle('btn-ghost', tab !== 'tags');
    }
    if (promptsTab) {
        promptsTab.classList.toggle('active', tab === 'prompts');
        promptsTab.classList.toggle('btn-secondary', tab === 'prompts');
        promptsTab.classList.toggle('btn-ghost', tab !== 'prompts');
    }
    loadLibraryContent();
}

async function loadLibraryContent() {
    const content = $('#library-content');
    const statsText = $('#library-stats-text');
    const sortBy = $('#library-sort')?.value || 'frequency';

    content.innerHTML = '<div class="spinner"></div>';

    try {
        if (libraryData.currentTab === 'tags') {
            const result = await API.getTagsLibrary(sortBy, 2000);
            libraryData.tags = result.tags;
            renderLibraryTags(result.tags);
            statsText.textContent = `${result.total} unique tags found`;
        } else {
            const result = await API.getPromptsLibrary(99999);
            libraryData.prompts = result.prompts;
            renderLibraryPrompts(result.prompts);
            statsText.textContent = `${result.total} unique prompts found`;
        }
    } catch (error) {
        content.innerHTML = '<p style="color: var(--accent-danger);">Failed to load library</p>';
        console.error('Library load error:', error);
    }
}

function renderLibraryTags(tags) {
    const content = $('#library-content');
    content.style.flexDirection = 'row';
    content.innerHTML = tags.map(t => `
        <div class="library-tag" data-tag="${t.tag}" title="Click to add as filter">
            <span class="tag-name">${t.tag}</span>
            <span class="tag-count">${t.count}</span>
        </div>
    `).join('');

    content.querySelectorAll('.library-tag').forEach(el => {
        el.addEventListener('click', () => {
            const tag = el.dataset.tag;
            if (!AppState.filters.tags.includes(tag)) {
                AppState.filters.tags.push(tag);
                updateFilterSummary();
                hideModal('tags-library-modal');
                loadImages();
                showToast(`Added "${tag}" to filters`, 'success');
            }
        });
    });
}

function renderLibraryPrompts(prompts) {
    const content = $('#library-content');
    content.style.flexDirection = 'row';
    content.innerHTML = prompts.map(p => `
        <div class="library-tag" data-prompt="${p.prompt}" title="Click to add as filter">
            <span class="tag-name">${p.prompt}</span>
            <span class="tag-count">${p.count}</span>
        </div>
    `).join('');

    content.querySelectorAll('.library-tag').forEach(el => {
        el.addEventListener('click', () => {
            const prompt = el.dataset.prompt;
            if (!AppState.filters.prompts.includes(prompt)) {
                AppState.filters.prompts.push(prompt);
                console.log('Added prompt to filters:', prompt, 'Current prompts:', AppState.filters.prompts);
                updateFilterSummary();
                hideModal('tags-library-modal');
                loadImages();
                showToast(`Added "${prompt}" to filters`, 'success');
            }
        });
    });
}

function filterLibraryContent() {
    const query = $('#library-search')?.value.toLowerCase() || '';

    if (libraryData.currentTab === 'tags') {
        const filtered = libraryData.tags.filter(t => t.tag.toLowerCase().includes(query));
        renderLibraryTags(filtered);
    } else {
        const filtered = libraryData.prompts.filter(p => p.prompt.toLowerCase().includes(query));
        renderLibraryPrompts(filtered);
    }
}

// ============== Modal Tag/Prompt Autocomplete ==============

// Debounced tag/prompt search for autocomplete
const debouncedTagSearch = debounce(async (query) => {
    const suggestionsDiv = $('#modal-tag-suggestions');
    if (!query || query.length < 2) {
        suggestionsDiv.innerHTML = '';
        suggestionsDiv.classList.remove('visible');
        return;
    }

    try {
        const tags = await API.getTagsLibrary('frequency', 1000);
        const filtered = tags.filter(t =>
            t.tag.toLowerCase().includes(query.toLowerCase())
        ).slice(0, 15);

        if (filtered.length === 0) {
            suggestionsDiv.innerHTML = '';
            suggestionsDiv.classList.remove('visible');
            return;
        }

        suggestionsDiv.innerHTML = filtered.map(t => `
            <div class="suggestion-item" data-tag="${t.tag}">
                <span class="suggestion-name">${t.tag}</span>
                <span class="suggestion-count">${t.count}</span>
            </div>
        `).join('');

        suggestionsDiv.classList.add('visible');

        // Add click handlers
        suggestionsDiv.querySelectorAll('.suggestion-item').forEach(item => {
            item.addEventListener('click', () => {
                const tag = item.dataset.tag;
                if (!AppState.filters.tags.includes(tag)) {
                    AppState.filters.tags.push(tag);
                    renderModalActiveTags();
                }
                $('#modal-tag-search').value = '';
                suggestionsDiv.innerHTML = '';
                suggestionsDiv.classList.remove('visible');
            });
        });
    } catch (e) {
        console.error('Error fetching tag suggestions:', e);
    }
}, 250);

function searchModalTags(query) {
    debouncedTagSearch(query);
}

const debouncedPromptSearch = debounce(async (query) => {
    const suggestionsDiv = $('#modal-prompt-suggestions');
    if (!query || query.length < 2) {
        suggestionsDiv.innerHTML = '';
        suggestionsDiv.classList.remove('visible');
        return;
    }

    try {
        const prompts = await API.getPromptsLibrary(1000);
        const filtered = prompts.filter(p =>
            p.prompt.toLowerCase().includes(query.toLowerCase())
        ).slice(0, 15);

        if (filtered.length === 0) {
            suggestionsDiv.innerHTML = '';
            suggestionsDiv.classList.remove('visible');
            return;
        }

        suggestionsDiv.innerHTML = filtered.map(p => `
            <div class="suggestion-item" data-prompt="${p.prompt}">
                <span class="suggestion-name">${p.prompt}</span>
                <span class="suggestion-count">${p.count}</span>
            </div>
        `).join('');

        suggestionsDiv.classList.add('visible');

        // Add click handlers
        suggestionsDiv.querySelectorAll('.suggestion-item').forEach(item => {
            item.addEventListener('click', () => {
                const prompt = item.dataset.prompt;
                if (!AppState.filters.prompts.includes(prompt)) {
                    AppState.filters.prompts.push(prompt);
                    renderModalActivePrompts();
                }
                $('#modal-prompt-search').value = '';
                suggestionsDiv.innerHTML = '';
                suggestionsDiv.classList.remove('visible');
            });
        });
    } catch (e) {
        console.error('Error fetching prompt suggestions:', e);
    }
}, 250);

function searchModalPrompts(query) {
    debouncedPromptSearch(query);
}

function renderModalActiveTags() {
    const container = $('#modal-active-tags');
    if (!container) return;

    container.innerHTML = AppState.filters.tags.map(tag => `
        <span class="active-tag" data-tag="${tag}">
            ${tag}
            <span class="remove-tag">×</span>
        </span>
    `).join('');

    container.querySelectorAll('.active-tag').forEach(el => {
        el.querySelector('.remove-tag').addEventListener('click', () => {
            const tag = el.dataset.tag;
            AppState.filters.tags = AppState.filters.tags.filter(t => t !== tag);
            renderModalActiveTags();
        });
    });
}

function renderModalActivePrompts() {
    const container = $('#modal-active-prompts');
    if (!container) return;

    container.innerHTML = AppState.filters.prompts.map(prompt => `
        <span class="active-tag" data-prompt="${prompt}">
            ${prompt}
            <span class="remove-tag">×</span>
        </span>
    `).join('');

    container.querySelectorAll('.active-tag').forEach(el => {
        el.querySelector('.remove-tag').addEventListener('click', () => {
            const prompt = el.dataset.prompt;
            AppState.filters.prompts = AppState.filters.prompts.filter(p => p !== prompt);
            renderModalActivePrompts();
        });
    });
}

function updateSelectionUI() {
    const fab = $('#selection-actions');
    const countEl = $('#selection-count');

    if (AppState.selectedIds.size > 0) {
        fab.style.display = 'flex';
        countEl.textContent = `${AppState.selectedIds.size} items selected`;
    } else {
        fab.style.display = 'none';
    }
}

function showConfirm(title, message, onOk) {
    $('#confirm-title').textContent = title;
    $('#confirm-message').textContent = message;

    const okBtn = $('#btn-confirm-ok');
    // Remove old listeners by cloning
    const newOkBtn = okBtn.cloneNode(true);
    okBtn.parentNode.replaceChild(newOkBtn, okBtn);

    newOkBtn.addEventListener('click', () => {
        hideModal('confirm-modal');
        onOk();
    });

    showModal('confirm-modal');
}

function showRandomImage() {
    if (AppState.images.length === 0) {
        showToast('No images available', 'info');
        return;
    }

    const randomIndex = Math.floor(Math.random() * AppState.images.length);
    const randomImage = AppState.images[randomIndex];

    if (window.Gallery) {
        Gallery.openPreview(randomImage.id);
    }
}

async function showAnalytics() {
    try {
        // Stats are already updated via loadStats regularly, but we can refresh
        await loadStats();
        const data = AppState.analytics;

        $('#analytics-checkpoints').innerHTML = data.checkpoints.length ?
            data.checkpoints.map(c => `
                <div class="analytics-item clickable" data-type="checkpoint" data-value="${c.checkpoint}">
                    <span class="item-name">${c.checkpoint}</span>
                    <span class="item-count">${c.count}</span>
                </div>
            `).join('') : '<p>No checkpoints found</p>';

        $('#analytics-loras').innerHTML = data.loras.length ?
            data.loras.map(l => `
                <div class="analytics-item clickable" data-type="lora" data-value="${l.lora}">
                    <span class="item-name">${l.lora}</span>
                    <span class="item-count">${l.count}</span>
                </div>
            `).join('') : '<p>No Loras found</p>';

        $('#analytics-tags').innerHTML = data.top_tags.length ?
            data.top_tags.map(t => `
                <div class="analytics-item clickable" data-type="tag" data-value="${t.tag}">
                    <span class="item-name">${t.tag}</span>
                    <span class="item-count">${t.count}</span>
                </div>
            `).join('') : '<p>No tags found</p>';

        // Add click handlers to all analytics items
        $$('#analytics-modal .analytics-item.clickable').forEach(el => {
            el.addEventListener('click', () => {
                const type = el.dataset.type;
                const value = el.dataset.value;
                applyAnalyticsFilter(type, value);
            });
        });

        showModal('analytics-modal');
    } catch (e) {
        showToast('Error loading analytics: ' + e.message, 'error');
    }
}

function applyAnalyticsFilter(type, value) {
    if (type === 'checkpoint') {
        AppState.filters.checkpoints = [value];
        updateModelSelectionSummaries();
    } else if (type === 'lora') {
        AppState.filters.loras = [value];
        updateModelSelectionSummaries();
    } else if (type === 'tag') {
        if (!AppState.filters.tags.includes(value)) {
            AppState.filters.tags.push(value);
            addTagToUI(value);
        }
    }
    hideModal('analytics-modal');
    loadImages();
    showToast(`Filter applied: ${value}`, 'success');
}

function addTagToUI(tag) {
    const container = $('#active-tags');
    const tagEl = document.createElement('span');
    tagEl.className = 'active-tag';
    tagEl.innerHTML = `${tag} <span class="remove-tag" data-tag="${tag}">×</span>`;
    tagEl.querySelector('.remove-tag').addEventListener('click', () => removeTagFilter(tag));
    container.appendChild(tagEl);
}


async function showExportModal() {
    if (AppState.selectedIds.size === 0) return;

    $('#export-title').textContent = '📤 Export Prompts';
    $('#export-count').textContent = `${AppState.selectedIds.size} images selected`;
    $('#btn-export-tags').innerHTML = '🏷️ Export Tags Instead';
    const textArea = $('#export-text');
    textArea.value = 'Loading prompts...';

    showModal('export-modal');

    try {
        const prompts = [];
        const ids = Array.from(AppState.selectedIds);

        for (const id of ids) {
            const result = await API.getImage(id);
            if (result.image.prompt) {
                prompts.push(result.image.prompt);
            }
        }

        textArea.value = prompts.join('\n\n');
    } catch (e) {
        textArea.value = 'Error loading prompts: ' + e.message;
    }
}

async function showExportTagsModal() {
    if (AppState.selectedIds.size === 0) return;

    $('#export-title').textContent = '🏷️ Export Tags';
    $('#export-count').textContent = `${AppState.selectedIds.size} images selected`;
    $('#btn-export-tags').innerHTML = '📤 Export Prompts Instead';
    const textArea = $('#export-text');
    textArea.value = 'Loading tags...';

    showModal('export-modal');
    try {
        const allTags = new Set();
        const ids = Array.from(AppState.selectedIds);

        for (const id of ids) {
            const result = await API.getImage(id);
            if (result.tags) {
                result.tags.forEach(t => allTags.add(t.tag));
            }
        }

        // Sort alphabetically and join
        const sortedTags = Array.from(allTags).sort();
        textArea.value = sortedTags.join(', ');
    } catch (e) {
        textArea.value = 'Error loading tags: ' + e.message;
    }
}

function showBatchExportModal() {
    if (AppState.selectedIds.size === 0) {
        showToast('Please select images first', 'error');
        return;
    }

    $('#batch-export-count').textContent = `${AppState.selectedIds.size} images selected`;
    $('#batch-export-progress').style.display = 'none';
    $('#btn-start-batch-export').disabled = false;
    showModal('batch-export-modal');
}

async function executeBatchExport() {
    const outputFolder = $('#batch-export-folder').value.trim();
    if (!outputFolder) {
        showToast('Please enter an output folder', 'error');
        return;
    }

    const prefix = $('#batch-export-prefix').value;
    const blacklistText = $('#batch-export-blacklist').value;
    const blacklist = blacklistText ? blacklistText.split(',').map(t => t.trim()).filter(t => t) : [];

    const imageIds = Array.from(AppState.selectedIds);

    // Show progress
    $('#batch-export-progress').style.display = 'block';
    $('#batch-export-progress-fill').style.width = '0%';
    $('#batch-export-progress-text').textContent = 'Exporting...';
    $('#btn-start-batch-export').disabled = true;

    try {
        const result = await API.exportTagsBatch(imageIds, outputFolder, blacklist, prefix);

        $('#batch-export-progress-fill').style.width = '100%';

        if (result.status === 'ok') {
            showToast(`Exported ${result.exported} tag files successfully!`, 'success');
            hideModal('batch-export-modal');
        } else {
            showToast('Export failed: ' + (result.errors?.join(', ') || 'Unknown error'), 'error');
        }
    } catch (e) {
        showToast('Export failed: ' + e.message, 'error');
    } finally {
        $('#batch-export-progress').style.display = 'none';
        $('#btn-start-batch-export').disabled = false;
    }
}

// ============== Filters ==============

function updateFiltersFromUI() {
    // Get generators
    const generators = [];
    $$('#modal-generator-filters input[type="checkbox"]:checked').forEach(cb => {
        generators.push(cb.value);
    });
    AppState.filters.generators = generators;

    // Get ratings
    const ratings = [];
    $$('#modal-rating-filters input[type="checkbox"]:checked').forEach(cb => {
        ratings.push(cb.value);
    });
    AppState.filters.ratings = ratings;
}

function applyFilters() {
    updateFiltersFromUI();
    loadImages();
}

function clearFilters() {
    $$('#modal-generator-filters input[type="checkbox"]').forEach(cb => {
        cb.checked = true;
    });
    $$('#modal-rating-filters input[type="checkbox"]').forEach(cb => {
        cb.checked = true;
    });
    AppState.filters.generators = ['comfyui', 'nai', 'webui', 'forge', 'unknown'];
    AppState.filters.ratings = ['general', 'sensitive', 'questionable', 'explicit'];
    AppState.filters.tags = [];
    AppState.filters.search = '';
    $('#tag-search').value = '';
    $('#prompt-search').value = '';
    $('#active-tags').innerHTML = '';
    loadImages();
}

async function searchTags(e) {
    const query = e.target.value.trim();
    if (query.length < 2) {
        $('#tag-suggestions').classList.remove('visible');
        return;
    }

    try {
        const result = await API.getTags();
        const filtered = result.tags
            .filter(t => t.tag.toLowerCase().includes(query.toLowerCase()))
            .slice(0, 10);

        const suggestionsEl = $('#tag-suggestions');
        suggestionsEl.innerHTML = filtered.map(t => `
            <div class="tag-suggestion" data-tag="${t.tag}">
                ${t.tag} <span style="color: var(--text-muted)">(${t.count})</span>
            </div>
        `).join('');

        suggestionsEl.classList.add('visible');

        // Add click handlers
        suggestionsEl.querySelectorAll('.tag-suggestion').forEach(el => {
            el.addEventListener('click', () => {
                addTagFilter(el.dataset.tag);
                $('#tag-search').value = '';
                suggestionsEl.classList.remove('visible');
            });
        });
    } catch (error) {
        console.error('Failed to search tags:', error);
    }
}

function addTagFilter(tag) {
    if (!AppState.filters.tags.includes(tag)) {
        AppState.filters.tags.push(tag);
        renderActiveTagFilters();
    }
}

function removeTagFilter(tag) {
    AppState.filters.tags = AppState.filters.tags.filter(t => t !== tag);
    renderActiveTagFilters();
}

function renderActiveTagFilters() {
    const container = $('#active-tags');
    container.innerHTML = AppState.filters.tags.map(tag => `
        <span class="active-tag">
            ${tag}
            <span class="remove-tag" data-tag="${tag}">✕</span>
        </span>
    `).join('');

    container.querySelectorAll('.remove-tag').forEach(el => {
        el.addEventListener('click', () => removeTagFilter(el.dataset.tag));
    });
}

// ============== Unified Filter Modal ==============

async function openFilterModal() {
    // Sync modal state with current AppState
    $$('#modal-generator-filters input').forEach(cb => {
        cb.checked = AppState.filters.generators.includes(cb.value);
    });
    $$('#modal-rating-filters input').forEach(cb => {
        cb.checked = AppState.filters.ratings.includes(cb.value);
    });
    $('#modal-prompt-search').value = AppState.filters.search || '';

    // Show active tags and prompts
    renderModalActiveTags();
    renderModalActivePrompts();

    // Load checkpoints and loras into modal lists
    await loadModalFilterLists();

    showModal('filter-modal');
}

function renderModalActiveTags() {
    const container = $('#modal-active-tags');
    container.innerHTML = AppState.filters.tags.map(tag => `
        <span class="active-tag">${tag} <span class="remove-modal-tag" data-tag="${tag}">×</span></span>
    `).join('');

    container.querySelectorAll('.remove-modal-tag').forEach(el => {
        el.addEventListener('click', () => {
            const tag = el.dataset.tag;
            AppState.filters.tags = AppState.filters.tags.filter(t => t !== tag);
            renderModalActiveTags();
        });
    });
}

function renderModalActivePrompts() {
    // Try to find or create a prompts display area in the modal
    let container = document.getElementById('modal-active-prompts');
    if (!container) {
        // Create container after prompt search input if it doesn't exist
        const promptSearch = document.getElementById('modal-prompt-search');
        if (promptSearch) {
            container = document.createElement('div');
            container.id = 'modal-active-prompts';
            container.className = 'active-tags';
            container.style.marginTop = '8px';
            promptSearch.parentNode.insertBefore(container, promptSearch.nextSibling);
        } else {
            return;
        }
    }

    container.innerHTML = AppState.filters.prompts.map(prompt => `
        <span class="active-tag">${prompt} <span class="remove-modal-prompt" data-prompt="${prompt}">×</span></span>
    `).join('');

    container.querySelectorAll('.remove-modal-prompt').forEach(el => {
        el.addEventListener('click', () => {
            const prompt = el.dataset.prompt;
            AppState.filters.prompts = AppState.filters.prompts.filter(p => p !== prompt);
            renderModalActivePrompts();
        });
    });
}

async function loadModalFilterLists() {
    try {
        const data = await API.getStats();

        // Render checkpoints
        const cpList = $('#modal-checkpoint-list');
        cpList.innerHTML = (data.checkpoints || []).map(cp => `
            <label class="checkbox-label">
                <input type="checkbox" value="${cp.checkpoint}" ${AppState.filters.checkpoints?.includes(cp.checkpoint) ? 'checked' : ''}>
                <span class="checkbox-custom"></span>
                <span class="checkbox-text">${cp.checkpoint}</span>
                <span class="checkbox-count">${cp.count}</span>
            </label>
        `).join('');

        // Render loras
        const loraList = $('#modal-lora-list');
        loraList.innerHTML = (data.loras || []).map(l => `
            <label class="checkbox-label">
                <input type="checkbox" value="${l.lora}" ${AppState.filters.loras?.includes(l.lora) ? 'checked' : ''}>
                <span class="checkbox-custom"></span>
                <span class="checkbox-text">${l.lora}</span>
                <span class="checkbox-count">${l.count}</span>
            </label>
        `).join('');
    } catch (e) {
        console.error('Failed to load filter lists:', e);
    }
}

async function searchModalTags(query) {
    const suggestionsEl = $('#modal-tag-suggestions');
    if (query.length < 2) {
        suggestionsEl.innerHTML = '';
        suggestionsEl.classList.remove('visible');
        return;
    }

    try {
        const result = await API.getTags();
        const filtered = result.tags
            .filter(t => t.tag.toLowerCase().includes(query.toLowerCase()))
            .slice(0, 8);

        suggestionsEl.innerHTML = filtered.map(t => `
            <div class="tag-suggestion" data-tag="${t.tag}">
                ${t.tag} <span style="color: var(--text-muted)">(${t.count})</span>
            </div>
        `).join('');

        // Show/hide based on results
        if (filtered.length > 0) {
            suggestionsEl.classList.add('visible');
        } else {
            suggestionsEl.classList.remove('visible');
        }

        suggestionsEl.querySelectorAll('.tag-suggestion').forEach(el => {
            el.addEventListener('click', () => {
                if (!AppState.filters.tags.includes(el.dataset.tag)) {
                    AppState.filters.tags.push(el.dataset.tag);
                    renderModalActiveTags();
                }
                $('#modal-tag-search').value = '';
                suggestionsEl.innerHTML = '';
                suggestionsEl.classList.remove('visible');
            });
        });
    } catch (e) {
        console.error('Failed to search tags:', e);
    }
}

// Cache for prompts library to avoid repeated API calls
let promptsLibraryCache = null;

async function searchModalPrompts(query) {
    const suggestionsEl = $('#modal-prompt-suggestions');
    if (!suggestionsEl) return;

    if (query.length < 2) {
        suggestionsEl.innerHTML = '';
        suggestionsEl.classList.remove('visible');
        return;
    }

    try {
        // Cache the prompts library for better performance
        if (!promptsLibraryCache) {
            const result = await API.getPromptsLibrary(5000);
            promptsLibraryCache = result.prompts || [];
        }

        const filtered = promptsLibraryCache
            .filter(p => p.prompt.toLowerCase().includes(query.toLowerCase()))
            .slice(0, 10);

        suggestionsEl.innerHTML = filtered.map(p => `
            <div class="tag-suggestion" data-prompt="${p.prompt}">
                ${p.prompt} <span style="color: var(--text-muted)">(${p.count})</span>
            </div>
        `).join('');

        // Show/hide based on results
        if (filtered.length > 0) {
            suggestionsEl.classList.add('visible');
        } else {
            suggestionsEl.classList.remove('visible');
        }

        suggestionsEl.querySelectorAll('.tag-suggestion').forEach(el => {
            el.addEventListener('click', () => {
                const prompt = el.dataset.prompt;
                if (!AppState.filters.prompts.includes(prompt)) {
                    AppState.filters.prompts.push(prompt);
                    renderModalActivePrompts();
                }
                $('#modal-prompt-search').value = '';
                suggestionsEl.innerHTML = '';
                suggestionsEl.classList.remove('visible');
            });
        });
    } catch (e) {
        console.error('Failed to search prompts:', e);
    }
}

function applyModalFilters() {
    // Get generators
    const generators = [];
    $$('#modal-generator-filters input:checked').forEach(cb => generators.push(cb.value));
    AppState.filters.generators = generators;

    // Get ratings
    const ratings = [];
    $$('#modal-rating-filters input:checked').forEach(cb => ratings.push(cb.value));
    AppState.filters.ratings = ratings;

    // Get checkpoints
    const checkpoints = [];
    $$('#modal-checkpoint-list input:checked').forEach(cb => checkpoints.push(cb.value));
    AppState.filters.checkpoints = checkpoints;

    // Get loras
    const loras = [];
    $$('#modal-lora-list input:checked').forEach(cb => loras.push(cb.value));
    AppState.filters.loras = loras;

    // Prompts: don't use search bar - prompts array is built via Enter key
    // Clear search bar since prompts are in the array now
    AppState.filters.search = '';
    $('#modal-prompt-search').value = '';

    // Get dimension filters
    const minWidth = parseInt($('#filter-min-width').value) || null;
    const maxWidth = parseInt($('#filter-max-width').value) || null;
    const minHeight = parseInt($('#filter-min-height').value) || null;
    const maxHeight = parseInt($('#filter-max-height').value) || null;
    AppState.filters.minWidth = minWidth;
    AppState.filters.maxWidth = maxWidth;
    AppState.filters.minHeight = minHeight;
    AppState.filters.maxHeight = maxHeight;

    // Get aspect ratio
    const aspectRadio = $('input[name="aspect-ratio"]:checked');
    AppState.filters.aspectRatio = aspectRadio ? aspectRadio.value : '';

    // Update all filter summaries (gallery sidebar + view-specific)
    updateFilterSummary();
    // Also update Auto-Separate and Manual Sort summaries if their functions exist
    if (typeof window.updateAutoSepSummary === 'function') window.updateAutoSepSummary();
    if (typeof window.updateManualSortFilterSummary === 'function') window.updateManualSortFilterSummary();

    hideModal('filter-modal');
    loadImages();
    showToast('Filters applied', 'success');
}

function resetAllFilters() {
    AppState.filters = {
        generators: ['comfyui', 'nai', 'webui', 'forge', 'unknown'],
        ratings: ['general', 'sensitive', 'questionable', 'explicit'],
        tags: [],
        checkpoints: [],
        loras: [],
        prompts: [],
        search: '',
        sortBy: 'newest',
        limit: 0,
        minWidth: null,
        maxWidth: null,
        minHeight: null,
        maxHeight: null,
        aspectRatio: ''
    };

    // Reset modal checkboxes
    $$('#modal-generator-filters input').forEach(cb => cb.checked = true);
    $$('#modal-rating-filters input').forEach(cb => cb.checked = true);
    $$('#modal-checkpoint-list input').forEach(cb => cb.checked = false);
    $$('#modal-lora-list input').forEach(cb => cb.checked = false);
    $('#modal-prompt-search').value = '';
    renderModalActiveTags();
    renderModalActivePrompts();

    // Reset dimension filters
    $('#filter-min-width').value = '';
    $('#filter-max-width').value = '';
    $('#filter-min-height').value = '';
    $('#filter-max-height').value = '';
    $$('input[name="aspect-ratio"]').forEach(r => r.checked = r.value === '');

    // Update all filter summaries
    updateFilterSummary();
    if (typeof window.updateAutoSepSummary === 'function') window.updateAutoSepSummary();
    if (typeof window.updateManualSortFilterSummary === 'function') window.updateManualSortFilterSummary();

    hideModal('filter-modal');
    loadImages();
    showToast('Filters cleared', 'success');
}

function updateFilterSummary() {
    const f = AppState.filters;
    const allGens = ['comfyui', 'nai', 'webui', 'forge', 'unknown'];
    const allRatings = ['general', 'sensitive', 'questionable', 'explicit'];

    // Generators
    $('#summary-generators').textContent =
        f.generators.length === allGens.length ? 'All' :
            f.generators.length === 0 ? 'None' :
                f.generators.length > 2 ? `${f.generators.length} selected` : f.generators.join(', ');

    // Ratings
    $('#summary-ratings').textContent =
        f.ratings.length === allRatings.length ? 'All' :
            f.ratings.length === 0 ? 'None' :
                f.ratings.length > 2 ? `${f.ratings.length} selected` : f.ratings.join(', ');

    // Tags
    $('#summary-tags').textContent =
        f.tags.length === 0 ? 'None' :
            f.tags.length > 2 ? `${f.tags.length} tags` : f.tags.join(', ');

    // Checkpoints
    $('#summary-checkpoints').textContent =
        (!f.checkpoints || f.checkpoints.length === 0) ? 'None' :
            `${f.checkpoints.length} selected`;

    // Loras
    $('#summary-loras').textContent =
        (!f.loras || f.loras.length === 0) ? 'None' :
            `${f.loras.length} selected`;

    // Prompt (now uses prompts array)
    const promptSummary = $('#summary-prompt');
    if (promptSummary) {
        promptSummary.textContent =
            (!f.prompts || f.prompts.length === 0) ? 'None' :
                f.prompts.length > 2 ? `${f.prompts.length} prompts` : f.prompts.join(', ');
    }
}

// ============== Initialization ==============

document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    switchView('gallery');
    loadStats();
    updateFilterSummary();

    // Initialize Censor Edit module so addToCensorQueue is available from Gallery
    if (typeof window.initCensorEdit === 'function') {
        window.initCensorEdit();
    }
});


// Export for other modules
window.App = {
    API,
    AppState,
    showToast,
    showModal,
    hideModal,
    formatSize,
    loadImages,
    loadStats,
    updateSelectionUI,
    showConfirm,
    showRandomImage,
    showAnalytics,
    showExportModal,
    showExportTagsModal,
    updateCollapsibleFilterUI,
    openModelSelect,
    renderModelSelectList,
    confirmModelSelection,
    updateModelSelectionSummaries,
    openFilterModal,
    applyModalFilters,
    resetAllFilters,
    updateFilterSummary,
    openTagsLibrary,
    switchLibraryTab,
    filterLibraryContent,
    $,
    $$
};
