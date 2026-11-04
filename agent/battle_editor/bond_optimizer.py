"""Offline, non-mutating adapter for the project's existing bond planner.

No Custom Action, device connection, network, storage write or Maa import belongs
here. The editor sends a snapshot and commits its returned tree as one edit.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

from .storage import EditorError, within

_name = '_maafgo_editor_bond_matcher'
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, Path(__file__).resolve().parents[1] / 'custom/bond_matcher.py')
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _module
    _spec.loader.exec_module(_module)
matcher = sys.modules[_name]

DEFAULT_OPTIONS = {'costLimit':117, 'bondBase':1200, 'preferredRarity':5,
                   'ownedServantsOnly':True, 'ownedEquipsOnly':True,
                   'modifyEquips':False, 'confirmedMlbOnly':False,
                   'reservedSupportSlot':None, 'excludeServantIds':[], 'currentCost':None}


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise EditorError('bond_option_invalid', f'{label}必须是{minimum}～{maximum}的整数')
    return value


def _id(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def _json(root, relative):
    path = within(root / relative, root)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f'文件缺失或过大：{relative}')
    return json.loads(path.read_text('utf-8-sig'))


def _inventory(root, kind):
    try:
        document = _json(root, f'config/Inventory/player_{kind}.json')
        if (not isinstance(document, dict) or document.get('schema_version') != 1
                or document.get('kind') != kind or document.get('scan_complete') is not True):
            raise ValueError('需要完成扫描的schema v1本地库')
        rows = document.get(kind)
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError('对象数组无效')
        ids = [_id(row.get('id')) if isinstance(row, dict) else None for row in rows]
        if None in ids or len(set(ids)) != len(ids) or type(document.get('count')) is not int or document['count'] != len(ids):
            raise ValueError('ID或count校验失败')
        return {'available':True, 'count':len(ids), 'generatedAt':document.get('generated_at'),
                'catalogComplete':document.get('catalog_complete') is not False,
                'mlbKnown':all(row.get('limit_break_known') is True for row in rows) if kind == 'equips' else None}, {i:row for i,row in zip(ids,rows)}
    except (OSError, ValueError, UnicodeError, EditorError) as error:
        return {'available':False, 'count':0, 'message':str(error)}, {}


def _requires_mlb(equip):
    return any(isinstance(e, dict) and e.get('strength_status') == 99
               for e in (equip.get('bond') or {}).get('atlas_effects') or [])


class BondPlanner:
    def __init__(self, project_root, catalog=None):
        self.root = Path(project_root).resolve()
        if catalog is None:
            from .catalog import build_catalog
            catalog = build_catalog(self.root)
        self.catalog = catalog
        self.public = {s['id']:s for s in catalog.get('servants', [])}
        self.canonical = {int(alias):s['id'] for s in self.public.values()
                          for alias in [s['id'], *(s.get('aliases') or [])] if _id(alias)}
        try:
            costs = _json(self.root, 'agent/custom/bond_completion_costs.json')
            if not isinstance(costs, dict): raise ValueError('COST索引无效')
            self.servants = self._database('servant', costs.get('servants') or {})
            self.equips = self._database('equip', costs.get('equips') or {})
        except (OSError, ValueError, UnicodeError, EditorError) as error:
            raise EditorError('bond_data_missing', f'羁绊规划资料不可用：{error}', 503) from error
        self.servant_status, self.owned_servants = _inventory(self.root, 'servants')
        self.equip_status, self.owned_equips = _inventory(self.root, 'equips')
        # The UI has additional atlas thumbnails which the recognition catalog
        # may not yet know. Keep their known COST without inventing bond effects.
        public_equips = {e['id']:e for e in catalog.get('equips',[])}
        for key,cost in (costs.get('equips') or {}).items():
            eid = _id(key)
            if eid and eid not in self.equips and type(cost) is int and 0 <= cost <= 100:
                self.equips[eid] = {'id':str(eid),'cost':cost,'name':public_equips.get(eid,{}).get('name',f'#{eid}'),
                                    'bond':{'bonus_type':'none','bonus':0},'_bond_unknown':True}

    def _database(self, kind, costs):
        document = _json(self.root, f'agent/custom/{kind}_list.json')
        rows = document.get(kind + 's') if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise ValueError(f'{kind}目录无效')
        result = {}
        for row in rows:
            identity = _id(row.get('id')) if isinstance(row, dict) else None
            cost = costs.get(str(identity))
            if identity and type(cost) is int and 0 <= cost <= 100:
                result[identity] = {**copy.deepcopy(row), 'id':str(identity), 'cost':cost}
        return result

    def settings(self):
        return {'defaults':copy.deepcopy(DEFAULT_OPTIONS),
                'inventory':{'servants':self.servant_status, 'equips':self.equip_status},
                'method':'工程原有星级优先、从者排序与礼装束搜索；按普通羁绊公式估算'}

    def _templates(self, item, kind):
        images = [name for name in item.get('images') or [] if isinstance(name, str) and Path(name).name == name]
        roots = [self.root / 'assets/resource/base/image', self.root / 'resource/base/image']
        folders = [('servant_face', 'ServantFaces')] if kind == 'servant' else [('EquipFaces/list',), ('EquipFaces/team',)]
        return bool(images) and all(any(within(base / folder / name, self.root).is_file()
                                        for base in roots for folder in alternatives for name in images)
                                   for alternatives in folders)

    def optimize(self, data, supplied=None):
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULT_OPTIONS):
            raise EditorError('bond_option_invalid', '羁绊选项格式无效')
        options = copy.deepcopy(DEFAULT_OPTIONS) | supplied
        for key, label, low, high in (('costLimit','队伍COST上限',1,300), ('bondBase','基础羁绊',1,1000000), ('preferredRarity','优先星级',0,5)):
            _integer(options[key], label, low, high)
        for key in ('ownedServantsOnly','ownedEquipsOnly','modifyEquips','confirmedMlbOnly'):
            if type(options[key]) is not bool:
                raise EditorError('bond_option_invalid', f'{key}必须是开关值')
        reserved = options['reservedSupportSlot']
        if reserved is not None:
            _integer(reserved, '预留助战槽位', 0, 5)
        if options['currentCost'] is not None:
            _integer(options['currentCost'], '已有编队实际COST', 0, 1000)
        excluded = options['excludeServantIds']
        if not isinstance(excluded, list) or len(excluded) > 1000 or any(type(i) is not int or i <= 0 for i in excluded):
            raise EditorError('bond_option_invalid', '排除从者需要有效的ID列表')
        if options['ownedServantsOnly'] and not self.servant_status['available']:
            raise EditorError('bond_inventory_unavailable', '本地从者库不可用，请先构建库存或明确选择全部图鉴')
        if options['ownedEquipsOnly'] and not self.equip_status['available']:
            raise EditorError('bond_inventory_unavailable', '本地礼装库不可用，请先构建库存或明确选择全部图鉴')
        if not isinstance(data, dict) or not isinstance(data.get('team'), dict):
            raise EditorError('bond_team_invalid', '需要有效的当前编队')
        before = copy.deepcopy(data)
        result = copy.deepcopy(data)
        roster = []
        for key in ('onFieldSvts','backupSvts'):
            rows = result['team'].get(key)
            if rows is None: rows = []
            if not isinstance(rows, list) or len(rows) > 3 or any(x is not None and not isinstance(x, dict) for x in rows):
                raise EditorError('bond_team_invalid', '编队必须是六个有效槽位')
            if any(m is not None and (
                (_id(m.get('svtId')) is None and not (
                    m.get('supportType') == 'friend' and (m.get('svtId') is None
                    or type(m.get('svtId')) is int and m['svtId'] == 0)))
                or m.get('supportType') not in (None,'none','friend','npc','fixed')) for m in rows):
                raise EditorError('bond_team_invalid', '已有成员的从者ID或类型无效，请先修正编队')
            roster.extend([*rows, *([None] * (3-len(rows)))])
        supports = [i for i,m in enumerate(roster) if m and m.get('supportType') in ('friend','npc','fixed')]
        if len(supports) > 1:
            raise EditorError('bond_team_invalid', '编队只能包含一个助战槽位')
        if supports:
            reserved = supports[0]
        elif reserved is None:
            reserved = next((i for i in range(5,-1,-1) if not roster[i] or not roster[i].get('svtId')), None)
        if reserved is None or (not supports and roster[reserved] and roster[reserved].get('svtId')):
            raise EditorError('bond_support_required', '请先保留一个空位或指定助战；最多规划五名自有从者')
        warnings = ['按工程当前算法进行有界优化，羁绊结果为估算；不包含助战礼装、活动加成或实时满羁绊状态。']
        if not supports:
            warnings.append(f'第{reserved+1}槽保持空位，供后续手动选择助战。')
        if not options['ownedServantsOnly'] or not options['ownedEquipsOnly']:
            warnings.append('本次包含全图鉴候选，请核对实际持有。')
        for name,status in (('从者',self.servant_status), ('礼装',self.equip_status)):
            if status.get('available') and status.get('catalogComplete') is False:
                warnings.append(f'本地{name}库可能遗漏缺模板对象，优化范围限于已记录持有。')
        has_grand = any(m and m.get('grandSvt') for i,m in enumerate(roster) if i != reserved)
        if has_grand:
            if options['currentCost'] is None:
                raise EditorError('bond_grand_cost_required', '冠位三礼装保持不变，请填写已有编队实际COST后规划')
            warnings.append('冠位从者及三礼装保持；COST从填写的实际值起算，冠位特殊羁绊效果未模拟。')
        elif options['currentCost'] is not None:
            raise EditorError('bond_option_invalid', '普通编队自动计算COST，不使用冠位实际COST覆盖')

        own, fixed, original_equips, equip_slots = [], [], [], []
        locked_equip_ids = set()
        current_cost = 0
        used_ids = set()
        changes = []
        def identity(sid): return self.canonical.get(sid, sid)
        def servant(sid, slot):
            record = self.servants.get(sid) or self.servants.get(identity(sid))
            if record is None:
                raise EditorError('bond_cost_unknown', f'槽位{slot+1}从者#{sid}缺少COST资料')
            return {**record, 'slot':slot}
        def equip(eid, slot, stored):
            raw = self.equips.get(eid)
            if raw is None:
                raise EditorError('bond_cost_unknown', f'槽位{slot+1}礼装#{eid}缺少COST资料')
            item = copy.deepcopy(raw)
            if item.get('_bond_unknown'):
                warnings.append(f'槽位{slot+1}礼装#{eid}缺少常驻羁绊公式，计入COST并按零加成保守估算。')
            if matcher.equip_is_permanent_bond(item) and _requires_mlb(item) and stored.get('limitBreak') is not True:
                base = [e for e in item['bond'].get('atlas_effects') or [] if isinstance(e, dict) and e.get('strength_status') == 1]
                field = 'percent_bonus' if item['bond']['bonus_type'] == 'percent' else 'flat_bonus'
                item['bond']['bonus'] = max((float(e.get(field) or 0) for e in base), default=0)
                warnings.append(f'槽位{slot+1}礼装未要求满破，按未满破/已知保守效果估算。')
            return item
        for slot, member in enumerate(roster):
            if slot == reserved or not member or not member.get('svtId'):
                continue
            sid = _id(member.get('svtId'))
            if sid is None:
                raise EditorError('bond_team_invalid', f'槽位{slot+1}从者ID无效')
            if identity(sid) in used_ids:
                raise EditorError('bond_duplicate_servant', '已有编队包含重复自有从者，请先检查成员类型')
            used_ids.add(identity(sid))
            row = servant(sid,slot)
            own.append(row)
            current_cost += row['cost']
            if row.get('bond', {}).get('tags_by_ascension') or row.get('bond', {}).get('tags_by_costume'):
                warnings.append('存在灵基/灵衣条件，沿用工程最高可能匹配加成；请核对实际形态。')
            keys = ('equip1','equip2','equip3') if member.get('grandSvt') else ('equip1',)
            for key in keys:
                stored = member.get(key) or {}
                if not isinstance(stored, dict):
                    raise EditorError('bond_team_invalid', '礼装字段必须是对象')
                eid = _id(stored.get('id'))
                if not eid and key == 'equip1':
                    eid = _id(member.get('ceId'))
                    if eid: stored = {'limitBreak':member.get('ceLimitBreak')}
                existing = equip(eid,slot,stored) if eid else None
                if existing:
                    current_cost += existing['cost']
                    if matcher.equip_is_permanent_bond(existing): original_equips.append(existing)
                locked = bool(member.get('grandSvt')) or not options['modifyEquips']
                if locked and eid:
                    locked_equip_ids.add(eid)
                    if matcher.equip_is_permanent_bond(existing): fixed.append(existing)
                elif not member.get('grandSvt'):
                    equip_slots.append(slot)
        if len(own) > 5:
            raise EditorError('bond_team_invalid', '最多规划五名自有从者')
        if options['currentCost'] is not None:
            current_cost = options['currentCost']
        baseline_cost = current_cost
        baseline_score = matcher.team_bond_score(own, original_equips, options['bondBase'])

        # Releasing an ordinary editable CE also clears legacy CE aliases. Other
        # member properties and the complete action tree remain untouched.
        equip_extras = {}
        for slot in equip_slots:
            member = roster[slot]
            equip_extras[slot] = {key:value for key,value in (member.get('equip1') or {}).items()
                                  if key not in ('id','lv','limitBreak')}
            eid = _id((member.get('equip1') or {}).get('id')) or _id(member.get('ceId'))
            if eid:
                current_cost -= self.equips[eid]['cost']
                for key in ('equip1','ceId','ceLimitBreak','ceLv'): member.pop(key,None)
                if equip_extras[slot]: member['equip1'] = copy.deepcopy(equip_extras[slot])
        if current_cost < 0:
            raise EditorError('bond_cost_invalid', '填写的实际COST小于可释放礼装COST，请检查')
        if current_cost > options['costLimit']:
            raise EditorError('bond_cost_exceeded', f'保留部分COST为{current_cost}，已超过上限{options["costLimit"]}')

        equip_candidates = [item for eid,item in sorted(self.equips.items())
                            if eid not in locked_equip_ids and matcher.equip_is_permanent_bond(item) and self._templates(item,'equip')
                            and (not options['ownedEquipsOnly'] or eid in self.owned_equips)
                            and (not options['confirmedMlbOnly'] or not _requires_mlb(item)
                                 or (self.owned_equips.get(eid,{}).get('limit_break_known') is True
                                     and self.owned_equips[eid].get('limit_break') is True))]
        if not equip_candidates:
            warnings.append('当前范围没有可用的常驻羁绊礼装候选；若库存已过时，请重新构建或明确取消仅持有限制；礼装仍限工程可识别范围。')
        if any(_requires_mlb(e) for e in equip_candidates):
            warnings.append('新增羁绊礼装按满破效果规划，并写入满破要求；库存未知满破状态时需在游戏中确认。')
        candidates = [s for sid,s in self.servants.items() if sid in self.public and s.get('bond')
                      and self._templates(s,'servant')
                      and (not options['ownedServantsOnly'] or any(self.canonical.get(i,i) == sid for i in self.owned_servants))
                      and sid not in {identity(i) for i in excluded}]
        fill_slots = [i for i,m in enumerate(roster) if i != reserved and (not m or not m.get('svtId'))]
        added = 0
        for slot in fill_slots:
            if len(own) >= 5: break
            chosen = None
            for rarity in matcher.rarity_order(options['preferredRarity']):
                # Equal COST/effect vectors are exact score ties. Keep the same
                # deterministic ID winner instead of re-running beam search for
                # hundreds of servants with identical relevant traits.
                groups = {}
                for row in sorted(candidates,key=lambda s:str(s['id'])):
                    sid = int(row['id'])
                    if identity(sid) in used_ids or row.get('rarity') != rarity or row['cost'] > options['costLimit']-current_cost:
                        continue
                    key = (row['cost'], tuple(matcher._best_percent(e,row) for e in [*fixed,*equip_candidates]))
                    groups.setdefault(key,{**row,'slot':slot})
                ranked = matcher.rank_servants(list(groups.values()), own, fixed, equip_candidates,
                                               len(equip_slots)+1, options['costLimit']-current_cost, options['bondBase'])
                if ranked:
                    chosen = ranked[0]['servant']
                    break
            if chosen is None: continue
            sid = int(chosen['id'])
            member = copy.deepcopy(roster[slot] or {})
            member.update({'svtId':sid,'supportType':'none','skillLvs':[10,10,10],'appendLvs':[0,0,0,0,0]})
            roster[slot] = member
            own.append(chosen)
            used_ids.add(identity(sid))
            current_cost += chosen['cost']
            equip_slots.append(slot)
            added += 1
        plan = matcher.optimize_equips(own,fixed,equip_candidates,len(equip_slots),options['costLimit']-current_cost,options['bondBase'])
        for slot,row in zip(equip_slots,plan.equips):
            roster[slot]['equip1'] = {**equip_extras.get(slot,{}),'id':int(row['id']), 'limitBreak':_requires_mlb(row)}
        current_cost += plan.cost
        if plan.score < baseline_score and baseline_cost <= options['costLimit']:
            result = before
            current_cost, score, added = baseline_cost, baseline_score, 0
            warnings.append('未得到更高的估算收益，已保留原编队。')
        else:
            score = plan.score
            for slot,member in enumerate(roster):
                if slot == reserved: continue
                original = (before['team'].get('onFieldSvts' if slot < 3 else 'backupSvts') or [])[slot%3:slot%3+1]
                if member != (original[0] if original else None):
                    key = 'onFieldSvts' if slot < 3 else 'backupSvts'
                    result['team'].setdefault(key,[])
                    if result['team'][key] is None: result['team'][key] = []
                    while len(result['team'][key]) <= slot%3: result['team'][key].append(None)
                    result['team'][key][slot%3] = member
                    changes.append({'slot':slot,'servantId':member.get('svtId') if member else None,
                                    'name':self.public.get(identity(member.get('svtId')),{}).get('name') if member else None})
        final_roster = [*((result['team'].get('onFieldSvts') or [])+[None]*3)[:3],
                        *((result['team'].get('backupSvts') or [])+[None]*3)[:3]]
        remaining_slots = sum(not m or not m.get('svtId') for i,m in enumerate(final_roster) if i != reserved)
        if remaining_slots: warnings.append(f'仍有{remaining_slots}个可补槽位，当前COST或候选范围内未找到合适从者。')
        if not changes: warnings.append('当前条件下没有需要应用的编队变更。')
        return {'data':result,'summary':{'beforeCost':baseline_cost,'afterCost':current_cost,'costLimit':options['costLimit'],
                'beforeBond':baseline_score,'afterBond':score,'gain':score-baseline_score,'addedServants':added,
                'changedSlots':len(changes),'reservedSupportSlot':reserved,
                'candidateServants':len(candidates),'candidateEquips':len(equip_candidates),'method':'星级优先与有界束搜索'},
                'changes':changes,'warnings':list(dict.fromkeys(warnings))}
