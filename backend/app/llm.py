"""Thin wrapper around the Anthropic Messages API.

Every AI call in the pipeline (lesson planning, Manim code generation, code
repair) goes through `generate_text` or `generate_structured`. They:

* stream the response (long outputs never hit HTTP timeouts),
* cache the system prompt (it is large and identical across calls),
* set the effort level (Claude Opus 5.5 always uses adaptive thinking),
* opt into server-side refusal fallback,
* map SDK exceptions to `LLMError` subclasses with UI-safe messages.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.pipeline.types import PipelineError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(PipelineError):
    """Any failure talking to the model."""


class LLMNotConfiguredError(LLMError):
    """No API credentials, or the credentials were rejected."""


class LLMRefusalError(LLMError):
    """The model declined the request."""


class LLMOutputError(LLMError):
    """The model answered, but the output was unusable (truncated, invalid JSON, schema mismatch)."""

    def __init__(self, user_message: str, *, detail: str | None = None, raw_text: str = "") -> None:
        super().__init__(user_message, detail=detail)
        self.raw_text = raw_text


@dataclass
class LLMResult:
    text: str
    model: str
    stop_reason: str | None
    duration_s: float
    usage: dict[str, Any] = field(default_factory=dict)


_client: anthropic.Anthropic | None = None
_fallback_enabled: bool | None = None


def is_configured() -> bool:
    return get_settings().anthropic_configured


def _get_client() -> anthropic.Anthropic:
    global _client
    if not is_configured():
        raise LLMNotConfiguredError(
            "AI generation is not configured. Set ANTHROPIC_API_KEY in the .env file and restart the backend."
        )
    if _client is None:
        # User-scoped keys (not tied to a workspace) must name the workspace to bill.
        workspace_id = get_settings().anthropic_workspace_id
        default_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        _client = anthropic.Anthropic(timeout=600.0, max_retries=2, default_headers=default_headers)
    return _client


def _fallback_on() -> bool:
    global _fallback_enabled
    if _fallback_enabled is None:
        _fallback_enabled = get_settings().refusal_fallback
    return _fallback_enabled


def _usage_dict(message: Any) -> dict[str, Any]:
    usage = getattr(message, "usage", None)
    if usage is None:
        return {}
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: getattr(usage, k, None) for k in keys}


def _final_text(message: Any) -> str:
    """Join text blocks, keeping only what came after the last fallback switch.

    With server-side fallback, a mid-stream decline leaves a partial text block
    from the first model, then a `fallback` block, then the fallback model's
    full answer. The partial must be discarded.
    """
    parts: list[str] = []
    for block in message.content:
        block_type = getattr(block, "type", None)
        if block_type == "fallback":
            parts = []
        elif block_type == "text":
            parts.append(block.text)
    return "".join(parts)


def generate_text(
    *,
    system: str,
    messages: list[dict[str, Any]] | str,
    purpose: str,
    model: str,
    effort: str = "",
    max_tokens: int = 32000,
    output_schema: dict[str, Any] | None = None,
    on_output_start: Optional[Callable[[], None]] = None,
    on_text: Optional[Callable[[str], None]] = None,
) -> LLMResult:
    """Stream one Messages API call and return the final text.

    `on_output_start` fires once, when the first output text token arrives
    (i.e. after the model has finished thinking). `on_text` receives the
    accumulated text of the current text block after every delta.
    """
    client = _get_client()
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]

    output_config: dict[str, Any] = {}
    if effort:
        output_config["effort"] = effort
    if output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": output_schema}

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        # The system prompt is long and identical across calls: cache it.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
    }
    if output_config:
        kwargs["output_config"] = output_config
    if _fallback_on():
        kwargs["extra_headers"] = {"anthropic-beta": _FALLBACK_BETA}
        kwargs["extra_body"] = {"fallbacks": "default"}

    started = time.monotonic()
    try:
        message = _call(client, kwargs, on_output_start, on_text, purpose)
    except _FallbackRejected as exc:
        # This account/model rejects the fallback opt-in: turn it off for the process and retry once.
        global _fallback_enabled
        _fallback_enabled = False
        log.warning("Server-side refusal fallback rejected; retrying without it: %s", exc.detail)
        kwargs.pop("extra_headers", None)
        kwargs.pop("extra_body", None)
        message = _call(client, kwargs, on_output_start, on_text, purpose)

    duration = time.monotonic() - started
    usage = _usage_dict(message)
    log.info("LLM %s: model=%s stop=%s %.1fs usage=%s", purpose, message.model, message.stop_reason, duration, usage)

    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise LLMRefusalError(
            "The AI declined to explain this topic. Try rephrasing your question.",
            detail=str(details),
        )
    text = _final_text(message)
    if message.stop_reason == "max_tokens":
        raise LLMOutputError(
            f"The AI response for {purpose} was too long and got cut off.",
            detail=f"stop_reason=max_tokens, {len(text)} chars",
            raw_text=text,
        )
    if not text.strip():
        raise LLMOutputError(f"The AI returned an empty response for {purpose}.", raw_text=text)

    return LLMResult(text=text, model=message.model, stop_reason=message.stop_reason, duration_s=duration, usage=usage)


def _stream(
    client: anthropic.Anthropic,
    kwargs: dict[str, Any],
    on_output_start: Optional[Callable[[], None]],
    on_text: Optional[Callable[[str], None]],
) -> Any:
    output_started = False
    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            if event.type == "text":
                if not output_started:
                    output_started = True
                    if on_output_start:
                        on_output_start()
                if on_text:
                    on_text(event.snapshot)
        return stream.get_final_message()


class _FallbackRejected(LLMError):
    """Internal: the request was rejected because of the `fallbacks` opt-in."""


def _call(
    client: anthropic.Anthropic,
    kwargs: dict[str, Any],
    on_output_start: Optional[Callable[[], None]],
    on_text: Optional[Callable[[str], None]],
    purpose: str,
) -> Any:
    """Run one streamed request, translating SDK exceptions into LLMError subclasses."""
    try:
        return _stream(client, kwargs, on_output_start, on_text)
    except anthropic.AuthenticationError as exc:
        raise LLMNotConfiguredError(
            "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in the .env file.", detail=str(exc)
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise LLMNotConfiguredError("The Anthropic API key lacks permission for this model.", detail=str(exc)) from exc
    except anthropic.NotFoundError as exc:
        raise LLMError(f"The configured model '{kwargs['model']}' was not found.", detail=str(exc)) from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("The AI service is rate limiting requests. Please try again in a minute.", detail=str(exc)) from exc
    except anthropic.BadRequestError as exc:
        if "extra_body" in kwargs and "fallback" in str(exc).lower():
            raise _FallbackRejected("fallback opt-in rejected", detail=str(exc)) from exc
        if "anthropic-workspace-id" in str(exc):
            raise LLMNotConfiguredError(
                "This Anthropic API key isn't scoped to a workspace. Set ANTHROPIC_WORKSPACE_ID in the .env file "
                "(or use a workspace-scoped key) and restart the backend.",
                detail=str(exc),
            ) from exc
        raise LLMError(f"The AI request for {purpose} was rejected.", detail=str(exc)) from exc
    except anthropic.APIStatusError as exc:
        raise LLMError("The AI service had a temporary problem. Please try again.", detail=str(exc)) from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the AI service. Check your internet connection.", detail=str(exc)) from exc


def generate_structured(
    *,
    system: str,
    messages: list[dict[str, Any]] | str,
    schema: type[T],
    purpose: str,
    model: str,
    effort: str = "",
    max_tokens: int = 16000,
    on_output_start: Optional[Callable[[], None]] = None,
) -> tuple[T, LLMResult]:
    """Like `generate_text`, but constrains output to `schema` and validates it.

    Raises `LLMOutputError` (with `raw_text`) when the JSON does not satisfy the
    Pydantic model, so callers can retry with the validation error as feedback.
    """
    result = generate_text(
        system=system,
        messages=messages,
        purpose=purpose,
        model=model,
        effort=effort,
        max_tokens=max_tokens,
        output_schema=anthropic.transform_schema(schema),
        on_output_start=on_output_start,
    )
    try:
        parsed = schema.model_validate_json(result.text)
    except ValidationError as exc:
        raise LLMOutputError(
            f"The AI returned an invalid {purpose}.", detail=str(exc), raw_text=result.text
        ) from exc
    except json.JSONDecodeError as exc:  # pragma: no cover - model_validate_json wraps this
        raise LLMOutputError(f"The AI returned malformed JSON for {purpose}.", detail=str(exc), raw_text=result.text) from exc
    return parsed, result


def reset_client_for_tests() -> None:
    global _client, _fallback_enabled
    _client = None
    _fallback_enabled = None


if os.environ.get("EXPLAINER_LLM_DEBUG"):
    logging.getLogger("anthropic").setLevel(logging.DEBUG)
