# -*- coding: utf-8 -*-
"""
全局识别结果打印 —— 通过 context_sink 监听所有 pipeline 节点的识别事件。

打印内容：
1. OCR 识别结果：命中打印 best 文本，未命中打印识别到的文本（最多 3 条）
2. 识别超时（error）：pipeline 节点 next 循环识别超时时打印（真正的"识别错误"）
3. TemplateMatch 普通识别结果：默认关闭（太吵），可用 _TEMPLATE_LOG_LEVEL 打开

要关闭：注释掉 main.py 里的 `import ocr_logger` 即可。
"""

from maa.agent.agent_server import AgentServer
from maa.context import ContextEventSink
from maa.event_sink import NotificationType

import mfaalog

_MAX_TEXTS = 3  # OCR 未命中时最多打印的文本条数
_DEFAULT_THRESHOLD = 0.7  # TemplateMatch 节点未写 threshold 时的默认值

# TemplateMatch 普通识别结果日志级别（默认只打命中）
#   "none" —— 不打普通识别结果
#   "hit"  —— 只在命中时打（默认）
#   "miss" —— 只在未命中时打
#   "all"  —— 命中 + 未命中都打
_TEMPLATE_LOG_LEVEL = "hit"


@AgentServer.context_sink()
class OcrLogger(ContextEventSink):
    # 缓存节点名 -> threshold，避免每次识别都反向 IPC 查节点配置
    _threshold_cache: dict = {}

    def on_raw_notification(self, context, msg, details):
        # 检测 pipeline 节点识别错误（超时）：
        # 超时表现为 Node.PipelineNode.Failed 且 details 里没有 node_details
        # （正常命中后 action 失败会带 node_details；超时只有 task_id/node_id/name/focus）
        if (
            msg.startswith("Node.PipelineNode")
            and msg.endswith(".Failed")
            and "node_details" not in details
        ):
            name = details.get("name", "")
            mfaalog.info(f"[超时] {name} 识别超时（next 列表未命中任何节点）")

    def on_node_recognition(self, context, noti_type, detail):
        # 只在识别结束（命中/未命中）时处理，跳过 Starting
        if noti_type not in (NotificationType.Succeeded, NotificationType.Failed):
            return

        try:
            reco = context.tasker.get_recognition_detail(detail.reco_id)
        except Exception:
            return
        if reco is None:
            return

        algo = str(reco.algorithm)
        if algo == "OCR":
            self._log_ocr(detail, reco, noti_type)
        elif algo == "TemplateMatch":
            self._log_template(context, detail, reco, noti_type)

    # ---------- OCR ----------
    def _log_ocr(self, detail, reco, noti_type):
        # 收集识别到的文本（去空、去重、保序）
        texts = []
        for r in reco.all_results:
            t = getattr(r, "text", None)
            if t and t.strip() and t not in texts:
                texts.append(t.strip())

        if noti_type == NotificationType.Succeeded:
            # 命中：打印匹配上的那条（best）
            best = reco.best_result
            best_text = getattr(best, "text", "") if best is not None else ""
            mfaalog.info(f"[OCR] {detail.name} 命中: {best_text.strip()!r}")
        else:
            # 未命中：只在识别到了文本时打印（有诊断价值），完全没识别到则静默
            if not texts:
                return
            preview = " / ".join(texts[:_MAX_TEXTS])
            if len(texts) > _MAX_TEXTS:
                preview += f" …共{len(texts)}条"
            mfaalog.info(f"[OCR] {detail.name} 未命中，识别到: {preview}")

    # ---------- TemplateMatch ----------
    def _log_template(self, context, detail, reco, noti_type):
        hit = noti_type == NotificationType.Succeeded
        if _TEMPLATE_LOG_LEVEL == "none":
            return
        if _TEMPLATE_LOG_LEVEL == "hit" and not hit:
            return
        if _TEMPLATE_LOG_LEVEL == "miss" and hit:
            return

        threshold = self._threshold(context, detail.name)
        scores = [r.score for r in reco.all_results if getattr(r, "score", None) is not None]

        if hit:
            # 命中：打印命中的 score / threshold
            best = reco.best_result
            score = getattr(best, "score", None) if best is not None else None
            if score is None:
                score = max(scores) if scores else None
            if score is None:
                return
            mfaalog.info(f"[模板] {detail.name} 命中: {score:.4f}/{threshold}")
        else:
            # 未命中：打印最高 score / threshold（能看到离阈值差多少）
            if not scores:
                return
            mfaalog.info(f"[模板] {detail.name} 未命中: 最高{max(scores):.4f}/{threshold}")

    @classmethod
    def _threshold(cls, context, name):
        if name in cls._threshold_cache:
            return cls._threshold_cache[name]
        th = cls._read_threshold(context, name)
        cls._threshold_cache[name] = th
        return th

    @staticmethod
    def _read_threshold(context, name):
        try:
            node = context.get_node_data(name) or {}
            param = (node.get("recognition") or {}).get("param") or {}
            th = param.get("threshold")
            if th is None:
                return _DEFAULT_THRESHOLD
            if isinstance(th, (list, tuple)):
                return float(th[0]) if th else _DEFAULT_THRESHOLD
            return float(th)
        except Exception:
            return _DEFAULT_THRESHOLD
