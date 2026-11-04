"""Prepare palette-PNG composition layers; never modify command portraits.

Maintenance only (Pillow); runtime composition uses OpenCV/NumPy, never the network.
Example:
  python tools/prepare_command_card_composition_assets.py --write \
      --common-source <restored-CommandAtlas-sprites> --download-np --region JP --region CN
Raw sources/backups stay outside resource bundles. Metadata is extensionless.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
from http.client import HTTPException
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from command_card_palette import PROFILE, palette_png, transform_metadata

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / 'assets/resource/base/image/CommandCards'
COMMON_NAMES = tuple(f'card_{part}_{color}' for part in ('bg', 'icon', 'txt') for color in ('buster', 'arts', 'quick')) + tuple(f'card_bg_np{color}' for color in ('buster', 'arts', 'quick'))
COMMON_SIZES = {'bg': (133, 170), 'icon': (185, 133), 'txt': (150, 73), 'bg_np': (140, 177)}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str, cache: Path) -> bytes | None:
    if cache.is_file():
        data = cache.read_bytes()
        palette_png(data, (256, 256))  # Validate cached originals, not HTTP error pages.
        return data
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'MaaFgo/card-composition-assets'}), timeout=10) as response:
                data = response.read()
            palette_png(data, (256, 256))
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
            return data
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == 2:
                raise
        except (OSError, ValueError, HTTPException):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)
    raise AssertionError('unreachable')


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='composition-', suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def deploy(staged: Path, destination: Path, backup: Path) -> None:
    """Manifest last, atomic files, exact-target rollback; keep staged copies."""
    deploy_packs([(staged, destination, '')], backup)


def deploy_packs(packs: list[tuple[Path, Path, str]], backup: Path) -> None:
    """One rollback scope for all regional bundles, not one transaction per pack."""
    written = []
    try:
        files = [(p, destination / p.relative_to(staged), Path(label) / p.relative_to(staged))
                 for staged, destination, label in packs for p in staged.rglob('*') if p.is_file()]
        files.sort(key=lambda item: (item[0].name == 'manifest', item[2].as_posix()))
        if len({target.resolve() for _, target, _ in files}) != len(files):
            raise ValueError('Regional deployment destinations overlap')
        for path, target, relative in files:
            data = path.read_bytes()
            if target.is_file() and target.read_bytes() == data:
                continue
            old = None
            if target.exists():
                old = backup / 'overwritten' / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, old)
            _atomic_write(target, data)
            written.append((target, old, digest(data)))
            if digest(target.read_bytes()) != digest(data):
                raise RuntimeError(f'Deployment hash mismatch: {target}')
    except BaseException:
        for target, old, expected in reversed(written):
            if not target.is_file() or digest(target.read_bytes()) != expected:
                raise RuntimeError(f'Destination changed concurrently; staged files and backups retained: {target}')
            if old is None:
                target.unlink()  # Only this run's newly created file; staged copy retained.
            else:
                _atomic_write(target, old.read_bytes())
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--region-root', action='append', default=[], metavar='REGION=PATH', help='Regional CommandCards destination; standard sibling roots are inferred')
    parser.add_argument('--common-source', type=Path, required=True, help='RGBA sprites with restored atlas alpha')
    parser.add_argument('--cn-common-source', type=Path, help='Optional CN RGBA layers: restore atlas alpha AND sprite padding, without rescaling')
    parser.add_argument('--download-np', action='store_true')
    parser.add_argument('--local-np-source', type=Path, help='Optional original-name tree: REGION/servant_id/card_servant_np.png; preferred to downloads')
    parser.add_argument('--region', action='append', choices=('JP', 'CN', 'NA', 'TW', 'KR'))
    parser.add_argument('--servant-id', action='append', default=[])
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--raw-cache', type=Path, default=ROOT / '.workbuddy/cache/command-np-originals')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    portrait_manifest = json.loads((args.root / 'manifest').read_text('utf8'))
    selected = sorted(set(args.servant_id) or set(portrait_manifest['servants']), key=int)
    if any(not sid.isdecimal() or sid not in portrait_manifest['servants'] for sid in selected):
        parser.error('Unknown or invalid servant ID')
    regions = sorted(set(args.region or ['JP']))
    requested = {'JP'}
    if args.cn_common_source:
        requested.add('CN')
    if args.download_np or args.local_np_source:
        requested.update(regions)
    roots = {'JP': args.root.resolve()}
    for value in args.region_root:
        region, separator, path = value.partition('=')
        if not separator or region not in ('CN', 'NA', 'TW', 'KR') or not path:
            parser.error('--region-root expects CN/NA/TW/KR=PATH; use --root for JP/base')
        roots[region] = Path(path).resolve()
    for region in requested - roots.keys():
        base = roots['JP']
        if base.parent.name != 'image' or base.parent.parent.name != 'base':
            parser.error(f'Cannot infer {region} directory; supply --region-root {region}=PATH')
        roots[region] = base.parents[2] / region.lower() / 'image/CommandCards'
    destinations = {region: roots[region] / '_composition' for region in requested}
    paths = list(destinations.values())
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(paths) for b in paths[i+1:]):
        parser.error('Regional destination roots must not overlap')
    for name in COMMON_NAMES:
        if not (args.common_source / (name + '.png')).is_file():
            raise FileNotFoundError(name)
        if args.cn_common_source and not (args.cn_common_source / (name + '.png')).is_file():
            raise FileNotFoundError('CN ' + name)
    if not args.write:
        print(f'Dry run: {len(COMMON_NAMES)} shared layers; CN layers={bool(args.cn_common_source)}; {len(selected)} servant IDs; regions={regions}; no writes/network')
        return 0
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')
    backup = ROOT / '.workbuddy/backups' / ('card-composition-' + stamp)
    staged = backup / 'staged'
    staged.mkdir(parents=True, exist_ok=False)
    manifests = {}
    for region in sorted(requested):
        previous = destinations[region] / 'manifest'
        if previous.exists():
            manifest = json.loads(previous.read_text('utf8'))
            if manifest.get('schema_version') != 2 or manifest.get('region') != region:
                raise ValueError(f'Migrate the legacy/mismatched composition manifest before maintenance: {previous}')
        else:
            manifest = {'schema_version': 2, 'region': region, 'shared': {}, 'np_names': {}, 'missing': {}}
        manifest['transform'] = transform_metadata()
        manifests[region] = manifest

    def prepare(region: str, data: bytes, relative: str, expected: tuple[int, int], source: str) -> dict:
        target = staged / region / relative
        original = backup / 'originals' / region / relative
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(data)
        result, rows, changed = palette_png(data, expected)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result)
        return {'file': relative, 'source': source, 'source_sha256': digest(data), 'sha256': digest(result), 'size': list(expected), 'mode': 'P', 'name_rows': rows, 'alpha_mask_changed_pixels': changed, 'fully_transparent_changed_pixels': 0, 'quantization_profile': PROFILE}

    for name in COMMON_NAMES:
        part = 'bg_np' if name.startswith('card_bg_np') else name.split('_')[1]
        src = args.common_source / (name + '.png')
        manifests['JP']['shared'][name] = prepare('JP', src.read_bytes(), 'shared/' + name + '.png', COMMON_SIZES[part], 'Local FGO CommandAtlas RGB + CommandAtlas_alpha.R: ' + name)
        if args.cn_common_source:
            src = args.cn_common_source / (name + '.png')
            data = src.read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                if image.convert('RGBA').getchannel('A').getextrema()[0] == 255:
                    raise ValueError(f'CN layer alpha has not been restored: {name}')
            manifests['CN']['shared'][name] = prepare('CN', data, 'shared/' + name + '.png', COMMON_SIZES[part], 'Local FGO CN CommandAtlas RGB + CommandAtlas_alpha.R; atlas padding restored without rescaling: ' + name)
    errors = []
    if args.download_np or args.local_np_source:
        jobs = [(region, sid) for region in regions for sid in selected]
        def job(region: str, sid: str):
            url = f'https://static.atlasacademy.io/{region}/Servants/Commands/{sid}/card_servant_np.png'
            local = args.local_np_source / region / sid / 'card_servant_np.png' if args.local_np_source else None
            if local is not None and local.is_file():
                data = local.read_bytes()
                source = f'Local FGO {region} Servants/Commands/{sid}/card_servant_np.png'
            elif args.download_np:
                data = download(url, args.raw_cache / region / (sid + '.png'))
                source = url
            else:
                return region, sid, None
            if data is None:
                return region, sid, None
            return region, sid, prepare(region, data, f'np/{sid}/card_servant_np.png', (256, 256), source)
        with ThreadPoolExecutor(max_workers=max(1, min(16, args.workers))) as pool:
            futures = {pool.submit(job, *job_args): job_args for job_args in jobs}
            for done, future in enumerate(as_completed(futures), 1):
                region, sid = futures[future]
                try:
                    _, _, entry = future.result()
                    if entry is None:
                        manifests[region]['missing'][sid] = 'HTTP 404: no corresponding regional NP-name texture; no fallback' if args.download_np else 'No local regional NP-name texture; no fallback'
                    else:
                        manifests[region]['np_names'][sid] = entry
                except Exception as exc:
                    errors.append(f'{region}/{sid}: {type(exc).__name__}: {exc}')
                    print(f'Failed (will not deploy this run): {errors[-1]}', flush=True)
                if done % 50 == 0 or done == len(jobs):
                    print(f'{done}/{len(jobs)} NP-name requests complete; errors={len(errors)}', flush=True)
    if errors:
        (backup / 'errors.txt').write_text('\n'.join(errors), encoding='utf8')
        raise RuntimeError(f'{len(errors)} download/validation failures; NO deployment. Raw cache retained. See {backup / "errors.txt"}')
    for region, manifest in manifests.items():
        for sid in manifest['np_names']:
            manifest['missing'].pop(sid, None)
        folder = staged / region
        folder.mkdir(exist_ok=True)
        payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
        (folder / 'manifest').write_text(payload, encoding='utf8')
    deploy_packs([(staged / region, destinations[region], region) for region in sorted(requested)], backup)
    count = sum(len(m['np_names']) for m in manifests.values())
    missing = sum(len(m['missing']) for m in manifests.values())
    print(f'Deployed regional packs={sorted(requested)}; NP-name textures={count}; missing={missing}; backup={backup}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
