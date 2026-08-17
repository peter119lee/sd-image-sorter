/**
 * @fileoverview User-friendly error message utilities
 * @module utils/errors
 */

/**
 * Map technical error messages to user-friendly messages
 * @constant {Object.<string, string>}
 */
function isZhCn() {
    return window.I18n?.getLang?.() === 'zh-CN';
}

function localizeErrorText(enText, zhText) {
    return isZhCn() ? zhText : enText;
}

function getErrorMessageMap() {
    return {
        // Network errors
        'Failed to fetch': localizeErrorText('Unable to connect to server. Please check if the server is running.', '无法连接到服务器。请检查软件是否已经启动。'),
        'NetworkError': localizeErrorText('Network connection error. Please check your internet connection.', '网络连接异常。请检查当前网络。'),
        'Network request failed': localizeErrorText('Network request failed. Please try again.', '网络请求失败，请重试。'),

        // Server errors
        'Internal Server Error': localizeErrorText('Server encountered an error. Please try again later.', '服务器发生错误，请稍后重试。'),
        'Service Unavailable': localizeErrorText('Service temporarily unavailable. Please wait and try again.', '服务暂时不可用，请稍后再试。'),
        'Bad Gateway': localizeErrorText('Server is temporarily unavailable. Please try again.', '服务器暂时不可用，请重试。'),
        'Gateway Timeout': localizeErrorText('Server response timed out. Please try again.', '服务器响应超时，请重试。'),

        // Client errors
        'Unauthorized': localizeErrorText('Authentication required. Please refresh the page.', '需要重新验证，请刷新页面。'),
        'Forbidden': localizeErrorText('Access denied. You do not have permission for this action.', '访问被拒绝，你没有执行此操作的权限。'),
        'Not Found': localizeErrorText('The requested resource was not found.', '找不到请求的资源。'),
        'Bad Request': localizeErrorText('Invalid request. Please check your input.', '请求无效，请检查输入内容。'),

        // File operations
        'ENOENT': localizeErrorText('File or folder not found.', '找不到文件或文件夹。'),
        'EACCES': localizeErrorText('Permission denied. Please check folder permissions.', '权限不足，请检查文件夹权限。'),
        'EPERM': localizeErrorText('Operation not permitted. Please check permissions.', '当前操作不被允许，请检查权限。'),
        'ENOSPC': localizeErrorText('Not enough disk space for this operation.', '磁盘空间不足，无法完成此操作。'),
        'EMFILE': localizeErrorText('Too many files open. Please close other applications.', '当前打开的文件过多，请先关闭其他程序。'),

        // Image operations
        'Invalid image': localizeErrorText('The image file is invalid or corrupted.', '图片文件无效或已损坏。'),
        'Image too large': localizeErrorText('The image is too large to process.', '图片过大，暂时无法处理。'),
        'Unsupported format': localizeErrorText('This image format is not supported.', '当前图片格式不受支持。'),

        // Tagging/AI operations
        'Model not loaded': localizeErrorText('AI model is not loaded. Please wait for initialization.', 'AI 模型还没加载完成，请稍等。'),
        'Model loading': localizeErrorText('AI model is loading. Please wait.', 'AI 模型正在加载，请稍等。'),
        'CUDA out of memory': localizeErrorText('Not enough GPU memory. Try closing other applications.', 'GPU 显存不足，建议先关闭其他程序再试。'),
        'Out of memory': localizeErrorText('Not enough memory. Try closing other applications.', '内存不足，建议先关闭其他程序再试。'),

        // Generic
        'timeout': localizeErrorText('Operation timed out. Please try again.', '操作超时，请重试。'),
        'cancelled': localizeErrorText('Operation was cancelled.', '操作已取消。'),
        'abort': localizeErrorText('Operation was cancelled.', '操作已取消。'),
    };
}

/**
 * Patterns to match error messages and map to user-friendly versions
 * @constant {Array.<{pattern: RegExp, message: string}>}
 */
function getErrorPatterns() {
    return [
        { pattern: /Failed to fetch/i, message: localizeErrorText('Unable to connect to server. Please check if the server is running.', '无法连接到服务器。请检查软件是否已经启动。') },
        { pattern: /NetworkError/i, message: localizeErrorText('Network connection error. Please check your connection.', '网络连接异常。请检查当前网络。') },
        { pattern: /ENOENT.*no such file/i, message: localizeErrorText('File or folder not found.', '找不到文件或文件夹。') },
        { pattern: /EACCES|EPERM/i, message: localizeErrorText('Permission denied. Please check folder permissions.', '权限不足，请检查文件夹权限。') },
        { pattern: /ENOSPC/i, message: localizeErrorText('Not enough disk space for this operation.', '磁盘空间不足，无法完成此操作。') },
        { pattern: /CUDA.*memory|out of memory/i, message: localizeErrorText('Not enough memory. Try closing other applications.', '内存不足，建议先关闭其他程序再试。') },
        { pattern: /timeout/i, message: localizeErrorText('Operation timed out. Please try again.', '操作超时，请重试。') },
        { pattern: /abort|cancelled/i, message: localizeErrorText('Operation was cancelled.', '操作已取消。') },
        { pattern: /source file is missing on disk/i, message: localizeErrorText('The original image file is no longer there. Use Find Moved Images if you moved it, or reconnect the drive/folder.', '原始图片文件已经不在原位置。如果你移动过图片，请用“找回图片”；如果在外置盘，请先重新连接磁盘。') },
        { pattern: /library entry does not contain a source image path/i, message: localizeErrorText('This gallery item has no usable original-file path. Scan the folder again to rebuild it.', '这个图库项目没有可用的原图路径。请重新扫描文件夹来重建它。') },
        { pattern: /invalid filename characters|invalid.*filename/i, message: localizeErrorText('The path contains characters Windows cannot use in a file or folder name. Remove characters like < > : " / \ | ? * and try again.', '路径里有 Windows 不允许用于文件名的字符。请删除 < > : " / \ | ? * 这类字符后再试。') },
        { pattern: /invalid.*path/i, message: localizeErrorText('This path cannot be used. Check that the folder exists and the name does not contain invalid characters.', '这个路径不能用。请确认文件夹存在，并且名称里没有非法字符。') },
        { pattern: /path.*not.*exist|folder.*not.*exist/i, message: localizeErrorText('That folder does not exist. Choose an existing folder, or create it first.', '这个文件夹不存在。请选择已有文件夹，或先创建它。') },
        { pattern: /trash|recycle bin|send2trash/i, message: localizeErrorText('The file was not moved to Trash. It may be on a drive or system where Trash is unavailable, so the app did not permanently delete it.', '没有成功移到回收站。这个磁盘或系统可能不支持回收站，所以软件没有永久删除文件。') },
        { pattern: /No module named ['"]torch|torch/i, message: localizeErrorText('The AI runtime is not ready. Open Model setup from the start page, then run the dependency/model check again.', 'AI 运行环境还没准备好。请从首页左下角打开模型管理，完成依赖/模型检查后再试。') },
        { pattern: /WD14|onnxruntime|ONNX/i, message: localizeErrorText('WD14 tagging is not ready yet. Open Model setup and prepare the tagger model/runtime.', 'WD14 自动打标还没准备好。请打开模型管理，准备打标模型/运行库后再试。') },
        { pattern: /connection.*refused/i, message: localizeErrorText('Cannot connect to server. Please ensure the server is running.', '无法连接到服务器。请确认软件已经启动。') },
    ];
}

/**
 * Convert technical error to user-friendly message
 * @param {Error|string} error - The error object or message
 * @param {string} [context] - Context for the error (e.g., 'loading images')
 * @returns {string} User-friendly error message
 */
function formatUserError(error, context = '') {
    // Get error message string
    const errorMsg = error instanceof Error ? error.message : String(error);
    const errorMessageMap = getErrorMessageMap();
    const errorPatterns = getErrorPatterns();

    // Prefix the caller's context, but drop it when the resolved message already
    // equals the context label — otherwise a backend "Detection failed" under a
    // "Detection failed" context read as "Detection failed: Detection failed".
    const withContext = (msg) => {
        if (!context) return msg;
        if (String(msg).trim().toLowerCase() === String(context).trim().toLowerCase()) return String(msg);
        return `${context}${isZhCn() ? '：' : ': '}${msg}`;
    };

    // A busy AI runtime is answered from its own structured 409 payload, before
    // the pattern list can reach it. Those patterns match on model NAMES, so
    // "WD14 tagging is using the AI runtime" hit the /WD14|ONNX/ rule and came
    // out as "WD14 tagging is not ready yet - open Model setup": false, since
    // the model is loaded and working, and pointing at the wrong screen.
    if (window.AiBusy?.isBusyError?.(error)) {
        const explained = window.AiBusy.explain(error.apiData);
        if (explained) return withContext(explained);
    }

    // Check for exact match first
    if (errorMessageMap[errorMsg]) {
        return withContext(errorMessageMap[errorMsg]);
    }

    // Check patterns
    for (const { pattern, message } of errorPatterns) {
        if (pattern.test(errorMsg)) {
            return withContext(message);
        }
    }

    // Preserve short, human-readable backend messages instead of collapsing them
    const hasTechnicalJargon = /(?:\b(?:ENOENT|EACCES|EPERM|ENOSPC|CUDA|undefined|null|TypeError|ReferenceError|SyntaxError)\b|https?:\/\/|\/api\/|[A-Za-z]:\\| at .+\(|stack trace)/i.test(errorMsg);

    if (!hasTechnicalJargon && errorMsg.length < 180) {
        return withContext(errorMsg);
    }
    
    // Return generic message with context
    if (context) {
        const normalizedContext = String(context).trim();
        if (isZhCn()) {
            return `${normalizedContext}。请重试。`;
        }
        const prefix = /^failed to\b/i.test(normalizedContext)
            ? normalizedContext
            : `Failed to ${normalizedContext}`;
        return `${prefix}. Please try again.`;
    }

    return localizeErrorText('An unexpected error occurred. Please try again.', '发生了未预期的错误，请重试。');
}

/**
 * Check if error is a cancellation/abort error
 * @param {Error} error - Error to check
 * @returns {boolean} True if error is cancellation
 */
function isCancellationError(error) {
    if (!error) return false;
    const name = error.name || '';
    const message = error.message || '';
    return name === 'AbortError' || 
           name === 'CancellationError' ||
           message.toLowerCase().includes('abort') ||
           message.toLowerCase().includes('cancel');
}

/**
 * Show user-friendly error toast
 * @param {Error|string} error - The error
 * @param {string} [context] - Context for the error
 * @param {Function} [showToast] - Toast function to use (defaults to window.showToast)
 */
function showUserError(error, context = '', showToast = null) {
    // Don't show toast for cancellation errors
    if (isCancellationError(error)) {
        return;
    }
    
    const message = formatUserError(error, context);
    const toastFn = showToast || window.showToast;
    
    if (typeof toastFn === 'function') {
        toastFn(message, 'error');
    } else {
        console.error('[Error]', context, error);
    }
}

// Export to global namespace
if (typeof window !== 'undefined') {
    window.formatUserError = formatUserError;
    window.showUserError = showUserError;
    window.isCancellationError = isCancellationError;
    window.ERROR_MESSAGE_MAP = getErrorMessageMap();
    window.ERROR_PATTERNS = getErrorPatterns();
    window.errorUtils = {
        formatUserError,
        showUserError,
        isCancellationError,
        getErrorMessageMap,
        getErrorPatterns,
    };

    document.addEventListener('languageChanged', () => {
        window.ERROR_MESSAGE_MAP = getErrorMessageMap();
        window.ERROR_PATTERNS = getErrorPatterns();
    });
}
