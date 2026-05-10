"""LLM abstraction for Layer 3 AI Core.

Routes to:
  - Anthropic Claude (cloud mode / hybrid mode) via the `anthropic` SDK
  - Ollama local server (offline mode / hybrid fallback) via HTTP
  - Groq via the `groq` SDK

The mode is controlled by settings.ai_core_model_mode:
  - "offline"  → Ollama only, raise if unreachable
  - "cloud"    → Claude only, raise if key missing
  - "hybrid"   → Claude first, fall back to Ollama on any error
  - "groq"     → Groq only, raise if key missing
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Claude models ──────────────────────────────────────────────────────────────
_CLAUDE_FAST = "claude-haiku-4-5"       # cheap bulk passes
_CLAUDE_POWER = "claude-sonnet-4-5"     # complex reasoning

# ── Groq models ────────────────────────────────────────────────────────────────
_GROQ_MODEL = "llama3-70b-8192"


class LLMClient:
    """Thread-safe, async LLM client with offline/cloud/hybrid routing."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._anthropic: Any = None
        self._groq: Any = None
        self._http: httpx.AsyncClient | None = None
        self._mode = self._settings.ai_core_model_mode

    async def __aenter__(self) -> "LLMClient":
        self._http = httpx.AsyncClient(timeout=120.0)
        
        # Auto-detect best available mode if "gemini" is set but keys vary
        if self._mode == "gemini" or not self._mode:
            if self._settings.gemini_api_key:
                self._mode = "gemini"
            elif self._settings.groq_api_key:
                self._mode = "groq"
            elif self._settings.anthropic_api_key:
                self._mode = "cloud"
            else:
                self._mode = "offline"

        if self._mode == "gemini":
            self._gemini = self._load_gemini()
        if self._mode == "groq":
            self._groq = self._load_groq()
        if self._mode in ("cloud", "hybrid"):
            self._anthropic = self._load_anthropic()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def complete(
        self,
        system: str,
        user: str,
        *,
        model: str = _CLAUDE_FAST,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        response_format: str = "text",   # "text" | "json"
    ) -> str:
        """Call LLM and return the text response.

        Args:
            system: System-level instruction.
            user: User-level prompt / document content.
            model: Claude model ID (ignored for Ollama/Gemini/Groq).
            max_tokens: Maximum output tokens.
            temperature: Sampling temperature.
            response_format: "json" forces JSON output.

        Returns:
            Raw LLM text response.
        """
        if self._mode == "gemini":
            return await self._gemini_complete(system, user, max_tokens, temperature, response_format)

        if self._mode == "groq":
            return await self._groq_complete(system, user, max_tokens, temperature, response_format)

        if self._mode == "offline":
            return await self._ollama(system, user, max_tokens, temperature, response_format)

        if self._mode == "cloud":
            return await self._claude(system, user, model, max_tokens, temperature, response_format)

        # hybrid: claude first, ollama fallback
        try:
            return await self._claude(system, user, model, max_tokens, temperature, response_format)
        except Exception as exc:
            logger.warning(
                "Claude call failed — falling back to Ollama",
                extra={"error": str(exc)},
            )
            return await self._ollama(system, user, max_tokens, temperature, response_format)

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str = _CLAUDE_FAST,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Like complete() but parses and returns the JSON dict.

        Strips markdown code fences if the model wraps the JSON.
        Raises ValueError if parsing fails.
        """
        raw = await self.complete(
            system, user, model=model, max_tokens=max_tokens, response_format="json"
        )
        return self._parse_json(raw)

    # ── Gemini backend ─────────────────────────────────────────────────────────

    def _load_gemini(self) -> Any:
        try:
            import google.generativeai as genai
            genai.configure(api_key=self._settings.gemini_api_key)
            return genai.GenerativeModel(
                model_name=self._settings.gemini_model,
                system_instruction=None # Set in generate_content
            )
        except ImportError:
            logger.warning("google-generativeai SDK not installed — Gemini unavailable")
            return None

    async def _gemini_complete(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        response_format: str,
    ) -> str:
        if self._gemini is None:
            raise RuntimeError("Gemini client not available (SDK not installed or no API key)")

        import google.generativeai as genai
        
        # Gemini uses system_instruction in constructor usually, but we can wrap it in the prompt 
        # or recreate the model. Recreating is safer for dynamic system prompts.
        model = genai.GenerativeModel(
            model_name=self._settings.gemini_model,
            system_instruction=system
        )
        
        config = genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            response_mime_type="application/json" if response_format == "json" else "text/plain"
        )
        
        # Blocking call wrap in thread for aiokafka/fastapi event loop
        import asyncio
        response = await asyncio.to_thread(
            model.generate_content,
            user,
            generation_config=config
        )
        
        text = response.text
        logger.debug("Gemini call complete", extra={"model": self._settings.gemini_model})
        return text

    # ── Claude backend ─────────────────────────────────────────────────────────

    def _load_anthropic(self) -> Any:
        try:
            import anthropic  # type: ignore[import]
            return anthropic.AsyncAnthropic(api_key=self._settings.anthropic_api_key)
        except ImportError:
            logger.warning("anthropic SDK not installed — cloud LLM unavailable")
            return None

    async def _claude(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        response_format: str,
    ) -> str:
        if self._anthropic is None:
            raise RuntimeError("Anthropic client not available (SDK not installed or no API key)")

        suffix = "\n\nRespond with valid JSON only." if response_format == "json" else ""
        message = await self._anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user + suffix}],
        )
        content = message.content[0]
        text: str = content.text if hasattr(content, "text") else str(content)

        logger.debug(
            "Claude call complete",
            extra={
                "model": model,
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        )
        return text

    # ── Groq backend ───────────────────────────────────────────────────────────

    def _load_groq(self) -> Any:
        try:
            from groq import AsyncGroq
            return AsyncGroq(api_key=self._settings.groq_api_key)
        except ImportError:
            logger.warning("groq SDK not installed — groq LLM unavailable")
            return None

    async def _groq_complete(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        response_format: str,
    ) -> str:
        if self._groq is None:
            raise RuntimeError("Groq client not available (SDK not installed or no API key)")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        
        kwargs: dict[str, Any] = {
            "model": _GROQ_MODEL,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
            messages[0]["content"] += "\n\nRespond with valid JSON only."

        response = await self._groq.chat.completions.create(**kwargs)
        text = response.choices[0].message.content or ""
        
        logger.debug(
            "Groq call complete",
            extra={"model": _GROQ_MODEL},
        )
        return text

    # ── Ollama backend ─────────────────────────────────────────────────────────

    async def _ollama(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        response_format: str,
    ) -> str:
        if self._http is None:
            raise RuntimeError("HTTP client not initialised — use async context manager")

        payload: dict[str, Any] = {
            "model": self._settings.ollama_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        if response_format == "json":
            payload["format"] = "json"

        url = f"{self._settings.ollama_base_url.rstrip('/')}/api/chat"
        response = await self._http.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        text: str = data["message"]["content"]

        logger.debug(
            "Ollama call complete",
            extra={
                "model": self._settings.ollama_model,
                "eval_count": data.get("eval_count", 0),
            },
        )
        return text

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """Strip markdown fences and parse JSON."""
        text = raw.strip()
        # Strip ```json ... ``` or ``` ... ```
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
        return json.loads(text.strip())


# ── Convenience factory ────────────────────────────────────────────────────────

def get_llm_client() -> LLMClient:
    """Return a new LLMClient instance (use as async context manager)."""
    return LLMClient()
