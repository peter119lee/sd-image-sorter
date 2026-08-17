/**
 * Client-side image obfuscation engine.
 *
 * Keeps the fast browser-side pixel pipeline while also supporting
 * Big Tomato-compatible PNG text chunk handling in the browser:
 * - Gilbert curve + golden ratio offset pixel scrambling
 * - password digits for step / extra width / extra height
 * - PNG tEXt/iTXt preservation with modern and legacy PNG Info modes
 * - container-aware source harvesting so a JPEG or WebP prompt is carried too,
 *   mirroring extract_source_text_chunks_from_bytes in backend/obfuscation.py
 * - Small Tomato JPEG-on-download compatibility remains in the caller
 */
(function () {
    'use strict';

    const BIG_TOMATO_MODE = 'big_tomato';
    const SMALL_TOMATO_MODE = 'small_tomato';
    const PNG_SIGNATURE = new Uint8Array([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
    const OBFUSCATE_MAX_FILE_BYTES_DEFAULT = 50 * 1024 * 1024;
    const OBFUSCATE_MAX_IMAGE_PIXELS_DEFAULT = 40_000_000;
    // Ceiling on a single harvested EXIF/XMP text value, mirroring
    // _MAX_CARRIED_TEXT_BYTES in backend/obfuscation.py. Real A1111 parameter
    // blocks are a few kB; an XMP packet bigger than this is a hostile or broken
    // file, and carrying it would balloon the protected PNG.
    const MAX_CARRIED_TEXT_BYTES = 1024 * 1024;
    const XMP_APP1_HEADER = 'http://ns.adobe.com/xap/1.0/\u0000';
    const EXIF_APP1_HEADER = 'Exif\u0000\u0000';
    const EXIF_TAG_IMAGE_DESCRIPTION = 0x010E;
    const EXIF_TAG_SUB_IFD_POINTER = 0x8769;
    const EXIF_TAG_USER_COMMENT = 0x9286;
    const EXIF_TYPE_ASCII = 2;
    const EXIF_TYPE_LONG = 4;
    const EXIF_TYPE_SIZES = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8 };
    const textEncoder = new TextEncoder();
    const textDecoder = new TextDecoder();

    function getObfuscateTestFlag(name, fallback) {
        const value = window?.__SD_SORTER_TEST_FLAGS__?.[name];
        const parsed = Number(value);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
    }

    function getObfuscateMaxFileBytes() {
        return getObfuscateTestFlag('obfuscateMaxFileBytes', OBFUSCATE_MAX_FILE_BYTES_DEFAULT);
    }

    function getObfuscateMaxImagePixels() {
        return getObfuscateTestFlag('obfuscateMaxImagePixels', OBFUSCATE_MAX_IMAGE_PIXELS_DEFAULT);
    }

    function tText(key, fallback, params) {
        const v = window.I18n?.t?.(key, params); return (v && v !== key) ? v : fallback;
    }

    function formatMegapixelLimit(pixels) {
        return `${(pixels / 1_000_000).toFixed(1)} MP`;
    }

    function formatMegabyteLimit(bytes) {
        return `${Math.round(bytes / (1024 * 1024))} MB`;
    }

    function normalizeCompatMode(value) {
        return value === SMALL_TOMATO_MODE ? SMALL_TOMATO_MODE : BIG_TOMATO_MODE;
    }

    function parsePassword(raw) {
        if (!raw) return { step: 1, extraWidth: 0, extraHeight: 0 };
        const source = String(raw);
        const stepPart = source.slice(0, 2);
        const extraWidthPart = source[2] || '';
        const extraHeightPart = source[3] || '';
        return {
            step: Math.max(1, parseInt(stepPart, 10) || 1),
            extraWidth: parseInt(extraWidthPart, 10) || 0,
            extraHeight: parseInt(extraHeightPart, 10) || 0,
        };
    }

    function resolvePassword(passwordStr, compatMode) {
        return normalizeCompatMode(compatMode) === SMALL_TOMATO_MODE
            ? { step: 1, extraWidth: 0, extraHeight: 0 }
            : parsePassword(passwordStr);
    }

    function passwordKey(password) {
        return [password.step, password.extraWidth, password.extraHeight];
    }

    function unitSign(value) {
        return value > 0 ? 1 : value < 0 ? -1 : 0;
    }

    function generate2d(result, x, y, ax, ay, bx, by) {
        const w = Math.abs(ax + ay);
        const h = Math.abs(bx + by);
        const dax = unitSign(ax);
        const day = unitSign(ay);
        const dbx = unitSign(bx);
        const dby = unitSign(by);

        if (h === 1) {
            for (let i = 0; i < w; i++) {
                result.push(x, y);
                x += dax;
                y += day;
            }
            return;
        }

        if (w === 1) {
            for (let i = 0; i < h; i++) {
                result.push(x, y);
                x += dbx;
                y += dby;
            }
            return;
        }

        let ax2 = Math.floor(ax / 2);
        let ay2 = Math.floor(ay / 2);
        let bx2 = Math.floor(bx / 2);
        let by2 = Math.floor(by / 2);
        const w2 = Math.abs(ax2 + ay2);
        const h2 = Math.abs(bx2 + by2);

        if (2 * w > 3 * h) {
            if ((w2 & 1) && w > 2) {
                ax2 += dax;
                ay2 += day;
            }
            generate2d(result, x, y, ax2, ay2, bx, by);
            generate2d(result, x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by);
            return;
        }

        if ((h2 & 1) && h > 2) {
            bx2 += dbx;
            by2 += dby;
        }

        generate2d(result, x, y, bx2, by2, ax2, ay2);
        generate2d(result, x + bx2, y + by2, ax, ay, bx - bx2, by - by2);
        generate2d(
            result,
            x + (ax - dax) + (bx2 - dbx),
            y + (ay - day) + (by2 - dby),
            -bx2,
            -by2,
            -(ax - ax2),
            -(ay - ay2)
        );
    }

    function gilbert2d(width, height) {
        if (width <= 0 || height <= 0) return [];
        const result = [];
        if (width >= height) {
            generate2d(result, 0, 0, width, 0, 0, height);
        } else {
            generate2d(result, 0, 0, 0, height, width, 0);
        }
        return result;
    }

    const positionCache = new Map();

    function pixelPositions(width, height) {
        const key = `${width}x${height}`;
        const cached = positionCache.get(key);
        if (cached) return cached;

        const total = width * height;
        const curve = gilbert2d(width, height);
        const offset = Math.round(((Math.sqrt(5) - 1) / 2) * total);
        const oldPos = new Int32Array(total);
        const newPos = new Int32Array(total);

        for (let i = 0; i < total; i++) {
            const ox = curve[i * 2];
            const oy = curve[i * 2 + 1];
            const nextIndex = ((i + offset) % total) * 2;
            const nx = curve[nextIndex];
            const ny = curve[nextIndex + 1];
            oldPos[i] = (ox + oy * width) << 2;
            newPos[i] = (nx + ny * width) << 2;
        }

        const entry = { oldPos, newPos };
        if (positionCache.size > 20) positionCache.clear();
        positionCache.set(key, entry);
        return entry;
    }

    function encryptPixels(sourceData, width, height, password) {
        const { oldPos, newPos } = pixelPositions(width, height);
        const total = width * height;
        let current = new Uint8ClampedArray(sourceData);
        let buffer = new Uint8ClampedArray(current.length);

        for (let stepIndex = 0; stepIndex < password.step; stepIndex++) {
            for (let i = 0; i < total; i++) {
                const oldIndex = oldPos[i];
                const nextIndex = newPos[i];
                buffer[nextIndex] = current[oldIndex];
                buffer[nextIndex + 1] = current[oldIndex + 1];
                buffer[nextIndex + 2] = current[oldIndex + 2];
                buffer[nextIndex + 3] = current[oldIndex + 3];
            }
            const swap = current;
            current = buffer;
            buffer = swap;
        }

        return current;
    }

    function decryptPixels(sourceData, width, height, password) {
        const { oldPos, newPos } = pixelPositions(width, height);
        const total = width * height;
        let current = new Uint8ClampedArray(sourceData);
        let buffer = new Uint8ClampedArray(current.length);

        for (let stepIndex = 0; stepIndex < password.step; stepIndex++) {
            for (let i = 0; i < total; i++) {
                const oldIndex = oldPos[i];
                const nextIndex = newPos[i];
                buffer[oldIndex] = current[nextIndex];
                buffer[oldIndex + 1] = current[nextIndex + 1];
                buffer[oldIndex + 2] = current[nextIndex + 2];
                buffer[oldIndex + 3] = current[nextIndex + 3];
            }
            const swap = current;
            current = buffer;
            buffer = swap;
        }

        return current;
    }

    function addPadding(sourceData, width, height, extraWidth, extraHeight) {
        if (extraWidth === 0 && extraHeight === 0) {
            return { data: sourceData, width, height };
        }

        const nextWidth = width + extraWidth;
        const nextHeight = height + extraHeight;
        const output = new Uint8ClampedArray(nextWidth * nextHeight * 4);

        for (let y = 0; y < nextHeight; y++) {
            for (let x = 0; x < nextWidth; x++) {
                const outputIndex = (x + y * nextWidth) << 2;
                let sourceIndex;

                if (y < height && x < width) {
                    sourceIndex = (x + y * width) << 2;
                } else if (y < height) {
                    sourceIndex = ((width - 1) + y * width) << 2;
                } else {
                    sourceIndex = (Math.min(x, width - 1) + (height - 1) * width) << 2;
                }

                output[outputIndex] = sourceData[sourceIndex];
                output[outputIndex + 1] = sourceData[sourceIndex + 1];
                output[outputIndex + 2] = sourceData[sourceIndex + 2];
                output[outputIndex + 3] = sourceData[sourceIndex + 3];
            }
        }

        return { data: output, width: nextWidth, height: nextHeight };
    }

    function cropPadding(sourceData, width, height, extraWidth, extraHeight) {
        if (extraWidth === 0 && extraHeight === 0) {
            return { data: sourceData, width, height };
        }

        const nextWidth = width - extraWidth;
        const nextHeight = height - extraHeight;
        const output = new Uint8ClampedArray(nextWidth * nextHeight * 4);

        for (let y = 0; y < nextHeight; y++) {
            const sourceOffset = y * width * 4;
            const outputOffset = y * nextWidth * 4;
            output.set(sourceData.subarray(sourceOffset, sourceOffset + nextWidth * 4), outputOffset);
        }

        return { data: output, width: nextWidth, height: nextHeight };
    }

    function toUint8Array(value) {
        if (value instanceof Uint8Array) return value;
        if (value instanceof ArrayBuffer) return new Uint8Array(value);
        if (ArrayBuffer.isView(value)) {
            return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
        }
        throw new Error('Unsupported binary input');
    }

    function isPngBytes(value) {
        const bytes = toUint8Array(value);
        if (bytes.length < PNG_SIGNATURE.length) return false;
        for (let i = 0; i < PNG_SIGNATURE.length; i++) {
            if (bytes[i] !== PNG_SIGNATURE[i]) return false;
        }
        return true;
    }

    function readUint32BE(bytes, offset) {
        return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(offset, false);
    }

    function writeUint32BE(target, offset, value) {
        new DataView(target.buffer, target.byteOffset, target.byteLength).setUint32(offset, value >>> 0, false);
    }

    function calculateCrc(data) {
        const bytes = toUint8Array(data);
        let crc = 0xFFFFFFFF;

        for (let i = 0; i < bytes.length; i++) {
            crc ^= bytes[i];
            for (let bit = 0; bit < 8; bit++) {
                crc = (crc >>> 1) ^ ((crc & 1) ? 0xEDB88320 : 0);
            }
        }

        return (crc ^ 0xFFFFFFFF) >>> 0;
    }

    function appendPngChunk(parts, typeBytes, dataBytes) {
        const chunk = new Uint8Array(12 + dataBytes.length);
        writeUint32BE(chunk, 0, dataBytes.length);
        chunk.set(typeBytes, 4);
        chunk.set(dataBytes, 8);

        const crcInput = new Uint8Array(typeBytes.length + dataBytes.length);
        crcInput.set(typeBytes, 0);
        crcInput.set(dataBytes, typeBytes.length);
        writeUint32BE(chunk, 8 + dataBytes.length, calculateCrc(crcInput));
        parts.push(chunk);
    }

    function toBase64Text(value) {
        const utf8Bytes = textEncoder.encode(value);
        const chunkSize = 0x8000;
        let encoded = '';

        for (let i = 0; i < utf8Bytes.length; i += chunkSize) {
            const chunk = utf8Bytes.subarray(i, i + chunkSize);
            encoded += String.fromCharCode.apply(null, Array.from(chunk));
        }

        return btoa(encoded);
    }

    function fromBase64Text(value) {
        let decoded;
        try {
            decoded = atob(value);
        } catch (error) {
            return null;
        }

        const bytes = new Uint8Array(decoded.length);
        for (let i = 0; i < decoded.length; i++) {
            bytes[i] = decoded.charCodeAt(i);
        }

        try {
            return textDecoder.decode(bytes);
        } catch (error) {
            return null;
        }
    }

    function encryptText(value, key, legacyMode) {
        const source = legacyMode ? value : toBase64Text(value);
        return source
            .split('')
            .map((char, index) => String.fromCharCode(char.charCodeAt(0) + key[index % key.length]))
            .join('');
    }

    function decryptText(value, key, legacyMode) {
        const decoded = value
            .split('')
            .map((char, index) => String.fromCharCode(char.charCodeAt(0) - key[index % key.length]))
            .join('');

        if (legacyMode) return decoded;
        return fromBase64Text(decoded) || '';
    }

    function extractPngTextChunksFromBytes(binary) {
        const bytes = toUint8Array(binary);
        if (!isPngBytes(bytes)) return [];

        const textChunks = [];
        let offset = PNG_SIGNATURE.length;

        while (offset + 12 <= bytes.length) {
            const length = readUint32BE(bytes, offset);
            const typeBytes = bytes.subarray(offset + 4, offset + 8);
            const type = String.fromCharCode(typeBytes[0], typeBytes[1], typeBytes[2], typeBytes[3]);
            const dataStart = offset + 8;
            const dataEnd = dataStart + length;
            if (dataEnd + 4 > bytes.length) break;

            if (type === 'tEXt' || type === 'iTXt') {
                const decoded = textDecoder.decode(bytes.subarray(dataStart, dataEnd));
                const parts = decoded.split('\u0000');
                if (parts.length >= 2) {
                    const key = parts[0];
                    const value = parts[parts.length - 1];
                    if (key && value) {
                        textChunks.push([key, value]);
                    }
                }
            }

            offset = dataEnd + 4;
        }

        return textChunks;
    }

    function readUint32LE(bytes, offset) {
        return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(offset, true);
    }

    function bytesStartWith(bytes, asciiPrefix, offset) {
        const start = offset || 0;
        if (bytes.length < start + asciiPrefix.length) return false;
        for (let i = 0; i < asciiPrefix.length; i++) {
            if (bytes[start + i] !== (asciiPrefix.charCodeAt(i) & 0xFF)) return false;
        }
        return true;
    }

    function decodeLatin1(bytes) {
        let text = '';
        for (let i = 0; i < bytes.length; i++) text += String.fromCharCode(bytes[i]);
        return text;
    }

    function decodeUtf8(bytes) {
        return textDecoder.decode(bytes);
    }

    // Pillow's TIFF reader drops one trailing NUL and decodes ASCII tags as
    // latin-1; matching it keeps the harvested value identical on both sides.
    function decodeTiffAscii(bytes) {
        const end = bytes.length && bytes[bytes.length - 1] === 0 ? bytes.length - 1 : bytes.length;
        return decodeLatin1(bytes.subarray(0, end));
    }

    function stripNullsAndSpaces(text) {
        let start = 0;
        let end = text.length;
        const trimmable = (char) => char === '\u0000' || char === ' ';
        while (start < end && trimmable(text[start])) start += 1;
        while (end > start && trimmable(text[end - 1])) end -= 1;
        return text.slice(start, end);
    }

    /**
     * Decode an EXIF UserComment payload the way SD tools write it.
     * Mirrors _decode_exif_user_comment in backend/obfuscation.py.
     */
    function decodeExifUserComment(value) {
        if (typeof value === 'string') {
            let text = value;
            if (text.startsWith('ASCII') || text.startsWith('UNICODE')) text = text.slice(7);
            return stripNullsAndSpaces(text);
        }
        if (!(value instanceof Uint8Array)) return '';

        let text;
        if (bytesStartWith(value, 'UNICODE\u0000')) {
            const payload = value.subarray(8);
            if (payload.length >= 2 && payload[0] === 0xFF && payload[1] === 0xFE) {
                text = new TextDecoder('utf-16le').decode(payload);
            } else if (payload.length >= 2 && payload[0] === 0xFE && payload[1] === 0xFF) {
                text = new TextDecoder('utf-16be').decode(payload);
            } else if (payload.length >= 2 && payload[1] === 0x00) {
                text = new TextDecoder('utf-16le').decode(payload);
            } else {
                text = new TextDecoder('utf-16be').decode(payload);
            }
        } else if (bytesStartWith(value, 'ASCII\u0000\u0000\u0000') || bytesStartWith(value, '\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000')) {
            text = decodeUtf8(value.subarray(8));
        } else {
            text = decodeUtf8(value);
        }
        return stripNullsAndSpaces(text);
    }

    /**
     * Pick the PNG text-chunk key that carries `text` back to the parser.
     * Mirrors _text_chunk_key_for in backend/obfuscation.py: the obfuscated and
     * restored files are always PNGs, so metadata harvested from a JPEG's or
     * WebP's EXIF/XMP has to be re-keyed to whatever the PNG reader looks for.
     * Unrecognised text is still carried, under UserComment, so a round trip
     * never silently drops something it could not classify.
     */
    function textChunkKeyFor(text) {
        const stripped = String(text || '').trim();
        if (stripped.includes('Steps:') && stripped.includes('Sampler:')) return 'parameters';
        if (stripped.startsWith('{')) {
            let payload;
            try {
                payload = JSON.parse(stripped);
            } catch (error) {
                return 'UserComment';
            }
            if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
                const isNode = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value) && 'class_type' in value;
                if (Object.values(payload).some(isNode)) return 'prompt';
                if ('prompt' in payload && 'uc' in payload) return 'Comment';
            }
        }
        return 'UserComment';
    }

    /**
     * Locate the raw TIFF block and the XMP packet a non-PNG container carries.
     * JPEG keeps both in APP1 segments; WebP keeps them in RIFF chunks. TIFF
     * sources are not handled because the browser cannot decode TIFF pixels at
     * all, so the engine already fails on them before metadata matters.
     */
    function findExifTiffAndXmp(bytes) {
        const found = { tiff: null, xmp: '' };

        if (bytes.length >= 4 && bytes[0] === 0xFF && bytes[1] === 0xD8) {
            let offset = 2;
            while (offset + 4 <= bytes.length) {
                if (bytes[offset] !== 0xFF) break;
                const marker = bytes[offset + 1];
                if (marker === 0xD8 || marker === 0x01 || (marker >= 0xD0 && marker <= 0xD7)) {
                    offset += 2;
                    continue;
                }
                // Entropy-coded data starts at SOS; nothing past it is a segment.
                if (marker === 0xD9 || marker === 0xDA) break;
                const length = (bytes[offset + 2] << 8) | bytes[offset + 3];
                if (length < 2 || offset + 2 + length > bytes.length) break;
                if (marker === 0xE1) {
                    const payload = bytes.subarray(offset + 4, offset + 2 + length);
                    if (!found.tiff && bytesStartWith(payload, EXIF_APP1_HEADER)) {
                        found.tiff = payload.subarray(EXIF_APP1_HEADER.length);
                    } else if (!found.xmp && bytesStartWith(payload, XMP_APP1_HEADER)) {
                        found.xmp = decodeUtf8(payload.subarray(XMP_APP1_HEADER.length));
                    }
                }
                offset += 2 + length;
            }
            return found;
        }

        if (bytesStartWith(bytes, 'RIFF') && bytesStartWith(bytes, 'WEBP', 8)) {
            let offset = 12;
            while (offset + 8 <= bytes.length) {
                const type = decodeLatin1(bytes.subarray(offset, offset + 4));
                const length = readUint32LE(bytes, offset + 4);
                const start = offset + 8;
                if (start + length > bytes.length) break;
                const payload = bytes.subarray(start, start + length);
                if (type === 'EXIF' && !found.tiff) {
                    found.tiff = bytesStartWith(payload, EXIF_APP1_HEADER)
                        ? payload.subarray(EXIF_APP1_HEADER.length)
                        : payload;
                } else if (type === 'XMP ' && !found.xmp) {
                    found.xmp = decodeUtf8(payload);
                }
                // RIFF pads odd-sized chunks to an even boundary.
                offset = start + length + (length & 1);
            }
        }

        return found;
    }

    function readExifTextTags(tiff) {
        const tags = { userComment: null, imageDescription: null };
        if (!tiff || tiff.length < 8) return tags;

        const view = new DataView(tiff.buffer, tiff.byteOffset, tiff.byteLength);
        const byteOrder = view.getUint16(0, false);
        if (byteOrder !== 0x4949 && byteOrder !== 0x4D4D) return tags;
        const littleEndian = byteOrder === 0x4949;
        if (view.getUint16(2, littleEndian) !== 42) return tags;

        const visitIfd = (ifdOffset, visit) => {
            if (!ifdOffset || ifdOffset < 8 || ifdOffset + 2 > tiff.length) return;
            const entryCount = view.getUint16(ifdOffset, littleEndian);
            for (let index = 0; index < entryCount; index++) {
                const entry = ifdOffset + 2 + index * 12;
                if (entry + 12 > tiff.length) return;
                const tag = view.getUint16(entry, littleEndian);
                const type = view.getUint16(entry + 2, littleEndian);
                const count = view.getUint32(entry + 4, littleEndian);
                const size = (EXIF_TYPE_SIZES[type] || 0) * count;
                if (!size) continue;
                // Values of four bytes or fewer sit inline in the entry itself.
                const valueOffset = size > 4 ? view.getUint32(entry + 8, littleEndian) : entry + 8;
                if (valueOffset + size > tiff.length) continue;
                visit(tag, type, tiff.subarray(valueOffset, valueOffset + size));
            }
        };

        let subIfdOffset = 0;
        visitIfd(view.getUint32(4, littleEndian), (tag, type, value) => {
            if (tag === EXIF_TAG_IMAGE_DESCRIPTION && type === EXIF_TYPE_ASCII) {
                tags.imageDescription = decodeTiffAscii(value);
            } else if (tag === EXIF_TAG_SUB_IFD_POINTER && type === EXIF_TYPE_LONG && value.length >= 4) {
                subIfdOffset = new DataView(value.buffer, value.byteOffset, value.byteLength).getUint32(0, littleEndian);
            }
        });

        visitIfd(subIfdOffset, (tag, type, value) => {
            if (tag !== EXIF_TAG_USER_COMMENT) return;
            tags.userComment = type === EXIF_TYPE_ASCII ? decodeTiffAscii(value) : value.slice();
        });

        return tags;
    }

    /** Collect SD metadata that a JPEG or WebP source carries in EXIF or XMP. */
    function harvestNonPngTextChunks(bytes) {
        const chunks = [];
        const seen = new Set();

        const add = (key, text) => {
            const cleaned = String(text || '').trim();
            if (!cleaned || textEncoder.encode(cleaned).length > MAX_CARRIED_TEXT_BYTES) return;
            if (seen.has(key)) return;
            seen.add(key);
            chunks.push([key, cleaned]);
        };

        let containers = { tiff: null, xmp: '' };
        let tags = { userComment: null, imageDescription: null };
        try {
            containers = findExifTiffAndXmp(bytes);
            tags = readExifTextTags(containers.tiff);
        } catch (error) {
            // An unreadable source or a malformed Exif IFD simply carries nothing.
            return chunks;
        }

        const userComment = tags.userComment;
        if (userComment && userComment.length) {
            const decoded = decodeExifUserComment(userComment);
            if (decoded) add(textChunkKeyFor(decoded), decoded);
        }

        const description = tags.imageDescription;
        if (typeof description === 'string' && description.includes('Steps:') && description.includes('Sampler:')) {
            add('parameters', description);
        }

        if (containers.xmp && containers.xmp.includes('Steps:') && containers.xmp.includes('Sampler:')) {
            add('parameters', containers.xmp);
        }

        return chunks;
    }

    /**
     * Collect the SD metadata a source image carries, whatever its container.
     * Mirrors extract_source_text_chunks_from_bytes in backend/obfuscation.py:
     * PNG sources keep their tEXt/iTXt chunks verbatim, while JPEG and WebP
     * sources carry the same information in EXIF or XMP instead. Reading PNG
     * only is how the prompt went missing across a protect/restore round trip
     * for those formats.
     */
    function extractSourceTextChunksFromBytes(binary) {
        const bytes = toUint8Array(binary);
        if (isPngBytes(bytes)) return extractPngTextChunksFromBytes(bytes);
        return harvestNonPngTextChunks(bytes);
    }

    function writePngTextChunks(pngBytes, textChunks, password, options) {
        const { decryptValues = false, legacyPngInfo = false } = options || {};
        const sourceBytes = toUint8Array(pngBytes);
        if (!isPngBytes(sourceBytes)) {
            throw new Error('Not a PNG file');
        }

        const parsedChunks = [];
        let offset = PNG_SIGNATURE.length;

        while (offset + 12 <= sourceBytes.length) {
            const length = readUint32BE(sourceBytes, offset);
            const typeBytes = sourceBytes.slice(offset + 4, offset + 8);
            const dataStart = offset + 8;
            const dataEnd = dataStart + length;
            if (dataEnd + 4 > sourceBytes.length) break;

            parsedChunks.push({
                typeBytes,
                dataBytes: sourceBytes.slice(dataStart, dataEnd),
            });

            offset = dataEnd + 4;
        }

        const transformedChunks = textChunks.map(([key, value]) => {
            const transformed = decryptValues
                ? decryptText(value, passwordKey(password), legacyPngInfo)
                : encryptText(value, passwordKey(password), legacyPngInfo);
            const keyBytes = textEncoder.encode(key);
            const valueBytes = textEncoder.encode(transformed);
            const payload = new Uint8Array(keyBytes.length + 1 + valueBytes.length);
            payload.set(keyBytes, 0);
            payload[keyBytes.length] = 0;
            payload.set(valueBytes, keyBytes.length + 1);
            return {
                typeBytes: textEncoder.encode('tEXt'),
                dataBytes: payload,
            };
        });

        const idatIndex = parsedChunks.findIndex((chunk) =>
            chunk.typeBytes[0] === 0x49 &&
            chunk.typeBytes[1] === 0x44 &&
            chunk.typeBytes[2] === 0x41 &&
            chunk.typeBytes[3] === 0x54
        );
        const insertAt = idatIndex >= 0 ? idatIndex : parsedChunks.length;
        const allChunks = [
            ...parsedChunks.slice(0, insertAt),
            ...transformedChunks,
            ...parsedChunks.slice(insertAt),
        ];

        const outputParts = [PNG_SIGNATURE.slice()];
        for (const chunk of allChunks) {
            appendPngChunk(outputParts, chunk.typeBytes, chunk.dataBytes);
        }

        return new Blob(outputParts, { type: 'image/png' });
    }

    async function loadImage(sourceUrl) {
        return await new Promise((resolve, reject) => {
            const image = new Image();
            image.onload = () => resolve(image);
            image.onerror = () => reject(new Error('Failed to load image'));
            image.src = sourceUrl;
        });
    }

    async function resolveSourceBlob(source) {
        if (source instanceof Blob) return source;
        if (typeof source === 'string') {
            const response = await fetch(source);
            if (!response.ok) {
                throw new Error(`Failed to load source image: ${response.status}`);
            }
            return await response.blob();
        }
        throw new Error('Unsupported image source');
    }

    async function blobToImageData(blob) {
        const maxBytes = getObfuscateMaxFileBytes();
        if (blob.size > maxBytes) {
            throw new Error(
                tText(
                    'tools.obfuscateFileTooLarge',
                    `Image file is too large for safe browser processing (max ${formatMegabyteLimit(maxBytes)}).`,
                    { limit: formatMegabyteLimit(maxBytes) }
                )
            );
        }

        const objectUrl = URL.createObjectURL(blob);
        try {
            const image = await loadImage(objectUrl);
            const width = image.naturalWidth || image.width;
            const height = image.naturalHeight || image.height;
            const maxPixels = getObfuscateMaxImagePixels();
            if ((width * height) > maxPixels) {
                throw new Error(
                    tText(
                        'tools.obfuscatePixelsTooLarge',
                        `Image dimensions are too large for safe browser processing (${width}x${height}, max ${formatMegapixelLimit(maxPixels)}).`,
                        { width, height, limit: formatMegapixelLimit(maxPixels) }
                    )
                );
            }

            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const context = canvas.getContext('2d');
            context.drawImage(image, 0, 0);
            return context.getImageData(0, 0, canvas.width, canvas.height);
        } finally {
            URL.revokeObjectURL(objectUrl);
        }
    }

    async function imageDataToPngBlob(data, width, height) {
        return await new Promise((resolve) => {
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const context = canvas.getContext('2d');
            context.putImageData(new ImageData(data, width, height), 0, 0);
            canvas.toBlob((blob) => resolve(blob), 'image/png', 1);
        });
    }

    async function processImage(source, passwordStr, options, mode) {
        const compatMode = normalizeCompatMode(options?.compatMode);
        const password = resolvePassword(passwordStr, compatMode);
        // Both directions emit a PNG and a PNG can always carry tEXt, so neither
        // the compat mode nor the source container decides whether the user's
        // prompt survives. Only the explicit opt-out does.
        const preserveMetadata = Boolean(options?.preserveMetadata);
        const legacyPngInfo = Boolean(options?.legacyPngInfo);
        const sourceBlob = await resolveSourceBlob(source);
        const textChunks = preserveMetadata
            ? extractSourceTextChunksFromBytes(new Uint8Array(await sourceBlob.arrayBuffer()))
            : [];
        const imageData = await blobToImageData(sourceBlob);
        const { width, height } = imageData;

        const pixelResult = mode === 'encode'
            ? addPadding(encryptPixels(imageData.data, width, height, password), width, height, password.extraWidth, password.extraHeight)
            : (() => {
                const cropped = cropPadding(imageData.data, width, height, password.extraWidth, password.extraHeight);
                return {
                    data: decryptPixels(cropped.data, cropped.width, cropped.height, password),
                    width: cropped.width,
                    height: cropped.height,
                };
            })();

        let outputBlob = await imageDataToPngBlob(pixelResult.data, pixelResult.width, pixelResult.height);
        if (preserveMetadata && textChunks.length) {
            outputBlob = writePngTextChunks(new Uint8Array(await outputBlob.arrayBuffer()), textChunks, password, {
                decryptValues: mode === 'decode',
                legacyPngInfo,
            });
        }

        return {
            blob: outputBlob,
            url: URL.createObjectURL(outputBlob),
            width: pixelResult.width,
            height: pixelResult.height,
            preservedTextChunks: textChunks.length,
        };
    }

    async function encode(source, passwordStr, options) {
        return await processImage(source, passwordStr, options, 'encode');
    }

    async function decode(source, passwordStr, options) {
        return await processImage(source, passwordStr, options, 'decode');
    }

    window.ObfuscateEngine = {
        encode,
        decode,
        parsePassword,
        __internals: {
            BIG_TOMATO_MODE,
            SMALL_TOMATO_MODE,
            normalizeCompatMode,
            extractPngTextChunksFromBytes,
            extractSourceTextChunksFromBytes,
            writePngTextChunks,
            encryptText,
            decryptText,
            calculateCrc,
            // Pixel-level hooks for the cross-implementation parity harness
            // (reference-site JS vs this engine vs backend/obfuscation.py).
            resolvePassword,
            gilbert2d,
            encryptPixels,
            decryptPixels,
            addPadding,
            cropPadding,
        },
    };
})();
