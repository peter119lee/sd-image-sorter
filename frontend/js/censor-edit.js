/**
 * SD Image Sorter - Censor Edit Module (Overhauled)
 * Queue-based workflow with professional editing tools.
 */

const CensorState = {
    // Queue of { id, originalFilename, outputFilename, originalUrl, currentDataUrl, regions, isProcessed, isModified }
    queue: [],
    activeId: null, // ID of currently edited image

    // Tools
    currentTool: 'brush', // brush, pen, eraser, clone
    brushSize: 30,
    isDrawing: false,
    lastPoint: null,

    // Pen tool properties
    penColor: '#ff0000',
    penOpacity: 1.0,

    // Canvas
    scale: 1,
    pan: { x: 0, y: 0 },
    originalImage: null,  // HTMLImageElement
    originalImageData: null, // ImageData for reset/compare

    // Clone tool state
    cloneSource: null,
    cloneOffset: null,
    cloneSourceSet: false, // Whether source has been set with Alt+click

    // Show changes state
    showingChanges: false,
    preChangesData: null, // Stores canvas state before showing changes

    // Undo/Redo
    undoStack: [],

    // Config
    modelPath: localStorage.getItem('censor_model_path') || '',
    outputFolder: localStorage.getItem('censor_output_folder') || '',
    confidence: 0.5,
    style: 'mosaic',
    blockSize: 16,
    targetClasses: ['breasts', 'pussy', 'dick', 'anus'], // Matches Wenaka YOLO model classes
    metadataOption: 'keep' // 'keep' or 'wash'
};

// Track bound handlers for cleanup to prevent memory leaks
let boundHandlers = {
    mousemove: null,
    mouseup: null,
    keydown: null
};

function cleanupGlobalListeners() {
    if (boundHandlers.mousemove) {
        window.removeEventListener('mousemove', boundHandlers.mousemove);
        boundHandlers.mousemove = null;
    }
    if (boundHandlers.mouseup) {
        window.removeEventListener('mouseup', boundHandlers.mouseup);
        boundHandlers.mouseup = null;
    }
    if (boundHandlers.keydown) {
        document.removeEventListener('keydown', boundHandlers.keydown);
        boundHandlers.keydown = null;
    }
}

// ============== Init ==============

function initCensorEdit() {
    const { $, $$ } = window.App;
    console.log('Initializing Censor Edit UI...');

    // Load saved settings
    if (CensorState.modelPath) $('#censor-model-path').value = CensorState.modelPath;
    if (CensorState.outputFolder) $('#rename-output-folder').value = CensorState.outputFolder;

    bindEvents();
    initDragAndDrop();
    initZoomControls();
    initPanControls();
}

function bindEvents() {
    const { $, $$ } = window.App;

    // Clean up any existing global listeners first to prevent accumulation
    cleanupGlobalListeners();

    // Sidebar: Queue Actions
    $('#btn-clear-queue')?.addEventListener('click', () => {
        if (confirm('Clear all images from queue?')) {
            CensorState.queue = [];
            renderQueue();
            clearCanvas();
        }
    });

    $('#btn-run-auto-censor')?.addEventListener('click', runAutoCensorBatch);
    $('#btn-batch-rename')?.addEventListener('click', () => {
        updateRenamePreview();
        $('#rename-modal').classList.add('visible');
    });

    // Detection Modal handlers
    $('#btn-open-detect-modal')?.addEventListener('click', () => {
        $('#detect-modal')?.classList.add('visible');
    });

    $('#btn-close-detect-modal')?.addEventListener('click', () => {
        $('#detect-modal')?.classList.remove('visible');
    });

    // Close modal when clicking backdrop
    $('#detect-modal .modal-backdrop')?.addEventListener('click', () => {
        $('#detect-modal')?.classList.remove('visible');
    });

    // Rename Modal
    $('#btn-cancel-rename')?.addEventListener('click', () => $('#rename-modal').classList.remove('visible'));
    $('#btn-close-rename')?.addEventListener('click', () => $('#rename-modal').classList.remove('visible'));
    $('#btn-apply-rename')?.addEventListener('click', applyBatchRename);

    // Live preview for rename
    $('#rename-base')?.addEventListener('input', updateRenamePreview);
    $('#rename-start')?.addEventListener('input', updateRenamePreview);

    // Properties Panel
    $('#censor-model-path')?.addEventListener('change', (e) => {
        CensorState.modelPath = e.target.value;
        localStorage.setItem('censor_model_path', CensorState.modelPath);
    });

    $('#censor-confidence')?.addEventListener('input', (e) => {
        CensorState.confidence = parseFloat(e.target.value);
        $('#censor-confidence-value').textContent = CensorState.confidence.toFixed(2);
    });

    $('#censor-style')?.addEventListener('change', (e) => CensorState.style = e.target.value);

    $('#censor-block-size')?.addEventListener('input', (e) => {
        CensorState.blockSize = parseInt(e.target.value);
        $('#censor-block-size-value').textContent = CensorState.blockSize;
    });

    // Checkboxes
    $$('.target-region-check').forEach(cb => {
        cb.addEventListener('change', () => {
            CensorState.targetClasses = Array.from($$('.target-region-check:checked')).map(c => c.value);
        });
    });

    // Tools (support both v1 and v2 class names)
    $$('.tool-btn[data-tool], .tool-btn-v2[data-tool]').forEach(btn => {
        btn.addEventListener('click', () => setTool(btn.dataset.tool));
    });

    $('#btn-undo')?.addEventListener('click', undo);

    // Consolidated Clear Queue
    const clearQueueHandler = () => {
        if (CensorState.queue.length === 0) return;
        window.App.showConfirm(
            'Clear Queue',
            'Are you sure you want to remove all images from the queue?',
            () => {
                CensorState.queue = [];
                CensorState.activeId = null;
                CensorState.undoStack = [];
                CensorState.originalImageData = null;
                renderQueue();
                clearCanvas();
                window.App.showToast('Queue cleared', 'success');
            }
        );
    };

    $('#btn-clear-queue')?.addEventListener('click', clearQueueHandler);
    $('#btn-clear-selected')?.addEventListener('click', clearQueueHandler);

    // Canvas Interactions
    const wrapper = $('#canvas-wrapper');
    if (wrapper) {
        wrapper.addEventListener('mousedown', onCanvasMouseDown);
        // Store references to global handlers so they can be removed later
        boundHandlers.mousemove = onCanvasMouseMove;
        boundHandlers.mouseup = onCanvasMouseUp;
        window.addEventListener('mousemove', boundHandlers.mousemove);
        window.addEventListener('mouseup', boundHandlers.mouseup);
        wrapper.addEventListener('mouseenter', () => $('#cursor-overlay').style.display = 'block');
        wrapper.addEventListener('mouseleave', () => $('#cursor-overlay').style.display = 'none');
        wrapper.addEventListener('contextmenu', e => e.preventDefault());
    }

    // Actions
    $('#btn-censor-detect-single')?.addEventListener('click', () => {
        if (CensorState.activeId) runDetectionForImage(CensorState.queue.find(i => i.id === CensorState.activeId));
    });

    $('#btn-save-all-processed')?.addEventListener('click', saveAllProcessed);

    // New button handlers
    $('#btn-auto-detect-current')?.addEventListener('click', () => {
        if (CensorState.activeId) {
            runDetectionForImage(CensorState.queue.find(i => i.id === CensorState.activeId));
        } else {
            window.App.showToast('No image selected', 'error');
        }
    });

    $('#btn-auto-detect-current-modal')?.addEventListener('click', () => {
        if (CensorState.activeId) {
            $('#detect-modal')?.classList.remove('visible');
            runDetectionForImage(CensorState.queue.find(i => i.id === CensorState.activeId));
        } else {
            window.App.showToast('No image selected', 'error');
        }
    });

    $('#btn-auto-detect-all-modal')?.addEventListener('click', () => {
        $('#detect-modal')?.classList.remove('visible');
        runDetectionForAll();
    });

    $('#btn-clear-edits')?.addEventListener('click', () => {
        if (!CensorState.activeId || !CensorState.originalImageData) {
            window.App.showToast('No image to reset', 'error');
            return;
        }
        window.App.showConfirm(
            'Reset All Edits',
            'This will revert all edits to the original image. Continue?',
            clearAllEdits
        );
    });

    $('#btn-show-changes')?.addEventListener('click', toggleShowChanges);

    // Pen/Brush settings
    $('#pen-color')?.addEventListener('input', (e) => {
        CensorState.penColor = e.target.value;
    });

    $('#pen-opacity')?.addEventListener('input', (e) => {
        CensorState.penOpacity = parseInt(e.target.value) / 100;
        $('#pen-opacity-value').textContent = e.target.value + '%';
    });

    $('#tool-size')?.addEventListener('input', (e) => {
        CensorState.brushSize = parseInt(e.target.value);
        $('#tool-size-value').textContent = e.target.value;
    });

    // Metadata option
    $('#censor-metadata-option')?.addEventListener('change', (e) => {
        CensorState.metadataOption = e.target.value;
    });

    // Browse model path button (opens prompt since browser can't access filesystem directly)
    $('#btn-browse-model')?.addEventListener('click', () => {
        const path = prompt('Enter the full path to your YOLO model (.pt or .onnx):', CensorState.modelPath);
        if (path !== null) {
            CensorState.modelPath = path;
            $('#censor-model-path').value = path;
            localStorage.setItem('censor_model_path', path);
        }
    });

    // Keybinds - track for cleanup
    boundHandlers.keydown = handleKeydown;
    document.addEventListener('keydown', boundHandlers.keydown);

    // Add to Queue (Hook for Gallery)
    window.App.addToCensorQueue = (imageIds) => {
        const { API } = window.App;
        imageIds.forEach(id => {
            if (!CensorState.queue.find(i => i.id === id)) {
                // Fetch basic info if not available (async)
                API.getImage(id).then(res => {
                    CensorState.queue.push({
                        id: id,
                        originalFilename: res.image.filename,
                        outputFilename: res.image.filename, // Default same name
                        originalUrl: API.getImageUrl(id),
                        currentDataUrl: null, // Will be loaded/generated
                        regions: [],
                        isProcessed: false,
                        isModified: false
                    });
                    renderQueue();
                    // Auto-select the first image if no active ID
                    if (!CensorState.activeId && CensorState.queue.length > 0) {
                        setTimeout(() => loadCanvasImage(CensorState.queue[0].id), 100);
                    }
                });
            }
        });
        // Switch view to censor tab - use nav tab click for reliable switching
        const censorTab = document.querySelector('.nav-tab[data-view="censor"]');
        if (censorTab) {
            censorTab.click();
        } else if (typeof window.App?.switchView === 'function') {
            window.App.switchView('censor');
        }
        // Ensure the censor view is scrolled into visibility
        const censorView = document.getElementById('view-censor');
        if (censorView) {
            censorView.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    };

    // Collapse toggle listener for sections
    document.addEventListener('click', (e) => {
        const header = e.target.closest('.section-header');
        if (header) {
            const sectionId = header.getAttribute('onclick')?.match(/'([^']+)'/)?.[1];
            if (sectionId) {
                toggleSection(sectionId);
                e.stopPropagation();
            } else {
                // If no onclick attribute with ID, look for ID on the content element next to it
                const content = header.nextElementSibling;
                if (content && content.classList.contains('section-content')) {
                    header.parentElement.classList.toggle('collapsed');
                }
            }
        }
    });
}

// ============== Queue Logic ==============

function renderQueue() {
    const list = document.getElementById('censor-queue-list');
    if (!list) return;

    // Handle empty state
    if (CensorState.queue.length === 0) {
        list.innerHTML = `
            <div class="queue-empty-state-v2">
                <span class="empty-icon">📷</span>
                <p>No images selected</p>
                <small>Select from Gallery</small>
            </div>
        `;
        return;
    }

    // Get existing thumbnails
    const existingThumbs = list.querySelectorAll('.queue-thumb-v2');
    const existingIds = new Set([...existingThumbs].map(t => t.dataset.id));
    const queueIds = new Set(CensorState.queue.map(item => item.id.toString()));

    // Remove thumbnails not in queue anymore
    existingThumbs.forEach(thumb => {
        if (!queueIds.has(thumb.dataset.id)) {
            thumb.remove();
        }
    });

    // Clear empty state if present
    const emptyState = list.querySelector('.queue-empty-state-v2');
    if (emptyState) emptyState.remove();

    // Update or create thumbnails
    CensorState.queue.forEach((item, index) => {
        const itemIdStr = item.id.toString();
        let img = list.querySelector(`.queue-thumb-v2[data-id="${itemIdStr}"]`);

        if (!img) {
            // Create new thumbnail
            img = document.createElement('img');
            img.className = 'queue-thumb-v2';
            img.draggable = true;
            img.dataset.id = itemIdStr;

            // Click to load
            img.addEventListener('click', () => {
                loadCanvasImage(item.id);
            });

            // DnD Events
            img.addEventListener('dragstart', handleDragStart);
            img.addEventListener('dragend', handleDragEnd);
            img.addEventListener('dragover', handleDragOver);
            img.addEventListener('drop', handleDrop);
            img.addEventListener('dragenter', (e) => e.target.classList.add('drag-over'));
            img.addEventListener('dragleave', (e) => e.target.classList.remove('drag-over'));

            list.appendChild(img);
        }

        // Always append to maintain order (appendChild moves existing node to end)
        list.appendChild(img);

        // Update properties (always update these - they may have changed)
        img.dataset.index = index;
        img.title = item.outputFilename;

        // Only update src if it changed (prevents reload flash)
        const newSrc = item.currentDataUrl || item.originalUrl;
        if (img.src !== newSrc) {
            img.src = newSrc;
        }

        // Update classes
        const isActive = item.id === CensorState.activeId;
        const isProcessed = item.isProcessed;
        img.classList.toggle('active', isActive);
        img.classList.toggle('processed', isProcessed);
    });
}

function initDragAndDrop() {
    // Basic setup handled in renderQueue listeners
}

let draggedItemIndex = null;

function handleDragStart(e) {
    draggedItemIndex = parseInt(this.dataset.index);
    e.dataTransfer.effectAllowed = 'move';
    // Use the ID as the data to ensure we identify the right item even if index changes
    e.dataTransfer.setData('text/plain', this.dataset.id);
    this.classList.add('dragging');
    // Set dragging opacity
    setTimeout(() => { this.style.opacity = '0.5'; }, 0);
}

function handleDragEnd(e) {
    this.style.opacity = '1';
    this.classList.remove('dragging');
    // Clean up all drag-over states
    document.querySelectorAll('.queue-thumb-v2').forEach(el => {
        el.classList.remove('drag-over');
    });
}

function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    return false;
}

function handleDrop(e) {
    e.stopPropagation();
    e.preventDefault();
    const targetItem = e.target.closest('.queue-thumb-v2');
    if (!targetItem) return false;

    const targetIndex = parseInt(targetItem.dataset.index);
    const draggedId = e.dataTransfer.getData('text/plain');

    // Find current index of the dragged item
    const currentIndex = CensorState.queue.findIndex(item => item.id.toString() === draggedId);

    if (currentIndex !== -1 && currentIndex !== targetIndex) {
        // Move item in array
        const item = CensorState.queue[currentIndex];
        CensorState.queue.splice(currentIndex, 1);
        CensorState.queue.splice(targetIndex, 0, item);

        // Re-render the whole queue to update indices and visuals
        renderQueue();

        // Save state if needed (optional, depends if queue should persist)
    }

    document.querySelectorAll('.queue-thumb-v2').forEach(el => {
        el.classList.remove('dragging', 'drag-over');
    });
    return false;
}

// ============== Canvas & Editing ==============

// State for double buffering
CensorState.activeCanvasId = 'censor-canvas';


async function loadCanvasImage(id) {
    const item = CensorState.queue.find(i => i.id === id);
    if (!item) return;

    if (CensorState.activeId && CensorState.activeId !== id) {
        saveCurrentCanvasToState();
    }

    CensorState.activeId = id;
    renderQueue();

    // Identify current and next canvas
    const currentCanvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');
    const nextCanvasId = CensorState.activeCanvasId === 'censor-canvas' ? 'censor-canvas-buffer' : 'censor-canvas';
    const nextCanvas = document.getElementById(nextCanvasId);

    // UI Updates
    const noImageEl = document.getElementById('censor-no-image');
    const filenameEl = document.getElementById('censor-filename');
    showLoading(true, 'Loading image...');

    try {
        const imgUrl = item.currentDataUrl || item.originalUrl;

        // Load images
        const [img, originalImg] = await Promise.all([
            loadImage(imgUrl),
            loadImage(item.originalUrl)
        ]);

        CensorState.originalImage = originalImg;

        // Draw to NEXT canvas (hidden)
        nextCanvas.width = img.width;
        nextCanvas.height = img.height;
        const ctx = nextCanvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0);

        // Store data
        CensorState.originalImageData = ctx.getImageData(0, 0, img.width, img.height);
        CensorState.undoStack = [];
        pushUndo();

        // Fit canvases to container before showing
        fitCanvasToContainer(nextCanvas, img.width, img.height);
        fitCanvasToContainer(currentCanvas, img.width, img.height);

        // SWAP: Show next, Hide current (with RAF)
        requestAnimationFrame(() => {
            nextCanvas.style.opacity = '1';
            nextCanvas.style.pointerEvents = 'auto';
            nextCanvas.style.zIndex = '10';

            currentCanvas.style.opacity = '0';
            currentCanvas.style.pointerEvents = 'none';
            currentCanvas.style.zIndex = '0';

            // Update State
            CensorState.activeCanvasId = nextCanvasId;

            // Finalize
            noImageEl.style.display = 'none';
            showLoading(false);
            if (filenameEl) filenameEl.textContent = item.outputFilename;

            resetZoom();
        });

    } catch (error) {
        console.error('Failed to load image:', error);
        showLoading(false);
        window.App.showToast('Error: ' + error.message, 'error');
    }
}

function fitCanvasToContainer(canvas, imgW, imgH) {
    const container = document.getElementById('canvas-container');
    if (!container) return;

    // Get container dimensions (minus padding if any)
    const contW = container.clientWidth;
    const contH = container.clientHeight;

    // Calculate aspect ratios
    const imgRatio = imgW / imgH;
    const contRatio = contW / contH;

    let finalW, finalH;

    if (imgRatio > contRatio) {
        // Image is wider than container - fit to width
        finalW = contW;
        finalH = contW / imgRatio;
    } else {
        // Image is taller than container - fit to height
        finalH = contH;
        finalW = contH * imgRatio;
    }

    // Check against max checks? No, container size is the truth.

    canvas.style.width = `${finalW}px`;
    canvas.style.height = `${finalH}px`;
}

// Re-fit on window resize
window.addEventListener('resize', () => {
    if (CensorState.activeId && CensorState.originalImage) {
        const c1 = document.getElementById('censor-canvas');
        const c2 = document.getElementById('censor-canvas-buffer');
        const img = CensorState.originalImage;
        fitCanvasToContainer(c1, img.width, img.height);
        fitCanvasToContainer(c2, img.width, img.height);
    }
});

function saveCurrentCanvasToState() {
    // Save from the CURRENT active canvas
    if (!CensorState.activeId) return;
    const item = CensorState.queue.find(i => i.id === CensorState.activeId);
    const canvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');

    if (item && canvas) {
        item.currentDataUrl = canvas.toDataURL('image/png');
        item.isModified = true;
    }
}

function clearCanvas() {
    const canvas = document.getElementById('censor-canvas');
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    document.getElementById('censor-no-image').style.display = 'flex';
    document.getElementById('censor-filename').textContent = '-';
}

// ============== Drawing Tools ==============

function onCanvasMouseDown(e) {
    if (!CensorState.activeId) return;
    CensorState.isDrawing = true;

    const { x, y } = getCanvasCoordinates(e);
    CensorState.lastPoint = { x, y };

    if (CensorState.currentTool === 'clone' && e.altKey) {
        CensorState.cloneSource = { x, y };
        CensorState.cloneOffset = null;
        CensorState.cloneSourceSet = true;
        window.App.showToast('Clone source set - now paint to clone', 'info');
        CensorState.isDrawing = false;
        return;
    }

    pushUndo();
    drawAtPoint(x, y);
}

function onCanvasMouseMove(e) {
    // Update cursor overlay position (relative to screen/wrapper)
    updateCursorOverlay(e);

    if (!CensorState.isDrawing || !CensorState.activeId) return;

    const { x, y } = getCanvasCoordinates(e);

    // Interpolate
    const steps = Math.max(1, Math.floor(Math.hypot(x - CensorState.lastPoint.x, y - CensorState.lastPoint.y) / 2));
    for (let i = 1; i <= steps; i++) {
        const t = i / steps;
        drawAtPoint(
            CensorState.lastPoint.x + (x - CensorState.lastPoint.x) * t,
            CensorState.lastPoint.y + (y - CensorState.lastPoint.y) * t
        );
    }
    CensorState.lastPoint = { x, y };
}

function onCanvasMouseUp() {
    CensorState.isDrawing = false;
    if (CensorState.activeId) saveCurrentCanvasToState(); // Save state after stroke
}

function getCanvasCoordinates(e) {
    const canvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');
    const rect = canvas.getBoundingClientRect();

    // Account for CSS scaling
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;

    return {
        x: (e.clientX - rect.left) * scaleX,
        y: (e.clientY - rect.top) * scaleY
    };
}

function drawAtPoint(x, y) {
    const canvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');
    const ctx = canvas.getContext('2d');
    const size = CensorState.brushSize;

    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, size / 2, 0, Math.PI * 2);

    if (CensorState.currentTool === 'brush') {
        applyCensorStyle(ctx, x, y, size);
    } else if (CensorState.currentTool === 'pen') {
        // Draw with pen color and opacity
        ctx.globalAlpha = CensorState.penOpacity;
        ctx.fillStyle = CensorState.penColor;
        ctx.fill();
        ctx.globalAlpha = 1.0;
    } else if (CensorState.currentTool === 'eraser') {
        // Restore from original image
        ctx.clip();
        if (CensorState.originalImage) {
            ctx.drawImage(CensorState.originalImage, 0, 0, canvas.width, canvas.height);
        } else if (CensorState.originalImageData) {
            // Fallback to stored image data
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = canvas.width;
            tempCanvas.height = canvas.height;
            tempCanvas.getContext('2d').putImageData(CensorState.originalImageData, 0, 0);
            ctx.drawImage(tempCanvas, 0, 0);
        }
    } else if (CensorState.currentTool === 'clone') {
        if (!CensorState.cloneSourceSet) {
            // Clone source not set - show hint
            return;
        }
        performClone(ctx, x, y, size);
    }

    ctx.restore();
}

function applyCensorStyle(ctx, x, y, size) {
    const style = CensorState.style;
    const b = CensorState.blockSize;
    const canvas = ctx.canvas;

    if (style === 'mosaic') {
        // Snap to grid
        const startX = Math.floor((x - size / 2) / b) * b;
        const startY = Math.floor((y - size / 2) / b) * b;
        const endX = Math.ceil((x + size / 2) / b) * b;
        const endY = Math.ceil((y + size / 2) / b) * b;

        for (let bx = startX; bx < endX; bx += b) {
            for (let by = startY; by < endY; by += b) {
                // Circle check
                if (Math.hypot(bx + b / 2 - x, by + b / 2 - y) <= size / 2) {
                    const data = ctx.getImageData(bx, by, b, b);
                    const avg = getAverageColor(data);
                    ctx.fillStyle = avg;
                    ctx.fillRect(bx, by, b, b);
                }
            }
        }
    } else if (style === 'blur') {
        // Apply actual blur effect
        const blurRadius = Math.max(8, CensorState.blockSize);
        const regionX = Math.max(0, Math.floor(x - size / 2));
        const regionY = Math.max(0, Math.floor(y - size / 2));
        const regionW = Math.min(canvas.width - regionX, Math.ceil(size));
        const regionH = Math.min(canvas.height - regionY, Math.ceil(size));

        if (regionW > 0 && regionH > 0) {
            // Create temporary canvas for blur
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = regionW;
            tempCanvas.height = regionH;
            const tempCtx = tempCanvas.getContext('2d');

            // Copy region to temp canvas
            tempCtx.drawImage(canvas, regionX, regionY, regionW, regionH, 0, 0, regionW, regionH);

            // Apply blur filter
            tempCtx.filter = `blur(${blurRadius}px)`;
            tempCtx.drawImage(tempCanvas, 0, 0);
            tempCtx.filter = 'none';

            // Draw back with circular clip
            ctx.save();
            ctx.beginPath();
            ctx.arc(x, y, size / 2, 0, Math.PI * 2);
            ctx.clip();
            ctx.drawImage(tempCanvas, regionX, regionY);
            ctx.restore();
        }
    } else if (style === 'black_bar') {
        ctx.fillStyle = '#000';
        ctx.fill();
    } else if (style === 'white_bar') {
        ctx.fillStyle = '#fff';
        ctx.fill();
    }
}

function getAverageColor(imageData) {
    const d = imageData.data;
    let r = 0, g = 0, b = 0, c = 0;
    for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; c++; }
    return c ? `rgb(${r / c | 0},${g / c | 0},${b / c | 0})` : '#000';
}

function performClone(ctx, x, y, size) {
    if (!CensorState.cloneSource) return;

    if (!CensorState.cloneOffset) {
        CensorState.cloneOffset = { x: CensorState.cloneSource.x - x, y: CensorState.cloneSource.y - y };
    }

    const sourceX = x + CensorState.cloneOffset.x;
    const sourceY = y + CensorState.cloneOffset.y;

    ctx.clip();
    // Draw directly from current canvas state (or original? usually current)
    // Actually cloning usually samples from same layer.
    // To simplify: Clone samples from a snapshot of the canvas taken at start of stroke?
    // For now: Clone from original image for simplicity (allows "repair" using clean parts)
    if (CensorState.originalImage) {
        ctx.drawImage(CensorState.originalImage, sourceX - size / 2, sourceY - size / 2, size, size, x - size / 2, y - size / 2, size, size);
    }
}

// ============== Auto Censor Logic ==============

async function runAutoCensorBatch() {
    const { showToast } = window.App;
    if (!CensorState.modelPath) { showToast('Please select a model first', 'error'); return; }

    showLoading(true, 'Batch Processing...');

    for (const item of CensorState.queue) {
        await runDetectionForImage(item, true); // true = silent/no-refresh
    }

    showLoading(false);
    renderQueue();
    // Reload canvas if active item was updated
    if (CensorState.activeId) loadCanvasImage(CensorState.activeId);
    showToast('Batch processing complete', 'success');
}

async function runDetectionForImage(item, silent = false) {
    try {
        const res = await fetch('/api/censor/detect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                image_id: item.id,
                model_path: CensorState.modelPath,
                confidence_threshold: CensorState.confidence
            })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail);

        // Log detection info and limit to top 50 highest confidence detections
        console.log('Total detections:', data.detections.length);
        if (data.detections.length > 0) {
            console.log('Sample detection classes:', data.detections.slice(0, 5).map(d => d.class));
        }
        // Sort by confidence and take top 50 to avoid processing thousands
        const sortedDetections = data.detections.sort((a, b) => b.confidence - a.confidence).slice(0, 50);
        // Filter by target classes if needed, otherwise use all sorted detections  
        let regions = sortedDetections.filter(d => CensorState.targetClasses.includes(d.class));
        // If no matches, check for class_id based filtering or use all
        if (regions.length === 0 && sortedDetections.length > 0) {
            console.log('No class name matches, using all top detections');
            regions = sortedDetections;
        }
        item.regions = regions;

        // Apply to a temporary canvas to generate DataURL
        const img = await loadImage(item.originalUrl);
        const cvs = document.createElement('canvas');
        cvs.width = img.width;
        cvs.height = img.height;
        const ctx = cvs.getContext('2d');
        ctx.drawImage(img, 0, 0);

        // Apply regions
        console.log(`Applying ${regions.length} regions to image with style: ${CensorState.style}`);

        ctx.save();
        regions.forEach(r => {
            const [x1, y1, x2, y2] = r.box;
            const w = x2 - x1, h = y2 - y1;

            if (CensorState.style === 'mosaic') {
                const b = CensorState.blockSize;
                for (let bx = x1; bx < x2; bx += b) {
                    for (let by = y1; by < y2; by += b) {
                        const bw = Math.min(b, x2 - bx);
                        const bh = Math.min(b, y2 - by);
                        const d = ctx.getImageData(bx, by, bw, bh);
                        ctx.fillStyle = getAverageColor(d);
                        ctx.fillRect(bx, by, bw, bh);
                    }
                }
            } else if (CensorState.style === 'blur') {
                // Apply blur
                ctx.save();
                ctx.beginPath();
                ctx.rect(x1, y1, w, h);
                ctx.clip();
                ctx.filter = `blur(${CensorState.blockSize / 2}px)`;
                ctx.drawImage(img, 0, 0);
                ctx.restore();
            } else if (CensorState.style === 'black_bar') {
                ctx.fillStyle = '#000';
                ctx.fillRect(x1, y1, w, h);
            } else if (CensorState.style === 'white_bar') {
                ctx.fillStyle = '#fff';
                ctx.fillRect(x1, y1, w, h);
            } else {
                // Default to black bar
                ctx.fillStyle = '#000';
                ctx.fillRect(x1, y1, w, h);
            }

            // Debug: Stroke box
            // ctx.strokeStyle = 'rgba(255, 0, 0, 0.5)';
            // ctx.lineWidth = 2;
            // ctx.strokeRect(x1, y1, w, h);
        });
        ctx.restore();

        item.currentDataUrl = cvs.toDataURL('image/png');
        item.isProcessed = true;

        if (!silent && item.id === CensorState.activeId) {
            loadCanvasImage(item.id);
            if (regions.length === 0) {
                window.App.showToast('No relevant regions found (Try lowering confidence)', 'info');
            } else {
                window.App.showToast(`Applied censorship to ${regions.length} regions`, 'success');
            }
        }

    } catch (e) {
        console.error(e);
        if (!silent) window.App.showToast('Detection error: ' + e.message, 'error');
    }
}

// ============== Batch Actions ==============

async function applyBatchRename() {
    const base = document.getElementById('rename-base').value || 'Image';
    const start = parseInt(document.getElementById('rename-start').value) || 1;
    const outputFolder = document.getElementById('rename-output-folder').value;

    if (outputFolder) {
        CensorState.outputFolder = outputFolder;
        localStorage.setItem('censor_output_folder', outputFolder);
    }

    CensorState.queue.forEach((item, i) => {
        const num = String(start + i).padStart(3, '0');
        item.outputFilename = `${base}_${num}.png`;
    });

    renderQueue();
    document.getElementById('rename-modal').classList.remove('visible');

    // Refresh current title if viewing
    if (CensorState.activeId) {
        const item = CensorState.queue.find(i => i.id === CensorState.activeId);
        if (item) document.getElementById('censor-filename').textContent = item.outputFilename;
    }
}

async function saveAllProcessed() {
    const folder = CensorState.outputFolder;
    if (!folder) {
        window.App.showToast('Set output folder in Rename or Setup first', 'error');
        return;
    }

    showLoading(true, 'Saving all images...');

    let count = 0;
    for (const item of CensorState.queue) {
        // Only save if processed or modified? Or all? Usually all in queue.
        // Use currentDataUrl if exists, else original
        const dataUrl = item.currentDataUrl || await urlToDataUrl(item.originalUrl);

        try {
            await fetch('/api/censor/save-data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    image_data: dataUrl,
                    filename: item.outputFilename,
                    output_folder: folder,
                    metadata_option: CensorState.metadataOption,
                    original_image_id: item.id  // Pass original image ID for metadata copying
                })
            });
            count++;
        } catch (e) {
            console.error(e);
        }
    }

    showLoading(false);
    window.App.showToast(`Saved ${count} images to ${folder}`, 'success');
}

// ============== Helpers ==============

function setTool(tool) {
    CensorState.currentTool = tool;
    // Update both v1 and v2 tool buttons
    document.querySelectorAll('.tool-btn, .tool-btn-v2').forEach(b => {
        b.classList.toggle('active', b.dataset.tool === tool);
    });
}

// Collapsible section toggle for V2 properties panel
function toggleSection(sectionId) {
    const section = document.getElementById(sectionId);
    if (section) {
        section.classList.toggle('collapsed');
    }
}
window.toggleSection = toggleSection; // Make globally accessible for onclick

function updateCursorOverlay(e) {
    const cursor = document.getElementById('cursor-overlay');
    const wrapper = document.getElementById('canvas-wrapper');
    if (!cursor || !wrapper) return;

    // e.clientX is global. Get relative to wrapper
    const rect = wrapper.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    // Visible only if inside wrapper
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) {
        cursor.style.display = 'none';
        return;
    }

    cursor.style.display = 'block';

    // Calculate visual size based on canvas scaling
    const canvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');
    let visualSize = CensorState.brushSize;

    if (canvas && canvas.width > 0 && CensorState.activeId) {
        const canvasRect = canvas.getBoundingClientRect();
        const scale = canvasRect.width / canvas.width;
        visualSize = CensorState.brushSize * scale;
    }

    cursor.style.width = `${visualSize}px`;
    cursor.style.height = `${visualSize}px`;
    // Position at mouse location - use transform for centering (set in CSS)
    cursor.style.left = `${x}px`;
    cursor.style.top = `${y}px`;
}

function pushUndo() {
    const canvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');
    if (!canvas || !canvas.width) return; // Guard against missing canvas
    // Use WebP for best compression while maintaining quality (falls back to PNG if unsupported)
    // WebP at 0.6 quality is ~30% smaller than JPEG at same quality, saves significant memory
    let dataUrl;
    try {
        dataUrl = canvas.toDataURL('image/webp', 0.6);
        // Check if browser actually produced WebP (some return PNG as fallback)
        if (!dataUrl.startsWith('data:image/webp')) {
            dataUrl = canvas.toDataURL('image/jpeg', 0.7);
        }
    } catch (e) {
        dataUrl = canvas.toDataURL('image/jpeg', 0.7);
    }
    CensorState.undoStack.push(dataUrl);
    if (CensorState.undoStack.length > 20) CensorState.undoStack.shift();
}


function undo() {
    if (CensorState.undoStack.length === 0) return;
    const prev = CensorState.undoStack.pop();
    const img = new Image();
    img.onload = () => {
        const canvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0);
        saveCurrentCanvasToState();
    };
    img.src = prev;
}

function handleKeydown(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    const key = e.key.toLowerCase();
    const code = e.code;

    // Navigation: ArrowLeft/ArrowRight for prev/next
    if (code === 'ArrowLeft') {
        navigateQueue(-1);
        e.preventDefault();
    } else if (code === 'ArrowRight') {
        navigateQueue(1);
        e.preventDefault();
    }
    // Brush size [ ]
    else if (e.key === '[') {
        CensorState.brushSize = Math.max(5, CensorState.brushSize - 5);
        updateBrushIndicator();
        e.preventDefault();
    } else if (e.key === ']') {
        CensorState.brushSize = Math.min(200, CensorState.brushSize + 5);
        updateBrushIndicator();
        e.preventDefault();
    }
    // Tool shortcuts
    else if (key === 'b') {
        setTool('brush');
        e.preventDefault();
    } else if (key === 'p') {
        setTool('pen');
        e.preventDefault();
    } else if (key === 'e') {
        setTool('eraser');
        e.preventDefault();
    } else if (key === 'g') {
        setTool('clone');
        e.preventDefault();
    }
    // Detection shortcut ('D' for Detect)
    else if (key === 'd' && !e.ctrlKey && !e.altKey && !e.shiftKey) {
        const activeItem = CensorState.queue.find(i => i.id === CensorState.activeId);
        if (activeItem) {
            runDetectionForImage(activeItem);
        }
        e.preventDefault();
    }
    // Undo
    else if (e.ctrlKey && key === 'z') {
        undo();
        e.preventDefault();
    }
}

function updateBrushIndicator() {
    const indicator = document.getElementById('brush-size-indicator');
    if (indicator) {
        indicator.textContent = `${CensorState.brushSize}px`;
        // Show briefly then fade
        indicator.style.opacity = '1';
        setTimeout(() => { indicator.style.opacity = '0'; }, 1000);
    }
    // Also sync the slider in the UI
    const slider = document.getElementById('tool-size');
    const label = document.getElementById('tool-size-value');
    if (slider) slider.value = CensorState.brushSize;
    if (label) label.textContent = CensorState.brushSize;
}

function navigateQueue(direction) {
    if (CensorState.queue.length === 0) return;

    const currentIndex = CensorState.queue.findIndex(item => item.id === CensorState.activeId);
    if (currentIndex === -1) {
        // No active, load first
        if (CensorState.queue.length > 0) {
            loadCanvasImage(CensorState.queue[0].id);
        }
        return;
    }

    const newIndex = currentIndex + direction;
    if (newIndex >= 0 && newIndex < CensorState.queue.length) {
        loadCanvasImage(CensorState.queue[newIndex].id);
    }
}

function updateRenamePreview() {
    const base = document.getElementById('rename-base')?.value || 'Image';
    const start = parseInt(document.getElementById('rename-start')?.value) || 1;

    const preview = document.querySelector('.rename-preview');
    if (preview) {
        preview.innerHTML = `
            <div class="preview-item">${base}_${String(start).padStart(3, '0')}.png</div>
            <div class="preview-item">${base}_${String(start + 1).padStart(3, '0')}.png</div>
            <div class="preview-item">${base}_${String(start + 2).padStart(3, '0')}.png</div>
            <div class="preview-hint">...and so on</div>
        `;
    }
}

function showLoading(show, msg) {
    const el = document.getElementById('censor-loading');
    if (el) {
        el.style.display = show ? 'flex' : 'none';
        if (msg) document.getElementById('censor-loading-msg').textContent = msg;
    }
}

async function loadImage(src) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => resolve(img);
        img.onerror = (e) => {
            console.error('Image load error:', src, e);
            reject(new Error('Failed to load image'));
        };
        img.src = src;
    });
}

function urlToDataUrl(url) {
    return fetch(url)
        .then(response => response.blob())
        .then(blob => new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        }));
}

// ============== New Helper Functions ==============

function clearAllEdits() {
    if (!CensorState.activeId || !CensorState.originalImageData) return;

    const canvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');
    const ctx = canvas.getContext('2d');

    // Restore original image data
    ctx.putImageData(CensorState.originalImageData, 0, 0);

    // Clear undo stack and push clean state
    CensorState.undoStack = [];
    pushUndo();

    // Clear modified flag
    const item = CensorState.queue.find(i => i.id === CensorState.activeId);
    if (item) {
        item.isModified = false;
        item.currentDataUrl = null;
    }

    window.App.showToast('Edits cleared - image restored to original', 'success');
}

function toggleShowChanges() {
    const canvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');
    const ctx = canvas.getContext('2d');
    const btn = document.getElementById('btn-show-changes');

    if (!CensorState.activeId || !CensorState.originalImageData) {
        window.App.showToast('No image to compare', 'error');
        return;
    }

    if (CensorState.showingChanges) {
        // Restore the pre-changes canvas state
        if (CensorState.preChangesData) {
            ctx.putImageData(CensorState.preChangesData, 0, 0);
        }
        CensorState.showingChanges = false;
        CensorState.preChangesData = null;
        if (btn) btn.classList.remove('active');
    } else {
        // Store current state
        CensorState.preChangesData = ctx.getImageData(0, 0, canvas.width, canvas.height);

        // Compare with original and highlight differences
        const currentData = CensorState.preChangesData.data;
        const originalData = CensorState.originalImageData.data;
        const highlightData = ctx.createImageData(canvas.width, canvas.height);

        for (let i = 0; i < currentData.length; i += 4) {
            // Check if pixel is different
            const diff = Math.abs(currentData[i] - originalData[i]) +
                Math.abs(currentData[i + 1] - originalData[i + 1]) +
                Math.abs(currentData[i + 2] - originalData[i + 2]);

            if (diff > 30) {
                // Mark as changed - red highlight with original underneath
                highlightData.data[i] = Math.min(255, currentData[i] + 100);
                highlightData.data[i + 1] = currentData[i + 1] * 0.5;
                highlightData.data[i + 2] = currentData[i + 2] * 0.5;
                highlightData.data[i + 3] = 255;
            } else {
                // Keep original colors
                highlightData.data[i] = currentData[i];
                highlightData.data[i + 1] = currentData[i + 1];
                highlightData.data[i + 2] = currentData[i + 2];
                highlightData.data[i + 3] = 255;
            }
        }

        ctx.putImageData(highlightData, 0, 0);
        CensorState.showingChanges = true;
        if (btn) btn.classList.add('active');
        window.App.showToast('Changed areas highlighted in red', 'info');
    }
}

async function runDetectionForAll() {
    const { showToast } = window.App;
    if (CensorState.queue.length === 0) {
        showToast('Queue is empty', 'error');
        return;
    }
    if (!CensorState.modelPath) {
        showToast('Please set a model path first', 'error');
        return;
    }

    showLoading(true, 'Running detection on all images...');
    let count = 0;

    for (const item of CensorState.queue) {
        try {
            await runDetectionForImage(item, true);
            count++;
        } catch (e) {
            console.error('Detection error for', item.id, e);
        }
    }

    showLoading(false);
    renderQueue();
    if (CensorState.activeId) loadCanvasImage(CensorState.activeId);
    showToast(`Detection complete: ${count}/${CensorState.queue.length} images processed`, 'success');
}


// ============== Zoom Functions ==============

function zoomCanvas(delta) {
    CensorState.scale = Math.max(0.1, Math.min(10, CensorState.scale + delta));
    applyZoom();
}

function resetZoom() {
    CensorState.scale = 1;
    CensorState.pan = { x: 0, y: 0 };
    applyZoom();
}

function applyZoom() {
    // Zoom the CONTAINER, not the canvas directly, so both canvases scale together
    const container = document.getElementById('canvas-container');
    if (container) {
        // Use transform on the container with translate for panning
        container.style.transform = `translate(${CensorState.pan.x}px, ${CensorState.pan.y}px) scale(${CensorState.scale})`;
        container.style.transformOrigin = 'center center';
    }
    updateZoomDisplay();
}

function updateZoomDisplay() {
    const zoomLevel = document.getElementById('zoom-level');
    if (zoomLevel) {
        zoomLevel.textContent = Math.round(CensorState.scale * 100) + '%';
    }
}

// Initialize zoom controls on DOM ready
function initZoomControls() {
    // Both v1/v2 IDs for compatibility if needed, but primary is v2 now
    document.getElementById('btn-zoom-in')?.addEventListener('click', () => zoomCanvas(0.25));
    document.getElementById('btn-zoom-out')?.addEventListener('click', () => zoomCanvas(-0.25));
    document.getElementById('btn-zoom-fit')?.addEventListener('click', resetZoom);

    // Mouse wheel zoom
    const wrapper = document.querySelector('.censor-canvas-wrapper-v2');
    if (wrapper) {
        wrapper.addEventListener('wheel', (e) => {
            if (e.ctrlKey) {
                e.preventDefault();
                const delta = e.deltaY > 0 ? -0.1 : 0.1;
                zoomCanvas(delta);
            }
        }, { passive: false });
    }
}

// ============== Pan (Drag) Functions ==============

let isPanning = false;
let panStart = { x: 0, y: 0 };
let spacePressed = false;

function initPanControls() {
    const wrapper = document.querySelector('.censor-canvas-wrapper-v2');
    if (!wrapper) return;

    // Space key to enable pan mode
    document.addEventListener('keydown', (e) => {
        if (e.code === 'Space' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
            spacePressed = true;
            wrapper.style.cursor = 'grab';
            e.preventDefault();
        }
    });

    document.addEventListener('keyup', (e) => {
        if (e.code === 'Space') {
            spacePressed = false;
            if (!isPanning) {
                wrapper.style.cursor = '';
            }
        }
    });

    // Middle mouse button or space+left click for panning
    wrapper.addEventListener('mousedown', (e) => {
        if (e.button === 1 || (spacePressed && e.button === 0)) {
            isPanning = true;
            panStart = {
                x: e.clientX - CensorState.pan.x,
                y: e.clientY - CensorState.pan.y
            };
            wrapper.style.cursor = 'grabbing';
            e.preventDefault();
        }
    });

    window.addEventListener('mousemove', (e) => {
        if (isPanning) {
            CensorState.pan.x = e.clientX - panStart.x;
            CensorState.pan.y = e.clientY - panStart.y;
            applyZoom();
        }
    });

    window.addEventListener('mouseup', (e) => {
        if (isPanning) {
            isPanning = false;
            wrapper.style.cursor = spacePressed ? 'grab' : '';
        }
    });
}


// Export
window.initCensorEdit = initCensorEdit;
