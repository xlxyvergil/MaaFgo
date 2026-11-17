# -*- coding: utf-8 -*-
"""
从事件自带的 JSON 保存识别细节，不查询完整识别结果或提取截图。

OCR 文字、模板分数写入 debug/recognition-<pid>.log；超时仍在客户端提示。
_TEMPLATE_LOG_LEVEL 控制本地模板日志；关闭监听可移除 main.py 的 import ocr_logger。
"""

from maa.agent.agent_server import AgentServer
from maa.context import ContextEventSink

import mfaalog

_MAX_TEXTS = 3  # OCR 未命中时最多打印的文本条数
_DEFAULT_THRESHOLD = 0.7  # TemplateMatch 节点未写 threshold 时的默认值

# TemplateMatch 本地详细日志级别（默认只打命中，不输出到客户端）
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
            mfaalog.warning(f"[超时] {name} 识别超时（next 列表未命中任何节点）")

        # 精确匹配，排除 RecognitionNode 和 Starting 事件。
        if msg not in ("Node.Recognition.Succeeded", "Node.Recognition.Failed"):
            return
        reco = details.get("reco_details")
        if not isinstance(reco, dict):
            # 不退回 get_recognition_detail：该接口会连带提取并复制图像。
            return
        raw = reco.get("detail")
        if not isinstance(raw, dict):
            return
        name = details.get("name", "")
        hit = msg == "Node.Recognition.Succeeded"
        if reco.get("algorithm") == "OCR":
            self._log_ocr(name, raw, hit)
        elif reco.get("algorithm") == "TemplateMatch":
            self._log_template(context, name, raw, hit)

    def _log_ocr(self, name, raw, hit):
        texts = []
        for result in raw.get("all") or []:
            text = result.get("text", "").strip()
            if text and text not in texts:
                texts.append(text)
        if hit:
            best_text = (raw.get("best") or {}).get("text", "").strip()
            mfaalog.diagnostic(f"[OCR] {name} 命中: {best_text!r}")
        elif texts:
            preview = " / ".join(texts[:_MAX_TEXTS])
            if len(texts) > _MAX_TEXTS:
                preview += f" …共{len(texts)}条"
            mfaalog.diagnostic(f"[OCR] {name} 未命中，识别到: {preview}")

    def _log_template(self, context, name, raw, hit):
        if _TEMPLATE_LOG_LEVEL == "none":
            return
        if _TEMPLATE_LOG_LEVEL == "hit" and not hit:
            return
        if _TEMPLATE_LOG_LEVEL == "miss" and hit:
            return
        scores = [r["score"] for r in raw.get("all") or [] if r.get("score") is not None]
        score = (raw.get("best") or {}).get("score") if hit else None
        if score is None:
            score = max(scores) if scores else None
        if score is None:
            return
        threshold = self._threshold(context, name)
        result = "命中: " if hit else "未命中: 最高"
        mfaalog.diagnostic(f"[模板] {name} {result}{score:.4f}/{threshold}")

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
