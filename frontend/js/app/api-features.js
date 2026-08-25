/**
 * app/api-features.js — app.js decomposition, stage 3 (API object, part 2).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, pre-split
 * lines 767-1288: libraries, stats, models, updates, scan, tagging, move,
 * collections, sorting and export endpoints. ONLY the first and last code
 * lines below are new (the documented seam): they merge these members onto
 * the object opened in app/api.js. Loads between app/api.js and app.js.
 */
Object.assign(API, {

    // Tags & Generators
    async getTags() {
        return this.get('/api/tags');
    },

    async getTagsLibrary(sortBy = 'frequency', options = {}) {
        const requestOptions = typeof options === 'number' ? { limit: options } : (options || {});
        const params = new URLSearchParams();
        params.set('sort_by', sortBy);
        if (requestOptions.limit != null) params.set('limit', String(requestOptions.limit));
        if (requestOptions.query) params.set('q', requestOptions.query);
        return this.get(`/api/tags/library?${params.toString()}`);
    },

    async importTags(images, overwrite = false) {
        return this.post('/api/tags/import', { images, overwrite });
    },

    async getPromptsLibrary(options = {}) {
        const requestOptions = typeof options === 'number' ? { limit: options } : (options || {});
        const params = new URLSearchParams();
        if (requestOptions.limit != null) params.set('limit', String(requestOptions.limit));
        if (requestOptions.query) params.set('q', requestOptions.query);
        const queryString = params.toString();
        return this.get(`/api/prompts/library${queryString ? `?${queryString}` : ''}`);
    },

    async getLorasLibrary(options = {}) {
        const requestOptions = typeof options === 'number' ? { limit: options } : (options || {});
        const params = new URLSearchParams();
        if (requestOptions.limit != null) params.set('limit', String(requestOptions.limit));
        if (requestOptions.query) params.set('q', requestOptions.query);
        const queryString = params.toString();
        return this.get(`/api/loras/library${queryString ? `?${queryString}` : ''}`);
    },

    // v3.3.0 FEAT-CHECKPOINT-TAB
    async getCheckpointsLibrary(options = {}) {
        const requestOptions = typeof options === 'number' ? { limit: options } : (options || {});
        const params = new URLSearchParams();
        if (requestOptions.limit != null) params.set('limit', String(requestOptions.limit));
        if (requestOptions.query) params.set('q', requestOptions.query);
        const queryString = params.toString();
        return this.get(`/api/checkpoints/library${queryString ? `?${queryString}` : ''}`);
    },

    async getGenerators() {
        return this.get('/api/generators');
    },

    // Stats
    async getStats() {
        return this.get('/api/stats');
    },

    async getAnalyticsFacet(facet, options = {}) {
        const params = new URLSearchParams();
        if (facet) params.set('facet', facet);
        if (options.query) params.set('q', options.query);
        if (options.limit != null) params.set('limit', String(options.limit));
        const queryString = params.toString();
        return this.get(`/api/analytics${queryString ? `?${queryString}` : ''}`);
    },

    async getAestheticStatus() {
        return this.get('/api/aesthetic/status');
    },

    async startAestheticScoring(force = false) {
        return this.post(`/api/aesthetic/score-all?force=${force ? 'true' : 'false'}`);
    },

    async getAestheticProgress() {
        return this.get('/api/aesthetic/progress');
    },

    async cancelAesthetic() {
        return this.post('/api/aesthetic/cancel');
    },

    async cancelSimilarityEmbed() {
        return this.post('/api/similarity/cancel');
    },

    async cancelArtistBatch() {
        return this.post('/api/artists/batch-cancel');
    },

    async getModelStatus() {
        return this.get('/api/models/status');
    },

    async getCacheStatus() {
        return this.get('/api/disk/cache-status');
    },

    async cleanCaches(keys) {
        return this.post('/api/disk/cleanup', { keys });
    },

    async setDiskSettings(settings) {
        return this.post('/api/disk/settings', settings);
    },

    async rebuildCoreRuntime() {
        return this.post('/api/disk/runtime/rebuild-core', {});
    },

    async getMirror() {
        return this.get('/api/models/mirror');
    },

    async setMirror(mirror) {
        return this.post('/api/models/mirror', { mirror });
    },

    async prepareModel(modelId, options = {}) {
        return this.post('/api/models/prepare', {
            model_id: modelId,
            source: options.source || null,
            variant: options.variant || null,
        });
    },

    async getModelBulkBundle() {
        return this.get('/api/models/bulk-bundle');
    },

    async getUpdateStatus(force = false) {
        return this.get(`/api/updates/status?force=${force ? 'true' : 'false'}`);
    },

    async getUpdateChannel() {
        return this.get('/api/updates/channel');
    },

    async saveUpdateProxy(proxyPrefix, channelName = 'Custom Proxy') {
        return this.post('/api/updates/channel/proxy', {
            proxy_prefix: proxyPrefix,
            channel_name: channelName,
        });
    },

    async resetUpdateChannel() {
        return this.delete('/api/updates/channel');
    },

    async applyUpdate(options = {}) {
        return this.post('/api/updates/apply', {
            force_check: options.forceCheck ?? true,
            relaunch: options.relaunch ?? true,
        });
    },

    async restartApp(options = {}) {
        return this.post('/api/updates/restart', {
            reason: options.reason || '',
        });
    },

    // Drop resolution
    async resolveDrop(folderName, droppedFiles) {
        return this.post('/api/resolve-drop', { folder_name: folderName, files: droppedFiles });
    },

    async importFiles(fileList) {
        const form = new FormData();
        for (let i = 0; i < fileList.length; i++) {
            form.append('files', fileList[i]);
        }
        const response = await (window.apiFetch || fetch)(`${API_BASE}/api/import-files`, {
            method: 'POST',
            body: form,
            headers: (window.libraryFetchHeaders || ((h) => h))({}),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || 'Import failed');
        }
        return response.json();
    },

    // Scan
    async startScan(folderPath, options = {}) {
        return this.post('/api/scan', {
            folder_path: folderPath,
            recursive: options.recursive ?? true,
            quick_import: options.quickImport ?? true,
            force_reparse: options.forceReparse ?? false,
            cleanup_missing: options.cleanupMissing ?? false,
        });
    },

    async getScanProgress() {
        return this.get('/api/scan/progress');
    },

    async getSupportDiagnostics(lines = 200) {
        return this.get(`/api/support/diagnostics?lines=${encodeURIComponent(lines)}`);
    },

    async openSupportLog() {
        return this.post('/api/support/open-log', {});
    },

    async cancelScan(identity) {
        return this.post('/api/scan/cancel', {
            run_id: identity.runId,
            source: identity.source,
        });
    },

    async startReconnectMissing(folderPath, options = {}) {
        return this.post('/api/images/reconnect-missing/start', {
            search_folder: folderPath,
            recursive: options.recursive ?? true,
            verify_uncertain: options.verifyUncertain ?? true,
        });
    },

    async getReconnectProgress() {
        return this.get('/api/images/reconnect-missing/progress');
    },

    async cancelReconnectMissing() {
        return this.post('/api/images/reconnect-missing/cancel');
    },

    // Tagging - with all new options
    async startTagging(options = {}) {
        return this.post('/api/tag/start', { // Unified with backend endpoint
            threshold: options.threshold || 0.35,
            character_threshold: options.characterThreshold || 0.85,
            model_name: options.modelName || null,
            model_path: options.modelPath || null,
            tags_path: options.tagsPath || null,
            custom_profile: options.customProfile || null,
            image_ids: options.imageIds || null,
            retag_all: options.retagAll || false,
            use_gpu: options.useGpu ?? true,
            allow_unsafe_acceleration: options.allowUnsafeAcceleration ?? false,
            batch_size: options.batchSize || null,
            // v3.2.2 T-power-PR1: pre-tag filters applied inside the worker.
            pre_tag_blacklist: Array.isArray(options.preTagBlacklist) ? options.preTagBlacklist : [],
            max_tags_per_image: Number.isFinite(options.maxTagsPerImage) ? Math.max(0, options.maxTagsPerImage) : 0,
        });
    },

    async getTagProgress() {
        return this.get('/api/tag/progress');
    },

    async cancelTagging() {
        return this.post('/api/tag/cancel');
    },

    async exportAllTags() {
        return this.get('/api/tags/export');
    },

    async getTaggerModels() {
        return this.get('/api/tagger/models');
    },

    // Move
    async moveImages(imageIds, destinationFolder, operation = 'move', options = {}) {
        const payload = { destination_folder: destinationFolder, operation };
        if (options.selectionToken) {
            payload.selection_token = options.selectionToken;
        } else {
            payload.image_ids = imageIds;
        }
        return this.post('/api/move', payload);
    },

    // v3.3.0 USR-1: background move/copy job with progress polling.
    async startMoveJob(imageIds, destinationFolder, operation = 'move', options = {}) {
        const payload = { destination_folder: destinationFolder, operation };
        if (options.selectionToken) {
            payload.selection_token = options.selectionToken;
        } else {
            payload.image_ids = imageIds;
        }
        return this.post('/api/move/start', payload);
    },

    async getMoveProgress() {
        return this.get('/api/move/progress');
    },

    async cancelMove() {
        return this.post('/api/move/cancel', {});
    },

    // v3.3.0 FEAT-COLLECTIONS
    async getFavoriteIds() {
        return this.get('/api/collections/favorites/ids');
    },

    async setFavorite(imageId, favorited) {
        return this.post('/api/collections/favorites', { image_id: imageId, favorited });
    },

    // v3.3.3 WIRING-01: set a user star rating (0-5; 0 = unrated). Backend:
    // POST /api/images/{id}/rating -> {image_id, user_rating, updated}.
    async setRating(imageId, stars) {
        return this.post(`/api/images/${imageId}/rating`, { stars });
    },

    async listCollections() {
        return this.get('/api/collections');
    },

    // v3.3.2 Library Navigation: distinct directories that contain indexed images.
    async listLibraryFolders() {
        return this.get('/api/folders');
    },

    async createCollection(name, folderPath = null) {
        return this.post('/api/collections', { name, folder_path: folderPath });
    },

    async renameCollection(collectionId, name) {
        return this.patch(`/api/collections/${collectionId}`, { name });
    },

    async deleteCollection(collectionId) {
        return this.delete(`/api/collections/${collectionId}`);
    },

    async getCollectionImageIds(collectionId) {
        return this.get(`/api/collections/${collectionId}/images`);
    },

    async setCollectionMembership(collectionId, imageId, member) {
        return this.post(`/api/collections/${collectionId}/items`, { image_id: imageId, member });
    },

    async setCollectionMembershipBulk(collectionId, { imageIds = [], selectionToken = null, member = true } = {}) {
        return this.post(`/api/collections/${collectionId}/items/bulk`, {
            image_ids: selectionToken ? [] : imageIds,
            selection_token: selectionToken,
            member,
        });
    },

    async batchMove(generators, tags, ratings, destinationFolder, checkpoints = null, loras = null, prompts = null, dimensions = null, search = null, aesthetic = null, operation = 'move', artist = null, promptMatchMode = 'exact', tagMode = 'and', excludeFilters = null, scopeFilters = null, splitBy = null) {
        return this.post('/api/batch-move', {
            generators,
            tags,
            tag_mode: tagMode === 'or' ? 'or' : 'and',
            ratings,
            checkpoints,
            loras,
            prompts,
            prompt_match_mode: normalizePromptMatchMode(promptMatchMode),
            artist: artist ? String(artist).trim() : null,
            search,
            min_width: dimensions?.minWidth || null,
            max_width: dimensions?.maxWidth || null,
            min_height: dimensions?.minHeight || null,
            max_height: dimensions?.maxHeight || null,
            aspect_ratio: normalizeAspectRatioFilter(dimensions?.aspectRatio) || null,
            min_aesthetic: aesthetic?.min ?? null,
            max_aesthetic: aesthetic?.max ?? null,
            exclude_tags: excludeFilters?.tags || null,
            exclude_generators: excludeFilters?.generators || null,
            exclude_ratings: excludeFilters?.ratings || null,
            exclude_checkpoints: excludeFilters?.checkpoints || null,
            exclude_loras: excludeFilters?.loras || null,
            // v3.3.x gallery-scope parity: without these, a collection/folder/
            // star-rating/exclude-scoped gallery copied into Auto-Separate moved
            // a WIDER set than displayed. `|| null` keeps 0/'' off the wire
            // (0-star and empty scopes mean "no restriction", matching getImages).
            exclude_prompts: scopeFilters?.excludePrompts?.length ? scopeFilters.excludePrompts : null,
            exclude_colors: scopeFilters?.excludeColors?.length ? scopeFilters.excludeColors : null,
            min_user_rating: scopeFilters?.minUserRating || null,
            brightness_min: scopeFilters?.brightnessMin ?? null,
            brightness_max: scopeFilters?.brightnessMax ?? null,
            color_temperature: scopeFilters?.colorTemperature || null,
            brightness_distribution: scopeFilters?.brightnessDistribution || null,
            collection_id: scopeFilters?.collectionId || null,
            scope: scopeFilters?.scope || 'library',
            folder: scopeFilters?.folder || null,
            has_metadata: typeof scopeFilters?.hasMetadata === 'boolean' ? scopeFilters.hasMetadata : null,
            destination_folder: destinationFolder,
            operation,
            // B3-②: optional subfolder split under destination (generator/checkpoint/rating).
            split_by: splitBy || null,
        });
    },

    // Manual Sort
    async startSortSession(generators, tags, ratings, folders, checkpoints = null, loras = null, prompts = null, dimensions = null, search = null, aesthetic = null, operationMode = 'copy', artist = null, replaceExisting = false, promptMatchMode = 'exact', tagMode = 'and', excludeFilters = null, collectionSlots = null, mode = 'slot', scopeFilters = null) {
        return this.post('/api/sort/start', {
            generators,
            tags,
            tag_mode: tagMode === 'or' ? 'or' : 'and',
            ratings,
            checkpoints,
            loras,
            prompts,
            prompt_match_mode: normalizePromptMatchMode(promptMatchMode),
            artist: artist ? String(artist).trim() : null,
            search,
            min_width: dimensions?.minWidth || null,
            max_width: dimensions?.maxWidth || null,
            min_height: dimensions?.minHeight || null,
            max_height: dimensions?.maxHeight || null,
            aspect_ratio: normalizeAspectRatioFilter(dimensions?.aspectRatio) || null,
            min_aesthetic: aesthetic?.min ?? null,
            max_aesthetic: aesthetic?.max ?? null,
            folders,
            // Default to copy (non-destructive) when no explicit mode is
            // passed. Locked by Principle #11 in docs/AI_PRINCIPLES.md.
            operation_mode: operationMode || 'copy',
            replace_existing: Boolean(replaceExisting),
            exclude_tags: excludeFilters?.tags || null,
            exclude_generators: excludeFilters?.generators || null,
            exclude_ratings: excludeFilters?.ratings || null,
            exclude_checkpoints: excludeFilters?.checkpoints || null,
            exclude_loras: excludeFilters?.loras || null,
            // v3.3.x gallery-scope parity: manual sort sessions started "from
            // gallery filters" must honor collection/folder/star-rating/exclude
            // scopes or the WASD queue is WIDER than what the gallery showed.
            exclude_prompts: scopeFilters?.excludePrompts?.length ? scopeFilters.excludePrompts : null,
            exclude_colors: scopeFilters?.excludeColors?.length ? scopeFilters.excludeColors : null,
            min_user_rating: scopeFilters?.minUserRating || null,
            brightness_min: scopeFilters?.brightnessMin ?? null,
            brightness_max: scopeFilters?.brightnessMax ?? null,
            color_temperature: scopeFilters?.colorTemperature || null,
            brightness_distribution: scopeFilters?.brightnessDistribution || null,
            collection_id: scopeFilters?.collectionId || null,
            scope: scopeFilters?.scope || 'library',
            folder: scopeFilters?.folder || null,
            has_metadata: typeof scopeFilters?.hasMetadata === 'boolean' ? scopeFilters.hasMetadata : null,
            // v3.3.1: per-slot collection ids ({ key: collectionId|null }).
            collection_slots: (collectionSlots && typeof collectionSlots === 'object') ? collectionSlots : null,
            // v3.3.2 WB-S3: session mode. "slot" = WASD folder sort (default);
            // "bracket" = A/B king-of-the-hill culling; "cull" = 留/汰 keep-reject (FF-1).
            mode: ['bracket', 'cull'].includes(mode) ? mode : 'slot',
        });
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

    async setSortFolders(folders, collectionSlots = null) {
        // v3.3.1: collectionSlots ({ key: collectionId|null }) lets a WASD slot
        // target a collection by reference instead of a destination folder.
        const payload = { folders };
        if (collectionSlots && typeof collectionSlots === 'object') {
            payload.collection_slots = collectionSlots;
        }
        return this.post('/api/sort/set-folders', payload);
    },

    // Batch Sidecar Export
    // Shared payload builder so the synchronous export and the v3.3.2 background
    // export job send byte-identical requests.
    _buildExportBatchPayload(imageIds, outputFolder, blacklist = [], prefix = '', contentMode = 'tags', overwritePolicy = 'unique', options = {}) {
        const payload = {
            output_folder: outputFolder,
            output_mode: options.outputMode || 'folder',
            blacklist: blacklist,
            prefix: prefix,
            content_mode: contentMode,
            overwrite_policy: overwritePolicy
        };
        if (options.templateOptions) {
            payload.template_options = options.templateOptions;
        }
        if (options.imageOverrides && typeof options.imageOverrides === 'object') {
            payload.image_overrides = options.imageOverrides;
        }
        if (options.captionTransforms && typeof options.captionTransforms === 'object') {
            payload.caption_transforms = options.captionTransforms;
        }
        if (options.imageTypes && typeof options.imageTypes === 'object') {
            payload.image_types = options.imageTypes;
        }
        if (options.imageNlOverrides && typeof options.imageNlOverrides === 'object') {
            payload.image_nl_overrides = options.imageNlOverrides;
        }
        if (typeof options.normalizeTagUnderscores === 'boolean') {
            payload.normalize_tag_underscores = options.normalizeTagUnderscores;
        }
        if (options.nlSidecar === true) {
            payload.nl_sidecar = true;
        }
        if (options.trainingPurpose) {
            payload.training_purpose = options.trainingPurpose;
        }
        if (options.dedupeImplications === true) {
            payload.dedupe_implications = true;
        }
        if (options.selectionToken) {
            payload.selection_token = options.selectionToken;
        } else {
            payload.image_ids = imageIds;
        }
        return payload;
    },

    async exportTagsBatch(imageIds, outputFolder, blacklist = [], prefix = '', contentMode = 'tags', overwritePolicy = 'unique', options = {}) {
        return this.post('/api/tags/export-batch', this._buildExportBatchPayload(imageIds, outputFolder, blacklist, prefix, contentMode, overwritePolicy, options));
    },

    // v3.3.2 Phase-1: background batch tag-export job (coarse progress, no
    // mid-run cancel — the underlying export pipeline is monolithic).
    async startExportJob(imageIds, outputFolder, blacklist = [], prefix = '', contentMode = 'tags', overwritePolicy = 'unique', options = {}) {
        return this.post('/api/tags/export-batch/start', this._buildExportBatchPayload(imageIds, outputFolder, blacklist, prefix, contentMode, overwritePolicy, options));
    },

    async getExportProgress() {
        return this.get('/api/tags/export-batch/progress');
    },

    // Debt-22: durable-id background sidecar export (per-image progress + real
    // mid-run cancel via /api/bulk-jobs, unlike the coarse Phase-1 job above).
    async startExportBulkJob(imageIds, outputFolder, blacklist = [], prefix = '', contentMode = 'tags', overwritePolicy = 'unique', options = {}) {
        const payload = this._buildExportBatchPayload(imageIds, outputFolder, blacklist, prefix, contentMode, overwritePolicy, options);
        payload.background = true;
        return this.post('/api/tags/export-batch', payload);
    },

    // Prompts Library — removed duplicate, kept single definition above
});

