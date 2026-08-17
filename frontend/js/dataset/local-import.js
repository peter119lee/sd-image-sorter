/**
 * Dataset Maker — local folder-import core: negative ids, path-keyed caption persistence, manifests, later-wins overrides (_thumbSrc, _fetch..., _remove..., _updateCount, _isReadyToExport) and the SINGLE _buildExportPayload.
 * Moved VERBATIM from dataset-maker-local-import.js L1-51 + L58-760.
 * Load order is pinned by the ordered async=false loader in dataset/core.js.
 */
/**
 * Dataset Maker — local folder-import (T7b, "small gallery" frontend).
 *
 * Adds direct folder-import to Dataset Maker so the user can build a
 * LoRA training set from a folder of images WITHOUT first registering
 * those images in the main library DB. Items added this way are
 * "local-source" and live entirely in the Dataset Maker session.
 *
 * Implementation strategy
 * -----------------------
 * Local items get a NEGATIVE pseudo-ID derived from the ``ds_id``
 * returned by ``POST /api/dataset/folder-scan``. Negative IDs never
 * collide with the gallery's positive int row IDs, which lets the
 * existing ``imageIds: number[]`` array work for both sources without
 * a schema rewrite.
 *
 * The places that previously called ``/api/image-thumbnail/{id}`` or
 * ``/api/tags/export-preview`` with image IDs are wrapped here to
 * branch on ``id < 0``:
 *   - thumbnail render uses inline ``thumb_b64`` when present, otherwise
 *     lazily fetches ``/api/dataset/local-thumbnail`` for visible items
 *   - meta + caption fetch is skipped (local items are fully populated
 *     by the scan response; captions live in localStorage)
 *   - export request splits positive IDs (image_ids) from negative IDs
 *     (resolved to ``abs_path`` and shipped as ``image_paths``); user
 *     edits for local items are sent as path-keyed ``image_overrides``.
 *
 * Caption persistence for local items
 * -----------------------------------
 * Edits land in ``localStorage`` keyed by absolute path. Re-importing
 * the same folder restores the user's captions because the ``ds_id``
 * is deterministic (sha1(abs_path)) and the path-keyed localStorage
 * entry is the same.
 */
(function () {
    'use strict';
    if (!window.DatasetMaker) return;
    const DM = window.DatasetMaker;

    const LOCAL_CAPTIONS_KEY = 'sd-image-sorter-dataset-local-captions';
    const LOCAL_CAPTION_TRIGGERS_KEY = 'sd-image-sorter-dataset-local-caption-triggers';

    const usesUnscopedLocalCaptionCache = (datasetMaker) => (
        datasetMaker._activeProject === null || datasetMaker._activeProject === undefined
    );

    /** Local-only state (in addition to the shared ``imageIds`` / ``meta``). */
    DM.localItemPaths = DM.localItemPaths || new Map();   // negative id -> abs_path
    DM.localItemDsIds = DM.localItemDsIds || new Map();   // negative id -> ds_id (for completeness)
    DM.localManifestTokens = DM.localManifestTokens || new Map(); // scan_token -> {total, excludedPaths}
    DM._folderScanToken = null;
    DM._folderScanNextOffset = 0;
    DM._folderScanHasMore = false;
    DM._folderScanTotal = 0;
    DM._folderScanPreviewed = 0;

    const NativeSet = globalThis.Set;
    const isNativeSet = (value) => typeof NativeSet === 'function' && value instanceof NativeSet;
    const newNativeSet = (items = []) => typeof NativeSet === 'function'
        ? new NativeSet(items)
        : { add() {}, has() { return false; }, size: 0, [Symbol.iterator]: function* () {} };

    /** Negative-id helper: true iff the supplied id refers to a local-source item. */
    DM.isLocalId = function (id) {
        return Number(id) < 0;
    };

    /** Convert backend ``ds_id`` ("ds:abc123...") to a stable negative integer id. */
    DM._dsIdToNumericId = function (dsId) {
        // Use 52 bits, not the old 31-bit slice. At 100k local images a
        // 31-bit birthday collision is realistic; 52 bits keeps it negligible
        // while staying inside JavaScript's safe integer range.
        const hex = String(dsId || '').replace(/^ds:/, '').slice(0, 13);
        let n = parseInt(hex, 16);
        if (!Number.isFinite(n) || n <= 0) {
            // Fallback: hash the ds_id string with a small djb2 so we
            // still get a unique negative id even if the format shifts.
            let h = 5381;
            for (let i = 0; i < (dsId || '').length; i++) {
                h = ((h << 5) + h + dsId.charCodeAt(i)) | 0;
            }
            n = Math.abs(h) || 1;
        }
        return -Math.min(n || 1, Number.MAX_SAFE_INTEGER);
    };

    function localThumbnailUrl(absPath, size = 256) {
        const path = String(absPath || '').trim();
        if (!path) return '';
        const px = Math.max(1, Math.min(4096, Math.round(Number(size) || 256)));
        return `/api/dataset/local-thumbnail?path=${encodeURIComponent(path)}&size=${px}`;
    }

    const original_thumbSrc = DM._thumbSrc;
    DM._thumbSrc = function (id, size = 128) {
        const numericId = Number(id);
        if (!this.isLocalId(numericId)) {
            return typeof original_thumbSrc === 'function'
                ? original_thumbSrc.call(this, numericId, size)
                : `/api/image-thumbnail/${numericId}?size=${size}`;
        }
        const meta = this.meta?.get?.(numericId) || {};
        if (meta.thumb_b64) return `data:image/jpeg;base64,${meta.thumb_b64}`;
        const absPath = meta.abs_path || this.localItemPaths?.get?.(numericId) || '';
        return localThumbnailUrl(absPath, size);
    };

    // -------- localStorage caption persistence (path-keyed) --------

    function storageFailureReason(error) {
        return error instanceof Error ? error.message : String(error);
    }

    function readStoredStringMap(storageKey) {
        let raw;
        try {
            raw = localStorage.getItem(storageKey);
        } catch (error) {
            throw new Error(
                `Could not read browser storage key "${storageKey}": ${storageFailureReason(error)}`,
            );
        }
        if (!raw) return {};
        let parsed;
        try {
            parsed = JSON.parse(raw);
        } catch (error) {
            throw new SyntaxError(
                `Browser storage key "${storageKey}" contains invalid JSON: ${storageFailureReason(error)}`,
            );
        }
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new TypeError(`Browser storage key "${storageKey}" must contain an object.`);
        }
        for (const [path, value] of Object.entries(parsed)) {
            if (typeof value !== 'string') {
                throw new TypeError(
                    `Browser storage key "${storageKey}" path "${path}" must contain a string.`,
                );
            }
        }
        return parsed;
    }

    DM._loadLocalCaptions = function () {
        return readStoredStringMap(LOCAL_CAPTIONS_KEY);
    };

    DM._saveLocalCaption = function (absPath, caption) {
        if (!absPath) return;
        const all = DM._loadLocalCaptions();
        if (caption == null || caption === '') {
            delete all[absPath];
        } else {
            all[absPath] = String(caption);
        }
        try {
            localStorage.setItem(LOCAL_CAPTIONS_KEY, JSON.stringify(all));
        } catch (error) {
            throw new Error(
                `Could not persist local caption for "${absPath}": ${storageFailureReason(error)}`,
            );
        }
    };

    DM._loadLocalCaptionTriggers = function () {
        return readStoredStringMap(LOCAL_CAPTION_TRIGGERS_KEY);
    };

    DM._persistLocalCaptionState = function (captions, captionTriggers) {
        let previousCaptions;
        try {
            previousCaptions = localStorage.getItem(LOCAL_CAPTIONS_KEY);
        } catch (error) {
            throw new Error(
                `Could not read browser storage before persisting local captions: ${storageFailureReason(error)}`,
            );
        }
        const nextCaptions = JSON.stringify(captions);
        const nextCaptionTriggers = JSON.stringify(captionTriggers);
        let captionsWritten = false;
        try {
            localStorage.setItem(LOCAL_CAPTIONS_KEY, nextCaptions);
            captionsWritten = true;
            localStorage.setItem(LOCAL_CAPTION_TRIGGERS_KEY, nextCaptionTriggers);
        } catch (error) {
            let rollbackError = null;
            if (captionsWritten) {
                try {
                    if (previousCaptions === null) localStorage.removeItem(LOCAL_CAPTIONS_KEY);
                    else localStorage.setItem(LOCAL_CAPTIONS_KEY, previousCaptions);
                } catch (caughtRollbackError) {
                    rollbackError = caughtRollbackError;
                }
            }
            const rollbackDetail = rollbackError === null
                ? ''
                : ` Caption rollback also failed: ${storageFailureReason(rollbackError)}.`;
            throw new Error(
                `Could not persist local caption ownership in browser storage: ${storageFailureReason(error)}.${rollbackDetail}`,
            );
        }
    };

    DM._saveManagedTriggerForLocalIds = function (ids, trigger, captionOverrides) {
        if (captionOverrides !== null && captionOverrides !== undefined && !(captionOverrides instanceof Map)) {
            throw new TypeError('Local caption overrides must be a Map, null, or undefined.');
        }
        if (!usesUnscopedLocalCaptionCache(this)) return;
        const localIdsWithBooruOverrides = Array.from(ids || [])
            .map(Number)
            .filter((id) => {
                if (!this.isLocalId(id)) return false;
                if (captionOverrides instanceof Map) return captionOverrides.has(id);
                return this.captionEdits.has(id);
            });
        if (localIdsWithBooruOverrides.length === 0) return;
        const captions = this._loadLocalCaptions();
        const captionTriggers = this._loadLocalCaptionTriggers();
        const cleanTrigger = String(trigger || '').trim();
        let changed = false;
        for (const id of localIdsWithBooruOverrides) {
            const hasOverride = captionOverrides?.has(id) === true;
            const absPath = this.localItemPaths.get(id);
            if (!absPath) continue;
            const caption = String(
                hasOverride ? captionOverrides.get(id) : this.captionEdits.get(id),
            );
            const preservesExplicitEmptyCaption = caption === '' && (
                hasOverride || Object.prototype.hasOwnProperty.call(captions, absPath)
            );
            if (caption || preservesExplicitEmptyCaption) {
                captions[absPath] = caption;
                if (cleanTrigger) captionTriggers[absPath] = cleanTrigger;
                else delete captionTriggers[absPath];
            } else {
                delete captions[absPath];
                delete captionTriggers[absPath];
            }
            changed = true;
        }
        if (changed) this._persistLocalCaptionState(captions, captionTriggers);
    };

    DM._clearLocalCaption = function (absPath) {
        if (!absPath) return;
        const captions = DM._loadLocalCaptions();
        const captionTriggers = DM._loadLocalCaptionTriggers();
        const changed = Object.prototype.hasOwnProperty.call(captions, absPath)
            || Object.prototype.hasOwnProperty.call(captionTriggers, absPath);
        delete captions[absPath];
        delete captionTriggers[absPath];
        if (changed) DM._persistLocalCaptionState(captions, captionTriggers);
    };

    DM._registerFolderManifest = function (data) {
        const token = String(data?.scan_token || '').trim();
        if (!token) return null;
        const existing = this.localManifestTokens.get(token) || {};
        this.localManifestTokens.set(token, {
            scan_token: token,
            folder_path: data.folder_path || existing.folder_path || '',
            total: Number(data.total_files_seen || existing.total || 0) || 0,
            queueIndex: Number.isSafeInteger(existing.queueIndex)
                ? existing.queueIndex
                : this.imageIds.length,
            excludedPaths: isNativeSet(existing.excludedPaths) ? existing.excludedPaths : newNativeSet(),
        });
        this._scheduleSaveSession?.();
        return token;
    };

    DM._markLocalManifestExcluded = function (id) {
        const numericId = Number(id);
        const meta = this.meta?.get?.(numericId) || {};
        const token = String(meta.folder_scan_token || '').trim();
        const absPath = this.localItemPaths?.get?.(numericId) || meta.abs_path || '';
        if (!token || !absPath) return;
        const source = this.localManifestTokens.get(token);
        if (!source) return;
        source.excludedPaths = isNativeSet(source.excludedPaths) ? source.excludedPaths : newNativeSet();
        source.excludedPaths.add(absPath);
        this._scheduleSaveSession?.();
    };

    DM._excludeLocalPathFromManifests = function (absPath) {
        const path = String(absPath || '').trim();
        if (!path || !this.localManifestTokens) return false;
        let touched = false;
        const sources = Array.from(this.localManifestTokens.values());
        for (const source of sources) {
            const root = String(source.folder_path || '').replace(/[\\/]+$/, '');
            const inSource = root
                ? (path === root || path.startsWith(root + '/') || path.startsWith(root + '\\'))
                : sources.length === 1;
            if (!inSource) continue;
            source.excludedPaths = isNativeSet(source.excludedPaths) ? source.excludedPaths : newNativeSet();
            if (!source.excludedPaths.has(path)) {
                source.excludedPaths.add(path);
                touched = true;
            }
        }
        return touched;
    };

    DM._localIdUsesManifest = function (id) {
        const meta = this.meta?.get?.(Number(id)) || {};
        const token = String(meta.folder_scan_token || '').trim();
        return !!(token && this.localManifestTokens?.has?.(token));
    };

    DM._getDatasetScanTokenSources = function () {
        const out = [];
        for (const [token, source] of this.localManifestTokens.entries()) {
            if (!token) continue;
            out.push({
                scan_token: token,
                exclude_paths: Array.from(source.excludedPaths || []),
            });
        }
        return out;
    };

    DM._getLogicalDatasetCount = function () {
        let count = 0;
        for (const id of this.imageIds || []) {
            const numericId = Number(id);
            if (this.isLocalId(numericId) && this._localIdUsesManifest(numericId)) continue;
            count += 1;
        }
        for (const source of this.localManifestTokens.values()) {
            const total = Number(source.total || 0) || 0;
            const excluded = isNativeSet(source.excludedPaths) ? source.excludedPaths.size : 0;
            count += Math.max(0, total - excluded);
        }
        return count;
    };

    function requireManifestPage(data, scanToken, offset, expectedTotal) {
        if (!data || typeof data !== 'object' || Array.isArray(data)) {
            throw new TypeError(`Folder manifest ${scanToken} returned an invalid response object.`);
        }
        if (data.scan_token !== scanToken) {
            throw new TypeError(`Folder manifest ${scanToken} returned a different scan token.`);
        }
        if (!Number.isSafeInteger(data.total_files_seen) || data.total_files_seen !== expectedTotal) {
            throw new TypeError(
                `Folder manifest ${scanToken} expected ${expectedTotal} files but returned ${data.total_files_seen}.`,
            );
        }
        if (!Number.isSafeInteger(data.offset) || data.offset !== offset) {
            throw new TypeError(`Folder manifest ${scanToken} returned an unexpected offset.`);
        }
        if (typeof data.has_more !== 'boolean' || !Array.isArray(data.items)) {
            throw new TypeError(`Folder manifest ${scanToken} returned invalid pagination fields.`);
        }
        const items = data.items.map((item, index) => {
            if (!item || typeof item !== 'object' || Array.isArray(item)) {
                throw new TypeError(`Folder manifest ${scanToken} item ${index} is invalid.`);
            }
            const dsId = String(item.ds_id || '');
            const absPath = String(item.abs_path || '').trim();
            const filename = String(item.filename || '').trim();
            const scanIndex = Number(item.scan_index);
            const width = Number(item.width);
            const height = Number(item.height);
            const mtime = Number(item.mtime);
            const size = Number(item.size);
            const sidecarCaption = item.sidecar_caption;
            if (!/^ds:[0-9a-f]{16}$/.test(dsId) || !absPath || !filename) {
                throw new TypeError(`Folder manifest ${scanToken} item ${index} has invalid identity fields.`);
            }
            if (
                !Number.isSafeInteger(scanIndex)
                || scanIndex < 0
                || scanIndex >= expectedTotal
                || scanIndex !== offset + index
            ) {
                throw new TypeError(`Folder manifest ${scanToken} item ${index} has an invalid scan index.`);
            }
            if (
                !Number.isSafeInteger(width)
                || width < 0
                || !Number.isSafeInteger(height)
                || height < 0
                || !Number.isFinite(mtime)
                || mtime < 0
                || !Number.isSafeInteger(size)
                || size < 0
                || typeof item.thumb_b64 !== 'string'
                || item.source_kind !== 'folder_path'
                || item.sidecar_capability !== 'beside_image'
                || (sidecarCaption !== undefined
                    && sidecarCaption !== null
                    && typeof sidecarCaption !== 'string')
            ) {
                throw new TypeError(`Folder manifest ${scanToken} item ${index} has invalid file fields.`);
            }
            return {
                ds_id: dsId,
                abs_path: absPath,
                filename,
                width,
                height,
                mtime,
                size,
                thumb_b64: item.thumb_b64,
                scan_index: scanIndex,
                folder_scan_token: scanToken,
                source_kind: 'folder_path',
                sidecar_capability: 'beside_image',
                sidecar_caption: typeof sidecarCaption === 'string' ? sidecarCaption : null,
            };
        });
        const nextOffset = data.next_offset;
        const pageEndOffset = offset + items.length;
        if (data.has_more) {
            if (
                !Number.isSafeInteger(nextOffset)
                || nextOffset !== pageEndOffset
                || nextOffset <= offset
                || nextOffset >= expectedTotal
            ) {
                throw new TypeError(`Folder manifest ${scanToken} did not advance pagination.`);
            }
        } else if (nextOffset !== null) {
            throw new TypeError(`Folder manifest ${scanToken} returned a terminal next offset.`);
        } else if (pageEndOffset !== expectedTotal) {
            throw new TypeError(`Folder manifest ${scanToken} returned an incomplete terminal page.`);
        }
        return { items, nextOffset };
    }

    async function requestManifestPage(scanToken, offset, limit, expectedTotal) {
        const response = await fetch('/api/dataset/folder-scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scan_token: scanToken,
                offset,
                limit,
                include_thumbnails: false,
            }),
        });
        let data;
        try {
            data = await response.json();
        } catch (error) {
            throw new TypeError(
                `Folder manifest ${scanToken} returned invalid JSON: ${String(error)}`,
            );
        }
        if (!response.ok) {
            throw new Error(
                `Folder manifest ${scanToken} failed with HTTP ${response.status}: ${JSON.stringify(data)}`,
            );
        }
        return requireManifestPage(data, scanToken, offset, expectedTotal);
    }

    async function readManifestPage(scanToken, offset, limit, expectedTotal) {
        let lastError = null;
        for (let attempt = 0; attempt < 2; attempt += 1) {
            try {
                return await requestManifestPage(scanToken, offset, limit, expectedTotal);
            } catch (error) {
                lastError = error;
                if (attempt === 1) throw error;
                window.Logger?.warn?.('dataset_project_manifest_retry', {
                    scan_token: scanToken,
                    offset,
                    attempt: attempt + 1,
                    error: String(error),
                });
            }
        }
        throw lastError;
    }

    async function loadCompleteManifest(source, pageSize) {
        const scanToken = String(source.scan_token || '').trim();
        const expectedTotal = Number(source.total);
        if (!scanToken || !Number.isSafeInteger(expectedTotal) || expectedTotal < 0) {
            throw new TypeError('Dataset project contains invalid folder manifest state.');
        }
        const items = [];
        let offset = 0;
        while (offset < expectedTotal) {
            const page = await readManifestPage(scanToken, offset, pageSize, expectedTotal);
            items.push(...page.items);
            if (page.nextOffset === null) break;
            offset = page.nextOffset;
        }
        if (items.length !== expectedTotal) {
            throw new TypeError(
                `Folder manifest ${scanToken} expected ${expectedTotal} files but materialized ${items.length}.`,
            );
        }
        const uniquePaths = new Set(items.map((item) => item.abs_path));
        if (uniquePaths.size !== items.length) {
            throw new TypeError(`Folder manifest ${scanToken} contains duplicate paths.`);
        }
        return { source, items };
    }

    DM._materializeProjectLocalItems = async function () {
        const sources = Array.from(this.localManifestTokens.values());
        if (sources.length === 0) return;
        const pageSize = Number(this._folderScanPageSize);
        if (!Number.isSafeInteger(pageSize) || pageSize <= 0) {
            throw new TypeError('Dataset folder scan page size is unavailable.');
        }

        const originalIds = this.imageIds.slice();
        const originalManifestState = new Map(sources.map((source) => [
            String(source.scan_token || ''),
            {
                source,
                excludedPaths: Array.from(source.excludedPaths || []),
            },
        ]));
        const originalTokenIds = new Map(sources.map((source) => {
            const token = String(source.scan_token || '');
            return [token, originalIds.filter((id) => (
                String(this.meta.get(Number(id))?.folder_scan_token || '') === token
            ))];
        }));
        for (const source of sources) {
            const token = String(source.scan_token || '');
            const total = Number(source.total);
            const excludedCount = isNativeSet(source.excludedPaths)
                ? source.excludedPaths.size
                : 0;
            if (total > excludedCount && (originalTokenIds.get(token) || []).length === 0) {
                throw new Error(
                    `Folder manifest ${token} has no loaded queue anchor. Load another preview page or scan the folder again before saving.`,
                );
            }
        }
        const manifests = await Promise.all(
            sources.map((source) => loadCompleteManifest(source, pageSize)),
        );
        const queueUnchanged = this.imageIds.length === originalIds.length
            && this.imageIds.every((id, index) => id === originalIds[index]);
        const manifestsUnchanged = this.localManifestTokens.size === originalManifestState.size
            && Array.from(originalManifestState.entries()).every(([token, state]) => {
                const current = this.localManifestTokens.get(token);
                const currentExcluded = Array.from(current?.excludedPaths || []);
                return current === state.source
                    && currentExcluded.length === state.excludedPaths.length
                    && currentExcluded.every((path, index) => path === state.excludedPaths[index]);
            });
        if (!queueUnchanged || !manifestsUnchanged) {
            throw new Error(
                'Dataset queue changed while project sources were being prepared. Save again with the current queue.',
            );
        }
        const includedPaths = [];
        for (const { source, items } of manifests) {
            const excludedPaths = isNativeSet(source.excludedPaths)
                ? source.excludedPaths
                : newNativeSet();
            includedPaths.push(...items
                .filter((item) => !excludedPaths.has(item.abs_path))
                .map((item) => item.abs_path));
        }
        if (new Set(includedPaths).size !== includedPaths.length) {
            throw new TypeError('Dataset folder manifests contain duplicate local paths.');
        }
        const allItems = manifests.flatMap(({ items }) => items);
        this.addLocalItems(allItems, {
            switchView: false,
            showToast: false,
            focusImportTab: false,
        });

        let nextIds = this.imageIds.slice();
        for (const { source, items } of manifests) {
            const token = String(source.scan_token);
            const excludedPaths = isNativeSet(source.excludedPaths)
                ? source.excludedPaths
                : newNativeSet();
            const idsByPath = new Map(
                Array.from(this.localItemPaths.entries()).map(([id, path]) => [path, Number(id)]),
            );
            const orderedIds = items
                .filter((item) => !excludedPaths.has(item.abs_path))
                .map((item) => {
                    const id = idsByPath.get(item.abs_path);
                    if (!Number.isSafeInteger(id) || id >= 0) {
                        throw new TypeError(
                            `Folder manifest ${token} could not resolve ${item.abs_path} in the Dataset queue.`,
                        );
                    }
                    return id;
                });
            const orderedSet = new Set(orderedIds);
            const preexistingIndices = (originalTokenIds.get(token) || [])
                .map((id) => nextIds.indexOf(id))
                .filter((index) => index >= 0);
            const requestedIndex = preexistingIndices.length > 0
                ? Math.min(...preexistingIndices)
                : Number(source.queueIndex);
            const withoutManifest = nextIds.filter((id) => !orderedSet.has(Number(id)));
            const insertionIndex = Number.isSafeInteger(requestedIndex)
                ? Math.max(0, Math.min(requestedIndex, withoutManifest.length))
                : withoutManifest.length;
            nextIds = [
                ...withoutManifest.slice(0, insertionIndex),
                ...orderedIds,
                ...withoutManifest.slice(insertionIndex),
            ];
            source.queueIndex = insertionIndex;
        }
        this.imageIds = nextIds;
        this._renderQueue();
        this._renderImportGallery?.();
        this._updateCount();
        this._updateExportEnabled();
        this._saveSession();
    };

    // -------- Add local items from folder-scan response --------

    /**
     * Ingest folder-scan items into the queue. Each item is the shape
     * returned by ``POST /api/dataset/folder-scan``: ``{ds_id, abs_path,
     * filename, width, height, mtime, size, thumb_b64}``.
     *
     * Returns the number of NEW items added (after dedup).
     */
    DM.addLocalItems = function (items, options = {}) {
        const switchView = options.switchView !== false;
        const showToast = options.showToast !== false;
        const focusImportTab = options.focusImportTab === true;

        const originalImageIds = [...this.imageIds];
        const originalActiveId = this.activeId;
        const originalLastClickedId = this._lastClickedId;
        const before = originalImageIds.length;
        const seen = new Set(this.imageIds.map(Number));
        const usePathCaptionCache = usesUnscopedLocalCaptionCache(this);
        const localCaptions = usePathCaptionCache ? this._loadLocalCaptions() : {};
        const localCaptionTriggers = usePathCaptionCache ? this._loadLocalCaptionTriggers() : {};
        const managedTrigger = String(this._quickfilledTrigger || '').trim();
        if (managedTrigger && typeof this._replaceManagedTriggerInCaptionsByOwner !== 'function') {
            throw new TypeError('Dataset trigger caption owner writer is unavailable.');
        }
        const addedLocalItems = new Map();
        const touchedItemState = new Map();
        let touchedActive = false;

        try {
            for (const item of (items || [])) {
                const dsId = String(item.ds_id || '');
                if (!dsId.startsWith('ds:')) continue;
                let numericId = this._dsIdToNumericId(dsId);
                const absPath = String(item.abs_path || '');
                if (!absPath) continue;

                // Extremely defensive collision handling for synthetic local IDs.
                while (seen.has(numericId) && this.localItemPaths.get(numericId) !== absPath) {
                    numericId -= 1;
                }
                if (!touchedItemState.has(numericId)) {
                    touchedItemState.set(numericId, {
                        path: mapEntry(this.localItemPaths, numericId),
                        dsId: mapEntry(this.localItemDsIds, numericId),
                        meta: mapEntry(this.meta, numericId),
                        caption: mapEntry(this.captions, numericId),
                        captionEdit: mapEntry(this.captionEdits, numericId),
                        nlCaption: mapEntry(this.nlCaptions, numericId),
                        nlEdit: mapEntry(this.nlEdits, numericId),
                        captionType: mapEntry(this.captionType, numericId),
                        undoStack: mapEntry(this._undoStacks, numericId),
                        selected: this._queueSelection.has(numericId),
                    });
                }

                if (!seen.has(numericId)) {
                    this.imageIds.push(numericId);
                    seen.add(numericId);
                    addedLocalItems.set(
                        numericId,
                        String(localCaptionTriggers[absPath] || '').trim(),
                    );
                }
                this.localItemPaths.set(numericId, absPath);
                this.localItemDsIds.set(numericId, dsId);
                const existing = this.meta.get(numericId) || {};
                const scanIndex = Number(item.scan_index);
                const nextMeta = {
                    ...existing,
                    source: 'local',
                    ds_id: dsId,
                    abs_path: absPath,
                    filename: item.filename || existing.filename || '',
                    thumbnail_path: '',
                    thumb_b64: item.thumb_b64 || existing.thumb_b64 || '',
                    width: Number(item.width || existing.width || 0),
                    height: Number(item.height || existing.height || 0),
                    mtime: Number(item.mtime || existing.mtime || 0),
                    size: Number(item.size || existing.size || 0),
                    scan_index: Number.isFinite(scanIndex) ? scanIndex : existing.scan_index,
                    folder_scan_token: item.folder_scan_token || existing.folder_scan_token || '',
                    source_kind: item.source_kind || existing.source_kind || 'folder_path',
                    sidecar_capability: item.sidecar_capability || existing.sidecar_capability || 'beside_image',
                };
                delete nextMeta.sidecar_caption;
                this.meta.set(numericId, nextMeta);
                if (Number(this.activeId) === Number(numericId)) touchedActive = true;
                if (typeof item.sidecar_caption === 'string' && !this.captions.has(numericId)) {
                    this.captions.set(numericId, item.sidecar_caption);
                }
                // Restore any saved caption for this path so re-imports
                // pick the user's previous edit back up.
                const hasSavedCaption = Object.prototype.hasOwnProperty.call(localCaptions, absPath);
                if (hasSavedCaption && !this.captionEdits.has(numericId)) {
                    this.captionEdits.set(numericId, String(localCaptions[absPath] ?? ''));
                }
            }
            if (managedTrigger && addedLocalItems.size > 0) {
                this._replaceManagedTriggerInCaptionsByOwner(addedLocalItems, managedTrigger);
            }
        } catch (error) {
            this.imageIds = originalImageIds;
            this.activeId = originalActiveId;
            this._lastClickedId = originalLastClickedId;
            const previousRestoringSession = this._restoringSession;
            this._restoringSession = true;
            try {
                for (const [id, snapshot] of touchedItemState.entries()) {
                    restoreMapEntry(this.localItemPaths, id, snapshot.path);
                    restoreMapEntry(this.localItemDsIds, id, snapshot.dsId);
                    restoreMapEntry(this.meta, id, snapshot.meta);
                    restoreMapEntry(this.captions, id, snapshot.caption);
                    restoreMapEntry(this.captionEdits, id, snapshot.captionEdit);
                    restoreMapEntry(this.nlCaptions, id, snapshot.nlCaption);
                    restoreMapEntry(this.nlEdits, id, snapshot.nlEdit);
                    restoreMapEntry(this.captionType, id, snapshot.captionType);
                    restoreMapEntry(this._undoStacks, id, snapshot.undoStack);
                    if (snapshot.selected) this._queueSelection.add(id);
                    else this._queueSelection.delete(id);
                }
            } finally {
                this._restoringSession = previousRestoringSession;
            }
            throw error;
        }

        const added = this.imageIds.length - before;
        this._renderQueue();
        this._updateCount();
        this._updateExportEnabled();
        this._syncSourceCapabilityStatus?.();
        this._syncOutputModeUi?.();
        if (typeof this._renderImportGallery === 'function') {
            this._renderImportGallery();
        }
        if (added > 0 && focusImportTab && typeof this._setPipelineTab === 'function') {
            this._setPipelineTab('import');
        }
        if (this.activeId == null && this.imageIds.length) {
            this._setActive(this.imageIds[0]);
        } else if (touchedActive && this.activeId != null) {
            this._setActive(this.activeId);
        }

        if (switchView && added > 0 && typeof window.switchView === 'function') {
            try { window.switchView('dataset'); } catch { /* ignore */ }
        }
        if (showToast) {
            if (added > 0) {
                this._toast(this._t('dataset.folderImportAdded',
                    'Added {count} local images (not added to main gallery)',
                    { count: added }), 'success');
            } else {
                this._toast(this._t('dataset.folderImportEmpty',
                    'No new images found in that folder.'), 'info');
            }
        }
        this._checkDuplicateFilenames();
        if (added > 0 || touchedActive) this._saveSession?.();
        return added;
    };

    DM._serializeLocalDatasetState = function () {
        const localItems = [];
        for (const [id, absPath] of this.localItemPaths.entries()) {
            const numericId = Number(id);
            const meta = { ...(this.meta.get(numericId) || {}) };
            delete meta.thumb_b64;
            delete meta.sidecar_caption;
            localItems.push({
                id: numericId,
                abs_path: absPath,
                ds_id: this.localItemDsIds.get(numericId) || meta.ds_id || '',
                meta,
                ...(this.captions.has(numericId)
                    ? { caption_baseline: String(this.captions.get(numericId) ?? '') }
                    : {}),
            });
        }
        const manifests = [];
        for (const [token, source] of this.localManifestTokens.entries()) {
            manifests.push({
                scan_token: token,
                folder_path: source?.folder_path || '',
                total: Number(source?.total || 0) || 0,
                queueIndex: Number.isSafeInteger(source?.queueIndex) ? source.queueIndex : 0,
                excludedPaths: Array.from(source?.excludedPaths || []),
            });
        }
        return { localItems, manifests };
    };

    DM._restoreLocalSession = function (local = {}) {
        if (!local || typeof local !== 'object') return;
        this.localItemPaths.clear();
        this.localItemDsIds.clear();
        this.localManifestTokens.clear();

        for (const source of (local.manifests || [])) {
            const token = String(source?.scan_token || '').trim();
            if (!token) continue;
            this.localManifestTokens.set(token, {
                scan_token: token,
                folder_path: source.folder_path || '',
                total: Number(source.total || 0) || 0,
                queueIndex: Number.isSafeInteger(source.queueIndex) ? source.queueIndex : 0,
                excludedPaths: newNativeSet(Array.isArray(source.excludedPaths) ? source.excludedPaths : []),
            });
        }

        for (const item of (local.localItems || [])) {
            const id = Number(item?.id);
            const absPath = String(item?.abs_path || item?.meta?.abs_path || '').trim();
            if (!Number.isFinite(id) || id >= 0 || !absPath) continue;
            const meta = { ...(item.meta || {}) };
            meta.source = 'local';
            meta.abs_path = absPath;
            meta.ds_id = item.ds_id || meta.ds_id || '';
            meta.source_kind = meta.source_kind || 'folder_path';
            meta.sidecar_capability = meta.sidecar_capability || 'beside_image';
            delete meta.sidecar_caption;
            this.localItemPaths.set(id, absPath);
            if (meta.ds_id) this.localItemDsIds.set(id, meta.ds_id);
            this.meta.set(id, meta);
            if (typeof item.caption_baseline === 'string') {
                this.captions.set(id, item.caption_baseline);
            }
        }
    };

    DM._restoreProjectLocalItems = function (items) {
        const idsByPosition = new Map();
        const restoredCaptionOwners = new Map();
        const usedIds = newNativeSet();
        for (const item of items) {
            const absPath = String(item.path);
            const dsId = String(item.ds_id);
            let numericId = this._dsIdToNumericId(dsId);
            while (usedIds.has(numericId)) numericId -= 1;
            usedIds.add(numericId);

            const filename = absPath.split(/[\\/]/).pop() || absPath;
            this.localItemPaths.set(numericId, absPath);
            this.localItemDsIds.set(numericId, dsId);
            this.meta.set(numericId, {
                source: 'local',
                ds_id: dsId,
                abs_path: absPath,
                filename,
                thumbnail_path: '',
                thumb_b64: '',
                width: 0,
                height: 0,
                mtime: Number(item.mtime_ns) / 1_000_000_000,
                mtime_ns: item.mtime_ns,
                size: item.size,
                source_device: item.device,
                source_inode: item.inode,
                source_kind: 'project_local',
                sidecar_capability: 'beside_image',
            });
            if (typeof item.sidecar_caption === 'string' && !this.captions.has(numericId)) {
                this.captions.set(numericId, item.sidecar_caption);
            }
            const hasExistingCaptionEdit = this.captionEdits.has(numericId);
            if (!hasExistingCaptionEdit) {
                restoredCaptionOwners.set(numericId, '');
            }
            idsByPosition.set(item.position, numericId);
        }
        return { idsByPosition, restoredCaptionOwners };
    };

    function mapEntry(map, key) {
        return map.has(key)
            ? { present: true, value: map.get(key) }
            : { present: false, value: null };
    }

    function restoreMapEntry(map, key, entry) {
        if (entry.present) map.set(key, entry.value);
        else map.delete(key);
    }

    DM._rekeyLocalCaptionState = function (plans) {
        if (!usesUnscopedLocalCaptionCache(this)) return;
        const captions = this._loadLocalCaptions();
        const captionTriggers = this._loadLocalCaptionTriggers();
        let changed = false;
        for (const plan of plans) {
            const oldPath = String(plan.oldPath);
            const newPath = String(plan.item.path);
            if (!oldPath || !newPath || oldPath === newPath) continue;
            if (Object.prototype.hasOwnProperty.call(captions, oldPath)) {
                captions[newPath] = captions[oldPath];
                delete captions[oldPath];
                changed = true;
            }
            if (Object.prototype.hasOwnProperty.call(captionTriggers, oldPath)) {
                captionTriggers[newPath] = captionTriggers[oldPath];
                delete captionTriggers[oldPath];
                changed = true;
            }
        }
        if (changed) this._persistLocalCaptionState(captions, captionTriggers);
    };

    DM._applySavedProjectLocalIdentities = function (items) {
        if (!Array.isArray(items) || items.length !== this.imageIds.length) {
            throw new TypeError('Saved Dataset project membership does not match the current queue.');
        }
        const usedIds = newNativeSet();
        const plans = [];
        for (const item of items) {
            const currentId = Number(this.imageIds[item.position]);
            if (item.item_type === 'library') {
                if (!Number.isSafeInteger(currentId) || currentId <= 0 || currentId !== item.image_id) {
                    throw new TypeError(`Saved Dataset project item ${item.position} does not match the Library queue.`);
                }
                continue;
            }
            if (item.source_status !== 'available' || !this.isLocalId(currentId)) {
                throw new TypeError(`Saved Dataset project item ${item.position} is not an available local queue item.`);
            }
            let nextId = this._dsIdToNumericId(item.ds_id);
            while (usedIds.has(nextId)) nextId -= 1;
            usedIds.add(nextId);
            const oldPath = String(this.localItemPaths.get(currentId) || '');
            if (!oldPath) {
                throw new TypeError(`Saved Dataset project item ${item.position} has no local path.`);
            }
            plans.push({
                currentId,
                nextId,
                oldPath,
                item,
                meta: mapEntry(this.meta, currentId),
                caption: mapEntry(this.captions, currentId),
                captionEdit: mapEntry(this.captionEdits, currentId),
                nlCaption: mapEntry(this.nlCaptions, currentId),
                nlEdit: mapEntry(this.nlEdits, currentId),
                captionType: mapEntry(this.captionType, currentId),
                undoStack: mapEntry(this._undoStacks, currentId),
                selected: this._queueSelection.has(currentId),
            });
        }

        this._rekeyLocalCaptionState(plans);

        this._restoringSession = true;
        try {
            for (const plan of plans) {
                this.localItemPaths.delete(plan.currentId);
                this.localItemDsIds.delete(plan.currentId);
                this.meta.delete(plan.currentId);
                this.captions.delete(plan.currentId);
                this.captionEdits.delete(plan.currentId);
                this.nlCaptions.delete(plan.currentId);
                this.nlEdits.delete(plan.currentId);
                this.captionType.delete(plan.currentId);
                this._undoStacks.delete(plan.currentId);
                this._queueSelection.delete(plan.currentId);
            }
            for (const plan of plans) {
                const oldMeta = plan.meta.present ? plan.meta.value : {};
                const {
                    folder_scan_token: ignoredFolderToken,
                    scan_index: ignoredScanIndex,
                    sidecar_caption: ignoredSidecarCaption,
                    ...preservedMeta
                } = oldMeta;
                void ignoredFolderToken;
                void ignoredScanIndex;
                void ignoredSidecarCaption;
                this.localItemPaths.set(plan.nextId, plan.item.path);
                this.localItemDsIds.set(plan.nextId, plan.item.ds_id);
                this.meta.set(plan.nextId, {
                    ...preservedMeta,
                    source: 'local',
                    ds_id: plan.item.ds_id,
                    abs_path: plan.item.path,
                    filename: String(plan.item.path).split(/[\\/]/).pop() || plan.item.path,
                    mtime: Number(plan.item.mtime_ns) / 1_000_000_000,
                    mtime_ns: plan.item.mtime_ns,
                    size: plan.item.size,
                    source_device: plan.item.device,
                    source_inode: plan.item.inode,
                    source_kind: 'project_local',
                    sidecar_capability: 'beside_image',
                });
                if (plan.caption.present) {
                    this.captions.set(plan.nextId, plan.caption.value);
                } else if (typeof plan.item.sidecar_caption === 'string') {
                    this.captions.set(plan.nextId, plan.item.sidecar_caption);
                }
                if (plan.captionEdit.present) {
                    this.captionEdits.set(plan.nextId, plan.captionEdit.value);
                }
                if (plan.nlCaption.present) this.nlCaptions.set(plan.nextId, plan.nlCaption.value);
                if (plan.nlEdit.present) this.nlEdits.set(plan.nextId, plan.nlEdit.value);
                if (plan.captionType.present) this.captionType.set(plan.nextId, plan.captionType.value);
                if (plan.undoStack.present) this._undoStacks.set(plan.nextId, plan.undoStack.value);
                if (plan.selected) this._queueSelection.add(plan.nextId);
            }
            const idByPosition = new Map(plans.map((plan) => [plan.item.position, plan.nextId]));
            this.imageIds = this.imageIds.map((id, position) => idByPosition.get(position) ?? id);
            const activePlan = plans.find((plan) => plan.currentId === Number(this.activeId));
            if (activePlan) this.activeId = activePlan.nextId;
            const clickedPlan = plans.find((plan) => plan.currentId === Number(this._lastClickedId));
            if (clickedPlan) this._lastClickedId = clickedPlan.nextId;
            this.localManifestTokens.clear();
            this._folderScanToken = null;
            this._folderScanNextOffset = 0;
            this._folderScanHasMore = false;
            this._folderScanTotal = 0;
            this._folderScanPreviewed = 0;
            this._setFolderLoadMoreState?.(false);
        } finally {
            this._restoringSession = false;
        }
    };

    if (DM._pendingLocalSession) {
        DM._restoreLocalSession(DM._pendingLocalSession);
        DM._pendingLocalSession = null;
    }

    // -------- Queue + editor patches: render local thumbs lazily --------

    // Queue-item decoration for local ids (FE-1 2b: formerly a
    // _buildQueueItem wrapper; now a decorator on part2's registry).
    DM._queueItemDecorators.push(function (node, id) {
        if (!this.isLocalId(id)) return;
        // Replace the ``/api/image-thumbnail/{id}`` request (which would 404
        // for negative ids) with either the inline scan thumb or the lazy
        // path-thumbnail endpoint.
        const meta = this.meta.get(id) || {};
        const img = node.querySelector('img.dataset-queue-thumb');
        if (img) {
            const src = this._thumbSrc(id, 160);
            if (src) {
                img.src = src;
                img.classList.remove('is-preview-pending');
                node.classList.remove('preview-pending');
            } else {
                img.removeAttribute('src');
                img.classList.add('is-preview-pending');
                node.classList.add('preview-pending');
            }
        }
        // Tag the item visually so the user can tell local vs gallery
        // apart at a glance.
        node.classList.add('source-local');
        const idLabel = node.querySelector('.dataset-queue-id');
        if (idLabel) idLabel.textContent = (meta.filename || '').slice(-40);
    });

    // Local-source _setActive branch (FE-1 2b: formerly a DM._setActive
    // wrapper that intercepted negative ids). part2's single _setActive
    // dispatches here for local ids, then runs DM._activeChangedHooks —
    // so this branch, like the old wrapper, skips the gallery-only side
    // effects (pending-edit flush, split-view refresh, caption diff).
    DM._setActiveLocal = function (id) {
        // Local-source path: render the same lazy thumbnail path used by
        // queue/import/export previews. No DB row is required.
        if (!this.imageIds.includes(id)) return;
        this.activeId = id;
        const meta = this.meta.get(id) || {};
        const filename = meta.filename || `(local image)`;

        const img = document.getElementById('dataset-editor-image');
        const empty = document.getElementById('dataset-editor-empty');
        const ta = document.getElementById('dataset-editor-textarea');
        const actions = document.getElementById('dataset-editor-actions');
        const filenameEl = document.getElementById('dataset-editor-filename');
        const zoomBar = document.getElementById('dataset-zoom-toolbar');

        if (img) {
            const absPath = meta.abs_path || this.localItemPaths?.get?.(id) || '';
            const src = absPath ? localThumbnailUrl(absPath, 2048) : this._thumbSrc(id, 1024);
            if (src) img.src = src;
            else img.removeAttribute('src');
            img.alt = filename;
            img.hidden = !src;
            img.onerror = () => {
                img.removeAttribute('src');
                img.hidden = true;
                if (empty) empty.hidden = false;
            };
        }
        if (empty) {
            const hasPreview = !!this._thumbSrc(id, 256);
            empty.hidden = hasPreview;
            const text = empty.querySelector('.dataset-editor-empty-text');
            if (text && !hasPreview) {
                text.textContent = this._t('dataset.previewPending',
                    'Preview not loaded yet. Use "Load more previews" in Step 1 to hydrate this folder batch.');
            }
        }
        if (filenameEl) filenameEl.textContent = `${filename}`;
        if (zoomBar) zoomBar.hidden = false;
        this._zoomLevel = 1;
        this._applyZoom?.();

        const caption = this.captionEdits.has(id)
            ? this.captionEdits.get(id)
            : (this.captions.get(id) || '');
        if (ta) {
            ta.value = caption;
            ta.hidden = false;
        }
        if (actions) actions.hidden = false;

        this._highlightActiveQueueItem();
        this._scrollActiveQueueItemIntoView?.();
        this._renderTagPills?.();
    };

    // -------- Caption edits: persist local-source edits to localStorage --------

    // The textarea input handler in dataset-maker.js writes to
    // ``captionEdits.set(id, ta.value)``. We monkey-patch ``set`` so any
    // local-source entry also lands in localStorage. Patching the
    // CaptionEdits Map via a property hook keeps the existing call sites
    // (revert, refresh, render) untouched.
    //
    // NOTE: dataset-maker.js already patches captionEdits.set/.delete in
    // ``_installCaptionEditPersistence`` to schedule a session save. That
    // patch wraps the ORIGINAL Map methods. To compose correctly we must
    // patch the CURRENT (already-patched) ``set``/``delete`` here —
    // calling ``.bind(DM.captionEdits)`` captures the live method, so
    // when our wrapper calls ``original_captionEdits_set(...)`` it runs
    // the session-save patch, which in turn runs the real Map.set. Both
    // side effects (session save + localStorage persist) fire in order.
    const original_captionEdits_set = DM.captionEdits.set.bind(DM.captionEdits);
    DM.captionEdits.set = function (id, val) {
        if (!DM._restoringSession && usesUnscopedLocalCaptionCache(DM) && DM.isLocalId(id)) {
            const absPath = DM.localItemPaths.get(Number(id));
            if (absPath) DM._saveLocalCaption(absPath, val);
        }
        return original_captionEdits_set(id, val);
    };
    const original_captionEdits_delete = DM.captionEdits.delete.bind(DM.captionEdits);
    DM.captionEdits.delete = function (id) {
        if (!DM._restoringSession && usesUnscopedLocalCaptionCache(DM) && DM.isLocalId(id)) {
            const absPath = DM.localItemPaths.get(Number(id));
            if (absPath) DM._clearLocalCaption(absPath);
        }
        return original_captionEdits_delete(id);
    };
    DM._deleteCaptionEditForDatasetRemoval = function (id) {
        return original_captionEdits_delete(id);
    };

    // -------- Removing items: clean up local maps --------

    const original_removeImageById = DM._removeImageById;
    DM._removeImageById = function (imageId, options = {}) {
        const id = Number(imageId);
        if (this.isLocalId(id)) this._markLocalManifestExcluded(id);
        return original_removeImageById.call(this, imageId, options);
    };

    const original_removeActive = DM._removeActive;
    DM._removeActive = function () {
        const id = Number(this.activeId);
        const wasLocal = this.isLocalId(id);
        if (wasLocal) this._markLocalManifestExcluded(id);
        original_removeActive.call(this);
        if (wasLocal) {
            this.localItemPaths.delete(id);
            this.localItemDsIds.delete(id);
            // Removing from the current dataset must not erase saved
            // path-keyed captions; re-importing the same folder should
            // restore the user's edits.
        }
    };

    DM._clearLocalDatasetState = function () {
        // Keep localStorage captions so re-importing the same folder restores
        // edits instead of silently losing work.
        this.localItemPaths.clear();
        this.localItemDsIds.clear();
        this.localManifestTokens.clear();
        this._scheduleSaveSession?.();
    };

    // -------- Export: split into image_ids + image_paths + path overrides --------
    //
    // FE-1 2b: this is the SINGLE _buildExportPayload implementation (the
    // former part3 copy was dead code — this one redefined it at load time
    // and is kept here beside the local-source state it reads:
    // isLocalId / localItemPaths / _localIdUsesManifest /
    // _getDatasetScanTokenSources). Gallery-only datasets simply produce
    // empty image_paths / dataset_scan_tokens. The wire-format key set is
    // pinned by tests/e2e/specs/dataset-payload-contract.spec.ts.

    DM._buildExportPayload = function () {
        const folder = document.getElementById('dataset-output-folder')?.value?.trim();
        const pattern = this._effectivePattern();
        const trigger = this._requireDatasetTrigger(
            document.getElementById('dataset-trigger')?.value || '',
            'settings.caption_render.trigger',
        );
        const imageOp = document.getElementById('dataset-image-op')?.value || 'copy';
        const outputMode = this._outputMode?.() || 'folder';
        const overwrite = document.getElementById('dataset-overwrite')?.value || 'unique';
        const normalize = !!document.getElementById('dataset-underscore-to-space')?.checked;
        const contentMode = this._exportContentMode?.() || 'template';
        const prefix = document.getElementById('dataset-export-prefix')?.value || '';
        // Newline OR comma separated — #dataset-blacklist is newline by
        // convention (TraitPruner appends with '\n'); comma-only split dropped
        // trait-pruned entries on this local-import export path too.
        const blacklist = (document.getElementById('dataset-blacklist')?.value || '')
            .split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
        const commonTags = (document.getElementById('dataset-common-tags')?.value || '')
            .split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);

        const galleryIds = [];
        const localPaths = [];
        for (const id of this.imageIds) {
            if (this.isLocalId(id)) {
                if (this._localIdUsesManifest(id)) continue;
                const p = this.localItemPaths.get(Number(id));
                if (p) localPaths.push(p);
            } else {
                galleryIds.push(Number(id));
            }
        }

        // image_overrides accepts both str(image_id) keys (gallery) and
        // absolute path keys (local). Build a single dict.
        const image_overrides = {};
        for (const [id, val] of this.captionEdits.entries()) {
            if (this.isLocalId(id)) {
                const p = this.localItemPaths.get(Number(id));
                if (p) image_overrides[p] = val;
            } else {
                image_overrides[String(id)] = val;
            }
        }
        // point 3: per-image NL type + edited NL text, same dual-key scheme.
        // Only non-default ('nl'/'both') and edited-NL entries are sent.
        const image_types = {};
        const image_nl_overrides = {};
        const _keyFor = (id) => {
            if (this.isLocalId(id)) {
                const p = this.localItemPaths.get(Number(id));
                return p || null;
            }
            return String(id);
        };
        for (const id of this.imageIds) {
            const key = _keyFor(id);
            if (!key) continue;
            const type = this._captionTypeFor ? this._captionTypeFor(id) : 'booru';
            if (type === 'nl' || type === 'both') image_types[key] = type;
            if (this.nlEdits.has(id)) image_nl_overrides[key] = this.nlEdits.get(id);
        }

        if (typeof this._trainerExportFields !== 'function') {
            throw new TypeError('Dataset export requires DatasetMaker._trainerExportFields');
        }
        if (typeof this._subjectCropExportSettings !== 'function') {
            throw new TypeError('Dataset export requires DatasetMaker._subjectCropExportSettings');
        }
        if (typeof this._bucketResizeExportSettings !== 'function') {
            throw new TypeError('Dataset export requires DatasetMaker._bucketResizeExportSettings');
        }
        if (typeof this._watermarkRemovalExportSettings !== 'function') {
            throw new TypeError('Dataset export requires DatasetMaker._watermarkRemovalExportSettings');
        }
        const trainerFields = this._trainerExportFields();

        return {
            image_ids: galleryIds,
            image_paths: localPaths,
            dataset_scan_tokens: this._getDatasetScanTokenSources(),
            output_folder: outputMode === 'beside_image' ? '' : folder,
            output_mode: outputMode,
            naming_pattern: pattern,
            trigger,
            image_op: outputMode === 'beside_image' ? 'copy' : imageOp,
            overwrite_policy: overwrite,
            content_mode: contentMode,
            prefix,
            template_options: contentMode === 'template' ? this._datasetTemplateOptions?.() : null,
            caption_transforms: this._captionTransforms?.() || {},
            normalize_tag_underscores: normalize,
            blacklist,
            common_tags: commonTags,
            image_overrides,
            image_types,
            image_nl_overrides,
            subject_crop: this._subjectCropExportSettings(),
            bucket_resize: this._bucketResizeExportSettings(),
            watermark_removal: this._watermarkRemovalExportSettings(),
            ...trainerFields,
        };
    };

    const original_updateCount = DM._updateCount;
    DM._updateCount = function () {
        original_updateCount.call(this);
        const logical = this._getLogicalDatasetCount ? this._getLogicalDatasetCount() : this.imageIds.length;
        const num = document.getElementById('dataset-count-num');
        if (num) num.textContent = String(logical);
        const importCount = document.getElementById('dataset-import-gallery-count');
        if (importCount && logical !== this.imageIds.length) {
            importCount.textContent = this._t('dataset.importGalleryManifestCount',
                '{loaded} previews loaded / {count} images in dataset',
                { loaded: this.imageIds.length, count: logical });
        }
    };

    // v3.4.5: the previous implementation probed the base readiness check
    // by temporarily setting ``this.imageIds = [1]`` (a magic placeholder)
    // so the base method's "non-empty dataset" guard would pass for a
    // local-only dataset, then restored the real list in ``finally``.
    // That mutated shared state across the call and coupled this patch to
    // the base method's internal use of ``imageIds.length``. We now read
    // the same readiness signals the base method reads — output folder,
    // disabled-reason, and a non-empty logical count — without the swap.
    const original_isReadyToExport = DM._isReadyToExport;
    DM._isReadyToExport = function () {
        const logical = this._getLogicalDatasetCount ? this._getLogicalDatasetCount() : this.imageIds.length;
        if (logical <= 0) return false;
        if (this._outputMode?.() === 'beside_image') {
            return !this._exportDisabledReason?.();
        }
        // Folder mode: the base check gates on (a) a non-empty dataset
        // and (b) no disabled-reason. We've already established (a) via
        // ``logical > 0`` above, so we only need (b). Calling the base
        // method with the real (possibly local-only) list works because
        // the base method's only use of imageIds beyond the emptiness
        // guard is the disabled-reason computation, which is
        // source-agnostic. No probe-swap needed.
        return !this._exportDisabledReason?.();
    };
})();
