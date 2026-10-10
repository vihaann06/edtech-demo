"""Tests for app.llm with a fake streaming Anthropic client (no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from app import llm
from app.config import get_settings


class FakeStream:
    def __init__(self, chunks: list[str], final: Any) -> None:
        self._chunks = chunks
        self._final = final

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def __iter__(self):
        snapshot = ""
        for chunk in self._chunks:
            snapshot += chunk
            yield SimpleNamespace(type="text", text=chunk, snapshot=snapshot)

    def get_final_message(self) -> Any:
        return self._final


class FakeClient:
    """Mimics `client.messages.stream(**kwargs)`; each call pops the next scripted outcome."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _message(blocks: list[Any], stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        stop_details=None,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )


def _text(t: str) -> Any:
    return SimpleNamespace(type="text", text=t)


def _status_error(cls: type[anthropic.APIStatusError], status: int, msg: str) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(msg, response=httpx2.Response(status, request=request), body=None)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_REFUSAL_FALLBACK", "true")
    get_settings.cache_clear()
    llm.reset_client_for_tests()
    yield
    get_settings.cache_clear()
    llm.reset_client_for_tests()


def _install(monkeypatch: pytest.MonkeyPatch, outcomes: list[Any]) -> FakeClient:
    client = FakeClient(outcomes)
    monkeypatch.setattr(llm, "_client", client)
    return client


def test_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    get_settings.cache_clear()
    with pytest.raises(llm.LLMNotConfiguredError):
        llm.generate_text(system="s", messages="hi", purpose="test", model="m")


def test_success_streams_and_sets_request_options(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, [FakeStream(["Hel", "lo"], _message([_text("Hello")]))])
    started: list[bool] = []
    snapshots: list[str] = []
    result = llm.generate_text(
        system="SYSTEM",
        messages="hi",
        purpose="test",
        model="claude-opus-5-5",
        effort="high",
        on_output_start=lambda: started.append(True),
        on_text=snapshots.append,
    )
    assert result.text == "Hello"
    assert started == [True]
    assert snapshots == ["Hel", "Hello"]
    kwargs = client.calls[0]
    assert kwargs["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["extra_body"] == {"fallbacks": "default"}
    assert kwargs["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}


def test_no_effort_and_no_fallback_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_REFUSAL_FALLBACK", "false")
    get_settings.cache_clear()
    client = _install(monkeypatch, [FakeStream(["x"], _message([_text("x")]))])
    llm.generate_text(system="s", messages="hi", purpose="test", model="m")
    assert "output_config" not in client.calls[0]
    assert "extra_body" not in client.calls[0]


def test_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [FakeStream([], _message([], stop_reason="refusal"))])
    with pytest.raises(llm.LLMRefusalError):
        llm.generate_text(system="s", messages="hi", purpose="test", model="m")


def test_truncated_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [FakeStream(["abc"], _message([_text("abc")], stop_reason="max_tokens"))])
    with pytest.raises(llm.LLMOutputError) as info:
        llm.generate_text(system="s", messages="hi", purpose="test", model="m")
    assert info.value.raw_text == "abc"


def test_text_after_fallback_block_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    blocks = [_text("partial from first model"), SimpleNamespace(type="fallback"), _text("full answer")]
    _install(monkeypatch, [FakeStream(["..."], _message(blocks))])
    result = llm.generate_text(system="s", messages="hi", purpose="test", model="m")
    assert result.text == "full answer"


def test_fallback_rejected_retries_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    rejected = _status_error(anthropic.BadRequestError, 400, "fallbacks: unsupported for this model")
    client = _install(monkeypatch, [rejected, FakeStream(["ok"], _message([_text("ok")])), FakeStream(["ok"], _message([_text("ok")]))])
    assert llm.generate_text(system="s", messages="hi", purpose="test", model="m").text == "ok"
    assert "extra_body" in client.calls[0] and "extra_body" not in client.calls[1]
    # Stays disabled for later calls in this process.
    llm.generate_text(system="s", messages="hi", purpose="test", model="m")
    assert "extra_body" not in client.calls[2]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_status_error(anthropic.AuthenticationError, 401, "bad key"), llm.LLMNotConfiguredError),
        (_status_error(anthropic.RateLimitError, 429, "slow down"), llm.LLMError),
        (_status_error(anthropic.InternalServerError, 500, "boom"), llm.LLMError),
        (_status_error(anthropic.BadRequestError, 400, "messages: invalid"), llm.LLMError),
    ],
)
def test_error_mapping(monkeypatch: pytest.MonkeyPatch, error: Exception, expected: type[Exception]) -> None:
    client = _install(monkeypatch, [error])
    with pytest.raises(expected) as info:
        llm.generate_text(system="s", messages="hi", purpose="test", model="m")
    assert info.value.user_message  # UI-safe message is always present
    assert len(client.calls) == 1  # no hidden duplicate requests


class Plan(BaseModel):
    title: str
    steps: list[str]


def test_structured_success_sends_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = '{"title": "T", "steps": ["a", "b"]}'
    client = _install(monkeypatch, [FakeStream([payload], _message([_text(payload)]))])
    plan, result = llm.generate_structured(system="s", messages="hi", schema=Plan, purpose="plan", model="m", effort="medium")
    assert plan == Plan(title="T", steps=["a", "b"])
    fmt = client.calls[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["required"] == ["title", "steps"]
    assert client.calls[0]["output_config"]["effort"] == "medium"


def test_structured_invalid_raises_with_raw_text(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = '{"title": "T"}'
    _install(monkeypatch, [FakeStream([payload], _message([_text(payload)]))])
    with pytest.raises(llm.LLMOutputError) as info:
        llm.generate_structured(system="s", messages="hi", schema=Plan, purpose="plan", model="m")
    assert info.value.raw_text == payload
    assert "steps" in (info.value.detail or "")
