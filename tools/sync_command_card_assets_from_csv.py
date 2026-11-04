"""Incrementally reconcile a servant CSV with Atlas; dry-run unless --write.

Portraits are JP/base; Chinese NP names are same-path overrides in cn.
Existing valid assets and unrelated catalogs/UI layers are never rewritten.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import csv
from datetime import datetime, timedelta, timezone
import hashlib
from http.client import HTTPException
import io
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from prepare_command_card_composition_assets import deploy_packs, palette_png
from command_card_palette import PROFILE, transform_metadata
from command_card_metadata import transformed_ids

ROOT = TOOLS.parent
DEFAULT_CSV = Path('F:/KumikoPythonCode/FGO主线剧情epub/servant_list.csv')
API = 'https://api.atlasacademy.io'
STATIC = 'https://static.atlasacademy.io'
PORTRAIT = re.compile(r'card_servant_[1-9][0-9]*\.png\Z')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url):
    if urlparse(url).scheme != 'https' or urlparse(url).hostname not in ('api.atlasacademy.io', 'static.atlasacademy.io'):
        raise ValueError(f'Untrusted asset host: {url}')
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'MaaFgo/csv-command-assets'}), timeout=15) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == 2:
                raise
        except (OSError, HTTPException):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'servantId', 'collectionNo'} <= set(reader.fieldnames or []):
            raise ValueError('CSV requires servantId and collectionNo columns')
        rows = list(reader)
    ids, numbers = set(), set()
    for row in rows:
        sid, number = row['servantId'], row['collectionNo']
        if not re.fullmatch(r'[1-9][0-9]*', sid) or not re.fullmatch(r'[1-9][0-9]*', number):
            raise ValueError(f'Invalid CSV identity: {sid}/{number}')
        if sid in ids or number in numbers:
            raise ValueError(f'Duplicate CSV identity: {sid}/{number}')
        ids.add(sid)
        numbers.add(number)
    return rows


def read_manifest(path, default, *, region=None):
    result = json.loads(path.read_text('utf8')) if path.exists() else copy.deepcopy(default)
    if region and (result.get('schema_version') != 2 or result.get('region') != region):
        raise ValueError(f'Unexpected composition manifest: {path}')
    return result


def existing_ok(root, entry):
    relative = Path(entry['file'])
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError(f'Unsafe manifest path: {relative}')
    if not path.is_file():
        return False
    data = path.read_bytes()
    if digest(data) != entry['sha256']:
        raise ValueError(f'Existing asset hash mismatch; inspect before overwriting: {path}')
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG' or image.size != (256, 256) or image.mode != 'P' or len(image.getpalette()) > 768:
            raise ValueError(f'Existing asset is not a 256x256 palette PNG: {path}')
    return True


def command_assets(record):
    assets = {}
    for group, values in (record.get('extraAssets', {}).get('commands') or {}).items():
        if not isinstance(values, dict):
            continue
        for key, url in values.items():
            parsed = urlparse(url)
            filename = Path(parsed.path).name
            if parsed.scheme != 'https' or parsed.hostname != 'static.atlasacademy.io' or not PORTRAIT.fullmatch(filename):
                raise ValueError(f'Invalid command portrait URL: {url}')
            if filename in assets and assets[filename]['source'] != url:
                raise ValueError(f'Conflicting portrait filenames: {filename}')
            assets.setdefault(filename, {'group': str(group), 'key': str(key), 'source': url})
    return assets


def sync(project, csv_path, *, write=False, selected_ids=(), check_existing=False, metadata_cache=None, fetcher=fetch):
    project = project.resolve()
    rows = read_csv(csv_path)
    selected = set(selected_ids)
    known = {r['servantId'] for r in rows}
    if selected - known:
        raise ValueError(f'Selected IDs not in CSV: {sorted(selected-known)}')
    if selected:
        rows = [r for r in rows if r['servantId'] in selected]
    run = project / '.workbuddy/backups' / ('csv-command-assets-' + datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f'))
    run.mkdir(parents=True)
    report = {'csv': str(csv_path), 'csv_rows': len(rows), 'write': write, 'new_servants': [], 'forms': {}, 'no_commands': [], 'csv_not_in_atlas': [], 'atlas_not_in_csv': [], 'planned': [], 'missing': [], 'downloaded': [], 'backup': str(run)}
    roots = {r: project / f'assets/resource/{pack}/image/CommandCards' for r, pack in [('JP','base'),('CN','cn')]}
    portrait_path = roots['JP'] / 'manifest'
    portraits = read_manifest(portrait_path, {'schema_version':1, 'region':'JP', 'servants':{}})
    old_portraits = copy.deepcopy(portraits)
    if portraits.get('region') != 'JP':
        raise ValueError('Base portrait manifest must use JP artwork')
    compositions = {}
    for region in roots:
        compositions[region] = read_manifest(roots[region]/'_composition/manifest', {'schema_version':2,'region':region,'shared':{},'np_names':{},'missing':{}}, region=region)
    old_compositions = copy.deepcopy(compositions)

    def get_json(url, filename):
        candidates = [metadata_cache / filename] if metadata_cache else []
        if metadata_cache and filename.startswith('nice-'):
            sid = filename.removeprefix('nice-').removesuffix('.json')
            candidates += [metadata_cache / f'nice-JP-{sid}.json', metadata_cache / f'form-JP-{sid}.json']
        cached = next((p for p in candidates if p.is_file()), None)
        raw = cached.read_bytes() if cached else fetcher(url)
        if raw is None:
            raise ValueError(f'Atlas metadata missing: {url}')
        value = json.loads(raw)
        path = run/'metadata'/filename
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        return value

    basic = get_json(API+'/export/JP/basic_servant.json', 'basic-JP.json')
    by_id = {str(r['id']):r for r in basic}
    by_number = {int(r['collectionNo']):r for r in basic}
    cn_ids = {str(r['id']) for r in get_json(API+'/export/CN/basic_servant.json', 'basic-CN.json')}
    report['atlas_not_in_csv'] = [str(r['id']) for r in basic if str(r['id']) not in known]
    need_records, active = [], []
    for row in rows:
        sid, number = row['servantId'], int(row['collectionNo'])
        record = by_id.get(sid)
        if record is None:
            if number in by_number:
                raise ValueError(f'CSV/Atlas identity conflict at collectionNo={number}: {sid} vs {by_number[number]["id"]}')
            report['csv_not_in_atlas'].append(sid)
            continue
        if int(record['collectionNo']) != number:
            raise ValueError(f'CSV/Atlas collectionNo mismatch for {sid}')
        if record.get('type') == 'enemyCollectionDetail':
            report['no_commands'].append({'id':sid,'reason':'Atlas enemyCollectionDetail; no playable command artwork requested'})
            continue
        active.append(sid)
        entries = portraits['servants'].get(sid, [])
        present = {Path(e['file']).name for e in entries if existing_ok(roots['JP'], e)}
        expected = {f'card_servant_{c["id"]}.png' for c in (record.get('costume') or {}).values() if int(c.get('id',0))>0}
        if check_existing or not entries or len(present) != len(entries) or expected - present:
            need_records.append(row)
        if not entries:
            report['new_servants'].append(sid)

    def nice(row):
        sid = row['servantId']
        record = get_json(API+f'/nice/JP/servant/{row["collectionNo"]}', f'nice-{sid}.json')
        if str(record.get('id')) != sid or int(record.get('collectionNo',0)) != int(row['collectionNo']):
            raise ValueError(f'NiceServant identity mismatch for {sid}')
        return sid, record

    jobs = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = list(pool.map(nice, need_records))
    visited = set()
    while pending:
        sid, record = pending.pop(0)
        if sid in visited:
            continue
        visited.add(sid)
        assets = command_assets(record)
        if not assets:
            report['no_commands'].append({'id':sid,'reason':'NiceServant.extraAssets.commands is empty'})
            if sid in active:
                active.remove(sid)
            continue
        if sid not in active:
            active.append(sid)
        existing = {e['file']:e for e in portraits['servants'].get(sid,[])}
        for filename, info in assets.items():
            relative = f'{sid}/{filename}'
            entry = existing.get(relative)
            if entry and entry['source'] != info['source']:
                raise ValueError(f'Existing portrait source changed; review explicitly: {relative}')
            if not entry or not existing_ok(roots['JP'],entry):
                jobs.append({'kind':'portrait','region':'JP','id':sid,'file':relative,**info})
        forms = transformed_ids(record)
        if forms:
            report['forms'][sid] = sorted(forms, key=int)
        for form_id in sorted(forms - visited, key=int):
            form = get_json(API+f'/nice/JP/servant/{form_id}', f'nice-{form_id}.json')
            if str(form.get('id')) != form_id:
                raise ValueError(f'Transformed NiceServant identity mismatch: {form_id}')
            pending.append((form_id, form))

    for sid in active:
        for region in ('JP','CN'):
            entry = compositions[region]['np_names'].get(sid)
            if entry and existing_ok(roots[region]/'_composition',entry):
                continue
            if region == 'CN' and sid not in cn_ids:
                report['missing'].append({'kind':'np','region':region,'id':sid,'reason':'Not in CN servant export yet; never substitute JP text'})
                continue
            jobs.append({'kind':'np','region':region,'id':sid,'file':f'_composition/np/{sid}/card_servant_np.png','source':STATIC+f'/{region}/Servants/Commands/{sid}/card_servant_np.png'})
    report['planned'] = jobs
    if not write:
        (run/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        return report

    def prepare(job):
        try:
            raw = fetcher(job['source'])
        except Exception as exc:
            raise RuntimeError(f'Download failed for {job["region"]}/{job["id"]}: {job["source"]}: {exc}') from exc
        if raw is None:
            if job['kind'] == 'portrait':
                raise ValueError(f'Declared command portrait returned HTTP 404: {job["source"]}')
            return job, None
        original = run/'originals'/job['region']/job['file']
        original.parent.mkdir(parents=True,exist_ok=True)
        original.write_bytes(raw)
        data, name_rows, changed = palette_png(raw,(256,256))
        if changed:
            raise ValueError(f'Quantization changed alpha>=128 mask: {job["source"]}')
        if job['kind']=='np' and not name_rows:
            raise ValueError(f'Empty NP-name texture: {job["source"]}')
        output = run/'staged'/job['region']/job['file']
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(data)
        entry = {'file':job['file'],'source':job['source'],'source_sha256':digest(raw),'sha256':digest(data), 'quantization_profile': PROFILE, 'fully_transparent_changed_pixels': 0}
        if job['kind']=='portrait':
            entry.update(group=job['group'],key=job['key'])
        else:
            entry.update(file=f'np/{job["id"]}/card_servant_np.png',size=[256,256],mode='P',name_rows=name_rows,alpha_mask_changed_pixels=changed)
        return job, entry

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            prepared = list(pool.map(prepare,jobs))
        for job,entry in prepared:
            sid,region=job['id'],job['region']
            if entry is None:
                compositions[region]['missing'][sid]='HTTP 404: no corresponding regional NP-name texture; no fallback'
                report['missing'].append({**job,'reason':'HTTP 404'})
                continue
            report['downloaded'].append(job)
            if job['kind']=='portrait':
                entries={e['file']:e for e in portraits['servants'].get(sid,[])}
                entries[entry['file']]=entry
                portraits['servants'][sid]=sorted(entries.values(),key=lambda e:e['file'])
            else:
                compositions[region]['np_names'][sid]=entry
                compositions[region]['missing'].pop(sid,None)
        if any(job['kind']=='portrait' for job in report['downloaded']):
            portraits['image_transform'] = {**transform_metadata(), 'size': [256, 256]}
        for region in roots:
            if any(job['kind']=='np' and job['region']==region for job in report['downloaded']):
                compositions[region]['transform'] = transform_metadata()
        def stage_manifest(region,relative,value,old):
            if value==old:
                return
            path=run/'staged'/region/relative
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8')
        stage_manifest('JP','manifest',portraits,old_portraits)
        for region in roots:
            stage_manifest(region,'_composition/manifest',compositions[region],old_compositions[region])
        packs=[(run/'staged'/region,roots[region],region) for region in roots if (run/'staged'/region).is_dir()]
        deploy_packs(packs,run)
        report['deployed_files']=sum(1 for stage,_,_ in packs for p in stage.rglob('*') if p.is_file())
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (run/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=ROOT)
    parser.add_argument('--csv',type=Path,default=DEFAULT_CSV)
    parser.add_argument('--servant-id',action='append',default=[])
    parser.add_argument('--check-existing',action='store_true',help='Also query NiceServant for every selected existing servant')
    parser.add_argument('--metadata-cache',type=Path,help='Explicitly reuse prior Atlas metadata, including linked forms')
    parser.add_argument('--write',action='store_true',help='Download, quantize, validate and deploy; otherwise API-only preview')
    args=parser.parse_args()
    report=sync(args.project,args.csv,write=args.write,selected_ids=args.servant_id,check_existing=args.check_existing,metadata_cache=args.metadata_cache)
    print(json.dumps({'csv_rows':report['csv_rows'],'new_servants':report['new_servants'],'no_commands':len(report['no_commands']),'planned_images':len(report['planned']),'downloaded':len(report['downloaded']),'missing':len(report['missing']),'report':str(Path(report['backup'])/'report.json')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
