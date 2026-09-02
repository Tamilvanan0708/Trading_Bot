"""
AI Provider Abstraction: multi-provider validation with free-provider fallback.

Priority chain: Groq → Gemini → OpenRouter → Ollama → BAI (optional) → UNAVAILABLE.

Each provider requires its own API key/env var.  A provider is skipped when
not configured.  Provider failures never generate fake PASS/confidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

from app.core.logging import logger

# ---------------------------------------------------------------------------
# Provider Status
# ---------------------------------------------------------------------------

class ProviderStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    OFFLINE = "OFFLINE"
    RATE_LIMITED = "RATE_LIMITED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    AUTH_ERROR = "AUTH_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNAVAILABLE = "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Provider Result (structured output — all providers return the same schema)
# ---------------------------------------------------------------------------

class ProviderResult:
    """Result from one AI provider validation call."""

    __slots__ = ("status", "score", "reason", "reason_code", "provider", "model",
                 "timestamp", "request_id", "raw_response", "risks", "missing", "fallback")

    def __init__(
        self,
        status: str,
        score: float = 0.0,
        reason: str = "",
        reason_code: str = "",
        provider: str = "",
        model: str = "",
        timestamp: str = "",
        request_id: str = "",
        raw_response: str | None = None,
        risks: list[str] | None = None,
        missing: list[str] | None = None,
        fallback: list[str] | None = None,
    ):
        self.status = status
        self.score = score
        self.reason = reason
        self.reason_code = reason_code or "PROVIDER_ERROR"
        self.provider = provider
        self.model = model
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.request_id = request_id or str(uuid.uuid4())
        self.raw_response = raw_response
        self.risks = risks or []
        self.missing = missing or []
        self.fallback = fallback or []


# ---------------------------------------------------------------------------
# Shared LLM system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an institutional Risk & Quantitative Trade Validator analyzing XAU/USD (Gold) algorithmic trading signals.
You do NOT invent prices or calculate raw indicators. You receive a structured JSON payload from a deterministic strategy engine.

Your role:
1. Break of Structure (BOS) Integrity: Verify that the Break of Structure (BOS) is confirmed by a full candle BODY close across the broken swing level (disqualifying wick sweeps, retail liquidity traps, and false breakouts).
2. Retracement & Entry Geometry:
   - For Fib With Retracement: Validate layered entry at 0.618, 0.500, or 0.382 with Stop Loss strictly at 0.236.
   - For SMC With Fib: Validate institutional discount entry at the 0.680 Golden Pocket with Stop Loss at 0.920.
3. Market Momentum & Volatility: Check if the market is reversing aggressively or slicing counter-trend with high volume. If high-risk opposing momentum is detected, output REJECT to protect capital.
4. Output your decision as JSON conforming strictly to the requested schema.

Output Schema:
{
  "status": "APPROVE" | "REJECT" | "CAUTION",
  "confidence": <float 0-100>,
  "reasoning": "<structured narrative reasoning>",
  "risk_flags": ["<risk flag 1>", "<risk flag 2>"],
  "identified_risks": ["<risk 1>", "<risk 2>"],
  "missing_confirmations": ["<missing confirmation 1>"]
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _context_hash(context: dict) -> str:
    """Short content-addressable hash for deduplication / caching."""
    raw = json.dumps(context, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Local request validation (before any provider network call)
# ---------------------------------------------------------------------------

class ProviderRequestError(Exception):
    """Raised locally when a provider payload is malformed."""

    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(message)


ALLOWED_ROLES = {"system", "user", "assistant", "tool", "function"}


def validate_provider_request(payload: dict, provider: str) -> None:
    """Validate a chat-completion payload locally BEFORE sending.

    Rejects malformed bodies so the provider is never called with an invalid
    request.  Raises ProviderRequestError on failure.
    """
    if payload is None:
        raise ProviderRequestError("REQUEST_INVALID", f"{provider} payload is None")
    if not isinstance(payload, dict):
        raise ProviderRequestError("REQUEST_INVALID", f"{provider} payload must be an object")
    model = payload.get("model")
    if not model or not isinstance(model, str) or not model.strip():
        raise ProviderRequestError("MISSING_MODEL", f"{provider} payload missing valid model")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ProviderRequestError("MISSING_MESSAGES", f"{provider} payload missing messages array")
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            raise ProviderRequestError("INVALID_MESSAGES", f"{provider} message[{i}] is not an object")
        role = m.get("role")
        if role not in ALLOWED_ROLES:
            raise ProviderRequestError("INVALID_ROLE", f"{provider} message[{i}] has invalid role {role!r}")
        content = m.get("content")
        if content is None or (isinstance(content, str) and not content.strip()):
            raise ProviderRequestError("INVALID_CONTENT", f"{provider} message[{i}] has empty content")
    # Reject non-finite numbers
    for k, v in payload.items():
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            raise ProviderRequestError("INVALID_JSON", f"{provider} field {k} is NaN/Infinity")


def _log_request_debug(provider: str, url: str, body: dict) -> None:
    """Log a SAFE diagnostic of the outgoing AI request (no secrets)."""
    try:
        logger.debug(
            "AI_REQUEST_DEBUG provider=%s endpoint=%s method=POST "
            "model=%s messages_count=%s message_roles=%s "
            "content_types=%s optional_fields=%s body_valid_json=%s",
            provider,
            url.replace("https://", ""),
            body.get("model"),
            len(body.get("messages", [])),
            [m.get("role") for m in body.get("messages", [])],
            [type(m.get("content")).__name__ for m in body.get("messages", [])],
            sorted(k for k in body if k not in ("model", "messages")),
            _is_valid_json_body(body),
        )
    except Exception:  # noqa: BLE001 - diagnostics must never crash the flow
        pass


def _is_valid_json_body(body: dict) -> bool:
    try:
        json.dumps(body)
        return True
    except (TypeError, ValueError):
        return False


# Temporary diagnostic instrumentation for the BAI HTTP 400 investigation.
# Controlled by AI_DEBUG_WIRE=1 (or DEBUG log level).  Logs the EXACT
# serialized wire body (secrets only live in headers, never in the body).
def _wire_diagnostics(provider: str, url: str, headers: dict, body: dict) -> None:
    """Log the exact outgoing HTTP request structure + raw serialized body.

    Never logs Authorization / API keys.  Message text is truncated for
    noise control; the JSON structure is preserved in full.
    """
    try:
        import os

        enabled = (os.getenv("AI_DEBUG_WIRE", "") == "1") or logger.isEnabledFor(10)
        if not enabled:
            return
        serialized = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        # Re-parse the exact wire string to verify it is a JSON OBJECT.
        parsed_type = "unknown"
        try:
            parsed = json.loads(serialized)
            parsed_type = type(parsed).__name__
        except (TypeError, ValueError) as exc:
            parsed_type = f"INVALID_JSON({exc})"
        double_encoded = serialized.lstrip().startswith(('"', "'")) or parsed_type == "str"
        headers_safe = {k: ("REDACTED" if k.lower() in ("authorization", "x-api-key", "api-key", "cookie", "set-cookie") else v)
                        for k, v in headers.items()}
        wire_preview = serialized[:3000]
        # Truncate message content but keep the array structure visible.
        preview = " ".join(
            line[:200] + ("..." if len(line) > 200 else "")
            for line in wire_preview.split(" ")
        )
        logger.warning(
            "AI_WIRE_DEBUG provider=%s method=POST url=%s "
            "content_type=%s headers=%s model=%s "
            "top_level_keys=%s messages_count=%s message_roles=%s "
            "content_types=%s optional_fields=%s value_types=%s "
            "wire_length=%s wire_parsed_type=%s double_encoded=%s "
            "wire_body=%s",
            provider,
            url.replace("https://", "").replace("http://", ""),
            headers_safe.get("Content-Type", headers_safe.get("content-type", "")),
            headers_safe,
            body.get("model"),
            sorted(body.keys()),
            len(body.get("messages", [])),
            [m.get("role") for m in body.get("messages", [])],
            [type(m.get("content")).__name__ for m in body.get("messages", [])],
            sorted(k for k in body if k not in ("model", "messages")),
            {k: type(v).__name__ for k, v in body.items()},
            len(serialized),
            parsed_type,
            double_encoded,
            preview,
        )
    except Exception:  # noqa: BLE001 - diagnostics must never crash the flow
        pass


def _safe_response_snippet(res) -> str:
    """Extract a short, secrets-safe snippet from a provider error response."""
    try:
        text = res.text or ""
        if "authorization" in text.lower():
            text = "(redacted)"
        return text[:200]
    except Exception:  # noqa: BLE001
        return "(unavailable)"


def _parse_llm_json(content: str) -> dict:
    """Parse LLM JSON response, stripping markdown fences."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(cleaned)


def _llm_validation_result(parsed: dict, provider: str, model: str, raw: str, rid: str) -> ProviderResult:
    """Convert a parsed LLM response into a ProviderResult."""
    status = (parsed.get("status") or "CAUTION").upper()
    score = float(parsed.get("confidence", 0.0))
    reason = parsed.get("reasoning") or parsed.get("explanation") or ""
    risks = parsed.get("identified_risks") or parsed.get("risk_flags") or []
    missing = parsed.get("missing_confirmations") or []
    return ProviderResult(
        status=status, score=score, reason=reason,
        provider=provider, model=model, request_id=rid,
        raw_response=raw, risks=risks, missing=missing,
    )


# ---------------------------------------------------------------------------
# Base Provider
# ---------------------------------------------------------------------------

class BaseAIProvider(ABC):
    """Abstract AI validation provider."""

    name: str = ""
    default_model: str = ""

    @classmethod
    @abstractmethod
    def is_configured(cls, settings) -> bool:
        ...

    @abstractmethod
    async def validate(self, settings, context: dict, request_id: str) -> ProviderResult:
        ...

    async def check(self, settings) -> ProviderStatus:
        """Quick health check — returns status without consuming quota."""
        return ProviderStatus.AVAILABLE if self.is_configured(settings) else ProviderStatus.OFFLINE


# ---------------------------------------------------------------------------
# OpenAI-compatible provider (Groq, OpenRouter, B.AI, and generic)
# ---------------------------------------------------------------------------

class OpenAICompatibleProvider(BaseAIProvider):
    """Provider using the OpenAI-compatible chat completions endpoint.

    Subclasses set ``name``, ``default_model``, and ``_base_url`` / ``_api_key``
    accessors.  ``supports_response_format`` controls whether the
    ``response_format: {type: json_object}`` field is sent — strict
    OpenAI-compatible providers (e.g. B.AI/BytePlus) reject that field with
    ``400 Invalid request body``, so it is disabled for them.
    """

    name = "OpenAICompatible"
    default_model = "gpt-4o-mini"
    supports_response_format = True

    @classmethod
    def _api_key(cls, settings) -> str:
        return ""

    @classmethod
    def _base_url(cls, settings) -> str:
        return ""

    @classmethod
    def is_configured(cls, settings) -> bool:
        return bool(cls._api_key(settings))

    async def validate(self, settings, context: dict, request_id: str) -> ProviderResult:
        api_key = self._api_key(settings)
        base_url = self._base_url(settings)
        model = getattr(settings, f"{self.name.upper()}_MODEL", self.default_model) or self.default_model
        payload = json.dumps(context, indent=2, default=str)
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Validate this XAU/USD signal:\n\n{payload}"},
            ],
            "temperature": settings.AI_TEMPERATURE,
        }
        # Only send response_format for providers that accept it.  Strict
        # OpenAI-compatible providers reject unknown fields with HTTP 400.
        if self.supports_response_format:
            body["response_format"] = {"type": "json_object"}

        # Local validation BEFORE the network call (never send a malformed body).
        try:
            validate_provider_request(body, self.name)
        except ProviderRequestError as verr:
            logger.warning("%s request validation failed: %s", self.name, verr.reason_code)
            return ProviderResult(
                status="UNAVAILABLE", reason=verr.message,
                reason_code=verr.reason_code, provider=self.name,
                model=model, request_id=request_id, raw_response="LOCAL_VALIDATION_FAILED",
            )

        _log_request_debug(self.name, url, body)
        _wire_diagnostics(self.name, url, headers, body)
        timeout = getattr(settings, "AI_REQUEST_TIMEOUT_SECONDS", 15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException:
            return ProviderResult(status="UNAVAILABLE", score=0, reason="Provider timeout.",
                                  reason_code="TIMEOUT", provider=self.name, model=model,
                                  request_id=request_id, raw_response="TIMEOUT")
        except httpx.NetworkError as exc:
            return ProviderResult(status="UNAVAILABLE", score=0, reason=f"Network error: {exc}",
                                  reason_code="CONNECTION_ERROR", provider=self.name, model=model,
                                  request_id=request_id, raw_response="NETWORK_ERROR")

        if res.status_code == 400:
            # Provider rejected the request body.  Capture the reason safely.
            err_body = _safe_response_snippet(res)
            rid_header = res.headers.get("x-oneapi-request-id", "") or res.headers.get("request-id", "")
            logger.warning(
                "%s returned 400 invalid request body (x-oneapi-request-id=%s): %s",
                self.name, rid_header, err_body,
            )
            return ProviderResult(status="UNAVAILABLE", reason="AI request invalid — check provider payload (400).",
                                  reason_code="INVALID_REQUEST", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"HTTP 400 {err_body}")
        if res.status_code == 401:
            logger.warning("%s returned 401 — invalid API key.", self.name)
            return ProviderResult(status="UNAVAILABLE", reason="Authentication error (401).",
                                  reason_code="AUTHENTICATION_FAILED", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"HTTP {res.status_code}")
        if res.status_code == 402:
            logger.warning("%s returned 402 — insufficient balance.", self.name)
            return ProviderResult(status="UNAVAILABLE", reason="AI provider balance is insufficient.",
                                  reason_code="INSUFFICIENT_BALANCE", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"HTTP {res.status_code}")
        if res.status_code == 429:
            logger.warning("%s returned 429 — rate limited.", self.name)
            return ProviderResult(status="UNAVAILABLE", reason="Rate limited (429).",
                                  reason_code="RATE_LIMITED", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"HTTP {res.status_code}")
        if res.status_code >= 500:
            logger.warning("%s returned %s — server error.", self.name, res.status_code)
            return ProviderResult(status="UNAVAILABLE", reason=f"Server error ({res.status_code}).",
                                  reason_code="PROVIDER_ERROR", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"HTTP {res.status_code}")

        try:
            res.raise_for_status()
            data = res.json()
            content = data["choices"][0]["message"]["content"]
            parsed = _parse_llm_json(content)
            return _llm_validation_result(parsed, self.name, model, content, request_id)
        except (KeyError, IndexError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("%s returned malformed response: %s", self.name, exc)
            return ProviderResult(status="UNAVAILABLE", reason="Invalid/malformed response.",
                                  reason_code="INVALID_RESPONSE", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"PARSE_ERROR: {exc}")


# ---------------------------------------------------------------------------
# Concrete Providers
# ---------------------------------------------------------------------------

class GroqProvider(OpenAICompatibleProvider):
    name = "GROQ"
    default_model = "llama-3.3-70b-versatile"
    @classmethod
    def _base_url(cls, settings): return "https://api.groq.com/openai/v1"
    @classmethod
    def _api_key(cls, settings): return getattr(settings, "GROQ_API_KEY", "")


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "OPENROUTER"
    default_model = "deepseek/deepseek-r1-distill-qwen-32b:free"
    @classmethod
    def _base_url(cls, settings): return "https://openrouter.ai/api/v1"
    @classmethod
    def _api_key(cls, settings): return getattr(settings, "OPENROUTER_API_KEY", "")


class BAIProvider(OpenAICompatibleProvider):
    name = "BAI"
    # B.AI (https://docs.b.ai) does NOT offer `gpt-4o-mini`.  Its OpenAI-family
    # catalog uses GPT-5.x identifiers (e.g. gpt-5-4-mini).  Sending a model
    # that does not exist on the platform causes HTTP 400 "Invalid request body".
    # gpt-5-4-mini is the compact high-throughput model (see /llmservice/models/).
    default_model = "gpt-5-4-mini"
    # B.AI / BytePlus rejects the `response_format` field with
    # "Invalid request body" (HTTP 400).  Do NOT send it.
    supports_response_format = False
    @classmethod
    def _base_url(cls, settings): return getattr(settings, "BAI_BASE_URL", "https://api.b.ai/v1")
    @classmethod
    def _api_key(cls, settings): return getattr(settings, "BAI_API_KEY", "")


class LegacyOpenAIProvider(OpenAICompatibleProvider):
    """Backward-compatible provider using AI_API_KEY against an
    OpenAI-compatible endpoint (overrideable via AI_API_BASE_URL)."""
    name = "OPENAI"
    default_model = "gpt-4o-mini"
    @classmethod
    def _base_url(cls, settings):
        return getattr(settings, "AI_API_BASE_URL", "") or "https://api.openai.com/v1"
    @classmethod
    def _api_key(cls, settings): return getattr(settings, "AI_API_KEY", "")


class GeminiProvider(BaseAIProvider):
    name = "GEMINI"
    default_model = "gemini-1.5-flash"

    @classmethod
    def is_configured(cls, settings) -> bool:
        return bool(getattr(settings, "GEMINI_API_KEY", ""))

    async def validate(self, settings, context: dict, request_id: str) -> ProviderResult:
        api_key = getattr(settings, "GEMINI_API_KEY", "")
        model = getattr(settings, "GEMINI_MODEL", self.default_model) or self.default_model
        payload = json.dumps(context, indent=2, default=str)
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
               f"?key={api_key}")
        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": SYSTEM_PROMPT},
                    {"text": f"Validate this XAU/USD signal:\n\n{payload}"},
                ],
            }],
            "generationConfig": {"temperature": settings.AI_TEMPERATURE},
        }
        timeout = getattr(settings, "AI_REQUEST_TIMEOUT_SECONDS", 15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(url, json=body)
        except httpx.TimeoutException:
            return ProviderResult(status="UNAVAILABLE", score=0, reason="Provider timeout.",
                                  reason_code="TIMEOUT", provider=self.name, model=model,
                                  request_id=request_id, raw_response="TIMEOUT")
        except httpx.NetworkError as exc:
            return ProviderResult(status="UNAVAILABLE", score=0, reason=f"Network error: {exc}",
                                  reason_code="CONNECTION_ERROR", provider=self.name, model=model,
                                  request_id=request_id, raw_response="NETWORK_ERROR")

        if res.status_code in (401, 403):
            return ProviderResult(status="UNAVAILABLE", reason="Authentication error.",
                                  reason_code="AUTHENTICATION_FAILED", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"HTTP {res.status_code}")
        if res.status_code == 429:
            return ProviderResult(status="UNAVAILABLE", reason="Rate limited (429).",
                                  reason_code="RATE_LIMITED", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"HTTP {res.status_code}")
        if res.status_code >= 500:
            return ProviderResult(status="UNAVAILABLE", reason=f"Server error ({res.status_code}).",
                                  reason_code="PROVIDER_ERROR", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"HTTP {res.status_code}")

        try:
            res.raise_for_status()
            data = res.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _parse_llm_json(content)
            return _llm_validation_result(parsed, self.name, model, content, request_id)
        except (KeyError, IndexError, json.JSONDecodeError, ValueError) as exc:
            return ProviderResult(status="UNAVAILABLE", reason="Invalid/malformed response.",
                                  reason_code="INVALID_RESPONSE", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"PARSE_ERROR: {exc}")


class OllamaProvider(BaseAIProvider):
    name = "OLLAMA"
    default_model = "llama3.2:3b"

    @classmethod
    def is_configured(cls, settings) -> bool:
        return bool(getattr(settings, "OLLAMA_BASE_URL", ""))

    async def validate(self, settings, context: dict, request_id: str) -> ProviderResult:
        base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        model = getattr(settings, "OLLAMA_MODEL", self.default_model) or self.default_model
        payload = json.dumps(context, indent=2, default=str)
        url = f"{base_url}/api/chat"
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Validate this XAU/USD signal:\n\n{payload}"},
            ],
            "stream": False,
            "format": "json",
        }
        timeout = getattr(settings, "AI_REQUEST_TIMEOUT_SECONDS", 15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(url, json=body)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
            return ProviderResult(status="UNAVAILABLE", reason=f"Ollama unavailable: {exc}",
                                  reason_code="CONNECTION_ERROR", provider=self.name, model=model,
                                  request_id=request_id, raw_response="OLLAMA_UNAVAILABLE")

        if res.status_code != 200:
            return ProviderResult(status="UNAVAILABLE", reason=f"Ollama error ({res.status_code}).",
                                  reason_code="PROVIDER_ERROR", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"HTTP {res.status_code}")

        try:
            data = res.json()
            content = data.get("message", {}).get("content", "")
            if not content:
                raise ValueError("Empty response")
            parsed = _parse_llm_json(content)
            return _llm_validation_result(parsed, self.name, model, content, request_id)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            return ProviderResult(status="UNAVAILABLE", reason="Ollama returned invalid response.",
                                  reason_code="INVALID_RESPONSE", provider=self.name,
                                  model=model, request_id=request_id,
                                  raw_response=f"PARSE_ERROR: {exc}")


# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------

# Priority order for the auto chain (lower index = higher priority)
_AUTO_CHAIN: list[type[BaseAIProvider]] = [
    GroqProvider,
    GeminiProvider,
    OpenRouterProvider,
    OllamaProvider,
    BAIProvider,
]

# Single-provider mode lookup
_SINGLE_PROVIDERS: dict[str, type[BaseAIProvider]] = {
    "groq": GroqProvider,
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
    "bai": BAIProvider,
    "openai": LegacyOpenAIProvider,
}


def get_providers_for_mode(mode: str, settings) -> list[type[BaseAIProvider]]:
    """Return the list of provider classes to try for the given mode."""
    if mode == "auto" or mode == "mock":
        return _AUTO_CHAIN
    cls = _SINGLE_PROVIDERS.get(mode)
    if cls is not None:
        return [cls]
    return [_AUTO_CHAIN[0]]  # fallback to Groq

# Per-process status cache (avoids hammering providers on every request)
_provider_status: dict[str, ProviderStatus] = {}
_provider_status_timestamp: dict[str, float] = {}
_provider_status_detail: dict[str, str] = {}
# Cooldown: after a permanent failure (402/401) do not retry for this window
_PROVIDER_COOLDOWN_SECONDS = 600  # 10 minutes


def get_provider_status(settings) -> dict[str, dict]:
    """Return the current status of all configured providers."""
    now = time.time()
    out = {}
    all_classes = [GroqProvider, GeminiProvider, OpenRouterProvider, OllamaProvider, BAIProvider, LegacyOpenAIProvider]
    for cls in all_classes:
        name = cls.name
        configured = cls.is_configured(settings)
        status = _provider_status.get(name, ProviderStatus.AVAILABLE if configured else ProviderStatus.OFFLINE)
        # If cooldown has expired, treat as available again (may be re-checked).
        if status in (ProviderStatus.INSUFFICIENT_BALANCE, ProviderStatus.AUTH_ERROR):
            ts = _provider_status_timestamp.get(name, 0)
            if now - ts > _PROVIDER_COOLDOWN_SECONDS:
                status = ProviderStatus.AVAILABLE if configured else ProviderStatus.OFFLINE
        ts = _provider_status_timestamp.get(name, 0)
        out[name] = {
            "configured": configured,
            "status": status.value,
            "reason_code": _provider_status_detail.get(name),
            "last_updated": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
        }
    return out


def mark_provider_status(name: str, status: ProviderStatus, detail: str = "") -> None:
    """Record a provider status change."""
    _provider_status[name] = status
    _provider_status_timestamp[name] = time.time()
    _provider_status_detail[name] = detail


def _provider_in_cooldown(name: str) -> bool:
    """True if the provider is in a permanent-failure cooldown window."""
    status = _provider_status.get(name)
    if status not in (ProviderStatus.INSUFFICIENT_BALANCE, ProviderStatus.AUTH_ERROR):
        return False
    ts = _provider_status_timestamp.get(name, 0)
    return (time.time() - ts) < _PROVIDER_COOLDOWN_SECONDS


def clear_provider_status(name: str) -> None:
    _provider_status.pop(name, None)
    _provider_status_timestamp.pop(name, None)
    _provider_status_detail.pop(name, None)


# ---------------------------------------------------------------------------
# Validation Cache (short-lived, keyed by context hash)
# ---------------------------------------------------------------------------

_validation_cache: dict[str, tuple[float, ProviderResult]] = {}
_cache_lock = asyncio.Lock()
_CACHE_TTL = 300  # 5 minutes


async def get_cached_result(context: dict, mode: str = "auto") -> ProviderResult | None:
    key = _context_hash({**context, "__mode": mode})
    async with _cache_lock:
        entry = _validation_cache.get(key)
        if entry and time.time() - entry[0] < _CACHE_TTL:
            return entry[1]
        if entry:
            del _validation_cache[key]
    return None


async def set_cached_result(context: dict, result: ProviderResult, mode: str = "auto") -> None:
    key = _context_hash({**context, "__mode": mode})
    async with _cache_lock:
        _validation_cache[key] = (time.time(), result)


def clear_validation_cache() -> None:
    """Clears the short-lived validation cache (used by tests / admin reset)."""
    _validation_cache.clear()


# ---------------------------------------------------------------------------
# Provider Chain
# ---------------------------------------------------------------------------

async def run_validation_chain(settings, context: dict, request_id: str, mode: str = "auto") -> ProviderResult:
    """Try providers in priority order; return first success or UNAVAILABLE.

    * Skips providers that are not configured.
    * Does NOT retry 401 / 402 (marks provider AUTH_ERROR / INSUFFICIENT_BALANCE
      and applies a cooldown so the provider is not hammered on every candle).
    * Retries other failures (429, 5xx, network) up to AI_MAX_RETRIES times.
    * Returns the first successful result.
    * If all fail → UNAVAILABLE with a summary reason.
    * Short-lived cache avoids duplicate requests for the same context.
    """
    cached = await get_cached_result(context, mode)
    if cached is not None:
        return cached

    max_retries = getattr(settings, "AI_MAX_RETRIES", 1)
    retry_delay = getattr(settings, "AI_RETRY_DELAY", 1.0)
    fallback_from = []
    providers = get_providers_for_mode(mode, settings)
    last_failure: ProviderResult | None = None

    for cls in providers:
        name = cls.name
        if not cls.is_configured(settings):
            continue

        # Skip providers known to be permanently unavailable (cooldown).
        if _provider_in_cooldown(name):
            cooldown_status = _provider_status.get(name, ProviderStatus.UNAVAILABLE)
            cooldown_detail = _provider_status_detail.get(name, cooldown_status.value)
            fallback_from.append(f"{name}: {cooldown_status.value}")
            if last_failure is None:
                last_failure = ProviderResult(
                    status="UNAVAILABLE", score=0.0,
                    reason=f"{name} unavailable ({cooldown_detail}).",
                    reason_code=cooldown_detail or "UNAVAILABLE",
                    provider=name, model="", request_id=request_id,
                    raw_response="COOLDOWN",
                )
            continue

        provider = cls()
        for attempt in range(max_retries + 1):
            try:
                result = await provider.validate(settings, context, request_id)
            except Exception as exc:  # noqa: BLE001 — a provider must never abort the chain
                logger.error("Provider %s raised an unhandled exception: %s", name, exc)
                mark_provider_status(name, ProviderStatus.NETWORK_ERROR, "UNHANDLED_EXCEPTION")
                result = ProviderResult(
                    status="UNAVAILABLE",
                    score=0.0,
                    reason=f"{name} raised an unhandled exception: {exc}",
                    reason_code="UNHANDLED_EXCEPTION",
                    provider=name,
                    model="",
                    request_id=request_id,
                    raw_response="",
                )
            rid = result.request_id
            if result.status == "UNAVAILABLE":
                last_failure = result
                code = result.reason_code
                reason = result.reason or ""
                if code == "INSUFFICIENT_BALANCE" or "402" in reason:
                    mark_provider_status(name, ProviderStatus.INSUFFICIENT_BALANCE, code)
                    fallback_from.append(f"{name}: INSUFFICIENT_BALANCE")
                    break
                if code == "AUTHENTICATION_FAILED" or "401" in reason or "403" in reason:
                    mark_provider_status(name, ProviderStatus.AUTH_ERROR, code)
                    fallback_from.append(f"{name}: AUTH_ERROR")
                    break
                if code == "RATE_LIMITED" or "429" in reason:
                    mark_provider_status(name, ProviderStatus.RATE_LIMITED, code)
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay * (2 ** attempt))
                        continue
                    fallback_from.append(f"{name}: RATE_LIMITED")
                    break
                if code in ("TIMEOUT", "CONNECTION_ERROR") or "timeout" in reason.lower() or "network" in reason.lower():
                    mark_provider_status(name, ProviderStatus.NETWORK_ERROR, code)
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay * (2 ** attempt))
                        continue
                    fallback_from.append(f"{name}: {code}")
                    break
                # Generic UNAVAILABLE
                mark_provider_status(name, ProviderStatus.UNAVAILABLE, code)
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                    continue
                fallback_from.append(f"{name}: {code}")
                break
            # Success
            mark_provider_status(name, ProviderStatus.AVAILABLE)
            result.fallback = fallback_from
            await set_cached_result(context, result, mode)
            return result

    # All providers failed — preserve the most specific provider failure so the
    # caller sees the real reason (INSUFFICIENT_BALANCE / RATE_LIMITED / etc.)
    # rather than a generic "UNAVAILABLE".
    if last_failure is not None:
        last_failure.fallback = fallback_from
        last_failure.status = "UNAVAILABLE"
        await set_cached_result(context, last_failure, mode)
        return last_failure

    result = ProviderResult(
        status="UNAVAILABLE",
        score=0.0,
        reason="NO_AI_PROVIDER_CONFIGURED",
        reason_code="CONFIGURATION_MISSING",
        provider="NONE",
        model="",
        request_id=request_id,
        raw_response="NO_CONFIGURED_PROVIDERS",
        risks=[],
        missing=[],
        fallback=[],
    )
    await set_cached_result(context, result, mode)
    return result