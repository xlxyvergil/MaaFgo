# -*- coding: utf-8 -*-
"""原生编队的茶壶状态守卫；识别、固定输入和等待均委托 Pipeline。"""
from dataclasses import dataclass
import html

import cv2
import numpy as np

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

import mfaalog


CONFIG_NODE = "原生战斗-茶壶配置"
WAIT_NODE = "茶壶-等待观察"
NOTICE_NODE = "茶壶-用户提示"
OBSERVATION_ROUNDS = 8
STABLE_FRAMES = 2


@dataclass(frozen=True)
class TeapotFrame:
    kind: str
    enabled: bool | None = None


class _Stopped(Exception):
    pass


class TeapotUnconfirmed(RuntimeError):
    pass


def _settings(context):
    node = context.get_node_data(CONFIG_NODE) or {}
    value = (node.get("attach") or {}).get("use_teapot", False)
    if type(value) is not bool:
        raise ValueError("是否使用茶壶必须为布尔值")
    return value


class TeapotRunner:
    """识别结果可通过 observe() 替换，流程不在 Python 中保存界面坐标。"""

    def __init__(self, context):
        self.context = context
        self.controller = context.tasker.controller

    def _guard(self):
        if self.context.tasker.stopping:
            raise _Stopped()

    def _run_task(self, name):
        return self.context.run_task(name)

    def _act(self, name):
        self._guard()
        detail = self._run_task(name)
        self._guard()
        status = getattr(detail, "status", None)
        if status is None or not status.succeeded:
            raise TeapotUnconfirmed(f"茶壶流程未成功：{name}")

    def _capture(self):
        self._guard()
        # Agent 控制器的截图 job.status 偶尔挂起；等待由独立 Pipeline 节点
        # 承载，随后读取缓存。切页旧帧由连续两帧复核和有限轮次过滤。
        self.controller.post_screencap()
        self._act(WAIT_NODE)
        image = self.controller.cached_image
        if hasattr(image, "to_numpy"):
            image = image.to_numpy()
        image = np.asarray(image)
        if image.size == 0:
            raise TeapotUnconfirmed("茶壶检查截图不可用")
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif image.ndim == 3 and image.shape[2] == 4:
            image = image[:, :, :3]
        if image.ndim != 3 or image.shape[2] != 3:
            raise TeapotUnconfirmed("茶壶检查截图格式无效")
        self._guard()
        return image.astype(np.uint8, copy=True)

    def _hit(self, name, image):
        self._guard()
        result = self.context.run_recognition(name, image)
        self._guard()
        return bool(result and result.hit)

    def observe(self):
        image = self._capture()
        if self._hit("茶壶-识别弹窗", image):
            enabled = self._hit("茶壶-弹窗已开启", image)
            disabled = self._hit("茶壶-弹窗已关闭", image)
            return TeapotFrame("popup", enabled if enabled != disabled else None)
        if not self._hit("茶壶-识别编队页", image):
            return TeapotFrame("unknown")
        # 两种UI的外层结构一致，必须等紫色茶壶图标对应的ON/OFF出现。
        # 风暴罐帧和info本身只证明有入口，不能推断茶壶状态或点击响应。
        enabled = self._hit("茶壶-直接已开启", image)
        disabled = self._hit("茶壶-直接已关闭", image)
        if enabled != disabled:
            return TeapotFrame("bar", enabled)
        if enabled or disabled:
            return TeapotFrame("entry")
        if self._hit("茶壶-识别风暴罐", image):
            return TeapotFrame("storm")
        # Rewardless quests may retain both info buttons without an automatic
        # item bar. The shared node requires the known party layout, both info
        # anchors and the full empty strip; info alone is still a transition.
        # _initial requires eight consecutive absent samples for every task.
        if self._hit("茶壶-识别无自动道具栏", image):
            return TeapotFrame("absent")
        if self._hit("茶壶-识别交替过渡", image):
            return TeapotFrame("transition")
        if self._hit("茶壶-识别任意入口", image):
            return TeapotFrame("entry")
        return TeapotFrame("absent")

    def _initial(self):
        previous = None
        consecutive = 0
        absent_rounds = 0
        saw_entry = False
        for _ in range(OBSERVATION_ROUNDS):
            self._guard()
            frame = self.observe()
            self._guard()
            if frame.kind == "absent":
                absent_rounds += 1
                previous, consecutive = None, 0
                if absent_rounds == OBSERVATION_ROUNDS:
                    return frame
                continue
            absent_rounds = 0
            saw_entry |= frame.kind in {"popup", "bar", "entry", "storm", "transition"}
            # 明确的风暴罐或交替过渡只是同一入口的闪烁相位，不丢弃
            # 上次茶壶读数；未知、冲突或弹窗不能作为中性帧。
            if (frame.kind in {"storm", "transition"} and previous is not None
                    and previous.kind == "bar"):
                continue
            ready = frame.kind in {"popup", "bar"} and type(frame.enabled) is bool
            if not ready:
                previous, consecutive = None, 0
                continue
            consecutive = consecutive + 1 if frame == previous else 1
            previous = frame
            if consecutive >= STABLE_FRAMES:
                return frame
        reason = "检测到茶壶入口，但无法确认开关状态" if saw_entry else "未能确认未遮挡的编队或茶壶设置界面"
        raise TeapotUnconfirmed(reason)

    def _wait(self, predicate, message):
        consecutive = 0
        previous = None
        for _ in range(OBSERVATION_ROUNDS):
            self._guard()
            frame = self.observe()
            self._guard()
            if (frame.kind in {"storm", "transition"} and previous is not None and previous.kind == "bar"
                    and predicate(previous)):
                continue
            if predicate(frame):
                consecutive = consecutive + 1 if frame == previous else 1
                previous = frame
                if consecutive >= STABLE_FRAMES:
                    return frame
            else:
                previous, consecutive = None, 0
        raise TeapotUnconfirmed(message)

    def _popup(self, desired, frame=None):
        frame = frame or self._wait(
            lambda value: value.kind == "popup" and type(value.enabled) is bool,
            "茶壶弹窗状态无法确认",
        )
        if frame.enabled != desired:
            self._act("茶壶-弹窗设为开启" if desired else "茶壶-弹窗设为关闭")
            self._wait(lambda value: value == TeapotFrame("popup", desired),
                       "茶壶弹窗切换后复核失败")
        self._act("茶壶-关闭弹窗")
        self._wait(lambda value: value == TeapotFrame("bar", desired),
                   "关闭茶壶弹窗后未确认编队茶壶状态")

    def _focus(self, message):
        self._guard()
        try:
            if self.context.override_pipeline({NOTICE_NODE: {
                "focus": {"Node.Recognition.Starting": html.escape(message, quote=True)},
            }}) is False:
                raise ValueError("无法更新提示文字")
            self._guard()
            self.context.run_task(NOTICE_NODE)
            self._guard()
        except _Stopped:
            raise
        except Exception as exc:
            # 普通日志仍会显示给用户，focus 失败不改变业务结论。
            mfaalog.warning(f"[茶壶] 用户提示失败：{exc}")

    def run(self):
        try:
            self._guard()
            desired = _settings(self.context)
            frame = self._initial()
            mfaalog.info(f"[茶壶] 界面={frame.kind}，当前状态={frame.enabled}，目标={'开启' if desired else '关闭'}")
            if frame.kind == "absent":
                message = "本关无法使用茶壶，跳过茶壶设置后继续战斗。"
                mfaalog.info(f"[茶壶] {message}")
                self._focus(message)
                return True
            if frame.kind == "popup":
                self._popup(desired, frame)
            elif frame.kind == "bar" and frame.enabled != desired:
                # 外层一次点击的响应可能是弹窗，也可能直接改变状态。
                # 先观察真实响应，不能根据副本类型或相同的双info结构猜测。
                self._act("茶壶-直接设为开启" if desired else "茶壶-直接设为关闭")
                response = self._wait(
                    lambda value: (value.kind == "popup" and type(value.enabled) is bool
                                   or value == TeapotFrame("bar", desired)),
                    "点击茶壶后未确认弹窗或目标开关状态",
                )
                mfaalog.info(f"[茶壶] 外层点击响应={response.kind}")
                if response.kind == "popup":
                    self._popup(desired, response)
            self._guard()
            mfaalog.info(f"[茶壶] 已确认{'开启' if desired else '关闭'}")
            return True
        except _Stopped:
            return False
        except Exception as exc:
            message = f"茶壶校验失败：{exc}；任务已停止，请检查当前编队界面。"
            mfaalog.error(f"[茶壶] {message}")
            if not self.context.tasker.stopping:
                try:
                    self._focus(message)
                except _Stopped:
                    pass
            return False


@AgentServer.custom_action("ensure_native_teapot")
class EnsureNativeTeapot(CustomAction):
    def run(self, context, argv):
        return CustomAction.RunResult(success=TeapotRunner(context).run())
