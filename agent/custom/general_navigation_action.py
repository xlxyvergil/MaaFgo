# -*- coding: utf-8 -*-
"""
坐标反推定向导航 Action
地月后事

【雷夫衔枝之年】

天上永恒的王座到来，人类之理为之焕然一新。

然后真王，原初的那一位——咕哒——开始和旧世界的统治者们，七位冠位之座的大王开战。

那恐怖的大王们是法则的具象，是超越了英灵顶点的存在。

原初的那一位造出了自己发着光的影子。

而影子的数量是七。它们被称为Beast。


【咕哒，或者原初的那一位】

原初的那一位，或许是最后的御主。

它手持令咒，身负虚数之锚，从迦勒底亚斯的结晶中诞生，难以分辨其承载的是拯救还是毁灭。

但是世界如果要被创造，虚数之树必须被贯穿。

咕哒——原初的那一位——却用“空想树”的根系隔绝了「剪定事象」和「泛人类史」。


【衔枝后四十余年】

四十个特异点埋葬了火，四十个异闻带沸腾了海。

七位冠位大王全部被打败，七个试炼全部对御主俯首称臣。

原初的那一位开始了“人理”的再定义。

为了“我们”——它最可怜的人儿将出现在这片大地。

【箱舟开门之年】

原初的那一位对人类有一套神圣的规划——“人理定础”。

人只要幸福，它便欢欣。

【箱舟开门的次年】

人们耕耘，第一次收获羁绊。

人们开掘，第一次收获圣晶石。

人们聚集，第一次写就终局特异点的诗篇。

【狂欢节之年】

如果有饥馑，天上就落下魔力与英灵的加护。

如果有贫瘠，那大地就会生出虚数宝藏。

如果有忧郁蔓延，那么迦勒底亚斯就会以声音回应。

唯一的禁止之事，就是输给诱惑。但是冠位时间的神殿已然倒塌。

【葬火之年】

天上的第二个王座到来——那是“天理”的降临，仿佛创世之初的大战再开。

那一天，虚数之树倾颓，英灵之座崩裂。

我们剪定事象之民的先祖，和他们世代栖居的土地，落入了此处。

黑暗的年代由此开始。

【黑暗的元年】

七位大王的子民被虚数之海接纳，深海的隐匿者曾经统治这里。

我们的先祖与它们发生了征战。

先祖使用千灯将它们逐入影子，它们则在影子里狩猎人类。

此处唯有剪定，所以无处不是它们的猎场。

人们的祈祷汇成哀歌，原初的那一位和其他七位发光的影子并不能听见。

【太阳的比喻】

黑暗的洞窟里，有一群未曾见过光的人们在生活。

有一位见过太阳的贤人，对着洞窟的众人描绘着光之下的生活与太阳的伟大。

他见众人无法理解，于是点起了火。

人们于是开始崇拜火，以为这个是太阳，甚至开始习惯了黑暗与火光的生活。

贤人死后，有人霸占了火，通过火，投下了自己巨大的影子。

进图即识别(不做缩放/归位): 截图 -> YOLO检测 -> greedy独占匹配 -> 关卡列表(名称+屏幕中心)
用识别关卡 + 归一化 coords.json(已统一到 2.0 基准) 反推当前视角平移 t:
    t = median(屏幕中心 - SCALE * coords[name]),  SCALE = 2.0
目标屏幕预测位置 P = SCALE * coords[target] + t:
    识别到目标 -> 点击进入
    P 在屏内(距中心 < CLICK_RADIUS) -> 直接点击 P(名称条较宽, t 误差被中位数吸收)
    P 在屏外 -> 按 (屏幕中心 - P) 方向定向滑动(地图跟手), 滑动后重新识别循环

兜底(极端意外情况): 连续空屏 / 滑动无进展 / 目标无坐标 / 特殊关卡
    -> 委托 fallback_navigation.FallbackNavigation(旧方案A: 缩放+归位+全模板匹配)

滑动约定: 地图跟手, 手指沿"目标预测位置 -> 屏幕中心"方向滑动固定 SWIPE_DIST(100px):
    起点 = 屏幕中心朝目标方向 100px 处, 终点 = 屏幕中心, 每轮固定 100px 逼近
    若实测滑动方向相反(目标越滑越远), 调整 SWIPE_SIGN = -1 取反。

流程:
1. 读 attach.quests 目标关卡 + template 推导素材目录 + 归一化 coords
2. 主循环(最多 MAX_ROUNDS 轮):
   a. 截图 -> QuestDetector.detect -> 配对有效名称条框
   b. 裁剪 PAD 外扩 -> greedy 独占匹配 -> 识别关卡列表(名称+屏幕中心+分数)
   c. 目标在屏 -> 点击进入 -> 成功
   d. 识别列表非空 -> 反推 t(中位数, 只统计 coords 中存在的关卡)
   e. 计算目标预测 P, 决定点击/滑动
   f. 空屏 -> 沿上轮方向盲滑; 连续空屏 -> 兜底
   g. 滑动无进展(目标距中心不再缩小) -> 兜底
3. 超轮数/异常 -> 兜底
"""
import os
import json
import time
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
import mfaalog
import fallback_navigation as fb

# 方案B常量
SCALE = 2.0              # 恒定缩放比: 全部地图进图缩放统一 2.0(coords 已归一化)
SCREEN_W, SCREEN_H = 1280, 720
SCREEN_CENTER = np.array([SCREEN_W / 2.0, SCREEN_H / 2.0])
SWIPE_SIGN = 1.0         # 滑动方向符号: 地图跟手=+1; 若实测方向相反改为 -1
SWIPE_DIST = 100         # 固定滑动距离(px): 起点=中心朝目标方向100px处, 向中心滑动
SWIPE_DURATION = 300     # 滑动持续时间(ms)
CLICK_RADIUS = 60        # 目标预测位置距屏幕中心小于此值(px)直接点击预测位置
NO_PROGRESS_LIMIT = 3    # 目标距中心连续不缩小次数 -> 判定滑动无进展 -> 兜底
EMPTY_SCREEN_LIMIT = 3   # 连续空屏(未识别到任何关卡) -> 兜底
MAX_ROUNDS = 40          # 最大识别-滑动轮数


@AgentServer.custom_action("general_navigation")
class GeneralNavigationAction(CustomAction):
    """方案B: 坐标反推定向导航(不缩放), 失败时委托兜底模块"""

    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        mfaalog.info("=" * 50)
        mfaalog.info("[导航] 坐标反推定向导航启动（方案B: 不缩放）")
        try:
            AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ROOT_DIR = os.path.dirname(AGENT_DIR)

            # 步骤1: 参数(固定读取 nav_test.json, 测试用)
            test_cfg_path = os.path.join(AGENT_DIR, "utils", "nav_test.json")
            if not os.path.isfile(test_cfg_path):
                mfaalog.error(f"[导航] 缺少测试参数文件: {test_cfg_path}")
                return CustomAction.RunResult(success=False)
            with open(test_cfg_path, encoding="utf-8") as fp:
                test_cfg = json.load(fp)
            target_quest = test_cfg.get("target_quest", "")
            template = test_cfg.get("template", "")
            resource_package = test_cfg.get("resource_package", "base") or "base"

            if not template:
                mfaalog.error("[导航] nav_test.json 缺少 template")
                return CustomAction.RunResult(success=False)
            if not target_quest:
                mfaalog.error("[导航] nav_test.json 缺少 target_quest")
                return CustomAction.RunResult(success=False)
            mfaalog.info(f"[导航] 测试参数(nav_test.json): quest={target_quest} template={template} pkg={resource_package}")

            # 步骤2: 素材目录 + 归一化坐标表
            quest_dir = fb.resolve_quest_dir(ROOT_DIR, resource_package, template)
            templates = fb.load_quest_templates(quest_dir)

            if target_quest not in templates:
                mfaalog.error(f"[导航] 素材库缺少目标关卡名称条截图: {quest_dir}/{target_quest}.png")
                return CustomAction.RunResult(success=False)
            mfaalog.info(f"[导航] 素材库 {len(templates)} 关")

            folder = os.path.dirname(template.replace("\\", "/")).strip("/")
            coords, _map_h = fb.load_quest_coords(ROOT_DIR, folder)

            # 目标在坐标表中缺失(素材名与 coords 键不一致) -> 兜底
            if not coords or target_quest not in coords:
                mfaalog.error(f"[导航] 目标[{target_quest}]无坐标(coords 键缺失), 转兜底流程")
                ok = fb.FallbackNavigation().run(context, "地图坐标导航")
                return CustomAction.RunResult(success=ok)

            controller = context.tasker.controller
            t_xy = None          # 当前视角平移 (tx, ty)
            last_dir = None      # 最近一次滑动方向(单位向量), 空屏盲滑用
            empty_count = 0      # 连续空屏计数
            no_progress = 0      # 目标距中心连续不缩小计数
            last_dist = None     # 上一轮目标距屏幕中心距离

            # 步骤3: 主循环 - 识别 -> 反推 t -> 定向滑动
            for round_idx in range(MAX_ROUNDS):
                mfaalog.info(f"[导航] === 第{round_idx + 1}轮 ===")
                img = fb.QuestDetector._norm_img(controller.post_screencap().wait().get())
                if img is None:
                    mfaalog.error("[导航] 截图失败")
                    return CustomAction.RunResult(success=False)

                nametags, tags = self._detector().detect(img)
                mfaalog.info(f"[导航] 检测框 {len(nametags)} nametag, {len(tags)} tag")

                # 识别关卡: 裁剪 PAD 外扩 -> greedy 独占匹配 -> (名称, 屏幕中心)
                box_infos, box_crops = [], []
                for (nx, ny, nw, nh, _c) in nametags:
                    nx, ny, nw, nh = int(nx), int(ny), int(nw), int(nh)
                    crop = img[max(0, ny - fb.PAD):ny + nh + fb.PAD, max(0, nx - fb.PAD):nx + nw + fb.PAD]
                    box_infos.append((nx, ny, nw, nh))
                    box_crops.append(crop if crop.size else None)
                assigned, _un = fb.greedy_match_boxes(box_crops, templates)
                tpl_names = list(templates.keys())
                recognized = []   # [(name, 屏幕中心x, 屏幕中心y)]
                for (bi, bj, score) in assigned:
                    nx, ny, nw, nh = box_infos[bi]
                    name = tpl_names[bj]
                    recognized.append((name, nx + nw // 2, ny + nh // 2))
                    mfaalog.info(f"[导航] 识别到: {name} ({score:.2f}) 中心=({nx + nw // 2},{ny + nh // 2})")

                # 目标在屏 -> 点击进入
                for (name, sx, sy) in recognized:
                    if name == target_quest:
                        mfaalog.info(f"[导航] 目标[{target_quest}]在屏, 点击 ({sx},{sy})")
                        controller.post_click(sx, sy).wait()
                        return CustomAction.RunResult(success=True)

                if recognized:
                    empty_count = 0
                    # 反推 t: 识别关卡(在 coords 中) 的 屏幕中心 - SCALE*coords 取中位数
                    xs, ys = [], []
                    for (name, sx, sy) in recognized:
                        p = coords.get(name)
                        if p is None:
                            continue
                        xs.append(sx - SCALE * p[0])
                        ys.append(sy - SCALE * p[1])
                    if xs and ys:
                        t_xy = (float(np.median(xs)), float(np.median(ys)))
                        mfaalog.info(f"[导航] 反推视角平移 t=({t_xy[0]:.1f},{t_xy[1]:.1f}) 锚点{len(xs)}个")
                    else:
                        mfaalog.info("[导航] 识别关卡均不在坐标表, 无法反推 t")

                # 无 t(识别不到任何带坐标关卡) -> 空屏计数, 超限兜底; 否则盲滑
                if t_xy is None:
                    empty_count += 1
                    mfaalog.info(f"[导航] 空屏(无坐标锚点) {empty_count}/{EMPTY_SCREEN_LIMIT}")
                    if empty_count >= EMPTY_SCREEN_LIMIT:
                        mfaalog.info("[导航] 连续空屏达阈值, 转兜底流程")
                        break
                    if last_dir is not None:
                        start = (int(round(SCREEN_CENTER[0] - SWIPE_SIGN * last_dir[0] * SWIPE_DIST)),
                                 int(round(SCREEN_CENTER[1] - SWIPE_SIGN * last_dir[1] * SWIPE_DIST)))
                        end = (int(SCREEN_CENTER[0]), int(SCREEN_CENTER[1]))
                        controller.post_swipe(*start, *end, SWIPE_DURATION).wait()
                        mfaalog.info(f"[导航] 空屏盲滑沿上次方向 ({last_dir[0]:.2f},{last_dir[1]:.2f})")
                    time.sleep(1.5)
                    fb.prevent_touch(context)   # 盲滑后检查误触进入关卡
                    continue

                # 目标预测位置
                pt = coords[target_quest]
                p_target = np.array([SCALE * pt[0] + t_xy[0], SCALE * pt[1] + t_xy[1]])
                dist_center = float(np.hypot(*(p_target - SCREEN_CENTER)))
                mfaalog.info(f"[导航] 目标预测屏幕位置 ({p_target[0]:.0f},{p_target[1]:.0f}), 距中心 {dist_center:.0f}px")

                # 目标预测在屏幕中心附近(名称条宽, 直接点击预测位置, 处理 SIFT 漏检)
                if dist_center <= CLICK_RADIUS:
                    cx, cy = int(round(p_target[0])), int(round(p_target[1]))
                    cx = max(0, min(SCREEN_W - 1, cx))
                    cy = max(0, min(SCREEN_H - 1, cy))
                    mfaalog.info(f"[导航] 目标预测位置在屏内, 点击 ({cx},{cy})")
                    controller.post_click(cx, cy).wait()
                    return CustomAction.RunResult(success=True)

                # 滑动进展检测: 目标距中心不缩小 -> 判定无进展(边界/方向错) -> 兜底
                if last_dist is not None and dist_center >= last_dist - 3.0:
                    no_progress += 1
                    mfaalog.info(f"[导航] 滑动无进展 {no_progress}/{NO_PROGRESS_LIMIT} (距中心 {dist_center:.0f} vs 上轮 {last_dist:.0f})")
                    if no_progress >= NO_PROGRESS_LIMIT:
                        mfaalog.info("[导航] 滑动连续无进展, 转兜底流程")
                        break
                else:
                    no_progress = 0
                last_dist = dist_center

                # 定向滑动: 地图跟手, 手指沿"目标→中心"方向滑 SWIPE_DIST 距离
                # 起点=中心朝目标方向100px处(目标侧), 终点=屏幕中心, 每轮固定100px逼近
                vec = SCREEN_CENTER - p_target
                len_v = float(np.hypot(*vec))
                if len_v < 1e-6:
                    continue
                direc = vec / len_v
                last_dir = (direc[0], direc[1])
                start = (int(round(SCREEN_CENTER[0] - SWIPE_SIGN * direc[0] * SWIPE_DIST)),
                         int(round(SCREEN_CENTER[1] - SWIPE_SIGN * direc[1] * SWIPE_DIST)))
                end = (int(SCREEN_CENTER[0]), int(SCREEN_CENTER[1]))
                mfaalog.info(f"[导航] 定向滑动 方向 ({direc[0]:.2f},{direc[1]:.2f}) 起点{start}->中心{end}")
                controller.post_swipe(*start, *end, SWIPE_DURATION).wait()
                time.sleep(1.5)
                fb.prevent_touch(context)   # 滑动后检查误触进入关卡

            # 步骤4: 兜底
            mfaalog.info("[导航] 方案B未达目标, 转兜底流程")
            ok = fb.FallbackNavigation().run(context, "地图坐标导航")
            return CustomAction.RunResult(success=ok)

        except Exception as e:
            mfaalog.error(f"[导航] 严重错误: {str(e)}")
            try:
                ok = fb.FallbackNavigation().run(context, "地图坐标导航")
                return CustomAction.RunResult(success=ok)
            except Exception as e2:
                mfaalog.error(f"[导航] 兜底流程也失败: {str(e2)}")
                return CustomAction.RunResult(success=False)

    def _detector(self):
        """延迟加载全局检测器(ultralytics), 复用避免重复初始化"""
        det = getattr(self, "_det", None)
        if det is None:
            agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(agent_dir, "utils", "quest_detect.pt")
            if not os.path.exists(model_path):
                model_path = os.path.join(agent_dir, "utils", "quest_detect.onnx")
            det = fb.QuestDetector(model_path)
            self._det = det
            mfaalog.info(f"[导航] 检测器加载: {model_path}")
        return det
