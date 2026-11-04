"""Stage all CommandCards from hash-verified originals; deploy only explicitly.

The source index is a JSON object with sources: {source_sha256: original_path}.
It must describe original downloads/restored atlas layers, not compressed assets.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from command_card_palette import PROFILE, palette_png, transform_metadata
from prepare_command_card_composition_assets import deploy_packs

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(project):
    documents, jobs = [], []
    for pack in ('base', 'cn'):
        root = project / f'assets/resource/{pack}/image/CommandCards'
        paths = ([root / 'manifest'] if pack == 'base' else []) + [root / '_composition/manifest']
        for path in paths:
            raw = path.read_bytes()
            manifest = json.loads(raw)
            updated = copy.deepcopy(manifest)
            if path.parent == root:
                entries = [e for group in updated['servants'].values() for e in group]
                updated['image_transform'] = {**transform_metadata(), 'size': [256, 256], 'date': datetime.now().strftime('%Y-%m-%d')}
            else:
                entries = list(updated['shared'].values()) + list(updated['np_names'].values())
                entries += [e for group in updated.get('np_extra_names', {}).values() for e in group.values()]
                updated['transform'] = transform_metadata()
            documents.append((path, raw, updated))
            for entry in entries:
                target = (path.parent / entry['file']).resolve()
                if not target.is_relative_to(root.resolve()):
                    raise ValueError(f'Unsafe manifest path: {target}')
                jobs.append((target, entry))
    targets = {path for path, _ in jobs}
    actual = {p.resolve() for pack in ('base', 'cn') for p in (project / f'assets/resource/{pack}/image/CommandCards').rglob('*.png')}
    if actual - targets:
        raise ValueError(f'Untracked PNGs must be reviewed: {actual-targets}')
    return documents, jobs


def stage(project, source_index, run, workers=4):
    sources = json.loads(source_index.read_text('utf8'))['sources']
    documents, jobs = inventory(project)
    run.mkdir(parents=True, exist_ok=False)
    snapshots, records = {}, []
    for path, raw, _ in documents:
        relative = path.relative_to(project / 'assets/resource').as_posix()
        backup = run / 'before' / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(raw)
        snapshots[relative] = digest(raw)

    def convert(job):
        path, entry = job
        original = Path(sources[entry['source_sha256']])
        if original.resolve().is_relative_to((project / 'assets/resource').resolve()):
            raise ValueError('Original must be outside deployed resource roots')
        raw = original.read_bytes()
        if digest(raw) != entry['source_sha256']:
            raise ValueError(f'Original hash mismatch: {original}')
        relative = path.relative_to(project / 'assets/resource').as_posix()
        previous = path.read_bytes() if path.exists() else None
        if previous is not None and digest(previous) != entry['sha256']:
            raise ValueError(f'Current asset hash mismatch: {path}')
        before = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
        if before is None or before.ndim != 3 or before.shape[2] != 4:
            raise ValueError(f'Original must decode as BGRA: {original}')
        expected_size = tuple(entry.get('size', (256, 256)))
        data, rows, changed = palette_png(raw, expected_size)
        after = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if after is None or after.shape != before.shape:
            raise ValueError(f'Palette decode changed dimensions/channels: {path}')
        np.testing.assert_array_equal(before[:, :, 3] == 0, after[:, :, 3] == 0)
        np.testing.assert_array_equal(before[:, :, 3] >= 128, after[:, :, 3] >= 128)
        if previous is not None:
            backup = run / 'before' / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(previous)
        output = run / 'staged' / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        entry.update(sha256=digest(data), quantization_profile=PROFILE, fully_transparent_changed_pixels=0, alpha_mask_changed_pixels=changed)
        if 'name_rows' in entry:
            entry['name_rows'] = rows
        mask = (before[:, :, 3] >= 128).astype(np.uint8) * 255
        score = cv2.matchTemplate(before[:, :, :3], after[:, :, :3], cv2.TM_CCOEFF_NORMED, mask=mask)[0, 0]
        return {'file': relative, 'original': str(original), 'source_sha256': digest(raw),
                'before_sha256': digest(previous) if previous is not None else None, 'sha256': digest(data),
                'source_bytes': len(raw), 'before_bytes': len(previous) if previous is not None else 0, 'after_bytes': len(data),
                'fully_transparent_changed_pixels': 0, 'alpha_mask_changed_pixels': 0,
                'self_similarity': float(score) if np.isfinite(score) else None}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(convert, job) for job in jobs]
        for done, future in enumerate(as_completed(futures), 1):
            record = future.result()
            records.append(record)
            snapshots[record['file']] = record['before_sha256']
            if done % 100 == 0 or done == len(jobs):
                print(f'Guarded palette conversion: {done}/{len(jobs)}', flush=True)
    for path, _, manifest in documents:
        relative = path.relative_to(project / 'assets/resource')
        (run / 'staged' / relative).write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf8')
    report = {'project': str(project), 'profile': PROFILE, 'status': 'staged', 'snapshots': snapshots, 'files': records,
              'summary': {'images': len(records), 'before_bytes': sum(r['before_bytes'] for r in records),
                          'source_bytes': sum(r['source_bytes'] for r in records), 'after_bytes': sum(r['after_bytes'] for r in records),
                          'minimum_self_similarity': min((r['self_similarity'] for r in records if r['self_similarity'] is not None), default=None),
                          'fully_transparent_changed_pixels': 0, 'alpha_mask_changed_pixels': 0}}
    (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2), flush=True)


def deploy(project, run):
    report = json.loads((run / 'report.json').read_text('utf8'))
    if Path(report['project']).resolve() != project or report['status'] != 'staged':
        raise ValueError('Run project/status does not match a staged transaction')
    for relative, expected in report['snapshots'].items():
        path = (project / 'assets/resource' / relative).resolve()
        if not path.is_relative_to((project / 'assets/resource').resolve()):
            raise ValueError('Unsafe deployment target')
        actual = digest(path.read_bytes()) if path.exists() else None
        if actual != expected:
            raise ValueError(f'Resource changed after staging; refusing overwrite: {path}')
    packs = [(run / 'staged' / pack / 'image/CommandCards', project / f'assets/resource/{pack}/image/CommandCards', pack) for pack in ('base', 'cn')]
    deploy_packs(packs, run)
    for record in report['files']:
        if digest((project / 'assets/resource' / record['file']).read_bytes()) != record['sha256']:
            raise ValueError('Final deployed hash mismatch')
    report['status'] = 'deployed_and_verified'
    (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(f'Deployed and hash-verified {len(report["files"])} images; backup={run}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--source-index', type=Path)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--deploy', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    project = args.project.resolve()
    if args.deploy:
        deploy(project, args.deploy.resolve())
    else:
        if not args.source_index:
            parser.error('--source-index required for staging originals')
        run = args.run or project / '.workbuddy/backups' / ('command-cards-alpha-guard-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        stage(project, args.source_index, run, args.workers)


if __name__ == '__main__':
    main()
