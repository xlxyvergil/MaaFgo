"""多模态 Provider 抽象、实现与创建入口。"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from .config import VisionConfig
from .models import VisionRequest, VisionResponse
from .parser import VisionParseError, parse_observation
from .prompts import SYSTEM_PROMPT, build_user_prompt


class VisionProvider(Protocol):
    def analyze(self, request: VisionRequest) -> VisionResponse: ...


class FakeVisionProvider:
    def __init__(self, response_text: str, *, model: str = "fake-vision") -> None:
        self.response_text = response_text
        self.model = model
        self.calls = 0

    def analyze(self, request: VisionRequest) -> VisionResponse:
        self.calls += 1
        try:
            observation = parse_observation(self.response_text, evidence_id=request.evidence_id)
            return VisionResponse(self.response_text, observation, model=self.model)
        except VisionParseError as exc:
            return VisionResponse(self.response_text, None, model=self.model, error=str(exc))


class ReplayVisionProvider:
    def __init__(self, fixture: str | Path, *, model: str = "replay-vision") -> None:
        self.fixture = Path(fixture)
        self.model = model
        self.calls = 0

    def analyze(self, request: VisionRequest) -> VisionResponse:
        self.calls += 1
        raw = self.fixture.read_text(encoding="utf-8")
        try:
            observation = parse_observation(raw, evidence_id=request.evidence_id)
            return VisionResponse(raw, observation, model=self.model)
        except VisionParseError as exc:
            return VisionResponse(raw, None, model=self.model, error=str(exc))


Urlopen = Callable[..., Any]


class OpenAICompatibleVisionProvider:
    def __init__(self, endpoint: str, api_key: str, model: str, *, timeout_s: float = 30.0, urlopen: Optional[Urlopen] = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self._urlopen = urlopen or urllib.request.urlopen

    def analyze(self, request: VisionRequest) -> VisionResponse:
        started = time.monotonic()
        raw_text = ""
        try:
            body = json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8")
            http_request = urllib.request.Request(self.endpoint, data=body, headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}, method="POST")
            with self._urlopen(http_request, timeout=self.timeout_s) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            raw_text = _extract_content(response_data)
            observation = parse_observation(raw_text, evidence_id=request.evidence_id)
            return VisionResponse(raw_text, observation, model=self.model, latency_ms=_elapsed_ms(started), usage=_usage(response_data))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, VisionParseError, KeyError, TypeError, ValueError) as exc:
            return VisionResponse(raw_text, None, model=self.model, latency_ms=_elapsed_ms(started), error=str(exc))

    def _payload(self, request: VisionRequest) -> dict[str, Any]:
        encoded = base64.b64encode(request.image).decode("ascii")
        return {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": [{"type": "text", "text": build_user_prompt(request)}, {"type": "image_url", "image_url": {"url": f"data:image/{request.image_format};base64,{encoded}"}}]}]}


def create_provider(config: VisionConfig) -> VisionProvider:
    if config.provider != "openai_compatible":
        raise ValueError(f"unsupported vision provider: {config.provider}")
    if not config.endpoint:
        raise ValueError("vision endpoint is required when enabled")
    if not config.model:
        raise ValueError("vision model is required when enabled")
    api_key = config.api_key or os.environ.get("MAAFGO_VISION_API_KEY", "local")
    return OpenAICompatibleVisionProvider(config.endpoint, api_key, config.model, timeout_s=config.timeout_s)


def _extract_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("response choices is empty")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("response message.content is missing")
    return message["content"]


def _usage(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("usage", {})
    return value if isinstance(value, dict) else {}


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)