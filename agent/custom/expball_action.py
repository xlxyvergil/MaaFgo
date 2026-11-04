"""搓丸子业务编排。识别、预览、点击与转场均调用具名 Pipeline。"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import traceback

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

import mfaalog
from expball_policy import (CardObservation, ExpballConfig, ExpballConfigError,
                            material_allowed, normalize_name, parse_targets,
                            read_level, read_number)


class ExpballFailure(RuntimeError):
    pass


class ExpballStopped(ExpballFailure):
    pass


def merge(base, patch):
    result = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def edge_names(node):
    return [re.sub(r"^\[[^]]+\]", "", edge if isinstance(edge, str) else edge["name"])
            for edge in node.get("next", [])]


def result_texts(detail):
    if detail is None or not detail.hit:
        return []
    return [r.text for r in detail.filtered_results if getattr(r, "text", "").strip()]


class ExpballRunner:
    def __init__(self, context, attach, *, catalog=None):
        self.ctx = context
        self.controller = context.tasker.controller
        self.config = ExpballConfig.parse(attach)
        self.targets = parse_targets(attach)
        self.catalog = catalog or json.loads(Path(__file__).with_name("expball_catalog.json").read_text("utf-8"))
        self.equips = {str(e["id"]): e for e in self.catalog["equips"]}
        self.servants = {str(e["id"]): e for e in self.catalog["servants"]}
        self.completed = self.batches = self.material_batches = self.locked = 0
        self.sold = self.stored = 0
        self.protected = {t.identity for t in self.targets if t.kind == "equip"}
        self.reason = "not_started"
        # 同一Agent可接收多个用户任务，不能把上一任务的网络中止标记带入新任务。
        for name in ("搓丸子网络-重试", "搓丸子网络-重试耗尽"):
            self.ctx.clear_hit_count(name)
        for target in self.targets:
            entry = (self.servants if target.kind == "servant" else self.equips).get(target.identity)
            if not entry or not entry.get("images"):
                raise ExpballConfigError(f"保留对象{target.identity}缺少当前可用识别图，不能开始自动抽取")

    def check_stop(self):
        if self.ctx.get_hit_count("搓丸子网络-重试耗尽"):
            raise ExpballFailure("网络重试次数用尽，已停止；未重新提交消耗操作")
        if self.ctx.tasker.stopping:
            raise ExpballStopped("用户已停止")

    def frame(self):
        self.check_stop()
        image = self.controller.post_screencap().wait().get()
        if image is None:
            raise ExpballFailure("截图失败")
        return image.copy()

    def reco(self, name, *, image=None, override=None):
        self.check_stop()
        result = self.ctx.run_recognition(name, image if image is not None else self.frame(),
                                          pipeline_override=override or {})
        self.check_stop()
        return result if result and result.hit else None

    def _graph(self, entry, overrides):
        pending, seen = [entry], set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            node = self.ctx.get_node_data(name)
            if node is None:
                raise ExpballFailure(f"缺少流程节点：{name}")
            node = merge(node, overrides.get(name, {}))
            pending.extend(edge_names(node))
        return seen

    def task(self, entry, terminals, override=None):
        """局部覆盖隔离旧任务，清本次计数，必须命中且只命中一个终点。"""
        self.check_stop()
        data = self.ctx.get_node_data(entry) or {}
        attach = data.get("attach", {})
        overrides = merge(attach.get("navigation_override", attach.get("ce_navigation_override", {})),
                          override or {})
        for terminal in terminals:
            overrides[terminal] = merge(overrides.get(terminal, {}), {"next": []})
        nodes = self._graph(entry, overrides)
        for name in nodes | set(terminals):
            self.ctx.clear_hit_count(name)
            overrides[name] = merge(overrides.get(name, {}), {"on_error": []})
        detail = self.ctx.run_task(entry, pipeline_override=overrides)
        self.check_stop()
        if detail is None or not detail.status.succeeded:
            raise ExpballFailure(f"流程未完成：{entry}")
        hits = [name for name in terminals if self.ctx.get_hit_count(name)]
        if len(hits) != 1:
            raise ExpballFailure(f"流程结束状态不明确：{entry}")
        terminal = hits[0]
        if terminal.endswith("识别失败"):
            raise ExpballFailure(f"界面未识别，已停止：{entry}")
        return terminal

    def _friend_overrides(self):
        result = {
            "每日免费友情-召唤": {"next": ["友情召唤-寻找友情池"]},
            "每日免费友情-菜单": {"next": ["每日免费友情-召唤"]},
            "友情召唤-翻到下一个卡池": {"action": {"param": {
                "target": [1235, 329, 33, 66] if self.config.direction == "right" else [5, 329, 33, 66]}}},
        }
        if self.config.draw_batch == 100:
            node = self.ctx.get_node_data("友情召唤-导航到友情池") or {}
            batch = node.get("attach", {}).get("batch_100_override", {})
            target = batch.get("友情召唤-点击抽取", {}).get("action", {}).get("param", {}).get("target")
            expected = [batch.get(name, {}).get("recognition", {}).get("param", {}).get("expected")
                        for name in ("友情召唤-确认使用友情点", "友情召唤-抽取数量确认")]
            if not target or any(not patterns or not all(isinstance(p, str) for p in patterns)
                                 for patterns in expected):
                raise ExpballFailure("缺少100抽按钮、友情点消耗文案或抽数配置，未开始导航")
            result = merge(result, batch)
        return result

    def _free_friend_overrides(self):
        node = self.ctx.get_node_data("友情召唤-导航到友情池") or {}
        free = node.get("attach", {}).get("free_10_override", {})
        click = free.get("友情召唤-点击抽取", {})
        guard = click.get("recognition", {}).get("param", {}).get("all_of")
        target = click.get("action", {}).get("param", {}).get("target")
        default = (self.ctx.get_node_data("友情召唤-点击抽取") or {}).get("action", {}).get("param", {}).get("target")
        waits = free.get("友情召唤-等待抽取确认", {}).get("next", [])
        authority, submit = "友情召唤-已验证免费十连授权", "友情召唤-免费确认安全后提交"
        forbidden = "友情召唤-确认出现圣晶石或呼符"
        if (guard != ["友情召唤-免费十连可用"] or not target or target != default
                or free.get(authority, {}).get("enabled") is not True
                or free.get(submit, {}).get("enabled") is not True
                or forbidden not in waits or authority not in waits
                or waits.index(forbidden) >= waits.index(authority)):
            raise ExpballFailure("缺少严格免费十连授权配置，未提交召唤")
        return deepcopy(free)

    def summon_once(self):
        overrides = self._friend_overrides()
        self.task("友情召唤-导航到友情池", ["友情召唤-已到友情池", "友情召唤-识别失败"], overrides)
        free = bool(self.reco("友情召唤-免费十连可用"))
        if free:
            # 只有友情池身份与免费十连按钮已联合确认，才局部启用免费路径。
            # 清掉100抽的按钮/付费确认补丁，免费轮不复用消耗友情点的规则。
            for name in ("友情召唤-点击抽取", "友情召唤-确认使用友情点", "友情召唤-抽取数量确认"):
                overrides.pop(name, None)
            overrides = merge(overrides, self._free_friend_overrides())
        else:
            texts = result_texts(self.reco("友情召唤-友情点余额"))
            balances = [n for text in texts if (n := read_number(text)) is not None]
            if len(set(balances)) != 1:
                raise ExpballFailure("无法确认友情点余额，未提交召唤")
            if balances[0] - self.config.draw_batch * 200 < self.config.friend_points_reserve:
                return "budget_reached"
        terminals = ["友情召唤-" + suffix for suffix in
                     ("结果就绪", "从者仓满", "礼装仓满", "纹章仓满", "友情点不足", "识别失败")]
        state = self.task("友情召唤-抽取一批", terminals, overrides)
        if state != "友情召唤-结果就绪":
            return {"友情召唤-从者仓满": "servant_full", "友情召唤-礼装仓满": "equip_full",
                    "友情召唤-纹章仓满": "code_full", "友情召唤-友情点不足": "currency_insufficient"}[state]
        self.batches += 1
        self.lock_targets()
        if self.config.stop_targets and all(not target.needed for target in self.targets):
            return "targets_reached"
        self.task("友情召唤-离开结果", ["友情召唤-离开结果完成", "友情召唤-识别失败"])
        return "drawn"

    def lock_targets(self):
        seen = set()
        for target in self.targets:
            entry = (self.servants if target.kind == "servant" else self.equips)[target.identity]
            if not target.needed:
                continue
            images = entry["images"]
            result = self.reco("友情召唤-保留对象候选", override={
                "友情召唤-保留对象候选": {"enabled": True, "recognition": {"param": {
                    "template": images, "roi": [70, 100, 1130, 520]}}}})
            boxes = [list(r.box) for r in result.filtered_results] if result else []
            for box in boxes:
                self.check_stop()
                if not target.needed:
                    break
                location = tuple(round(v / 8) for v in box[:2])
                if location in seen:
                    continue
                seen.add(location)
                names = ["^" + re.escape(n) + "$" for n in (entry.get("name"), entry.get("name_jp")) if n]
                if not names:
                    raise ExpballFailure("保留目标缺少身份名称")
                terminal = self.task("友情召唤-锁定候选", [
                    "友情召唤-锁定完成", "友情召唤-原已锁定", "友情召唤-识别失败"], {
                    "友情召唤-打开候选": {"enabled": True, "action": {"param": {"target": box}}},
                    "友情召唤-候选名称": {"recognition": {"param": {"expected": names}}},
                })
                if terminal == "友情召唤-锁定完成" and target.record():
                    self.locked += 1

    def grid(self):
        node = self.ctx.get_node_data("搓丸子-制作一轮") or {}
        grid = node.get("attach", {}).get("grid", {})
        xs = grid.get("x", [60 + 133 * i for i in range(7)])
        ys = grid.get("y", [183 + 142 * i for i in range(3)])
        width, height = grid.get("w", 128), grid.get("h", 132)
        for y in ys:
            for x in xs:
                if 0 <= x and 0 <= y and x + width <= 1280 and y + height <= 720:
                    yield [x, y, width, height]

    def _has_card(self, box):
        image = self.frame()
        if not self.reco("搓丸子-列表标志", image=image):
            raise ExpballFailure("候选列表身份未确认")
        if self.reco("搓丸子-候选为空", image=image, override={
                "搓丸子-候选为空": {"enabled": True, "recognition": {"param": {"roi": box}}}}):
            return False
        # 空白背景有动画，旧空位模板未命中不能证明存在卡片。
        # 先由 Pipeline 正向确认当前格的等级文字，再允许预览或选择。
        probe = self.ctx.get_node_data("搓丸子-候选等级标志") or {}
        header_roi = probe.get("recognition", {}).get("param", {}).get("roi", [])
        if len(header_roi) != 4 or header_roi[3] <= 0:
            raise ExpballFailure("缺少候选等级识别区域，未预览或选择卡片")
        header = [*box[:3], min(box[3], header_roi[3])]
        return bool(self.reco("搓丸子-候选等级标志", image=image, override={
            "搓丸子-候选等级标志": {"recognition": {"param": {"roi": header}}}}))

    def visible_pages(self, grid=None, *, prefix="搓丸子"):
        """只有 Pipeline 明确检测到底才结束扫描，未知状态和页数上限均报错。"""
        boxes = list(self.grid() if grid is None else grid)
        for index in range(12):
            self.check_stop()
            yield index, boxes
            if self.reco(f"{prefix}-列表到底"):
                return
            self.task(f"{prefix}-下一页", [f"{prefix}-翻页完成", f"{prefix}-识别失败"])
        raise ExpballFailure("列表扫描达到12页上限，不能确认已无更多对象")

    def card_detail(self, kind):
        image = self.frame()
        raw_names = result_texts(self.reco(f"搓丸子-{kind}名称", image=image))
        matches = []
        for equip in self.equips.values():
            names = {normalize_name(n) for n in (equip.get("name"), equip.get("name_jp")) if n}
            if any(normalize_name(text) in names for text in raw_names):
                matches.append(equip)
        if len(matches) != 1:
            raise ExpballFailure(f"无法唯一确认{kind}身份，未提交强化")
        levels = [level for text in result_texts(self.reco(f"搓丸子-{kind}等级", image=image))
                  if (level := read_level(text)) is not None]
        if len(set(levels)) != 1:
            raise ExpballFailure(f"无法确认{kind}等级")
        locked = bool(self.reco("搓丸子-详情已锁定", image=image))
        unlocked = bool(self.reco("搓丸子-详情未锁定", image=image))
        if locked == unlocked:
            raise ExpballFailure(f"无法确认{kind}锁定状态")
        marked = bool(self.reco("搓丸子-详情标记", image=image))
        equip, (current, maximum) = matches[0], levels[0]
        return CardObservation(str(equip["id"]), int(equip["rarity"]), current, maximum, locked, marked)

    def close_card(self):
        self.task("搓丸子-关闭详情", ["搓丸子-详情已关闭", "搓丸子-识别失败"])

    def _preview(self, box, kind="底卡"):
        image = self.frame()
        list_mark = bool(self.reco("搓丸子-卡片已标记", image=image, override={
            "搓丸子-卡片已标记": {"recognition": {"param": {"roi": box}}}}))
        name = "搓丸子-预览底卡" if kind == "底卡" else "搓丸子-预览素材"
        terminal = "搓丸子-底卡详情就绪" if kind == "底卡" else "搓丸子-素材详情就绪"
        self.task(name, [terminal, "搓丸子-识别失败"], {
            name: {"action": {"param": {"target": box}}}})
        try:
            card = self.card_detail(kind)
            return replace(card, marked=card.marked or list_mark)
        finally:
            if not self.ctx.tasker.stopping:
                self.close_card()

    def current_base(self):
        self.task("搓丸子-预览当前底卡", ["搓丸子-底卡详情就绪", "搓丸子-识别失败"])
        try:
            return self.card_detail("底卡")
        finally:
            if not self.ctx.tasker.stopping:
                self.close_card()

    def base_overrides(self):
        override = {}
        if self.config.ball_star == 2:
            override = {
                "搓丸子-底卡只选丸子星级": {"recognition": {"param": {
                    "template": "整理礼物盒/2星未选中.png", "roi": [745, 200, 180, 100]}}},
                "搓丸子-底卡星级已选": {"recognition": {"param": {
                    "template": "搓丸子/2星已选中.png", "roi": [785, 210, 95, 75]}}},
            }
        return override

    def add_network_waits(self, nodes, overrides):
        """给复用任务局部挂接通用Pipeline重试，不改原独立任务的流程。"""
        prefix = ["[JumpBack]搓丸子网络-重试", "搓丸子网络-重试耗尽"]
        for name in nodes:
            if name.startswith("搓丸子网络-"):
                continue
            node = merge(self.ctx.get_node_data(name) or {}, overrides.get(name, {}))
            if node.get("next"):
                edges = [edge for edge in node["next"] if edge not in prefix]
                overrides[name] = merge(overrides.get(name, {}), {"next": prefix + edges})
        return overrides

    def wait_network(self):
        self.task("搓丸子网络-等待恢复", ["搓丸子网络-界面无通信弹窗"])

    def reset_servant_list(self):
        self.task("搓丸子-从者列表复位顶部", ["搓丸子-从者列表复位完成"])

    def enter_servant_select(self):
        self.task("搓丸子-进入从者选择", ["搓丸子-从者选择列表已到"])

    def selected_servant_page_ready(self):
        # 仅本搓丸子的复用实例采用新页面身份；旧独立强化任务保持原判断。
        self.wait_network()
        return bool(self.reco("搓丸子-从者强化已选页面"))

    def find_servant_candidate(self, servant, templates):
        node = self.ctx.get_node_data("搓丸子-从者列表复位顶部") or {}
        extra = node.get("attach", {}).get("servant_templates", {}).get(str(servant["id"]), [])
        images = list(dict.fromkeys([*templates, *extra]))
        self.check_stop()
        result = self.ctx.run_recognition("搓丸子-升级从者头像候选", self.frame(), pipeline_override={
            "搓丸子-升级从者头像候选": {"enabled": True, "recognition": {"param": {
                "template": images, "threshold": 0.90, "roi": [60, 160, 960, 560]}}}})
        self.check_stop()
        all_results = list(getattr(result, "all_results", []) or [])
        best = max(all_results, key=lambda item: item.score, default=None)
        mfaalog.info(f"[搓丸子从者] {servant['name']} Pipeline最高分="
                     f"{getattr(best, 'score', 0):.4f} 阈值=0.90 模板={images}")
        hits = list(getattr(result, "filtered_results", []) or []) if result and result.hit else []
        positions = {tuple(round(v / 8) for v in list(hit.box)[:2]) for hit in hits}
        if len(positions) > 1:
            raise ExpballFailure("升级从者候选不唯一，未选择未知个体")
        if best is None:
            return None
        x, y, w, h = list(best.box)
        return best.score, x + w // 2, y + h // 2, "Pipeline:升级从者头像候选"

    def verify_selected_servant(self, servant):
        self.wait_network()
        names = ["^" + re.escape(name) + "$" for name in (servant.get("name"), servant.get("name_jp")) if name]
        if not names:
            raise ExpballFailure("已选从者名称未确认，不报告定位成功或提交强化")
        image = self.frame()
        if not self.reco("搓丸子-从者强化标题", image=image):
            raise ExpballFailure("已选从者页面身份未确认，不打开未知卡片资料")
        if self.reco("搓丸子-已选从者名称", image=image, override={
            "搓丸子-已选从者名称": {"recognition": {"param": {"expected": names}}}}):
            return
        # 小字在国服可能不可读；资料的大字号姓名须与目标精确匹配，
        # 不放宽旧ROI或阈值。名字不符时保留资料现场，不能继续升级。
        self.task("搓丸子-打开当前从者资料", ["搓丸子-从者资料就绪"])
        if not self.reco("搓丸子-资料从者名称", override={
            "搓丸子-资料从者名称": {"recognition": {"param": {"expected": names}}}}):
            raise ExpballFailure("已选从者名称未确认，已保留资料现场；不报告定位成功或提交强化")
        self.task("搓丸子-关闭当前从者资料", ["搓丸子-从者资料已关闭"])

    def clear_test_servant_materials(self):
        # 只清测试时游戏自动预选的素材，不触碰自动配置开关。
        self.task("搓丸子-清除测试从者素材", ["搓丸子-测试从者素材已清空"])

    def select_servant_candidate(self, servant, candidate):
        _score, x, y, _label = candidate
        image = self.frame()
        if self.reco("搓丸子-候选已是当前从者", image=image, override={
            "搓丸子-候选已是当前从者": {"recognition": {"param": {
                "roi": [max(60, x - 100), min(610, y + 30), 200, min(110, 720 - min(610, y + 30))]}}}}):
            self.task("搓丸子-关闭从者选择", ["搓丸子-关闭从者选择执行"])
        else:
            self.task("搓丸子-点击升级从者候选", ["搓丸子-点击升级从者候选执行"], {
                "搓丸子-点击升级从者候选执行": {"action": {"param": {"target": [x, y, 1, 1]}}}})

    def finish_ball(self, base, *, empty_base=False):
        if empty_base:
            self.task("搓丸子-制作一轮", ["搓丸子-底卡候选就绪", "搓丸子-无底卡", "搓丸子-识别失败"], self.base_overrides())
        else:
            self.task("搓丸子-保护成品", ["搓丸子-成品待标记", "搓丸子-识别失败"])
        self.task("搓丸子-成品列表准备", ["搓丸子-成品列表就绪", "搓丸子-识别失败"])
        matches = []
        last_page = 0
        for page, boxes in self.visible_pages():
            last_page = page
            for box in boxes:
                if not self._has_card(box):
                    continue
                card = self._preview(box)
                if card.identity == base.identity and card.complete and not card.marked:
                    matches.append((page, box, card))
        if len(matches) != 1:
            raise ExpballFailure("无法唯一重定位本轮成品，不标记未知个体")
        page, box, card = matches[0]
        if last_page != page:
            for _ in range(last_page - page):
                self.task("搓丸子-上一页", ["搓丸子-翻页完成", "搓丸子-识别失败"])
            relocated = []
            for current_box in self.grid():
                if self._has_card(current_box):
                    current_card = self._preview(current_box)
                    if current_card.identity == base.identity and current_card.complete and not current_card.marked:
                        relocated.append((current_box, current_card))
            if len(relocated) != 1:
                raise ExpballFailure("成品翻页后无法唯一定位")
            box, card = relocated[0]
        if not card.locked:
            self.task("搓丸子-预览底卡", ["搓丸子-底卡详情就绪", "搓丸子-识别失败"], {
                "搓丸子-预览底卡": {"action": {"param": {"target": box}}}})
            actual = self.card_detail("底卡")
            if actual != card or actual.identity != base.identity or not actual.complete:
                raise ExpballFailure("成品锁定前身份变化")
            self.task("搓丸子-锁定当前礼装", ["搓丸子-半成品保护完成", "搓丸子-识别失败"])
            self.close_card()
        self.task("搓丸子-进入标记模式", ["搓丸子-标记模式就绪", "搓丸子-识别失败"])
        self.task("搓丸子-标记当前候选", ["搓丸子-候选标记完成", "搓丸子-识别失败"], {
            "搓丸子-给候选补标记": {"action": {"param": {"target": box}}},
            "搓丸子-候选已有标记": {"recognition": {"param": {"roi": box}}},
            "搓丸子-候选标记后复查": {"recognition": {"param": {"roi": box}}},
        })
        self.task("搓丸子-退出标记模式", ["搓丸子-标记模式已退出", "搓丸子-识别失败"])
        actual = self._preview(box)
        if actual.identity != base.identity or not actual.complete or not actual.locked or not actual.marked:
            raise ExpballFailure("成品实际锁定和标记未确认")
        self.completed += 1
        if self.config.store_balls:
            self.store_ball(actual)
        return "completed"

    def select_base(self):
        for _attempt in range(2):
            state = self.task("搓丸子-制作一轮", ["搓丸子-底卡候选就绪", "搓丸子-无底卡", "搓丸子-识别失败"], self.base_overrides())
            if state == "搓丸子-无底卡":
                return None
            base = None
            for _page, boxes in self.visible_pages():
                for box in boxes:
                    if not self._has_card(box):
                        continue
                    candidate = self._preview(box)
                    if (candidate.rarity == self.config.ball_star and not candidate.complete
                            and not candidate.locked and not candidate.marked and candidate.current == 1
                            and candidate.identity not in self.protected):
                        base = candidate
                        terminals = ["搓丸子-底卡选定", "搓丸子-底卡已取消", "搓丸子-识别失败"]
                        if self.reco("搓丸子-候选为当前底卡", override={
                                "搓丸子-候选为当前底卡": {"recognition": {"param": {"roi": box}}}}):
                            state = self.task("搓丸子-复用当前底卡", terminals)
                        else:
                            state = self.task("搓丸子-选定底卡", terminals, {
                                "搓丸子-选定底卡": {"enabled": True, "action": {"param": {"target": box}}}})
                        break
                if base is not None:
                    break
            if base is None:
                return None
            if state == "搓丸子-底卡选定":
                return base
        raise ExpballFailure("底卡连续取消选中，已停止；未提交强化")

    def make_one(self):
        base = self.select_base()
        if base is None:
            return "no_base"
        selected_identity = base.identity
        expected_base = base
        for _ in range(60):
            self.check_stop()
            base = self.current_base()
            if (base != expected_base or base.identity != selected_identity or base.locked or base.marked
                    or base.identity in self.protected):
                raise ExpballFailure("当前底卡与本轮选择或保护状态不一致，未提交强化")
            if base.complete:
                return self.finish_ball(base)
            if base.rarity != self.config.ball_star:
                raise ExpballFailure("底卡星级与选择不一致")
            material_overrides = {}
            for star, enabled in ((3, self.config.eat3), (4, self.config.eat4)):
                if enabled:
                    material_overrides[f"搓丸子-材料选择{star}星"] = {"next": [f"搓丸子-材料{star}星已选", f"搓丸子-材料点{star}星"]}
            self.task("搓丸子-打开素材列表", ["搓丸子-素材候选就绪", "搓丸子-素材不足", "搓丸子-识别失败"], material_overrides)
            planned = []
            for _page, boxes in self.visible_pages():
                for box in boxes:
                    if not self._has_card(box):
                        continue
                    candidate = self._preview(box, "材料")
                    if material_allowed(candidate, base, self.config, self.protected):
                        planned.append((box, candidate))
                    if len(planned) >= 20:
                        break
                if planned:
                    break
            if not planned:
                self.task("搓丸子-保护半成品", ["搓丸子-半成品保护完成", "搓丸子-识别失败"])
                protected = self.current_base()
                if protected.identity != base.identity or not protected.locked:
                    raise ExpballFailure("未能确认半成品保护状态")
                return "materials_exhausted"
            for box, planned_card in planned:
                # 列表重排或选择状态变化时取消本批，不能消耗旧坐标对应的新个体。
                actual = self._preview(box, "材料")
                if actual != planned_card or not material_allowed(actual, base, self.config, self.protected):
                    raise ExpballFailure("素材列表发生变化，已停止提交")
                self.task("搓丸子-选择素材", ["搓丸子-选择素材完成", "搓丸子-识别失败"], {
                    "搓丸子-选择素材": {"enabled": True, "action": {"param": {"target": box}}},
                    "搓丸子-素材已选中": {"recognition": {"param": {"roi": box}}},
                })
            high_star = any(card.rarity in (3, 4) for _box, card in planned)
            # 只对本批已逐张核验并选中的实际高星素材授权，不因配置开关直接确认。
            confirm_override = {"搓丸子-高星素材决定确认": {"next": ["搓丸子-高星素材决定授权确认"]}} if high_star else {}
            submit_override = {"搓丸子-高星材料确认": {"next": ["搓丸子-高星材料授权确认"]}} if high_star else {}
            self.task("搓丸子-确认素材", ["搓丸子-素材已选择", "搓丸子-识别失败"], confirm_override)
            state = self.task("搓丸子-提交强化", ["搓丸子-强化一批完成", "搓丸子-成品完成", "搓丸子-QP不足", "搓丸子-识别失败"], submit_override)
            if state == "搓丸子-QP不足":
                return "qp_insufficient"
            if self.reco("搓丸子-当前底卡为空"):
                # 占位不是满级证明；回列表核验已知身份和实际最终等级。
                return self.finish_ball(base, empty_base=True)
            after = self.current_base()
            if after.identity != base.identity or (after.current, after.maximum) <= (base.current, base.maximum):
                raise ExpballFailure("强化后未确认等级或上限增长")
            expected_base = after
            self.material_batches += 1
        raise ExpballFailure("当前丸子达到强化批次上限")

    def run(self):
        # 开发阶段尚未接通的类别必须在任何游戏输入之前拒绝，而不是忽略开关。
        unsupported = [label for key, label in (("sell_servants", "友情从者贩卖"), ("sell_wz", "纹章贩卖"))
                       if getattr(self.config, key)]
        if unsupported:
            raise ExpballConfigError("当前阶段尚未开放：" + "、".join(unsupported))
        if self.config.mode == "test_servant":
            return self.level_servant(test=True)
        if not self.config.draw_fp:
            # 非抽取模式按配置先处理空间，再连续制作，最后升级，不能忽略其他开关。
            for store in (True, False):
                enabled = (self.config.store4 or self.config.store5) if store else (self.config.sell4 or self.config.sell5)
                if enabled:
                    for _ in range(self.config.max_cycles):
                        if not self.cleanup_experience_once(store=store):
                            break
                    else:
                        raise ExpballFailure("经验值卡整理达到循环上限")
        for _ in range(self.config.max_cycles):
            self.check_stop()
            if self.config.draw_fp:
                if self.batches >= self.config.max_draw_batches:
                    self.reason = "batch_limit"
                    return True
                state = self.summon_once()
                if state in ("budget_reached", "targets_reached", "currency_insufficient"):
                    self.reason = state
                    return True
                if state == "drawn":
                    continue
                if not self.recover_capacity(state):
                    raise ExpballFailure("当前配置不能释放所需空间，已停止循环")
            elif self.config.make_ball:
                state = self.make_one()
                if state == "completed":
                    continue
                self.reason = state
                if self.config.levelup:
                    self.level_servant()
                return state in ("no_base", "materials_exhausted", "qp_insufficient")
            else:
                if self.config.levelup:
                    self.level_servant()
                self.reason = "processed"
                return True
        raise ExpballFailure("达到循环上限，未报告全部完成")

    def level_servant(self, test=False):
        self.check_stop()
        from servant_up_action import ExecuteServantUp
        action = ExecuteServantUp()
        # 沿用旧业务但隔离其筛选与错误恢复参数，不污染原强化任务。
        action._context, action._controller = self.ctx.clone(), self.controller
        action._network_check = self.wait_network
        action._list_reset = self.reset_servant_list
        action._enter_select = self.enter_servant_select
        action._in_levelup = self.selected_servant_page_ready
        action._candidate_locator = self.find_servant_candidate
        action._candidate_selector = self.select_servant_candidate
        action._verify_selected = self.verify_selected_servant
        action._face_threshold = 0.90
        action._face_roi = (60, 160, 960, 560)
        if test:
            action._max_find_rounds = 6
        action._init_dirs()
        action._init_scale()
        servant = action._resolve_servant(self.config.level_servant_id)
        if servant is None:
            raise ExpballFailure("未找到配置的升级从者")
        classes = {"saber": "剑士", "archer": "弓兵", "lancer": "枪兵", "rider": "骑兵",
                   "caster": "魔术师", "assassin": "暗杀者", "berserker": "狂战士",
                   "ruler": "裁定者", "avenger": "复仇者", "moonCancer": "月之癌",
                   "alterEgo": "他人格", "foreigner": "降临者", "pretender": "伪装者",
                   "shielder": "盾兵", "beast": "兽", "unBeast": "兽"}
        class_name = classes.get(servant.get("class"))
        if not class_name:
            raise ExpballFailure("升级从者职介无法映射")
        overrides = {
            "强化从者-点菜单进强化": {"recognition": {"param": {
                "roi": [1070, 590, 210, 130], "order_by": "Score"}}},
            "强化从者-筛选职介": {"recognition": {"param": {
                "template": f"强化从者/职介-{class_name}.png"}}},
            "强化从者-筛选星级": {"recognition": {"param": {
                "template": f"整理礼物盒/{servant['rarity']}星未选中.png"}}},
        }
        if servant["rarity"] == 0:
            overrides["强化从者-筛选星级"] = {
                "recognition": {"type": "DirectHit"}, "action": {"type": "DoNothing"}}
        nodes = self._graph("强化从者-导航到强化界面", overrides) | self._graph("强化从者-筛选准备", overrides)
        if not test:
            nodes |= self._graph("强化从者-强化一次", overrides)
        self.add_network_waits(nodes, overrides)
        nodes |= self._graph("强化从者-导航到强化界面", overrides) | self._graph("强化从者-筛选准备", overrides)
        if not test:
            nodes |= self._graph("强化从者-强化一次", overrides)
        for name in nodes:
            overrides[name] = merge(overrides.get(name, {}), {"on_error": []})
            self.ctx.clear_hit_count(name)
        if not action._context.override_pipeline(overrides):
            raise ExpballFailure("升级从者参数未能隔离")
        if test:
            self.wait_network()
            names = ["^" + re.escape(name) + "$" for name in (servant.get("name"), servant.get("name_jp")) if name]
            if names:
                image = self.frame()
                if (self.reco("搓丸子-从者强化标题", image=image)
                        and self.reco("搓丸子-已选从者名称", image=image, override={
                            "搓丸子-已选从者名称": {"recognition": {"param": {"expected": names}}}})):
                    self.clear_test_servant_materials()
                    self.reason = "servant_found"
                    return True
        # 礼装强化和从者强化共用若干图标；先回已核验主界面，再复用旧导航，
        # 避免把CE列表误作从者列表而先改错类别的筛选。目标配置已在上方校验。
        self.return_main()
        if test:
            # 只做既有导航与查找，不调用包含材料提交的升级入口。
            self.task("强化从者-导航到强化界面", ["强化从者-导航完成"], overrides)
            action._enter_servant_select()
            found = action._select_servant(servant)
            self.check_stop()
            if not found:
                raise ExpballFailure("从者选择测试未找到目标")
            self.clear_test_servant_materials()
            self.reason = "servant_found"
            return True
        result = action._levelup_servant(servant, self.config.target_level)
        self.check_stop()
        if result not in ("target_reached", "dogfood_empty", "no_material"):
            raise ExpballFailure(f"从者强化未完成：{result}")
        self.reason = "servant_" + result
        return True

    def recover_capacity(self, kind):
        self.check_stop()
        progress = False
        if kind == "equip_full" and self.config.make_ball:
            before = self.material_batches
            state = self.make_one()
            progress = state == "completed" or self.material_batches > before
        if kind in ("servant_full", "standalone"):
            if self.config.store4 or self.config.store5:
                progress |= self.cleanup_experience_once(store=True)
            if self.config.sell4 or self.config.sell5:
                progress |= self.cleanup_experience_once(store=False)
            if self.config.levelup:
                self.level_servant()
                progress |= self.ctx.get_hit_count("强化从者-强化二次确认") > 0
        return bool(progress)

    def return_main(self):
        self.check_stop()
        self.wait_network()
        image = self.frame()
        if (self.reco("强化从者-已在强化列表", image=image)
                or self.reco("强化从者-已在强化界面", image=image)
                or self.reco("搓丸子-从者强化已选页面", image=image)
                or self.reco("搓丸子-详情判定", image=image)):
            # Android返回键会在强化页和选择列表间往返；沿用现有模板后退。
            # 国服已选从者/礼装页可能不含旧空卡/列表标志；复用各自
            # 正向标题与底部帮助联合身份，不依赖素材槽是否空，且这里只读识别。
            overrides = {
                "强化从者-消耗完毕后退": {"max_hit": 4},
                "强化从者-回到有菜单的界面": {"recognition": {"param": {
                    "roi": [1070, 590, 210, 130], "order_by": "Score"}}},
            }
            nodes = self._graph("强化从者-狗粮消耗完毕", overrides)
            self.add_network_waits(nodes, overrides)
            self.task("强化从者-狗粮消耗完毕", ["强化从者-狗粮消耗完毕结束"], overrides)
        overrides = {
            "无弹窗": {"next": []},
            "主界面判断-返回": {"max_hit": 6},
            "有弹窗返回": {"max_hit": 6},
        }
        nodes = self._graph("回主界面", overrides)
        self.add_network_waits(nodes, overrides)
        self.task("回主界面", ["无弹窗"], overrides)

    def storage_capacity(self):
        node = self.ctx.get_node_data("搓丸子整理-保管室容量") or {}
        semantics = node.get("attach", {}).get("capacity_semantics", "used_total")
        if semantics not in ("used_total", "remaining_total"):
            raise ExpballFailure("保管室容量语义未配置，未提交存储")
        values = []
        for text in result_texts(self.reco("搓丸子整理-保管室容量")):
            match = re.search(r"([\d,]+)\s*/\s*([\d,]+)", text)
            if match:
                used, maximum = (read_number(x) for x in match.groups())
                if used is not None and maximum is not None and 0 <= used <= maximum:
                    values.append(used if semantics == "remaining_total" else maximum - used)
        if len(set(values)) != 1:
            raise ExpballFailure("无法确认保管室容量，未提交存储")
        return values[0]

    def selected_count(self, node="搓丸子整理-已选数量"):
        values = []
        for text in result_texts(self.reco(node)):
            match = re.search(r"([\d,]+)\s*/\s*([\d,]+)", text)
            if match:
                selected, maximum = (read_number(x) for x in match.groups())
                if selected is not None and maximum is not None and 0 <= selected <= maximum:
                    values.append(selected)
        if len(set(values)) != 1:
            raise ExpballFailure("无法确认已选对象总数，未提交整理")
        return values[0]

    def require_selection(self, expected, node="搓丸子整理-已选数量"):
        if self.selected_count(node) != expected:
            raise ExpballFailure(f"已选对象总数不等于{expected}，未提交整理")

    def cleanup_experience_once(self, *, store):
        allowed = {star for star in (4, 5) if getattr(self.config, ("store" if store else "sell") + str(star))}
        if not allowed:
            return False
        if store:
            self.return_main()
            self.task("搓丸子整理-导航保管室", ["搓丸子整理-保管室就绪"])
            self.task("搓丸子整理-保管室切从者", ["搓丸子整理-保管室从者就绪"])
            if not self.storage_capacity():
                raise ExpballFailure("保管室已满")
        else:
            self.task("搓丸子整理-导航贩卖", ["整理礼物盒-导航到灵基变还完成"])
        override = {
            "整理礼物盒-筛卖选3星": {"next": ["整理礼物盒-筛卖选4星"]},
            "整理礼物盒-筛卖选4星": {"next": ["整理礼物盒-筛卖点4星" if 4 in allowed else "整理礼物盒-筛卖选5星"]},
            "整理礼物盒-筛卖选5星": {"next": ["整理礼物盒-筛卖点5星" if 5 in allowed else "整理礼物盒-筛卖找经验值"]},
        }
        if not store:
            filter_node = self.ctx.get_node_data("搓丸子整理-筛选经验卡") or {}
            override = merge(override, filter_node.get("attach", {}).get("sale_filter_override", {}))
        self.task("搓丸子整理-筛选经验卡", ["整理礼物盒-已在灵基图标大小检测"], override)
        count_node = "搓丸子整理-已选数量" if store else "搓丸子整理-贩卖已选数量"
        self.require_selection(0, count_node)
        if self.reco("搓丸子整理-列表无对象"):
            return False
        centers = (self.ctx.get_node_data("搓丸子整理-预览候选") or {}).get("attach", {}).get("grid_centers", [])
        if not centers:
            raise ExpballFailure("缺少整理列表网格，未选择对象")
        names = {4: {"睿智的猛火", "叡智の猛火"}, 5: {"睿智的业火", "叡智の業火"}}
        boxes = [[max(0, x - 60), max(0, y - 65), 120, 130] for x, y in centers]
        for _page, current_boxes in self.visible_pages(boxes, prefix="搓丸子整理"):
            image = self.frame()
            if not self.reco("搓丸子整理-列表就绪", image=image):
                raise ExpballFailure("未确认整理候选列表")
            for box in current_boxes:
                matched = [star for star in allowed if self.reco(
                    f"搓丸子整理-{'四' if star == 4 else '五'}星经验值候选", image=image, override={
                        f"搓丸子整理-{'四' if star == 4 else '五'}星经验值候选": {"recognition": {"param": {"roi": box}}}})]
                if len(matched) != 1:
                    continue
                star = matched[0]
                self.task("搓丸子整理-预览候选", ["搓丸子整理-详情就绪"], {
                    "搓丸子整理-预览候选": {"action": {"param": {"target": box}}}})
                frame = self.frame()
                titles = result_texts(self.reco("搓丸子整理-候选名称", image=frame))
                identity_ok = any(normalize_name(t) in {normalize_name(n) for n in names[star]} for t in titles)
                locked = bool(self.reco("搓丸子整理-候选已锁定", image=frame))
                unlocked = bool(self.reco("搓丸子整理-候选未锁定", image=frame))
                self.task("搓丸子整理-关闭详情", ["搓丸子整理-列表就绪"])
                if locked == unlocked:
                    raise ExpballFailure("整理候选锁定状态不明确")
                if not identity_ok or locked:
                    image = self.frame()
                    continue
                self.task("搓丸子整理-选择候选", ["搓丸子整理-候选已选择"], {
                    "搓丸子整理-选择候选": {"action": {"param": {"target": box}}},
                    "搓丸子整理-候选已选择": {"recognition": {"param": {"roi": [box[0], box[1], box[2], 5]}}},
                })
                self.require_selection(1, count_node)
                if store:
                    terminal = self.task("搓丸子整理-提交存储", ["搓丸子整理-存储已完成", "搓丸子整理-保管室已满"])
                    if terminal.endswith("保管室已满"):
                        raise ExpballFailure("保管室已满")
                    self.stored += 1
                else:
                    aqf = (self.ctx.get_node_data("搓丸子整理-提交贩卖") or {}).get("attach", {}).get("aqf_override", {}) if self.config.aqf else {}
                    terminals = ["搓丸子整理-贩卖已完成"] if self.config.aqf else ["搓丸子整理-贩卖已完成", "搓丸子整理-QP已满"]
                    terminal = self.task("搓丸子整理-提交贩卖", terminals, aqf)
                    if terminal.endswith("QP已满"):
                        raise ExpballFailure("QP已满，按设置停止")
                    self.sold += 1
                return True
        return False

    def store_ball(self, ball):
        self.return_main()
        self.task("搓丸子整理-导航保管室", ["搓丸子整理-保管室就绪"])
        self.task("搓丸子-保管室切礼装", ["搓丸子-保管室礼装就绪", "搓丸子-识别失败"])
        self.require_selection(0)
        if not self.storage_capacity():
            raise ExpballFailure("保管室已满，成品已留在持有列表并锁定标记")
        matches = []
        node = self.ctx.get_node_data("搓丸子-保管室切礼装") or {}
        grid = node.get("attach", {}).get("grid", {})
        boxes = [[x, y, grid.get("w", 123), grid.get("h", 134)]
                 for y in grid.get("y", [181, 323, 465]) for x in grid.get("x", [75, 208, 341, 475, 608, 741, 875])]
        last_page = 0
        for page, current_boxes in self.visible_pages(boxes):
            last_page = page
            for box in current_boxes:
                if not self._has_card(box):
                    continue
                candidate = self._preview(box)
                if candidate.identity == ball.identity and candidate.complete and candidate.locked and candidate.marked:
                    matches.append((page, box))
        if len(matches) != 1:
            raise ExpballFailure("保管室无法唯一确认本轮成品，未存入其他库存")
        page, box = matches[0]
        if last_page != page:
            for _ in range(last_page - page):
                self.task("搓丸子-上一页", ["搓丸子-翻页完成", "搓丸子-识别失败"])
            relocated = []
            for candidate_box in boxes:
                if self._has_card(candidate_box):
                    candidate = self._preview(candidate_box)
                    if candidate.identity == ball.identity and candidate.complete and candidate.locked and candidate.marked:
                        relocated.append(candidate_box)
            if len(relocated) != 1:
                raise ExpballFailure("保管室翻页后成品定位不唯一")
            box = relocated[0]
        actual = self._preview(box)
        if actual != ball or not actual.complete or not actual.locked or not actual.marked:
            raise ExpballFailure("保管室提交前成品身份或保护状态变化")
        self.task("搓丸子整理-选择候选", ["搓丸子整理-候选已选择"], {
            "搓丸子整理-选择候选": {"action": {"param": {"target": box}}},
            "搓丸子整理-候选已选择": {"recognition": {"param": {"roi": [box[0], box[1], box[2], 5]}}},
        })
        self.require_selection(1)
        terminal = self.task("搓丸子-存入成品", ["搓丸子-存储一批完成", "搓丸子-保管室已满", "搓丸子-识别失败"])
        if terminal.endswith("保管室已满"):
            raise ExpballFailure("保管室已满，未确认成品存入")
        self.stored += 1


@AgentServer.custom_action("execute_expball")
class ExecuteExpball(CustomAction):
    def run(self, context, argv):
        runner = None
        try:
            attach = (context.get_node_data(argv.node_name) or {}).get("attach", {})
            runner = ExpballRunner(context, attach)
            result = runner.run()
            mfaalog.info(f"[搓丸子] 结束原因={runner.reason} 成品={runner.completed} "
                         f"召唤批次={runner.batches} 新增锁定={runner.locked}")
            return CustomAction.RunResult(success=result)
        except (ExpballFailure, ExpballConfigError) as exc:
            mfaalog.error(f"[搓丸子] {exc}")
            return CustomAction.RunResult(success=False)
        except Exception:
            mfaalog.error(f"[搓丸子] 异常，已停止\n{traceback.format_exc()}")
            return CustomAction.RunResult(success=False)
