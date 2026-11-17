"""
LLM provider wrapper. The agent talks to the small interface below, never to the
Gemini SDK directly, so the provider can be swapped and tests can use a scripted fake.

Interface:
    llm.start_chat(system, tools) -> ChatSession
    await session.send_text(text)            -> Step
    await session.send_tool_results(results) -> Step
    await llm.structured(system, prompt, schema) -> pydantic model

Free-tier handling (Gemini ~15 requests/min):
    - 429 / 5xx: exponential backoff, then fall through to the next model in GEMINI_MODELS
    - one request in flight at a time across the whole process (semaphore), so two
      webhooks arriving together queue instead of burning the per-minute quota together
"""

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger("llm")


@dataclass
class ToolCall:
    id: str | None
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    call: ToolCall
    output: dict[str, Any]


@dataclass
class Step:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the arguments


class LLMUnavailable(Exception):
    """Raised when every model failed (quota exhausted, outage, bad key)."""


class ChatSession(Protocol):
    async def send_text(self, text: str) -> Step: ...
    async def send_tool_results(self, results: list[ToolResult]) -> Step: ...


class LLM(Protocol):
    def start_chat(self, system: str, tools: list[ToolSpec]) -> ChatSession: ...
    async def structured(self, system: str, prompt: str, schema: type[BaseModel]) -> BaseModel: ...


# ─────────────────────────────────────────────────────────────
# Gemini implementation
# ─────────────────────────────────────────────────────────────

_gemini_lock = asyncio.Semaphore(1)
_RETRYABLE = {408, 429, 500, 502, 503, 504}


class GeminiLLM:
    def __init__(self, api_key: str | None = None, models: list[str] | None = None):
        from google import genai  # deferred: keeps app import fast and tests free of the SDK
        from google.genai import types

        key = api_key or settings.GEMINI_API_KEY
        if not key:
            raise LLMUnavailable("GEMINI_API_KEY is not set")
        self._client = genai.Client(
            api_key=key, http_options=types.HttpOptions(timeout=settings.GEMINI_TIMEOUT_S * 1000)
        )
        self.models = models or settings.GEMINI_MODELS
        # Chat history is only valid for the model that produced it (Gemini attaches
        # thought signatures to function calls), so a session sticks to one model once chosen.

    async def _generate(self, contents, config, models: list[str]):
        from google.genai import errors

        last_error: Exception | None = None
        for model in models:
            for attempt in range(3):
                try:
                    async with _gemini_lock:
                        resp = await self._client.aio.models.generate_content(
                            model=model, contents=contents, config=config
                        )
                    return model, resp
                except errors.APIError as exc:
                    last_error = exc
                    code = getattr(exc, "code", None)
                    if code not in _RETRYABLE:
                        logger.warning("Gemini %s failed with %s, not retrying: %s", model, code, str(exc)[:200])
                        break  # wrong model name, bad request: try the next model
                    if code == 503 and len(models) > 1 and attempt == 0:
                        # On turn 1 with multiple models available, move to the next model immediately
                        logger.warning("Gemini %s is overloaded (503), trying next model", model)
                        break
                    delay = min(15.0, (2**attempt) * 2 + random.random())
                    logger.warning("Gemini %s returned %s, retry %d in %.1fs", model, code, attempt + 1, delay)
                    await asyncio.sleep(delay)
                except (asyncio.TimeoutError, OSError) as exc:  # includes httpx timeouts/connection errors
                    last_error = exc
                    logger.warning("Gemini %s timed out or connection failed (%s), retry %d", model, exc, attempt + 1)
                    if attempt >= 2:
                        break
                    await asyncio.sleep(2.0)
                except Exception as exc:
                    if "timeout" not in type(exc).__name__.lower():
                        raise
                    last_error = exc
                    logger.warning("Gemini %s timed out, retry %d", model, attempt + 1)
                    if attempt >= 2:
                        break
                    await asyncio.sleep(2.0)
        raise LLMUnavailable(f"All Gemini models failed. Last error: {last_error}")

    def start_chat(self, system: str, tools: list[ToolSpec]) -> "GeminiChat":
        return GeminiChat(self, system, tools)

    async def structured(self, system: str, prompt: str, schema: type[BaseModel]) -> BaseModel:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
        )
        contents = prompt
        for attempt in range(2):
            _, resp = await self._generate(contents, config, self.models)
            if isinstance(resp.parsed, schema):
                return resp.parsed
            # Validate ourselves and feed the error back once.
            try:
                return schema.model_validate_json(resp.text or "")
            except Exception as exc:
                logger.warning("Structured output invalid (attempt %d): %s", attempt + 1, exc)
                contents = (
                    f"{prompt}\n\nYour previous answer was not valid JSON for the schema: {exc}. "
                    "Return only valid JSON."
                )
        raise LLMUnavailable("Model did not return valid structured output")


class GeminiChat:
    def __init__(self, llm: GeminiLLM, system: str, tools: list[ToolSpec]):
        from google.genai import types

        self._llm = llm
        self._contents: list = []
        self._model: str | None = None
        self._config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name, description=t.description, parameters_json_schema=t.parameters
                        )
                        for t in tools
                    ]
                )
            ],
            # We run the tools ourselves so every call is logged and capped.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    async def _send(self, parts) -> Step:
        from google.genai import types

        self._contents.append(types.Content(role="user", parts=parts))
        models = [self._model] if self._model else self._llm.models
        self._model, resp = await self._llm._generate(self._contents, self._config, models)

        candidate = resp.candidates[0] if resp.candidates else None
        if candidate is None or candidate.content is None:
            reason = getattr(candidate, "finish_reason", None) if candidate else "no candidates"
            raise LLMUnavailable(f"Empty response from Gemini ({reason})")
        # Append the model turn verbatim: it carries thought signatures Gemini needs back.
        self._contents.append(candidate.content)

        step = Step()
        for part in candidate.content.parts or []:
            if part.function_call:
                fc = part.function_call
                step.tool_calls.append(ToolCall(id=fc.id, name=fc.name, args=dict(fc.args or {})))
            elif part.text and not getattr(part, "thought", False):
                step.text += part.text
        return step

    async def send_text(self, text: str) -> Step:
        from google.genai import types

        return await self._send([types.Part.from_text(text=text)])

    async def send_tool_results(self, results: list[ToolResult]) -> Step:
        from google.genai import types

        parts = []
        for r in results:
            part = types.Part.from_function_response(name=r.call.name, response=r.output)
            if r.call.id:
                part.function_response.id = r.call.id
            parts.append(part)
        return await self._send(parts)

