"""Model client protocol and the OpenAI-compatible production adapters.

The SDK only exists inside this module. AgentLoop, ContextManager and tests
deal exclusively with project-internal ``AssistantTurn``/``ToolCall`` and
provider-neutral :mod:`coding_agent.streaming` events. ``request()`` remains
as a compatibility helper that consumes ``stream()`` or a non-stream response;
production GUI/CLI paths use ``stream()`` through AgentLoop.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Protocol, Sequence

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

from .config import ResolvedModelConnection
from .errors import ConfigError, ModelRequestError
from .models import AssistantTurn, ToolCall
from .provider_config import (
    WIRE_API_CHAT_COMPLETIONS,
    WIRE_API_RESPONSES,
)
from .streaming import (
    REASONING_NONE,
    REASONING_RAW_VISIBLE,
    ModelCapabilities,
    ModelRequestOptions,
    ModelStreamEvent,
    OpaqueContinuationReceived,
    ReasoningDelta,
    ReasoningSummaryDelta,
    RefusalDelta,
    StreamCompleted,
    StreamFailed,
    StreamStarted,
    TextDelta,
    ToolCallArgumentsDelta,
    ToolCallStarted,
    TurnStreamAccumulator,
    UsageReceived,
)


def _optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _map_responses_error(exc: Exception) -> ModelRequestError:
    """Map SDK errors without copying provider bodies or request secrets."""
    if isinstance(exc, APITimeoutError):
        return ModelRequestError("模型请求超时", retryable=True)
    if isinstance(exc, APIConnectionError):
        return ModelRequestError("模型连接失败", retryable=True)
    if isinstance(exc, RateLimitError):
        return ModelRequestError("模型 API 限流（HTTP 429）", retryable=True)
    if isinstance(exc, APIStatusError):
        retryable = exc.status_code == 429 or exc.status_code >= 500
        return ModelRequestError(
            f"模型 API 错误 HTTP {exc.status_code}", retryable=retryable
        )
    if isinstance(exc, APIError):
        return ModelRequestError("模型 API 返回无效响应", retryable=False)
    return ModelRequestError("Responses 模型流处理失败", retryable=False)


def _validate_request_options(
    capabilities: ModelCapabilities, options: ModelRequestOptions
) -> None:
    if options.reasoning_mode not in {"auto", "off", "visible"}:
        raise ModelRequestError("不支持的 reasoning mode", retryable=False)
    if options.reasoning_mode == "visible" and capabilities.visible_reasoning == "none":
        raise ModelRequestError("当前 wire API 不提供可展示 reasoning", retryable=False)
    if options.reasoning_effort is not None:
        if options.reasoning_mode == "off":
            raise ModelRequestError(
                "reasoning 关闭时不能设置 reasoning effort", retryable=False
            )
        if options.reasoning_effort not in capabilities.reasoning_efforts:
            raise ModelRequestError(
                "当前 wire API 不支持该 reasoning effort", retryable=False
            )
    if not options.stream:
        raise ModelRequestError("当前 AgentLoop 要求 streaming", retryable=False)


class ModelClient(Protocol):
    capabilities: ModelCapabilities
    stream: Any

    def request(
        self, messages: Sequence[dict], tools: Sequence[dict]
    ) -> AssistantTurn: ...


class ModelClientFactory:
    """Dispatch a resolved connection to the right model client adapter.

    The factory is the only place that maps ``wire_api`` to a concrete
    client; AgentLoop and every provider-neutral layer never branch on
    provider names.
    """

    @staticmethod
    def create(connection: ResolvedModelConnection) -> ModelClient:
        wire_api = connection.wire_api
        if wire_api == WIRE_API_CHAT_COMPLETIONS:
            return OpenAIModelClient(
                api_key=connection.api_key,
                model=connection.model,
                base_url=connection.base_url,
                provider_id=connection.provider_id,
            )
        if wire_api == WIRE_API_RESPONSES:
            return OpenAIResponsesClient(
                api_key=connection.api_key,
                model=connection.model,
                base_url=connection.base_url,
                provider_id=connection.provider_id,
            )
        raise ConfigError(f"不支持的 wire_api：{wire_api!r}")


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


class _ChatCompletionsMixin:
    """Shared Chat Completions request construction and error mapping."""

    capabilities = ModelCapabilities(
        wire_api=WIRE_API_CHAT_COMPLETIONS,
        streaming=True,
        visible_reasoning="raw_visible",
        reasoning_efforts=("low", "medium", "high"),
        usage_in_stream=True,
        supports_cancel=True,
    )

    def _chat_payload(
        self,
        messages: Sequence[dict],
        tools: Sequence[dict],
        *,
        stream: bool,
        options: Optional[ModelRequestOptions] = None,
    ) -> Dict[str, Any]:
        options = options or ModelRequestOptions()
        _validate_request_options(self.capabilities, options)
        processed_messages: List[Dict[str, Any]] = []
        last_user_index = max(
            (
                index
                for index, message in enumerate(messages)
                if message.get("role") == "user"
            ),
            default=-1,
        )
        for index, message in enumerate(messages):
            item = dict(message)
            item.pop("_provider_continuations", None)
            # Only DeepSeek-style visible reasoning requires round-tripping
            # the provider-visible reasoning_content in a tool sub-request.
            # Other modes/providers must not receive this non-standard field.
            keep_reasoning = (
                options.reasoning_mode != "off"
                and self._provider_id != "openai"
                and index > last_user_index
                and bool(item.get("tool_calls"))
            )
            if not keep_reasoning:
                item.pop("reasoning_content", None)
            processed_messages.append(item)
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": processed_messages,
            "stream": stream,
            "timeout": self._timeout,
        }
        if self._provider_id == "openai":
            payload["parallel_tool_calls"] = False
            if stream:
                payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        # ``reasoning_effort`` is not part of the generic compatible Chat
        # contract (notably DeepSeek rejects it). Never inject an optional
        # provider parameter unless the selected provider is known to accept
        # it and the user explicitly requested a value.
        if options.reasoning_effort and self._provider_id == "openai":
            payload["reasoning_effort"] = options.reasoning_effort
        return payload

    def _map_chat_error(self, exc: Exception) -> ModelRequestError:
        if isinstance(exc, APITimeoutError):
            return ModelRequestError("模型请求超时", retryable=True)
        if isinstance(exc, APIConnectionError):
            return ModelRequestError("模型连接失败", retryable=True)
        if isinstance(exc, RateLimitError):
            return ModelRequestError("模型 API 限流（HTTP 429）", retryable=True)
        if isinstance(exc, APIStatusError):
            retryable = exc.status_code == 429 or exc.status_code >= 500
            return ModelRequestError(
                f"模型 API 错误 HTTP {exc.status_code}", retryable=retryable
            )
        if isinstance(exc, APIError):
            return ModelRequestError("模型 API 返回无效响应", retryable=False)
        return ModelRequestError("模型流处理失败", retryable=False)


class OpenAIModelClient(_ChatCompletionsMixin):
    """OpenAI-compatible Chat Completions adapter (streaming + compatibility)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        provider_id: str = "openai",
        timeout: float = 120.0,
        client: Any = None,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._provider_id = provider_id
        self.capabilities = ModelCapabilities(
            wire_api=WIRE_API_CHAT_COMPLETIONS,
            streaming=True,
            visible_reasoning=(
                REASONING_NONE if provider_id == "openai" else REASONING_RAW_VISIBLE
            ),
            reasoning_efforts=(
                ("low", "medium", "high") if provider_id == "openai" else ()
            ),
            usage_in_stream=True,
            supports_cancel=True,
        )
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI

            kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": timeout}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)

    def request(self, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantTurn:
        payload = self._chat_payload(messages, tools, stream=False)
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:
            raise self._map_chat_error(exc) from exc
        return normalize_response(response)

    def stream(
        self,
        messages: Sequence[dict],
        tools: Sequence[dict],
        *,
        options: Optional[ModelRequestOptions] = None,
        cancel: Optional[Any] = None,
    ) -> Iterator[ModelStreamEvent]:
        payload = self._chat_payload(messages, tools, stream=True, options=options)
        try:
            iterator = self._client.chat.completions.create(**payload)
        except Exception as exc:
            raise self._map_chat_error(exc) from exc
        finish_reason: Optional[str] = None
        try:
            yield StreamStarted()
            for chunk in iterator:
                if cancel is not None and cancel():
                    return
                try:
                    choices = list(getattr(chunk, "choices", []) or [])
                    if len(choices) > 1 or any(
                        _event_index(choice, "index", 0) != 0 for choice in choices
                    ):
                        yield StreamFailed(
                            code="unsupported_choices",
                            message="模型流返回了不支持的多 choice 输出",
                            retryable=False,
                        )
                        return
                    for choice in choices:
                        reason = getattr(choice, "finish_reason", None)
                        if reason:
                            mapped = {
                                "stop": "stop",
                                "tool_calls": "tool_calls",
                                "function_call": "tool_calls",
                            }.get(str(reason))
                            if mapped is None:
                                yield StreamFailed(
                                    code="incomplete_response",
                                    message="模型流未正常完成",
                                    retryable=False,
                                )
                                return
                            if mapped == "tool_calls" or finish_reason is None:
                                finish_reason = mapped
                except TypeError:
                    pass
                for event in self._chunk_to_events(chunk):
                    yield event
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    details = getattr(usage, "completion_tokens_details", None)
                    yield UsageReceived(
                        input_tokens=_optional_int(
                            getattr(usage, "prompt_tokens", None)
                        ),
                        output_tokens=_optional_int(
                            getattr(usage, "completion_tokens", None)
                        ),
                        reasoning_tokens=_optional_int(
                            getattr(details, "reasoning_tokens", None)
                        ),
                    )
            if finish_reason is None:
                yield StreamFailed(
                    code="truncated_stream",
                    message="模型流在完成标记前中断",
                    retryable=True,
                )
                return
            yield StreamCompleted(finish_reason=finish_reason)
        except Exception as exc:
            raise self._map_chat_error(exc) from exc
        finally:
            if hasattr(iterator, "close"):
                try:
                    iterator.close()
                except Exception:
                    pass

    @classmethod
    def _chunk_to_events(cls, chunk: Any) -> List[ModelStreamEvent]:
        events: List[ModelStreamEvent] = []
        try:
            choices = list(getattr(chunk, "choices", []) or [])
        except TypeError:
            return events
        for fallback_index, choice in enumerate(choices):
            raw_index = getattr(choice, "index", fallback_index)
            try:
                output_index = int(raw_index)
            except (TypeError, ValueError):
                output_index = fallback_index
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:
                events.append(TextDelta(output_index, content))
            refusal = getattr(delta, "refusal", None)
            if isinstance(refusal, str) and refusal:
                events.append(RefusalDelta(output_index, refusal))
            reasoning = getattr(delta, "reasoning_content", None)
            if isinstance(reasoning, str) and reasoning:
                events.append(
                    ReasoningDelta(output_index, reasoning, visibility="raw_visible")
                )
            for item in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(item, "index", 0) or 0)
                call_id = getattr(item, "id", None)
                function = getattr(item, "function", None)
                name = getattr(function, "name", None) if function is not None else None
                arguments = (
                    getattr(function, "arguments", None)
                    if function is not None
                    else None
                )
                if call_id or name or arguments is not None:
                    events.append(
                        ToolCallStarted(output_index, index, call_id=call_id, name=name)
                    )
                if isinstance(arguments, str) and arguments:
                    events.append(
                        ToolCallArgumentsDelta(output_index, index, arguments)
                    )
        return events


class OpenAIResponsesClient:
    """OpenAI Responses API adapter (streaming reasoning summary etc)."""

    capabilities = ModelCapabilities(
        wire_api=WIRE_API_RESPONSES,
        streaming=True,
        visible_reasoning="summary",
        reasoning_efforts=("low", "medium", "high"),
        usage_in_stream=True,
        supports_cancel=True,
    )

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        provider_id: str = "openai",
        timeout: float = 120.0,
        client: Any = None,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._provider_id = provider_id
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI

            kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": timeout}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)

    def request(self, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantTurn:
        accumulator = TurnStreamAccumulator()
        for event in self.stream(messages, tools):
            accumulator.absorb(event)
        return accumulator.to_turn()

    def stream(
        self,
        messages: Sequence[dict],
        tools: Sequence[dict],
        *,
        options: Optional[ModelRequestOptions] = None,
        cancel: Optional[Any] = None,
    ) -> Iterator[ModelStreamEvent]:
        # Convert project-neutral messages to Responses input. The exact SDK
        # input object shape is adapter-owned.
        input_items = _responses_input(messages)
        payload: Dict[str, Any] = {
            "model": self._model,
            "input": input_items,
            "stream": True,
            "store": False,
        }
        if tools:
            payload["tools"] = _responses_tools(tools)
        options = options or ModelRequestOptions()
        _validate_request_options(self.capabilities, options)
        if options.reasoning_mode != "off":
            reasoning: Dict[str, Any] = {"summary": "auto"}
            if options.reasoning_effort:
                reasoning["effort"] = options.reasoning_effort
            payload["reasoning"] = reasoning
        try:
            stream = self._client.responses.create(**payload)
        except Exception as exc:
            raise _map_responses_error(exc) from exc
        completed = False
        tool_indices: Dict[tuple[int, str], int] = {}
        tool_meta: Dict[tuple[int, str], tuple[str, str]] = {}
        tool_arguments: Dict[tuple[int, str], List[str]] = {}
        text_parts: Dict[int, List[str]] = {}
        summary_parts: Dict[tuple[int, int], List[str]] = {}
        next_tool_index: Dict[int, int] = {}
        try:
            yield StreamStarted()
            for event in stream:
                if cancel is not None and cancel():
                    return
                event_type = str(getattr(event, "type", "") or "")
                output_index = _event_index(event, "output_index", 0)
                neutral_events: List[ModelStreamEvent]
                if event_type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    item_type = str(getattr(item, "type", "") or "")
                    if item_type == "function_call":
                        item_id = _response_item_id(item)
                        if not item_id:
                            raise ModelRequestError(
                                "Responses 工具调用缺少 item id", retryable=False
                            )
                        key = (output_index, item_id)
                        if key in tool_indices:
                            raise ModelRequestError(
                                "Responses 工具调用重复开始", retryable=False
                            )
                        tool_index = next_tool_index.get(output_index, 0)
                        next_tool_index[output_index] = tool_index + 1
                        tool_indices[key] = tool_index
                        tool_meta[key] = (
                            str(
                                getattr(item, "call_id", None)
                                or getattr(item, "id", None)
                                or ""
                            ),
                            str(getattr(item, "name", None) or ""),
                        )
                        tool_arguments[key] = []
                        neutral_events = [
                            ToolCallStarted(
                                output_index,
                                tool_index,
                                call_id=(
                                    getattr(item, "call_id", None)
                                    or getattr(item, "id", None)
                                ),
                                name=getattr(item, "name", None),
                            )
                        ]
                    else:
                        neutral_events = []
                elif event_type == "response.function_call_arguments.delta":
                    item_id = str(getattr(event, "item_id", "") or "")
                    key = (output_index, item_id)
                    if key not in tool_indices:
                        raise ModelRequestError(
                            "Responses 工具参数先于工具调用开始", retryable=False
                        )
                    delta = getattr(event, "delta", None)
                    if isinstance(delta, str) and delta:
                        tool_arguments[key].append(delta)
                    neutral_events = (
                        [ToolCallArgumentsDelta(output_index, tool_indices[key], delta)]
                        if isinstance(delta, str) and delta
                        else []
                    )
                elif event_type == "response.completed":
                    response = getattr(event, "response", None)
                    status = str(getattr(response, "status", "completed") or "")
                    if status != "completed":
                        neutral_events = [
                            StreamFailed(
                                code="incomplete_response",
                                message="Responses 模型流未正常完成",
                                retryable=False,
                            )
                        ]
                    else:
                        neutral_events = []
                        usage = getattr(response, "usage", None)
                        if usage is not None:
                            details = getattr(usage, "output_tokens_details", None)
                            neutral_events.append(
                                UsageReceived(
                                    input_tokens=_optional_int(
                                        getattr(usage, "input_tokens", None)
                                    ),
                                    output_tokens=_optional_int(
                                        getattr(usage, "output_tokens", None)
                                    ),
                                    reasoning_tokens=_optional_int(
                                        getattr(details, "reasoning_tokens", None)
                                    ),
                                )
                            )
                        neutral_events.append(StreamCompleted("stop", status))
                        completed = True
                elif event_type == "response.output_item.done":
                    # Final item objects repeat data already delivered by
                    # delta events. Treat this as lifecycle only so text,
                    # summaries and tool arguments are never duplicated.
                    item = getattr(event, "item", None)
                    item_type = str(getattr(item, "type", "") or "")
                    if item_type == "function_call":
                        item_id = _response_item_id(item)
                        key = (output_index, item_id)
                        if key not in tool_indices:
                            raise ModelRequestError(
                                "Responses 工具调用完成但未开始", retryable=False
                            )
                        final_identity = (
                            str(
                                getattr(item, "call_id", None)
                                or getattr(item, "id", None)
                                or ""
                            ),
                            str(getattr(item, "name", None) or ""),
                        )
                        if final_identity != tool_meta[key]:
                            raise ModelRequestError(
                                "Responses 工具调用身份在流中发生变化",
                                retryable=False,
                            )
                        final_arguments = getattr(item, "arguments", None)
                        streamed_arguments = "".join(tool_arguments[key])
                        if (
                            isinstance(final_arguments, str)
                            and streamed_arguments
                            and final_arguments != streamed_arguments
                        ):
                            raise ModelRequestError(
                                "Responses 工具参数 final 与 delta 不一致",
                                retryable=False,
                            )
                        neutral_events = (
                            [
                                ToolCallArgumentsDelta(
                                    output_index,
                                    tool_indices[key],
                                    final_arguments,
                                )
                            ]
                            if isinstance(final_arguments, str)
                            and final_arguments
                            and not streamed_arguments
                            else []
                        )
                    elif item_type == "message":
                        final_text = "".join(
                            str(getattr(part, "text", "") or "")
                            for part in (getattr(item, "content", None) or [])
                            if getattr(part, "type", "") == "output_text"
                        )
                        streamed_text = "".join(text_parts.get(output_index, []))
                        if streamed_text and final_text != streamed_text:
                            raise ModelRequestError(
                                "Responses 文本 final 与 delta 不一致",
                                retryable=False,
                            )
                        neutral_events = (
                            [TextDelta(output_index, final_text)]
                            if final_text and not streamed_text
                            else []
                        )
                    elif item_type == "reasoning":
                        encrypted = getattr(item, "encrypted_content", None)
                        item_id = getattr(item, "id", None)
                        summary = tuple(
                            str(getattr(part, "text", "") or "")
                            for part in (getattr(item, "summary", None) or [])
                            if isinstance(getattr(part, "text", None), str)
                        )
                        neutral_events = []
                        for summary_index, final_summary in enumerate(summary):
                            streamed_summary = "".join(
                                summary_parts.get((output_index, summary_index), [])
                            )
                            if streamed_summary and final_summary != streamed_summary:
                                raise ModelRequestError(
                                    "Responses reasoning summary final 与 delta 不一致",
                                    retryable=False,
                                )
                            if final_summary and not streamed_summary:
                                neutral_events.append(
                                    ReasoningSummaryDelta(
                                        output_index,
                                        summary_index,
                                        final_summary,
                                    )
                                )
                        continuation = (
                            [
                                OpaqueContinuationReceived(
                                    wire_api=WIRE_API_RESPONSES,
                                    item_id=item_id,
                                    encrypted_content=encrypted,
                                    summary=summary,
                                )
                            ]
                            if isinstance(item_id, str)
                            and item_id
                            and isinstance(encrypted, str)
                            and encrypted
                            else []
                        )
                        neutral_events.extend(continuation)
                    else:
                        neutral_events = []
                else:
                    neutral_events = self._event_to_neutral(event)
                    if event_type == "response.output_text.delta":
                        delta = getattr(event, "delta", None)
                        if isinstance(delta, str) and delta:
                            text_parts.setdefault(output_index, []).append(delta)
                    elif event_type in (
                        "response.reasoning_summary_text.delta",
                        "reasoning_summary_text.delta",
                    ):
                        delta = getattr(event, "delta", None)
                        summary_index = _event_index(event, "summary_index", 0)
                        if isinstance(delta, str) and delta:
                            summary_parts.setdefault(
                                (output_index, summary_index), []
                            ).append(delta)
                for neutral in neutral_events:
                    yield neutral
                    if isinstance(neutral, StreamFailed):
                        return
            if not completed:
                raise ModelRequestError(
                    "Responses 模型流在完成事件前结束", retryable=True
                )
        except Exception as exc:
            if isinstance(exc, ModelRequestError):
                raise
            raise _map_responses_error(exc) from exc
        finally:
            if hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass

    @classmethod
    def _event_to_neutral(cls, event: Any) -> List[ModelStreamEvent]:
        event_type = str(getattr(event, "type", "") or "")
        output_index = _event_index(event, "output_index", 0)
        if event_type == "response.output_text.delta":
            delta = getattr(event, "delta", None) or ""
            if isinstance(delta, str):
                return [TextDelta(output_index, delta)]
        if event_type in (
            "response.reasoning_summary_text.delta",
            "reasoning_summary_text.delta",
        ):
            delta = getattr(event, "delta", None) or ""
            if isinstance(delta, str):
                return [ReasoningSummaryDelta(output_index, 0, delta)]
        if event_type in (
            "response.refusal.delta",
            "response.output_text.refusal.delta",
        ):
            delta = getattr(event, "delta", None) or ""
            if isinstance(delta, str):
                return [RefusalDelta(output_index, delta)]
        # Function-call argument deltas require state from the corresponding
        # output_item.added event and are therefore handled in ``stream``.
        if event_type in (
            "response.function_call_arguments.delta",
            "response.completed",
        ):
            return []
        if event_type == "response.incomplete":
            return [
                StreamFailed(
                    code="response_incomplete",
                    message="Responses 模型输出不完整",
                    retryable=False,
                )
            ]
        if event_type == "response.failed":
            response = getattr(event, "response", None)
            error = getattr(response, "error", None)
            error_code = str(getattr(error, "code", "") or "")
            return [
                StreamFailed(
                    code="response_stream_failed",
                    message="Responses 模型流失败",
                    retryable=error_code in {"server_error", "rate_limit_exceeded"},
                )
            ]
        if event_type == "response.cancelled":
            return [
                StreamFailed(
                    code="response_cancelled",
                    message="Responses 模型流已取消",
                    retryable=False,
                )
            ]
        # Item lifecycle events are stateful and handled in ``stream``.
        return []

    @classmethod
    def _responses_item_to_neutral(
        cls, item: Any, output_index: int
    ) -> List[ModelStreamEvent]:
        item_type = str(getattr(item, "type", "") or "")
        if item_type == "message":
            content = getattr(item, "content", None) or []
            text = "".join(
                str(getattr(part, "text", "") or "")
                for part in content
                if getattr(part, "type", "") == "output_text"
            )
            return [TextDelta(output_index, text)] if text else []
        if item_type == "function_call":
            call_id = getattr(item, "id", None) or getattr(item, "call_id", None)
            name = getattr(item, "name", None)
            arguments = getattr(item, "arguments", "") or ""
            events: List[ModelStreamEvent] = []
            if call_id or name:
                events.append(
                    ToolCallStarted(output_index, 0, call_id=call_id, name=name)
                )
            if isinstance(arguments, str) and arguments:
                events.append(ToolCallArgumentsDelta(output_index, 0, arguments))
            return events
        if item_type == "reasoning":
            summary = getattr(item, "summary", None) or []
            text = "".join(str(getattr(part, "text", "") or "") for part in summary)
            return [ReasoningSummaryDelta(output_index, 0, text)] if text else []
        return []


def _responses_input(messages: Sequence[dict]) -> List[dict]:
    """Convert provider-neutral messages to a minimal Responses input list.

    This is intentionally adapter-local; it does not leak into AgentLoop.
    """
    out: List[dict] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            out.append({"role": "system", "content": content or ""})
        elif role == "user":
            out.append({"role": "user", "content": content or ""})
        elif role == "assistant":
            for continuation in message.get("_provider_continuations") or []:
                if not isinstance(continuation, dict):
                    continue
                if continuation.get("wire_api") != WIRE_API_RESPONSES:
                    continue
                item_id = continuation.get("item_id")
                encrypted = continuation.get("encrypted_content")
                if not isinstance(item_id, str) or not isinstance(encrypted, str):
                    continue
                summary = continuation.get("summary") or []
                out.append(
                    {
                        "type": "reasoning",
                        "id": item_id,
                        "encrypted_content": encrypted,
                        "summary": [
                            {"type": "summary_text", "text": text}
                            for text in summary
                            if isinstance(text, str)
                        ],
                    }
                )
            if content:
                out.append({"role": "assistant", "content": content})
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict):
                    continue
                out.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )
        elif role == "tool":
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id", ""),
                    "output": content or "",
                }
            )
    return out


def _event_index(event: Any, field: str, default: int) -> int:
    try:
        value = int(getattr(event, field, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _response_item_id(item: Any) -> str:
    return str(getattr(item, "id", None) or getattr(item, "call_id", None) or "")


def _responses_tools(tools: Sequence[dict]) -> List[dict]:
    return [
        {
            "type": "function",
            "name": tool["function"]["name"],
            "parameters": tool["function"].get("parameters"),
        }
        for tool in tools
    ]
