"""OpenAI-compatible adapter tests: normalization and error classification."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

from coding_agent.errors import ModelRequestError
from coding_agent.model_client import OpenAIModelClient, normalize_response

REQUEST = httpx.Request("POST", "http://localhost/v1/chat/completions")


def _response(status: int = 200, request: httpx.Request = REQUEST) -> httpx.Response:
    return httpx.Response(status, request=request)


def make_sdk_response(content="hello", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeCompletions:
    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self._exception is not None:
            raise self._exception
        return self._response


class FakeClient:
    def __init__(self, response=None, exception=None):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(response=response, exception=exception)
        )


def make_client(response=None, exception=None):
    return OpenAIModelClient(
        api_key="sk-test", model="test-model", client=FakeClient(response, exception)
    )


def test_normalizes_text_response():
    client = make_client(response=make_sdk_response("plain answer"))
    turn = client.request([{"role": "user", "content": "hi"}], [])
    assert turn.text == "plain answer"
    assert turn.tool_calls == ()


def test_normalizes_multiple_tool_calls_with_raw_arguments():
    calls = [
        SimpleNamespace(
            id="a",
            type="function",
            function=SimpleNamespace(name="glob", arguments='{"pattern": "*.py"}'),
        ),
        SimpleNamespace(
            id="b",
            type="function",
            function=SimpleNamespace(name="grep", arguments='{"pattern": "x"}'),
        ),
    ]
    client = make_client(response=make_sdk_response("step plan", calls))
    turn = client.request([], [])
    assert turn.text == "step plan"
    assert [c.id for c in turn.tool_calls] == ["a", "b"]
    assert turn.tool_calls[0].arguments_raw == '{"pattern": "*.py"}'


def test_missing_content_and_arguments_become_empty_values():
    calls = [
        SimpleNamespace(
            id="a",
            type="function",
            function=SimpleNamespace(name="glob", arguments=None),
        )
    ]
    client = make_client(response=make_sdk_response(None, calls))
    turn = client.request([], [])
    assert turn.text == ""
    assert turn.tool_calls[0].arguments_raw == "{}"


def test_request_payload_uses_no_stream_and_single_tool_calls():
    client = make_client(response=make_sdk_response("ok"))
    client.request([{"role": "user", "content": "hi"}], [{"type": "function"}])
    kwargs = client._client.chat.completions.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["stream"] is False
    assert kwargs["parallel_tool_calls"] is False
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["tools"] == [{"type": "function"}]


def test_request_without_tools_omits_tool_choice():
    client = make_client(response=make_sdk_response("ok"))
    client.request([{"role": "user", "content": "hi"}], [])
    kwargs = client._client.chat.completions.kwargs
    assert "tool_choice" not in kwargs
    assert "tools" not in kwargs


@pytest.mark.parametrize(
    ("exception", "expected_retryable"),
    [
        (APITimeoutError(request=REQUEST), True),
        (APIConnectionError(request=REQUEST), True),
        (RateLimitError("rate limited", response=_response(429), body=None), True),
        (APIStatusError("server error", response=_response(500), body=None), True),
        (APIStatusError("server error", response=_response(503), body=None), True),
        (APIStatusError("bad request", response=_response(400), body=None), False),
        (APIError("other", request=REQUEST, body=None), False),
    ],
)
def test_exception_classification(exception, expected_retryable):
    client = make_client(exception=exception)
    with pytest.raises(ModelRequestError) as excinfo:
        client.request([], [])
    assert excinfo.value.retryable is expected_retryable


def test_unsupported_tool_call_type_is_non_retryable_error():
    calls = [
        SimpleNamespace(
            id="a",
            type="not_function",
            function=SimpleNamespace(name="f", arguments="{}"),
        )
    ]
    client = make_client(response=make_sdk_response("", calls))
    with pytest.raises(ModelRequestError) as excinfo:
        client.request([], [])
    assert excinfo.value.retryable is False


def test_empty_choices_is_non_retryable_error():
    response = SimpleNamespace(choices=[])
    client = make_client(response=response)
    with pytest.raises(ModelRequestError) as excinfo:
        client.request([], [])
    assert excinfo.value.retryable is False


def test_normalize_response_requires_choices():
    with pytest.raises(ModelRequestError):
        normalize_response(SimpleNamespace(nonsense=True))
