"""Reconcile all Atlas-declared NP name textures for a CSV roster, dry-run first.

Preserves default names, discovers ascension overrides and explicit transformed
servants, and uses the existing palette conversion and cross-pack transaction.
No new runtime dependencies. HTTP(S)_PROXY is respected; --proxy is explicit.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime
import gzip
from http.client import HTTPException
import ipaddress
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler, Request

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from sync_command_card_assets_from_csv import API, DEFAULT_CSV, ROOT, digest, existing_ok, read_csv, read_manifest
from prepare_command_card_composition_assets import deploy_packs, palette_png
from command_card_palette import PROFILE, transform_metadata
from command_card_metadata import walk, transformed_ids

NAME = re.compile(r'[A-Za-z0-9_-]+\.png\Z')
ASSET = re.compile(r'/(JP|CN)/Servants/Commands/([1-9][0-9]*)/([A-Za-z0-9_-]+\.png)\Z')


def downloader(proxy=None, transport='urllib', static_ip=None):
    if proxy:
        parsed = urlparse(proxy)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('Proxy must be an HTTP(S) proxy URL')
    opener = build_opener(ProxyHandler({'http': proxy, 'https': proxy}) if proxy else ProxyHandler())
    curl = shutil.which('curl.exe') or shutil.which('curl') if transport == 'curl' else None
    if transport == 'curl' and not curl:
        raise ValueError('curl transport requested but curl is not installed')
    if static_ip:
        ipaddress.IPv4Address(static_ip)
        if not curl:
            raise ValueError('--static-ip requires --transport curl')
    sessions = threading.local()
    if transport == 'requests':
        try:
            import requests
        except ImportError as exc:
            raise ValueError('Optional requests transport requires requests in the maintenance Python environment, not the Agent runtime') from exc

    def fetch(url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('api.atlasacademy.io', 'static.atlasacademy.io'):
            raise ValueError(f'Untrusted Atlas URL: {url}')
        for attempt in range(3):
            try:
                if transport == 'requests':
                    if not hasattr(sessions, 'session'):
                        sessions.session = requests.Session()
                        if proxy:
                            sessions.session.trust_env = False
                            sessions.session.proxies = {'http': proxy, 'https': proxy}
                    response = sessions.session.get(url, timeout=(10, 20))
                    if response.status_code == 404:
                        return None
                    response.raise_for_status()
                    return response.content
                if curl:
                    args = [curl, '--silent', '--show-error', '--compressed', '--proto', '=https',
                            '--connect-timeout', '10', '--max-time', '30', '--write-out', '\n%{http_code}', url]
                    if proxy:
                        args += ['--proxy', proxy]
                    if static_ip and parsed.hostname == 'static.atlasacademy.io':
                        # Per-request DNS override; TLS still verifies the original
                        # hostname and SNI. Never modify system DNS or proxy rules.
                        args += ['--connect-to', f'static.atlasacademy.io:443:{static_ip}:443']
                    completed = subprocess.run(args, capture_output=True, timeout=40)
                    raw, _, status = completed.stdout.rpartition(b'\n')
                    if completed.returncode:
                        raise OSError(completed.stderr.decode('utf8', errors='replace'))
                    if status == b'404':
                        return None
                    if status != b'200':
                        raise OSError(f'curl HTTP status {status!r}: {url}')
                    return raw
                with opener.open(Request(url, headers={'User-Agent': 'MaaFgo/np-command-assets', 'Accept-Encoding': 'gzip'}), timeout=60) as response:
                    raw = response.read()
                    return gzip.decompress(raw) if response.headers.get('Content-Encoding') == 'gzip' else raw
            except HTTPError as exc:
                if exc.code == 404:
                    return None
                if attempt == 2:
                    raise
            except (OSError, HTTPException, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
            time.sleep(attempt + 1)
        raise AssertionError('unreachable')
    return fetch


def name_assets(record, region):
    """Only names declared as NP icons or overwrite TD filename resources."""
    values = [np.get('icon') for np in record.get('noblePhantasms', [])]
    for node in walk(record):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() == 'overwritetdfilename':
                    values.extend(v for v in walk(value) if isinstance(v, str))
    assets = {}
    for url in values:
        if not url:
            continue
        parsed = urlparse(url)
        match = ASSET.fullmatch(parsed.path)
        if parsed.scheme != 'https' or parsed.netloc != 'static.atlasacademy.io' or parsed.query or parsed.fragment or not match or match[1] != region:
            raise ValueError(f'Invalid regional NP-name URL: {url}')
        _, owner, filename = match.groups()
        if not NAME.fullmatch(filename) or re.fullmatch(r'card_servant_[0-9]+\.png', filename):
            raise ValueError(f'NP icon points to a portrait: {url}')
        relative = f'np/{owner}/{filename}'
        if relative in assets and assets[relative] != url:
            raise ValueError(f'Conflicting NP sources: {relative}')
        assets[relative] = url
    return assets


def sync(project, csv_path, *, write=False, proxy=None, metadata_cache=None, raw_cache=None, transport='urllib', static_ip=None, selected_ids=(), fetcher=None):
    project = project.resolve()
    rows = read_csv(csv_path)
    selected = set(selected_ids)
    unknown = selected - {row['servantId'] for row in rows}
    if unknown:
        raise ValueError(f'Selected IDs not in CSV: {sorted(unknown)}')
    if selected:
        rows = [row for row in rows if row['servantId'] in selected]
    fetch = fetcher or downloader(proxy, transport, static_ip)
    run = project / '.workbuddy/backups' / ('np-command-assets-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    run.mkdir(parents=True)
    report = {'csv_rows': len(rows), 'write': write, 'backup': str(run), 'metadata_cache': str(metadata_cache) if metadata_cache else None,
              'raw_cache': str(raw_cache) if raw_cache else None, 'transport': transport, 'static_ip': static_ip,
              'planned': [], 'downloaded': [], 'missing': [], 'no_commands': [], 'forms': {}, 'unavailable_region': []}
    roots = {r: project / f'assets/resource/{pack}/image/CommandCards/_composition' for r, pack in [('JP', 'base'), ('CN', 'cn')]}
    manifests = {r: read_manifest(root / 'manifest', {'schema_version': 2, 'region': r, 'shared': {}, 'np_names': {}, 'missing': {}}, region=r) for r, root in roots.items()}
    originals = copy.deepcopy(manifests)

    def metadata(url, filename):
        cached = metadata_cache / filename if metadata_cache else None
        raw = cached.read_bytes() if cached and cached.is_file() else fetch(url)
        if raw is None:
            raise ValueError(f'Atlas metadata missing: {url}')
        target = run / 'metadata' / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return json.loads(raw)

    try:
        for region in roots:
            print(f'{region}: fetching basic servant export via configured proxy...', flush=True)
            records = metadata(API + f'/export/{region}/basic_servant.json', f'basic-{region}.json')
            by_id = {str(r['id']): r for r in records}
            by_number = {int(r['collectionNo']): str(r['id']) for r in records if int(r['collectionNo']) > 0}
            assets, queue, seen = {}, [], set()
            for row in rows:
                sid = row['servantId']
                record = by_id.get(sid)
                if record is None:
                    if int(row['collectionNo']) in by_number:
                        raise ValueError(f'CSV/Atlas identity conflict: {sid}/{row["collectionNo"]}')
                    report['unavailable_region'].append({'region': region, 'id': sid})
                    continue
                if int(record['collectionNo']) != int(row['collectionNo']):
                    raise ValueError(f'CSV/Atlas collectionNo mismatch: {sid}')
                if record.get('type') == 'enemyCollectionDetail':
                    report['no_commands'].append({'region': region, 'id': sid})
                    continue
                queue.append(sid)
            def nice(sid):
                record = metadata(API + f'/nice/{region}/servant/{sid}', f'nice-{region}-{sid}.json')
                if str(record.get('id')) != sid or int(record.get('collectionNo', 0)) != int(by_id[sid]['collectionNo']):
                    raise ValueError(f'NiceServant identity mismatch: {region}/{sid}')
                return sid, record

            # NiceServant exports are very large. Bounded individual requests
            # give useful progress and preserve per-record retry caches.
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(nice, sid) for sid in queue]
                for done, future in enumerate(as_completed(futures), 1):
                    sid, record = future.result()
                    by_id[sid] = record
                    if done % 50 == 0 or done == len(futures):
                        print(f'{region}: NiceServant {done}/{len(futures)}', flush=True)
            while queue:
                sid = queue.pop(0)
                if sid in seen:
                    continue
                seen.add(sid)
                record = by_id.get(sid)
                if record is None or 'noblePhantasms' not in record:
                    record = metadata(API + f'/nice/{region}/servant/{sid}', f'form-{region}-{sid}.json')
                    if str(record.get('id')) != sid:
                        raise ValueError(f'Transformed servant identity mismatch: {sid}')
                    by_id[sid] = record
                assets.update(name_assets(record, region))
                forms = transformed_ids(record)
                if forms:
                    report['forms'][f'{region}/{sid}'] = sorted(forms, key=int)
                    queue.extend(sorted(forms - seen, key=int))
            print(f'{region}: checked {len(seen)} playable/form records, {len(assets)} NP textures declared', flush=True)
            for relative, url in sorted(assets.items()):
                _, sid, filename = relative.split('/')
                manifest = manifests[region]
                entry = manifest['np_names'].get(sid) if filename == 'card_servant_np.png' else manifest.get('np_extra_names', {}).get(sid, {}).get(filename)
                if entry:
                    if entry['file'] != relative or entry['source'] != url:
                        raise ValueError(f'NP resource source changed; review explicitly: {region}/{relative}')
                    if existing_ok(roots[region], entry):
                        continue
                if (roots[region] / relative).exists() and entry is None:
                    raise ValueError(f'Untracked existing NP asset; refusing overwrite: {region}/{relative}')
                report['planned'].append({'region': region, 'id': sid, 'file': relative, 'source': url})
            del records, by_id
        if not write:
            return report

        def prepare(job):
            cached = raw_cache / job['region'] / job['file'] if raw_cache else None
            raw = cached.read_bytes() if cached and cached.is_file() else fetch(job['source'])
            if raw is None:
                return job, None
            original = run / 'originals' / job['region'] / job['file']
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(raw)
            data, name_rows, changed = palette_png(raw, (256, 256))
            if changed or not name_rows:
                raise ValueError(f'Empty NP name or alpha mask changed: {job["source"]}')
            output = run / 'staged' / job['region'] / job['file']
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            return job, {'file': job['file'], 'source': job['source'], 'source_sha256': digest(raw), 'sha256': digest(data),
                         'size': [256, 256], 'mode': 'P', 'name_rows': name_rows, 'alpha_mask_changed_pixels': changed, 'fully_transparent_changed_pixels': 0, 'quantization_profile': PROFILE}

        prepared, errors = [], []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(prepare, job): job for job in report['planned']}
            for done, future in enumerate(as_completed(futures), 1):
                job = futures[future]
                try:
                    prepared.append(future.result())
                except Exception as exc:
                    errors.append(f'Download/validation failed: {job["source"]}: {exc}')
                if done % 10 == 0 or done == len(futures):
                    print(f'NP textures prepared: {done}/{len(futures)}', flush=True)
        if errors:
            report['download_errors'] = errors
            raise RuntimeError(f'{len(errors)} NP download/validation failures; no deployment. First: {errors[0]}')
        for job, entry in prepared:
            manifest, sid = manifests[job['region']], job['id']
            if entry is None:
                manifest.setdefault('np_missing', {})[job['file']] = 'HTTP 404; no regional substitution'
                report['missing'].append(job)
                continue
            if Path(job['file']).name == 'card_servant_np.png':
                manifest['np_names'][sid] = entry
                manifest['missing'].pop(sid, None)
            else:
                manifest.setdefault('np_extra_names', {}).setdefault(sid, {})[Path(job['file']).name] = entry
            manifest.get('np_missing', {}).pop(job['file'], None)
            report['downloaded'].append(job)
            manifest['transform'] = transform_metadata()
        packs = []
        for region, manifest in manifests.items():
            current = read_manifest(roots[region] / 'manifest', {'schema_version': 2, 'region': region, 'shared': {}, 'np_names': {}, 'missing': {}}, region=region)
            if current != originals[region]:
                raise RuntimeError(f'Composition manifest changed during this run; refusing overwrite: {region}')
            if manifest != originals[region]:
                folder = run / 'staged' / region
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'manifest').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf8')
                packs.append((folder, roots[region], region))
        deploy_packs(packs, run)
        return report
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--csv', type=Path, default=DEFAULT_CSV)
    parser.add_argument('--servant-id', action='append', default=[], help='Limit to CSV IDs; repeatable, including linked transformed NP assets')
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--proxy')
    parser.add_argument('--metadata-cache', type=Path)
    parser.add_argument('--raw-cache', type=Path, help='Explicitly reuse prior originals/ directory after a failed download')
    parser.add_argument('--transport', choices=('urllib', 'curl', 'requests'), default='urllib')
    parser.add_argument('--static-ip', help='curl-only per-request IPv4 override, after verifying current static.atlasacademy.io DNS')
    args = parser.parse_args()
    report = sync(args.project, args.csv, write=args.write, proxy=args.proxy, metadata_cache=args.metadata_cache, raw_cache=args.raw_cache, transport=args.transport, static_ip=args.static_ip, selected_ids=args.servant_id)
    print(json.dumps({key: len(report[key]) for key in ('planned', 'downloaded', 'missing', 'no_commands', 'unavailable_region')} | {'report': str(Path(report['backup']) / 'report.json')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
