"""LLM provider abstraction.

One implementation covers every OpenAI-compatible chat-completions API with tool calling:
OpenAI, Groq, OpenRouter, Together, and local Ollama (http://localhost:11434/v1).
When no LLM is configured, ``get_llm_provider()`` returns None and the agent uses its
offline policy (app.services.offline_agent) instead.
"""
import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("llm_service")


class LLMError(Exception):
    pass


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # [{id, name, arguments}]
    usage: Dict[str, int] = field(default_factory=dict)


def _parse_arguments(raw) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _tool_calls_from_text(content: str, tool_names: set) -> List[Dict[str, Any]]:
    """Some small local models emit a tool call as JSON text instead of a structured call."""
    text = content.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text).strip()
    text = re.sub(r"^<tool_call>|</tool_call>$", "", text).strip()
    if not text.startswith("{"):
        return []
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return []
    name = obj.get("name") or obj.get("function")
    if name in tool_names:
        args = obj.get("arguments") or obj.get("parameters") or {}
        return [{"id": f"call_{uuid.uuid4().hex[:8]}", "name": name, "arguments": _parse_arguments(args)}]
    return []


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str, model: str, temperature: float, max_tokens: int,
                 timeout: float):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(base_url=base_url or None, api_key=api_key or "not-needed",
                                  timeout=timeout, max_retries=1)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url

    @property
    def name(self) -> str:
        host = "ollama" if "11434" in (self.base_url or "") else \
            (re.sub(r"^https?://(api\.)?|/.*$", "", self.base_url) if self.base_url else "openai")
        return f"{host}:{self.model}"

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict]] = None,
                   temperature: float = None, max_tokens: int = None, json_mode: bool = False) -> LLMResponse:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("llm_request_failed", provider=self.name, error=str(e))
            raise LLMError(str(e)) from e

        message = response.choices[0].message
        result = LLMResponse(content=(message.content or "").strip())
        if message.tool_calls:
            result.tool_calls = [
                {"id": tc.id or f"call_{uuid.uuid4().hex[:8]}", "name": tc.function.name,
                 "arguments": _parse_arguments(tc.function.arguments)}
                for tc in message.tool_calls
            ]
        elif tools and result.content:
            names = {t["function"]["name"] for t in tools}
            parsed = _tool_calls_from_text(result.content, names)
            if parsed:
                result.tool_calls, result.content = parsed, ""
        if response.usage:
            result.usage = {"prompt_tokens": response.usage.prompt_tokens,
                            "completion_tokens": response.usage.completion_tokens}
        return result


_provider = None
_resolved = False


def get_llm_provider() -> Optional[OpenAICompatibleProvider]:
    """Return the configured LLM provider, or None for offline mode."""
    global _provider, _resolved
    if _resolved:
        return _provider
    s = get_settings()
    mode = s.llm_provider.lower()
    if mode in ("openai", "ollama", "groq") or (mode == "auto" and (s.llm_base_url or s.llm_api_key)):
        _provider = OpenAICompatibleProvider(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_temperature,
                                             s.llm_max_tokens, s.llm_timeout_seconds)
        logger.info("llm_provider", provider=_provider.name)
    else:
        logger.info("llm_provider", provider="offline")
    _resolved = True
    return _provider


def reset_llm_provider():
    """Testing hook: force re-resolution (or inject a provider)."""
    global _provider, _resolved
    _provider, _resolved = None, False


def set_llm_provider(provider):
    global _provider, _resolved
    _provider, _resolved = provider, True


async def warmup():
    """Load the model into memory (relevant for local Ollama) so the first turn isn't slow."""
    provider = get_llm_provider()
    if provider is None:
        return
    try:
        await asyncio.wait_for(provider.chat([{"role": "user", "content": "hi"}], max_tokens=1), 120)
        logger.info("llm_warm", provider=provider.name)
    except Exception as e:
        logger.warning("llm_warmup_failed", error=str(e))
