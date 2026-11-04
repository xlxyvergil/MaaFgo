"""Maintenance-only libimagequant palette conversion with ServantStatus guards.

No runtime module imports this file. Install tools/requirements-command-card-assets.txt
in the maintenance interpreter; never add imagequant to the Agent/APK dependencies.
"""
from __future__ import annotations

import io
import numpy as np
from PIL import Image

PROFILE = 'libimagequant-256-alpha-classes-v1'


def transform_metadata():
    return {'profile': PROFILE, 'colors': 256, 'mode': 'P', 'method': 'LIBIMAGEQUANT',
            'quantize': 'LIBIMAGEQUANT', 'imagequant_version': '1.1.5', 'dither': 'NONE',
            'optimize': True, 'compress_level': 9,
            'alpha_guard': {'preserve_fully_transparent': True, 'preserve_binary_mask_threshold': 128,
                            'remap': 'nearest RGBA palette entry in the original alpha class'}}


def alpha_class(alpha):
    return np.where(alpha == 0, 0, np.where(alpha < 128, 1, 2))


def quantize_image(image):
    """Return a P image and metrics; preserve alpha=0 and alpha>=128 positions.

    Intermediate alpha is still lossy. Palette repair handles tiny alpha classes
    that libimagequant omitted, rather than silently flattening those pixels.
    """
    try:
        import imagequant
    except ImportError as exc:
        raise RuntimeError('Maintenance dependency missing: install tools/requirements-command-card-assets.txt; no FASTOCTREE fallback is allowed') from exc
    rgba = image.convert('RGBA')
    before = np.asarray(rgba)
    raw_indices, raw_palette = imagequant.quantize_raw_rgba_bytes(
        rgba.tobytes(), rgba.width, rgba.height, dithering_level=0,
        max_colors=256, min_quality=0, max_quality=100)
    indices = np.frombuffer(raw_indices, np.uint8).reshape(rgba.height, rgba.width).copy()
    palette = np.asarray(raw_palette, np.uint8).reshape(-1, 4).copy()
    original_class = alpha_class(before[:, :, 3])
    required = set(np.unique(original_class))
    reserved = 0
    for desired in np.unique(original_class):
        classes = alpha_class(palette[:, 3])
        if np.any(classes == desired):
            continue
        usage = np.bincount(indices.ravel(), minlength=len(palette))
        replaceable = [i for i in range(len(palette)) if classes[i] not in required or np.count_nonzero(classes == classes[i]) > 1]
        unused = [i for i in replaceable if usage[i] == 0]
        if len(palette) < 256 and not len(unused):
            palette = np.vstack((palette, [0, 0, 0, 0])).astype(np.uint8)
            slot = len(palette) - 1
        elif len(unused):
            slot = int(unused[0])
        else:
            slot = min(replaceable, key=lambda i: (usage[i], i))
        colors, counts = np.unique(before[original_class == desired], axis=0, return_counts=True)
        palette[slot] = colors[np.argmax(counts)]
        reserved += 1
    classes = alpha_class(palette[:, 3])
    mismatched = original_class != classes[indices]
    for desired in (0, 1, 2):
        pixels = mismatched & (original_class == desired)
        if not pixels.any():
            continue
        candidates = np.flatnonzero(classes == desired)
        colors, inverse = np.unique(before[pixels], axis=0, return_inverse=True)
        nearest = []
        for start in range(0, len(colors), 1024):
            difference = colors[start:start + 1024].astype(np.float32)[:, None, :] - palette[candidates].astype(np.float32)[None, :, :]
            nearest.extend(candidates[np.argmin(np.square(difference).sum(axis=2), axis=1)])
        indices[pixels] = np.asarray(nearest, np.uint8)[inverse]
    result = Image.fromarray(indices)
    result.putpalette(palette[:, :3].flatten().tolist())
    result.info['transparency'] = palette[:, 3].tobytes()
    after = np.asarray(result.convert('RGBA'))
    if not np.array_equal(alpha_class(after[:, :, 3]), original_class):
        raise ValueError('Palette conversion changed alpha classes')
    return result, {'alpha_class_corrected_pixels': int(mismatched.sum()), 'reserved_alpha_classes': reserved,
                    'fully_transparent_changed_pixels': 0, 'alpha_mask_changed_pixels': 0}


def palette_png(data, expected_size):
    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        if opened.format != 'PNG' or opened.size != expected_size:
            raise ValueError(f'Expected PNG {expected_size}, got {opened.format} {opened.size}')
        image = opened.convert('RGBA')
    quantized, metrics = quantize_image(image)
    stream = io.BytesIO()
    quantized.save(stream, 'PNG', optimize=True, compress_level=9)
    with Image.open(io.BytesIO(stream.getvalue())) as saved:
        decoded = np.asarray(saved.convert('RGBA'))
        if saved.mode != 'P' or not np.array_equal(alpha_class(decoded[:, :, 3]), alpha_class(np.asarray(image)[:, :, 3])):
            raise ValueError('Saved palette PNG changed alpha classes')
    rows = []
    if expected_size == (256, 256):
        rows = [r for r in (0, 1) if np.count_nonzero(decoded[r * 128:(r + 1) * 128, :, 3] >= 128) >= 32]
    return stream.getvalue(), rows, metrics['alpha_mask_changed_pixels']
