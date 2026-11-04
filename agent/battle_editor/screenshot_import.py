"""Bounded local screenshot recognition. Never connects to or operates a device."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from .storage import EditorError, MAX_JSON_BYTES

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 30_000
IMAGE_TYPES = ('image/png', 'image/jpeg', 'image/webp')


def decode_image(raw):
    from PIL import Image, ImageOps, UnidentifiedImageError
    import numpy as np
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise EditorError('image_size', '图片为空或超过16 MiB，请换一张较小的截图。', 413)
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if original.format not in ('PNG', 'JPEG', 'WEBP'):
                raise EditorError('image_format', '支持 PNG、JPG/JPEG、WebP 静态图片。', 415)
            if getattr(original, 'n_frames', 1) != 1:
                raise EditorError('image_animation', '请选择静态截图，不支持动图。', 415)
            w, h = original.size
            if w * h > MAX_IMAGE_PIXELS or max(w, h) > MAX_IMAGE_SIDE:
                raise EditorError('image_dimensions', '图片不能超过2000万像素或单边30000像素。', 413)
            image = ImageOps.exif_transpose(original)
            if min(image.size) < 64 or max(image.size) < 280:
                raise EditorError('image_resolution', '图片过小，请使用清晰完整的编队截图或一图流。', 422)
            rgba = image.convert('RGBA')
            background = Image.new('RGBA', rgba.size, 'white')
            image = Image.alpha_composite(background, rgba).convert('RGB')
            return np.array(image)[:, :, ::-1].copy()
    except EditorError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise EditorError('image_decode', '图片损坏或无法解码，请重新上传 PNG、JPG 或 WebP 截图。', 415) from exc


def locate_panel(image):
    """Find the six-card light frame; fit uniformly, independent of screen borders."""
    import cv2
    import numpy as np
    h, w = image.shape[:2]
    ratio = min(1.0, 1600 / w, 1000 / h)
    small = cv2.resize(image, (round(w * ratio), round(h * ratio)), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 0, 165]), np.array([179, 65, 255]))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        if cw > small.shape[1] * .45 and ch > small.shape[0] * .22 and 2.05 < cw / ch < 3.35:
            candidates.append((cw * ch, (x / ratio, y / ratio, cw / ratio, ch / ratio)))
    if not candidates:
        raise EditorError('image_layout', '未找到完整的六人编队面板，请使用游戏编队确认页截图，保留六个槽位和礼装区域。', 422)
    candidates.sort(reverse=True)
    x, y, cw, ch = candidates[0][1]
    scale = 1246 / cw
    # White frame geometry, not a screenshot-specific resolution or crop offset.
    matrix = np.float32([[scale, 0, 22 - x * scale], [0, scale, 90 - y * scale]])
    normalized = cv2.warpAffine(image, matrix, (1280, 720), flags=cv2.INTER_AREA,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    return normalized, {'panel': [round(v) for v in (x, y, cw, ch)], 'scale': scale}


class ScreenshotIndex:
    def __init__(self, root):
        import importlib.util
        import re
        import sys
        import cv2
        import numpy as np
        from .catalog import _ALIASES
        self.root = Path(root)
        image_root = self.root / 'assets/resource/base/image'
        if not image_root.is_dir():
            image_root = self.root / 'resource/base/image'
        if not image_root.is_dir():
            raise EditorError('image_resources', '编队识别资源缺失，请使用完整的桌面资源包。', 503)
        spec = importlib.util.spec_from_file_location('_editor_formation_candidates',
                    Path(__file__).resolve().parent.parent / 'battle/perception/formation_candidates.py')
        module = sys.modules.get(spec.name)
        if module is None:
            module = importlib.util.module_from_spec(spec);sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        self.index_type = module.FormationCandidateIndex
        records = _ALIASES.canonical_servants(json.loads(
            (self.root / 'agent/custom/servant_list.json').read_text(encoding='utf-8-sig'))['servants'])
        self.records = {str(row['id']): row for row in records}
        owners, explicit = {}, {}
        for sid, row in self.records.items():
            for identity in {sid, *_ALIASES.servant_aliases(row)}:
                owners.setdefault(str(identity)[:-2], set()).add(sid)
            for name in row.get('images', []): explicit.setdefault(name, set()).add(sid)
        self.servants = {sid: [] for sid in self.records}
        def read(path):
            return cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        for family in ('NarrowFigures', 'servant_face'):
            primary = {sid for sid, values in self.servants.items() if values}
            for path in sorted((image_root / family).glob('f_*.png')):
                match = re.fullmatch(r'f_(\d+)\d{3}d?\.png', path.name, re.I)
                ids = set(explicit.get(path.name, ())) | (owners.get(match[1], set()) if match else set())
                if len(ids) != 1: continue
                sid = next(iter(ids))
                if family == 'servant_face' and sid in primary: continue
                image = read(path)
                if image is not None: self.servants[sid].append((path.name, image))
        self.servants = {sid: rows for sid, rows in self.servants.items() if rows}
        self.crops = {sid: [(name, im[round(im.shape[0]*.15):round(im.shape[0]*.82),
                                      round(im.shape[1]*.08):round(im.shape[1]*.92)]) for name, im in rows]
                      for sid, rows in self.servants.items()}
        self.servant_index = self.index_type(self.servants, (.08, .15, .92, .82))
        self.equips, self.equip_meta = {}, {}
        records = json.loads((self.root / 'agent/custom/equip_list.json').read_text(encoding='utf-8-sig'))['equips']
        for row in records:
            sid = str(row['id']);path = image_root / 'EquipFaces/team' / f'f_{sid}0.png'
            if not path.is_file(): continue
            image = read(path)
            if image is not None:
                self.equips[sid] = [(path.name, image)];self.equip_meta[sid] = row
        if not self.servants or not self.equips:
            raise EditorError('image_resources', '从者或礼装识别模板缺失，请补齐桌面资源包。', 503)
        self.equip_index = self.index_type(self.equips)
        self.support = read(image_root / 'battle/助战标记.png')

    @staticmethod
    def rank(image, roi, templates, ids):
        import cv2
        x, y, w, h = roi;region = image[max(0,y):y+h, max(0,x):x+w]
        ranked = []
        for sid in ids:
            best = None
            for name, template in templates[sid]:
                th, tw = template.shape[:2]
                if th > region.shape[0] or tw > region.shape[1]: continue
                _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))
                if best is None or score > best[0]:
                    best = (float(score), sid, (x+point[0],y+point[1],tw,th),name)
            if best is not None: ranked.append(best)
        return sorted(ranked, key=lambda row: (-row[0],row[1]))

    def recognize(self, raw, *, decoded=None):
        import cv2
        import numpy as np
        started = time.perf_counter()
        original = decode_image(raw) if decoded is None else decoded
        image, layout = locate_panel(original)
        xs = (41,241,440,654,854,1055)
        rois = tuple((x-5,140,197,300) for x in xs)
        pools = self.servant_index.shortlists(image, rois)
        slots = []
        for i, (roi,pool) in enumerate(zip(rois,pools)):
            x,y,w,h = roi
            core = image[y+22:y+100,x+30:x+w-30]
            empty = float(np.mean(np.std(core,axis=(0,1)))) < 23 and float(np.ptp(core.mean(axis=(0,1)))) < 8
            if empty:
                slots.append({'slot':i,'servantId':None,'status':'empty','support':False});continue
            ranked = self.rank(image,roi,self.crops,pool.ids)
            if pool.fallback or not ranked or ranked[0][0] < .86:
                ranked = self.rank(image,roi,self.crops,self.crops)
            best = ranked[0] if ranked else (0,None,None,None)
            margin = best[0]-ranked[1][0] if len(ranked)>1 else 0
            accepted = best[0]>=.86 and margin>=.15
            support = self.rank(image,roi,{'support':[('',self.support)]},['support']) if self.support is not None else []
            slots.append({'slot':i,'servantId':int(best[1]) if accepted else None,
                          'status':'identified' if accepted else 'unknown',
                          'name':self.records[best[1]]['name'] if accepted else None,
                          'score':round(best[0],4),'support':bool(support and support[0][0]>=.8)})
        if not any(slot['status']=='identified' for slot in slots):
            raise EditorError('image_no_servants', '没有可靠识别到从者，请使用清晰的游戏编队确认页截图。', 422)
        # Confirmation layouts have different CE row heights. Search the band,
        # then derive its common top from actual hits rather than hardcoding an offset.
        ce_rois = tuple((x-5,355,197,210) for x in xs)
        pools = self.equip_index.shortlists(image,ce_rois)
        hits = []
        for slot,roi,pool in zip(slots,ce_rois,pools):
            if slot['status']=='empty': continue
            ranked = self.rank(image,roi,self.equips,pool.ids)
            if pool.fallback:
                ranked = self.rank(image,roi,self.equips,self.equips)
            best = ranked[0] if ranked else (0,None,None,None)
            margin = best[0]-ranked[1][0] if len(ranked)>1 else 0
            if best[0]>=.84 and margin>=.15:
                slot.update(equipId=int(best[1]),equipName=self.equip_meta[best[1]]['name'],equipScore=round(best[0],4),equipStatus='identified')
                hits.append(best[2][1])
        common_top = int(np.median(hits)) if hits else 425
        warnings = []
        for slot,x in zip(slots,xs):
            if slot['status']=='empty': slot['equipStatus']='empty'
            elif 'equipId' not in slot:
                area = image[common_top:common_top+65,x+8:x+179]
                gray = cv2.cvtColor(area,cv2.COLOR_BGR2GRAY)
                hsv = cv2.cvtColor(area,cv2.COLOR_BGR2HSV)
                # Without any matched CE there is no verified row position;
                # do not turn an unlocated/unsupported CE into a confirmed empty.
                empty = bool(hits) and float(gray.std())<=35 and float(np.mean(hsv[:,:,1]>60))<=.3
                slot['equipStatus']='empty' if empty else 'unknown'
            if slot['status']=='unknown' or slot['equipStatus']=='unknown':
                warnings.append(f"槽位{slot['slot']+1}有未识别项目，请核对或手动补选。")
        if sum(slot['support'] for slot in slots)>1:
            for slot in slots: slot['support']=False
            warnings.append('助战标记有歧义，请手动指定助战位置。')
        return {'slots':slots,'warnings':warnings,'layout':layout,
                'sourceSize':[original.shape[1],original.shape[0]],
                'elapsedMs':round((time.perf_counter()-started)*1000)}


def recognize_image(raw, root, index=None):
    """Identify visual protocols before loading the full servant/equip index."""
    from .share_image import decode_share_image
    from .chaldea_image import recognize_chaldea_image
    started = time.perf_counter()
    original = decode_image(raw)
    # A damaged MaaFGO packet raises here; never guess characters from it.
    result = decode_share_image(original)
    if result is None:
        result = recognize_chaldea_image(original, root)
    if result is None:
        if index is None:
            index = ScreenshotIndex(root)
        result = index.recognize(raw, decoded=original)
    return result | {'elapsedMs': round((time.perf_counter() - started) * 1000)}, index


def stream_worker(root):
    # Set limits before importing numerical libraries, in this process only.
    os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['MKL_NUM_THREADS']='1'
    import cv2
    cv2.setNumThreads(1)
    index = None
    while True:
        line = sys.stdin.buffer.readline(30)
        if not line: return
        length = int(line)
        if not 0 < length <= MAX_IMAGE_BYTES: return
        raw = sys.stdin.buffer.read(length)
        if len(raw) != length: return
        try:
            result, index = recognize_image(raw, root, index)
            response = {'ok':True,'result':result}
        except EditorError as exc:
            response = {'ok':False,'error':(exc.code,exc.message,exc.status)}
        except Exception:
            response = {'ok':False,'error':('image_recognition','截图识别失败，请换一张完整编队截图重试。',422)}
        sys.stdout.buffer.write(json.dumps(response,ensure_ascii=False,separators=(',',':')).encode('utf-8')+b'\n')
        sys.stdout.buffer.flush()


class ScreenshotWorker:
    """One lazy process, one request at a time, with a bounded lifetime on timeout."""
    def __init__(self, root):
        self.root=str(root);self.lock=threading.Lock();self.process=None;self.responses=None

    def close(self):
        process=self.process
        self.process=None
        self.responses=None
        if process is None: return
        try:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=3)
            else:
                process.wait()
        except (OSError, subprocess.TimeoutExpired):
            # Cleanup must not replace the recognition failure. Try one final
            # owned-process kill, then release both pipes independently.
            try:
                if process.poll() is None:
                    process.kill();process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        finally:
            for stream in (process.stdin,process.stdout):
                if stream is not None:
                    try: stream.close()
                    except (OSError, ValueError): pass

    def recognize(self, raw, timeout=45):
        if not self.lock.acquire(blocking=False):
            raise EditorError('image_busy','已有截图正在识别，请稍候。',409)
        try:
            if self.process is None or self.process.poll() is not None:
                self.close()
                entry='import sys;sys.path.insert(0,sys.argv[1]);from battle_editor.screenshot_import import stream_worker;stream_worker(sys.argv[2])'
                self.process=subprocess.Popen([sys.executable,'-u','-c',entry,str(Path(__file__).resolve().parent.parent),self.root],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                    env=os.environ|{'PYTHONUTF8':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'})
                self.responses=queue.Queue(maxsize=1)
                def receive(process, responses):
                    try:
                        while True:
                            line=process.stdout.readline(MAX_JSON_BYTES + 1024*1024)
                            if not line: break
                            responses.put(json.loads(line))
                    except (OSError, ValueError): pass
                    finally:
                        try: responses.put_nowait(None)
                        except queue.Full: pass
                threading.Thread(target=receive,args=(self.process,self.responses),daemon=True,name='editor-image-response').start()
            self.process.stdin.write(str(len(raw)).encode()+b'\n'+raw);self.process.stdin.flush()
            try: result=self.responses.get(timeout=timeout)
            except queue.Empty:
                self.close();raise EditorError('image_timeout','截图识别超时，请换一张更清晰的截图重试。',408)
            if result is None: raise EOFError()
            if not result['ok']: raise EditorError(*result['error'])
            return result['result']
        except (EOFError, OSError) as exc:
            self.close();raise EditorError('image_worker','识别进程已结束，请重试。',503) from exc
        finally:
            self.lock.release()
