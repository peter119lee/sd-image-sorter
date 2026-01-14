/**
 * SD Image Sorter - Manual Sort Module
 * Rhythm-game style keyboard-driven image sorting
 */

const ManualSortState = {
    active: false,
    currentImage: null,
    currentTags: [],
    folders: { w: '', a: '', s: '', d: '' },
    index: 0,
    total: 0,
    combo: 0,
    lastActionTime: 0,
    history: [],
    images: []  // For gallery preview
};

// Key mappings
const KEY_MAP = {
    'w': 'w', 'W': 'w', 'ArrowUp': 'w',
    'a': 'a', 'A': 'a', 'ArrowLeft': 'a',
    's': 's', 'S': 's', 'ArrowDown': 's',
    'd': 'd', 'D': 'd', 'ArrowRight': 'd',
    ' ': 'skip',
    'z': 'undo', 'Z': 'undo'
};

const DIRECTION_MAP = {
    'w': 'up',
    'a': 'left',
    's': 'down',
    'd': 'right'
};

// ============== Initialization ==============

function initManualSort() {
    const { $, $$ } = window.App;

    // Folder path inputs
    $$('.folder-path-input').forEach(input => {
        input.addEventListener('change', () => {
            ManualSortState.folders[input.dataset.key] = input.value;
        });
    });

    // Edit Filters button - open unified filter modal
    const filterBtn = $('#btn-manual-sort-filters');
    if (filterBtn) {
        filterBtn.addEventListener('click', () => {
            if (window.App.openFilterModal) {
                window.App.openFilterModal();
            }
        });
    }

    // Start sorting button
    $('#btn-start-sorting').addEventListener('click', startSorting);

    // Exit sorting button
    $('#btn-exit-sorting').addEventListener('click', exitSorting);

    // Keyboard listener (added when sorting starts)

    // Update filter summary display initially
    updateManualSortFilterSummary();
}

// ============== Start Sorting ==============

async function startSorting() {
    const { $, $$, API, showToast, AppState } = window.App;

    // Collect folder paths
    const folders = {};
    $$('.folder-path-input').forEach(input => {
        if (input.value.trim()) {
            folders[input.dataset.key] = input.value.trim();
        }
    });

    // Validate at least one folder
    if (Object.keys(folders).length === 0) {
        showToast('Please configure at least one destination folder', 'error');
        return;
    }

    ManualSortState.folders = folders;

    // Get filters from unified AppState
    const filters = AppState.filters;
    const generators = filters.generators && filters.generators.length > 0 ? filters.generators : null;
    const ratings = filters.ratings && filters.ratings.length > 0 ? filters.ratings : null;
    const tags = filters.tags && filters.tags.length > 0 ? filters.tags : null;

    try {
        // Set folders on server
        await API.setSortFolders(folders);

        // Start session with unified filters
        const result = await API.startSortSession(
            generators,
            ratings,
            folders,
            tags
        );

        if (result.total_images === 0) {
            showToast('No images to sort with current filters', 'error');
            return;
        }

        // Fetch images for gallery preview
        const imagesResult = await API.getImages({
            generators: generators.length > 0 ? generators : null,
            limit: 10000
        });

        ManualSortState.active = true;
        ManualSortState.total = result.total_images;
        ManualSortState.index = 0;
        ManualSortState.combo = 0;
        ManualSortState.history = [];
        ManualSortState.images = imagesResult.images || [];

        // Update folder names in UI
        updateFolderNames();

        // Show sort interface
        $('#sort-setup').style.display = 'none';
        $('#sort-interface').style.display = 'flex';

        // Load first image
        await loadCurrentImage();

        // Add keyboard listener
        document.addEventListener('keydown', handleSortKeypress);

        // Play start sound
        window.AudioManager?.play('start');

    } catch (error) {
        showToast('Failed to start sorting: ' + error.message, 'error');
    }
}

function updateFolderNames() {
    const { $ } = window.App;

    Object.entries(ManualSortState.folders).forEach(([key, path]) => {
        const nameEl = $(`#folder-name-${key}`);
        if (nameEl && path) {
            // Get folder name from path
            const parts = path.split(/[/\\]/);
            nameEl.textContent = parts[parts.length - 1] || path;
        }
    });
}

// ============== Load Current Image ==============

async function loadCurrentImage() {
    const { $, API } = window.App;

    try {
        const result = await API.getCurrentSortImage();

        if (result.done) {
            finishSorting();
            return;
        }

        ManualSortState.currentImage = result.image;
        ManualSortState.currentTags = result.tags || [];
        ManualSortState.index = result.index;
        ManualSortState.total = result.total;

        // Update image
        const imgWrapper = $('.current-image-wrapper');
        imgWrapper.classList.remove('fly-up', 'fly-down', 'fly-left', 'fly-right', 'skip');
        imgWrapper.classList.add('slide-in');

        const img = $('#current-image');
        img.src = API.getImageUrl(result.image.id);

        // Update tags
        const tagsEl = $('#current-image-tags');
        const topTags = ManualSortState.currentTags.slice(0, 5);
        tagsEl.innerHTML = topTags.map(t => `<span class="image-tag">${t.tag}</span>`).join('');

        // Update progress
        updateProgress();

        // Remove slide-in class after animation
        setTimeout(() => {
            imgWrapper.classList.remove('slide-in');
        }, 300);

    } catch (error) {
        console.error('Failed to load current image:', error);
    }
}

function updateProgress() {
    const { $ } = window.App;
    const percent = ManualSortState.total > 0
        ? (ManualSortState.index / ManualSortState.total) * 100
        : 0;

    $('#sort-progress-fill').style.width = percent + '%';
    $('#sort-progress-text').textContent = `${ManualSortState.index} / ${ManualSortState.total}`;

    // Also update gallery preview
    updateGalleryPreview();
}

function updateGalleryPreview() {
    const { $, API } = window.App;
    const container = $('#preview-scroll');
    if (!container) return;

    // Get surrounding images (5 before, current, 10 after)
    const startIdx = Math.max(0, ManualSortState.index - 5);
    const endIdx = Math.min(ManualSortState.images?.length || 0, ManualSortState.index + 11);

    if (!ManualSortState.images || ManualSortState.images.length === 0) {
        container.innerHTML = '<span style="color: var(--text-muted); font-size: 12px;">No images loaded</span>';
        return;
    }

    const thumbsHTML = [];
    for (let i = startIdx; i < endIdx; i++) {
        const img = ManualSortState.images[i];
        if (!img) continue;

        let className = 'preview-thumb';
        if (i === ManualSortState.index) {
            className += ' current';
        } else if (i < ManualSortState.index) {
            className += ' processed';
        }

        thumbsHTML.push(`
            <div class="${className}" data-index="${i}" title="Image ${i + 1}">
                <img src="${API.getImageUrl(img.id)}" alt="" loading="lazy">
            </div>
        `);
    }

    container.innerHTML = thumbsHTML.join('');

    // Scroll to keep current image centered
    const currentThumb = container.querySelector('.current');
    if (currentThumb) {
        currentThumb.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    }
}

// ============== Handle Keypress ==============

function handleSortKeypress(e) {
    if (!ManualSortState.active) return;

    const action = KEY_MAP[e.key];
    if (!action) return;

    e.preventDefault();

    if (action === 'undo') {
        undoLastAction();
    } else if (action === 'skip') {
        performSkip();
    } else {
        performMove(action);
    }
}

async function performMove(folderKey) {
    const { $, API, showToast } = window.App;

    // Check if folder is configured
    if (!ManualSortState.folders[folderKey]) {
        showToast(`Folder ${folderKey.toUpperCase()} not configured`, 'error');
        return;
    }

    // Animate folder highlight
    const folderEl = $(`.sort-folder[data-key="${folderKey}"]`);
    folderEl?.classList.add('active');
    setTimeout(() => folderEl?.classList.remove('active'), 300);

    // Animate image flying away
    const direction = DIRECTION_MAP[folderKey];
    const imgWrapper = $('.current-image-wrapper');
    imgWrapper.classList.add(`fly-${direction}`);

    // Play sound
    window.AudioManager?.play('move', folderKey);

    // Update combo
    updateCombo();

    // Wait for animation
    await sleep(300);

    // Send action to server
    try {
        const result = await API.sortAction('move', folderKey);

        if (result.done) {
            finishSorting();
            return;
        }

        // Load next image
        await loadCurrentImage();

    } catch (error) {
        console.error('Failed to move image:', error);
        showToast('Failed to move image', 'error');
    }
}

async function performSkip() {
    const { $, API } = window.App;

    // Animate skip
    const imgWrapper = $('.current-image-wrapper');
    imgWrapper.classList.add('skip');

    // Play skip sound
    window.AudioManager?.play('skip');

    // Reset combo
    ManualSortState.combo = 0;
    updateComboDisplay();

    await sleep(300);

    try {
        const result = await API.sortAction('skip');

        if (result.done) {
            finishSorting();
            return;
        }

        await loadCurrentImage();

    } catch (error) {
        console.error('Failed to skip:', error);
    }
}

async function undoLastAction() {
    const { $, API, showToast } = window.App;

    // Play undo sound
    window.AudioManager?.play('undo');

    // Reset combo
    ManualSortState.combo = 0;
    updateComboDisplay();

    try {
        const result = await API.sortAction('undo');
        await loadCurrentImage();
        showToast('Undid last action', 'info');
    } catch (error) {
        console.error('Failed to undo:', error);
    }
}

// ============== Combo System ==============

function updateCombo() {
    const now = Date.now();
    const timeSinceLast = now - ManualSortState.lastActionTime;

    // Combo window is 2 seconds
    if (timeSinceLast < 2000) {
        ManualSortState.combo++;
    } else {
        ManualSortState.combo = 1;
    }

    ManualSortState.lastActionTime = now;
    updateComboDisplay();

    // Play combo sound at milestones
    if (ManualSortState.combo % 5 === 0 && ManualSortState.combo > 0) {
        window.AudioManager?.play('combo');
    }
}

function updateComboDisplay() {
    const { $ } = window.App;
    const comboEl = $('#combo-display');
    const comboNum = comboEl.querySelector('.combo-number');

    if (ManualSortState.combo >= 3) {
        comboEl.classList.add('visible');
        comboNum.textContent = ManualSortState.combo;

        // Pulse animation
        comboNum.style.transform = 'scale(1.2)';
        setTimeout(() => {
            comboNum.style.transform = 'scale(1)';
        }, 100);
    } else {
        comboEl.classList.remove('visible');
    }
}

// ============== Finish/Exit ==============

function finishSorting() {
    const { $, showToast } = window.App;

    ManualSortState.active = false;
    document.removeEventListener('keydown', handleSortKeypress);

    // Play finish sound
    window.AudioManager?.play('finish');

    showToast(`Sorting complete! Processed ${ManualSortState.total} images`, 'success');

    // Return to setup
    $('#sort-interface').style.display = 'none';
    $('#sort-setup').style.display = 'block';

    // Refresh gallery
    if (window.loadImages) {
        window.loadImages();
    }
}

function exitSorting() {
    const { $, showToast } = window.App;

    ManualSortState.active = false;
    document.removeEventListener('keydown', handleSortKeypress);

    $('#sort-interface').style.display = 'none';
    $('#sort-setup').style.display = 'block';

    showToast('Exited sorting mode', 'info');
}

// ============== Filter Summary ==============

function updateManualSortFilterSummary() {
    const { $, AppState } = window.App;
    if (!AppState) return;

    const f = AppState.filters || {};
    const allGens = ['comfyui', 'nai', 'webui', 'forge', 'unknown'];
    const allRatings = ['general', 'sensitive', 'questionable', 'explicit'];

    // Generators
    const genEl = $('#manual-sort-summary-generators');
    if (genEl) {
        genEl.textContent =
            !f.generators || f.generators.length === allGens.length ? 'All' :
                f.generators.length === 0 ? 'None' :
                    f.generators.length > 2 ? `${f.generators.length} selected` : f.generators.join(', ');
    }

    // Tags
    const tagEl = $('#manual-sort-summary-tags');
    if (tagEl) {
        tagEl.textContent =
            !f.tags || f.tags.length === 0 ? 'None' :
                f.tags.length > 2 ? `${f.tags.length} tags` : f.tags.join(', ');
    }

    // Ratings
    const ratingEl = $('#manual-sort-summary-ratings');
    if (ratingEl) {
        ratingEl.textContent =
            !f.ratings || f.ratings.length === allRatings.length ? 'All' :
                f.ratings.length === 0 ? 'None' :
                    f.ratings.length > 2 ? `${f.ratings.length} selected` : f.ratings.join(', ');
    }
}

// ============== Utilities ==============

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ============== Initialize ==============

document.addEventListener('DOMContentLoaded', () => {
    initManualSort();
});

// Export
window.ManualSortState = ManualSortState;
