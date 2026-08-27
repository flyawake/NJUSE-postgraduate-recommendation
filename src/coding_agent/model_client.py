"""Model client protocol and the OpenAI-compatible production adapter.

The SDK only exists inside this module. AgentLoop, ContextManager and tests
deal exclusively with project-internal AssistantTurn/ToolCall objects.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

from .errors import ModelRequestError
from .models import AssistantTurn, ToolCall


class ModelClient(Protocol):
    def request(self, messages: List[dict], tools: List[dict]) -> AssistantTurn: ...


def normalize_response(response: Any) -> AssistantTurn:
    """Convert a raw OpenAI-style response into internal types."""
    try:
        choices = list(response.choices)
    except (AttributeError, TypeError) as exc:
        raise ModelRequestError("模型响应缺少 choices 字段", retryable=False) from exc
    if not choices:
        raise ModelRequestError("模型返回了空的 choices", retryable=False)
    message = getattr(choices[0], "message", None)
    if message is None:
        raise ModelRequestError("模型响应缺少 message", retryable=False)

    content = getattr(message, "content", None)
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    else:
        text = str(content)

    calls: List[ToolCall] = []
    for item in getattr(message, "tool_calls", None) or []:
        if getattr(item, "type", "function") != "function":
            raise ModelRequestError(
                f"模型返回了不支持的工具调用类型: {getattr(item, 'type', None)!r}",
                retryable=False,
            )
        function = getattr(item, "function", None)
        if function is None:
            raise ModelRequestError("工具调用缺少 function 字段", retryable=False)
        name = getattr(function, "name", None) or ""
        arguments = getattr(function, "arguments", None)
        raw_arguments = "{}" if arguments is None else str(arguments)
        calls.append(
            ToolCall(
                id=getattr(item, "id", None) or "",
                name=name,
                arguments_raw=raw_arguments,
            )
        )
    return AssistantTurn(text=text, tool_calls=tuple(calls))


class OpenAIModelClient:
    """Single-attempt OpenAI-compatible Chat Completions client.

    Retries are owned by AgentLoop so step and provider-attempt counters stay
    accurate. ``client`` is a test seam for an opaque fake SDK object.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
        client: Any = None,
    ) -> None:
        self._model = model
        self._timeout = timeout
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI

            kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": timeout}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)

    def request(self, messages: List[dict], tools: List[dict]) -> AssistantTurn:
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "parallel_tool_calls": False,
            "timeout": self._timeout,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            response = self._client.chat.completions.create(**payload)
        except APITimeoutError as exc:
            raise ModelRequestError("模型请求超时", retryable=True) from exc
        except APIConnectionError as exc:
            raise ModelRequestError(f"模型连接失败：{exc}", retryable=True) from exc
        except RateLimitError as exc:
            raise ModelRequestError(
                "模型 API 限流（HTTP 429）", retryable=True
            ) from exc
        except APIStatusError as exc:
            retryable = exc.status_code == 429 or exc.status_code >= 500
            raise ModelRequestError(
                f"模型 API 错误 HTTP {exc.status_code}", retryable=retryable
            ) from exc
        except APIError as exc:
            raise ModelRequestError(f"模型 API 错误：{exc}", retryable=False) from exc
        return normalize_response(response)
