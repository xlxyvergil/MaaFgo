"""Export a card using the same independent module future Agent callers use.

PNG export defaults to guarded libimagequant/256 colours/no dithering (tooling
only). --rgba uses OpenCV without Pillow. The runtime import needs neither
Pillow nor a subprocess, disk writes or network access.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.battle.card_composer import CommandCardComposer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--servant-id', required=True)
    parser.add_argument('--color', choices=('buster', 'arts', 'quick'), required=True)
    parser.add_argument('--portrait-variant', default='1')
    parser.add_argument('--card-type', choices=('face', 'np'), default='face')
    parser.add_argument('--region', choices=('JP', 'CN', 'NA', 'TW', 'KR'), help='Face: JP (default) or CN; NP: explicit name region required')
    parser.add_argument('--np-name-row', type=int, default=0)
    parser.add_argument('--np-name-file', help='NP name PNG basename under _composition/np/<servant-id>/; default: card_servant_np.png')
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--asset-root', type=Path, action='append')
    parser.add_argument('--region-asset-root', action='append', default=[], metavar='REGION=PATH', help='Regional CommandCards root, overrides base-relative files; repeatable')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mask-output', type=Path)
    parser.add_argument('--rgba', action='store_true', help='Write BGRA PNG via OpenCV; no palette quantization')
    args = parser.parse_args()
    regional = {}
    for value in args.region_asset_root:
        region, separator, path = value.partition('=')
        if not separator or region not in ('JP', 'CN', 'NA', 'TW', 'KR') or not path:
            parser.error('--region-asset-root expects REGION=PATH')
        regional.setdefault(region, []).append(Path(path))
    composer = CommandCardComposer(args.asset_root, region_roots=regional)
    all_roots = composer.roots + tuple(p for values in composer.region_roots.values() for p in values)
    for target in (args.output, args.mask_output):
        if target is None:
            continue
        if target.suffix.lower() != '.png':
            parser.error('Output must be PNG')
        if target.exists():
            parser.error(f'Refusing to overwrite existing file: {target}')
        if any(target.resolve().is_relative_to(root) for root in all_roots):
            parser.error('Export outside the resource roots; do not overwrite or add generated runtime templates there')
    if args.mask_output and args.mask_output.resolve() == args.output.resolve():
        parser.error('Image and mask output paths must differ')
    try:
        card = composer.compose(args.servant_id, args.color, portrait_variant=args.portrait_variant, card_type=args.card_type, region=args.region, np_name_file=args.np_name_file, np_name_row=args.np_name_row, viewport=(args.width, args.height))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        export_mask = card.mask
        if args.rgba:
            ok, encoded = cv2.imencode('.png', card.bgra)
            if not ok:
                raise RuntimeError('PNG encoding failed')
            encoded.tofile(args.output)
        else:
            from PIL import Image
            image = Image.fromarray(cv2.cvtColor(card.bgra, cv2.COLOR_BGRA2RGBA))
            from command_card_palette import quantize_image
            image, _ = quantize_image(image)
            image.save(args.output, 'PNG', optimize=True, compress_level=9)
            export_mask = np.where(np.asarray(image.convert('RGBA'))[:, :, 3] >= 128, 255, 0).astype(np.uint8)
        if args.mask_output:
            args.mask_output.parent.mkdir(parents=True, exist_ok=True)
            ok, encoded = cv2.imencode('.png', export_mask)
            if not ok:
                raise RuntimeError('Mask PNG encoding failed')
            encoded.tofile(args.mask_output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'{args.output}: size={card.bgra.shape[1]}x{card.bgra.shape[0]}, anchor={card.anchor}, profile={card.profile}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
