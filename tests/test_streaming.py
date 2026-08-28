"""task_005 streaming contract tests: accumulator, chat adapter, AgentLoop.

All offline. These tests assert that partial deltas never become canonical
history and that the final AssistantTurn is exactly the aggregated text.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from coding_agent.agent import AgentLoop
from coding_agent.completion import CompletionPolicy
from coding_agent.context import ContextManager
from coding_agent.conversations.service import _PersistEventSink
from coding_agent.errors import ModelRequestError
from coding_agent.model_client import OpenAIModelClient
from coding_agent.models import AgentEvent, AssistantMessage, LoopPhase, RunStatus
from coding_agent.streaming import (
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
from coding_agent.tools import build_default_tools
from coding_agent.tools.executor import ToolExecutor
from coding_agent.tools.observation import FileObservationTracker
from coding_agent.tools.paths import Workspace
from coding_agent.tools.policy import WorkspaceToolPolicy


class _RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [event.type for event in self.events]


class FakeStreamingModel:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def capabilities(self):
        from coding_agent.streaming import (
            REASONING_RAW_VISIBLE,
            ModelCapabilities,
        )

        return ModelCapabilities(
            wire_api="openai_chat_completions",
            visible_reasoning=REASONING_RAW_VISIBLE,
        )

    def request(self, messages, tools):
        raise AssertionError("AgentLoop should use stream()")

    def stream(self, messages, tools, *, options=None, cancel=None):
        self.calls += 1
        yield StreamStarted()
        yield TextDelta(0, "Hello ")
        yield ReasoningDelta(0, "thinking visible", visibility="raw_visible")
        yield TextDelta(0, "world")
        yield StreamCompleted(finish_reason="stop")


class TestTurnStreamAccumulator:
    def test_rejects_event_before_start_and_unknown_tool_index(self):
        with pytest.raises(ModelRequestError, match="before stream start"):
            TurnStreamAccumulator().absorb(TextDelta(0, "x"))
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        with pytest.raises(ModelRequestError, match="before tool call start"):
            acc.absorb(ToolCallArgumentsDelta(0, 9, "{}"))

    def test_rejects_conflicting_tool_identity_and_unknown_finish(self):
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        acc.absorb(ToolCallStarted(0, 0, call_id="a", name="glob"))
        with pytest.raises(ModelRequestError, match="conflicting tool call id"):
            acc.absorb(ToolCallStarted(0, 0, call_id="b", name="glob"))
        other = TurnStreamAccumulator()
        other.absorb(StreamStarted())
        with pytest.raises(ModelRequestError, match="finish reason"):
            other.absorb(StreamCompleted("length"))

    def test_orders_outputs_and_maps_refusal_to_assistant_text(self):
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        acc.absorb(TextDelta(1, "second"))
        acc.absorb(TextDelta(0, "first"))
        acc.absorb(StreamCompleted("stop"))
        assert acc.to_turn().text == "firstsecond"

        refusal = TurnStreamAccumulator()
        refusal.absorb(StreamStarted())
        refusal.absorb(RefusalDelta(0, "not allowed"))
        refusal.absorb(StreamCompleted("refusal"))
        assert refusal.to_turn().text == "not allowed"

    def test_aggregates_text_reasoning_and_tool_calls(self):
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        acc.absorb(TextDelta(0, "Hello "))
        acc.absorb(ReasoningDelta(0, "thinking ", visibility="raw_visible"))
        acc.absorb(TextDelta(0, "world"))
        acc.absorb(ToolCallStarted(0, 0, call_id="c1", name="glob"))
        acc.absorb(ToolCallArgumentsDelta(0, 0, '{"pattern":'))
        acc.absorb(ToolCallArgumentsDelta(0, 0, '"*.py"}'))
        acc.absorb(StreamCompleted("tool_calls"))
        turn = acc.to_turn()
        assert turn.text == "Hello world"
        assert acc.reasoning_text.startswith("thinking ")
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].id == "c1"
        assert turn.tool_calls[0].name == "glob"
        assert json.loads(turn.tool_calls[0].arguments_raw) == {"pattern": "*.py"}

    def test_incomplete_tool_call_fails_closed(self):
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        acc.absorb(TextDelta(0, "x"))
        acc.absorb(StreamCompleted("tool_calls"))
        with pytest.raises(Exception):
            acc.to_turn()

    def test_event_after_completion_fails_closed(self):
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        acc.absorb(StreamCompleted("stop"))
        with pytest.raises(ModelRequestError):
            acc.absorb(TextDelta(0, "late"))

    def test_duplicate_stream_start_fails_closed(self):
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        with pytest.raises(ModelRequestError):
            acc.absorb(StreamStarted())

    def test_duplicate_completion_fails_closed(self):
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        acc.absorb(StreamCompleted("stop"))
        with pytest.raises(ModelRequestError):
            acc.absorb(StreamCompleted("stop"))

    def test_unicode_and_json_escape_across_chunk_boundaries(self):
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        acc.absorb(TextDelta(0, "中"))
        acc.absorb(TextDelta(0, "文😀"))
        acc.absorb(ToolCallStarted(0, 0, call_id="c1", name="grep"))
        acc.absorb(ToolCallArgumentsDelta(0, 0, '{"pattern":"a\\"b'))
        acc.absorb(ToolCallArgumentsDelta(0, 0, '\\\\c"}'))
        acc.absorb(StreamCompleted("tool_calls"))
        turn = acc.to_turn()
        assert turn.text == "中文😀"
        args = json.loads(turn.tool_calls[0].arguments_raw)
        assert args == {"pattern": 'a"b\\c'}

    def test_two_thousand_deltas_aggregate_without_loss(self):
        acc = TurnStreamAccumulator()
        acc.absorb(StreamStarted())
        for _ in range(2_000):
            acc.absorb(TextDelta(0, "x"))
        acc.absorb(StreamCompleted("stop"))
        turn = acc.to_turn()
        assert len(turn.text) == 2_000
        assert set(turn.text) == {"x"}


class TestChatStreamingAdapter:
    def make_chunks(self):
        return [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="Hel", tool_calls=None)
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        index=0,
                        delta=SimpleNamespace(content=None, tool_calls=None),
                        finish_reason="tool_calls",
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="lo",
                            reasoning_content=" reason ",
                            tool_calls=None,
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            reasoning_content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="c1",
                                    function=SimpleNamespace(
                                        name="glob", arguments='{"pat'
                                    ),
                                )
                            ],
                        )
                    )
                ]
            ),
        ]

    def test_stream_emits_text_reasoning_tool_fragments(self):
        class FakeCompletions:
            def __init__(self, chunks):
                self.chunks = iter(chunks)

            def create(self, **kwargs):
                assert kwargs["stream"] is True
                return self.chunks

        client = OpenAIModelClient(
            api_key="k",
            model="m",
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=FakeCompletions(self.make_chunks()))
            ),
        )
        events = list(client.stream([{"role": "user", "content": "x"}], []))
        text = "".join(e.delta for e in events if isinstance(e, TextDelta))
        assert text == "Hello"
        reasoning = [e for e in events if isinstance(e, ReasoningDelta)]
        assert reasoning and reasoning[0].delta == " reason "
        tools = [
            e
            for e in events
            if isinstance(e, (ToolCallStarted, ToolCallArgumentsDelta))
        ]
        assert len(tools) == 2
        assert events[-1].finish_reason == "tool_calls"


class TestReasoningRoundTrip:
    def test_provider_message_preserves_visible_reasoning(self):
        from coding_agent.context import to_provider_message
        from coding_agent.models import AssistantMessage, ToolCall

        message = AssistantMessage(
            text="call tool",
            tool_calls=(ToolCall(id="c1", name="glob", arguments_raw="{}"),),
            reasoning="visible thought",
        )
        rendered = to_provider_message(message)
        assert rendered["reasoning_content"] == "visible thought"
        assert rendered["tool_calls"][0]["id"] == "c1"

    def test_chat_payload_strips_reasoning_when_off(self):
        from coding_agent.model_client import OpenAIModelClient
        from coding_agent.streaming import ModelRequestOptions

        client = OpenAIModelClient(
            api_key="k",
            model="m",
            provider_id="deepseek",
            client=SimpleNamespace(),
        )
        messages = [
            {
                "role": "assistant",
                "content": "x",
                "reasoning_content": "hidden",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "glob", "arguments": "{}"},
                    }
                ],
            }
        ]
        payload = client._chat_payload(
            messages,
            [],
            stream=False,
            options=ModelRequestOptions(reasoning_mode="off"),
        )
        assert "reasoning_content" not in payload["messages"][0]
        visible = client._chat_payload(
            messages,
            [],
            stream=False,
            options=ModelRequestOptions(reasoning_mode="visible"),
        )
        assert visible["messages"][0]["reasoning_content"] == "hidden"
        auto = client._chat_payload(
            messages,
            [],
            stream=False,
            options=ModelRequestOptions(reasoning_mode="auto"),
        )
        assert auto["messages"][0]["reasoning_content"] == "hidden"
        next_user = client._chat_payload(
            [*messages, {"role": "user", "content": "next turn"}],
            [],
            stream=False,
            options=ModelRequestOptions(reasoning_mode="auto"),
        )
        assert "reasoning_content" not in next_user["messages"][0]


class TestChatStreamCancellation:
    def test_stream_closes_iterator_when_cancelled(self):
        class FakeCompletions:
            def __init__(self, stream):
                self._stream = stream

            def create(self, **kwargs):
                return self._stream

        class FakeStream:
            def __init__(self):
                self.closed = False
                self._sent = False

            def __iter__(self):
                return self

            def __next__(self):
                if self._sent:
                    raise StopIteration
                self._sent = True
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="x", tool_calls=None),
                            finish_reason=None,
                        )
                    ]
                )

            def close(self):
                self.closed = True

        stream = FakeStream()
        client = OpenAIModelClient(
            api_key="k",
            model="m",
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=FakeCompletions(stream))
            ),
        )
        events = list(
            client.stream([{"role": "user", "content": "x"}], [], cancel=lambda: True)
        )
        assert not any(isinstance(e, TextDelta) for e in events)
        assert not any(isinstance(e, StreamCompleted) for e in events)
        assert stream.closed is True

    def test_missing_finish_marker_is_retryable_failure(self):
        class FakeCompletions:
            def create(self, **kwargs):
                return iter(
                    [
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    index=0,
                                    delta=SimpleNamespace(
                                        content="partial", tool_calls=None
                                    ),
                                    finish_reason=None,
                                )
                            ]
                        )
                    ]
                )

        client = OpenAIModelClient(
            api_key="k",
            model="m",
            client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
        )
        events = list(client.stream([], []))
        failed = next(item for item in events if isinstance(item, StreamFailed))
        assert failed.code == "truncated_stream"
        assert failed.retryable is True


class TestResponsesAdapter:
    def test_maps_text_and_reasoning_summary_events(self):
        class FakeResponses:
            def __init__(self):
                self.chunks = iter(
                    [
                        SimpleNamespace(type="response.output_text.delta", delta="Hi"),
                        SimpleNamespace(
                            type="response.reasoning_summary_text.delta",
                            delta="summary",
                        ),
                        SimpleNamespace(
                            type="response.completed",
                            response=SimpleNamespace(status="completed", usage=None),
                        ),
                    ]
                )

            def create(self, **kwargs):
                return self.chunks

        from coding_agent.model_client import OpenAIResponsesClient

        client = OpenAIResponsesClient(
            api_key="k",
            model="m",
            client=SimpleNamespace(responses=FakeResponses()),
        )
        events = list(client.stream([{"role": "user", "content": "x"}], []))
        texts = [e.delta for e in events if isinstance(e, TextDelta)]
        summaries = [e.delta for e in events if isinstance(e, ReasoningSummaryDelta)]
        assert texts == ["Hi"]
        assert summaries == ["summary"]

    def test_maps_parallel_function_calls_usage_and_opaque_continuation(self):
        class FakeResponses:
            def create(self, **kwargs):
                assert kwargs["store"] is False
                return iter(
                    [
                        SimpleNamespace(
                            type="response.output_item.added",
                            output_index=0,
                            item=SimpleNamespace(
                                type="function_call",
                                id="item-a",
                                call_id="call-a",
                                name="glob",
                            ),
                        ),
                        SimpleNamespace(
                            type="response.output_item.added",
                            output_index=1,
                            item=SimpleNamespace(
                                type="function_call",
                                id="item-b",
                                call_id="call-b",
                                name="grep",
                            ),
                        ),
                        SimpleNamespace(
                            type="response.function_call_arguments.delta",
                            output_index=1,
                            item_id="item-b",
                            delta='{"pattern":"b"}',
                        ),
                        SimpleNamespace(
                            type="response.function_call_arguments.delta",
                            output_index=0,
                            item_id="item-a",
                            delta='{"pattern":"a"}',
                        ),
                        SimpleNamespace(
                            type="response.output_item.done",
                            output_index=2,
                            item=SimpleNamespace(
                                type="reasoning",
                                id="rs_1",
                                encrypted_content="opaque-ciphertext",
                                summary=[SimpleNamespace(text="safe summary")],
                            ),
                        ),
                        SimpleNamespace(
                            type="response.completed",
                            response=SimpleNamespace(
                                status="completed",
                                usage=SimpleNamespace(
                                    input_tokens=3,
                                    output_tokens=5,
                                    output_tokens_details=SimpleNamespace(
                                        reasoning_tokens=2
                                    ),
                                ),
                            ),
                        ),
                    ]
                )

        from coding_agent.model_client import OpenAIResponsesClient

        client = OpenAIResponsesClient(
            api_key="k",
            model="m",
            client=SimpleNamespace(responses=FakeResponses()),
        )
        accumulator = TurnStreamAccumulator()
        events = list(client.stream([{"role": "user", "content": "x"}], []))
        for item in events:
            accumulator.absorb(item)
        turn = accumulator.to_turn()
        assert [call.id for call in turn.tool_calls] == ["call-a", "call-b"]
        assert [
            json.loads(call.arguments_raw)["pattern"] for call in turn.tool_calls
        ] == ["a", "b"]
        assert turn.continuations[0].encrypted_content == "opaque-ciphertext"
        usage = next(item for item in events if isinstance(item, UsageReceived))
        assert (usage.input_tokens, usage.output_tokens, usage.reasoning_tokens) == (
            3,
            5,
            2,
        )

    def test_done_item_does_not_duplicate_text_delta(self):
        class FakeResponses:
            def create(self, **kwargs):
                return iter(
                    [
                        SimpleNamespace(
                            type="response.output_text.delta",
                            output_index=0,
                            delta="Hi",
                        ),
                        SimpleNamespace(
                            type="response.output_item.done",
                            output_index=0,
                            item=SimpleNamespace(
                                type="message",
                                content=[
                                    SimpleNamespace(type="output_text", text="Hi")
                                ],
                            ),
                        ),
                        SimpleNamespace(
                            type="response.completed",
                            response=SimpleNamespace(status="completed", usage=None),
                        ),
                    ]
                )

        from coding_agent.model_client import OpenAIResponsesClient

        events = list(
            OpenAIResponsesClient(
                api_key="k",
                model="m",
                client=SimpleNamespace(responses=FakeResponses()),
            ).stream([], [])
        )
        assert [item.delta for item in events if isinstance(item, TextDelta)] == ["Hi"]

    def test_responses_input_replays_reasoning_before_tool_output(self):
        from coding_agent.model_client import _responses_input

        items = _responses_input(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "_provider_continuations": [
                        {
                            "wire_api": "openai_responses",
                            "item_id": "rs_1",
                            "encrypted_content": "cipher",
                            "summary": ["summary"],
                        }
                    ],
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {"name": "glob", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            ]
        )
        assert [item["type"] for item in items] == [
            "reasoning",
            "function_call",
            "function_call_output",
        ]


class RetryStreamingModel:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, messages, tools):
        raise AssertionError("should use stream")

    def stream(self, messages, tools, *, options=None, cancel=None):
        self.calls += 1
        yield StreamStarted()
        if self.calls == 1:
            yield TextDelta(0, "partial ")
            yield ReasoningDelta(0, "half-thought", visibility="raw_visible")
            raise ModelRequestError("injected mid-stream failure", retryable=True)
        yield TextDelta(0, "final answer")
        yield StreamCompleted(finish_reason="stop")


class TestAgentLoopPartialRetry:
    def test_partial_failure_abandons_old_attempt_and_retries(self, tmp_path):
        model = RetryStreamingModel()
        tracker = FileObservationTracker()
        registry = build_default_tools(Workspace(tmp_path), tracker)
        executor = ToolExecutor(registry, WorkspaceToolPolicy())
        sink = _RecordingSink()
        loop = AgentLoop(
            model_client=model,
            tool_registry=registry,
            tool_executor=executor,
            context_manager=ContextManager(120_000),
            completion_policy=CompletionPolicy(),
            event_sink=sink,
            run_id="r",
            is_cancelled=lambda: False,
            max_provider_attempts=2,
            sleeper=lambda _: None,
        )
        result = loop.run("task")
        assert result.status is RunStatus.SUCCESS
        assert result.final_text == "final answer"
        assert "stream_attempt_abandoned" in sink.types()
        assert model.calls == 2
        assistant_messages = [
            m for m in loop.history if isinstance(m, AssistantMessage)
        ]
        assert len(assistant_messages) == 1
        assert "partial" not in assistant_messages[0].text


class CountingStreamRepo:
    def __init__(self) -> None:
        self.public_batches = 0
        self.public_events = []
        self.checkpoint_batches = 0
        self.checkpoints: dict[tuple[int, str], str] = {}

    def append_public_events_batch(self, entries):
        self.public_batches += 1
        self.public_events.extend(entries)

    def upsert_stream_checkpoints_batch(self, entries):
        self.checkpoint_batches += 1
        for entry in entries:
            key = (int(entry["attempt"]), str(entry["channel"]))
            self.checkpoints[key] = self.checkpoints.get(key, "") + str(entry["text"])


class TestStreamCheckpointQuantitative:
    def test_2000_deltas_use_bounded_checkpoint_batches(self):
        repo = CountingStreamRepo()
        sink = _PersistEventSink(repo, "conv", "turn", "run")
        for index in range(2_000):
            sink.emit(
                AgentEvent(
                    sequence=index + 1,
                    run_id="run",
                    type="assistant_text_delta",
                    step=1,
                    phase=LoopPhase.REQUESTING_MODEL,
                    payload={"attempt": 1, "delta": "x"},
                )
            )
        sink.emit(
            AgentEvent(
                sequence=2_001,
                run_id="run",
                type="run_finished",
                step=1,
                phase=LoopPhase.TERMINAL,
                payload={"status": "SUCCESS"},
            )
        )
        assert repo.public_batches <= 100
        delta_events = [
            entry
            for entry in repo.public_events
            if entry["kind"] == "assistant_text_delta"
        ]
        assert len(delta_events) <= 100
        assert (
            "".join(entry["payload"]["payload"]["delta"] for entry in delta_events)
            == "x" * 2_000
        )
        assert repo.checkpoint_batches <= 100
        assert repo.checkpoints[(1, "text")] == "x" * 2_000


class TestResponsesOpaqueReasoning:
    def test_opaque_reasoning_never_becomes_display_event(self):
        from coding_agent.model_client import OpenAIResponsesClient

        item = SimpleNamespace(
            type="reasoning",
            summary=None,
            opaque="encrypted-provider-secret",
        )
        events = OpenAIResponsesClient._responses_item_to_neutral(item, 0)
        assert events == []


class TestReasoningOff:
    def test_reasoning_off_hides_public_delta_and_canonical_reasoning(self, tmp_path):
        from coding_agent.streaming import ModelRequestOptions

        model = FakeStreamingModel()
        tracker = FileObservationTracker()
        registry = build_default_tools(Workspace(tmp_path), tracker)
        executor = ToolExecutor(registry, WorkspaceToolPolicy())
        sink = _RecordingSink()
        loop = AgentLoop(
            model_client=model,
            tool_registry=registry,
            tool_executor=executor,
            context_manager=ContextManager(120_000),
            completion_policy=CompletionPolicy(),
            event_sink=sink,
            run_id="r",
            is_cancelled=lambda: False,
            request_options=ModelRequestOptions(reasoning_mode="off"),
        )
        result = loop.run("hi")
        assert result.status is RunStatus.SUCCESS
        assert "reasoning_delta" not in sink.types()
        assistant_messages = [
            m for m in loop.history if isinstance(m, AssistantMessage)
        ]
        assert len(assistant_messages) == 1
        # The canonical assistant item still keeps provider-visible reasoning
        # for internal continuation, but it is never published as a public
        # delta or used as ordinary user text.
        assert assistant_messages[0].reasoning is not None


class TestAgentLoopStreaming:
    def test_streaming_model_emits_deltas_and_commits_once(self, tmp_path):
        model = FakeStreamingModel()
        tracker = FileObservationTracker()
        registry = build_default_tools(Workspace(tmp_path), tracker)
        executor = ToolExecutor(registry, WorkspaceToolPolicy())
        sink = _RecordingSink()
        loop = AgentLoop(
            model_client=model,
            tool_registry=registry,
            tool_executor=executor,
            context_manager=ContextManager(120_000),
            completion_policy=CompletionPolicy(),
            event_sink=sink,
            run_id="r",
            is_cancelled=lambda: False,
        )
        result = loop.run("hi")
        assert result.status is RunStatus.SUCCESS
        assert result.final_text == "Hello world"
        assert "assistant_text_delta" in sink.types()
        assert "reasoning_delta" in sink.types()
        assistant_messages = [
            m for m in loop.history if isinstance(m, AssistantMessage)
        ]
        assert len(assistant_messages) == 1
        assert assistant_messages[0].text == "Hello world"
