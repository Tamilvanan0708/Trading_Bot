"""
Isolated AI-provider diagnostic — bypasses the application AI abstraction.

Purpose: determine whether the repeated `400 Invalid request body` comes from
(A) provider/configuration or (B) the application-generated payload.

This script:
  1. Loads the real runtime provider configuration.
  2. Reports provider / endpoint / model / API-key presence (NEVER the key).
  3. Sends the SMALLEST valid request directly to the provider.
  4. Captures HTTP status, response body, and request ID (secrets redacted).

If no API key is configured in this environment, it reports that and exits
without making a network call.
"""

import asyncio
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _mask(value: str) -> str:
    if not value:
        return "MISSING"
    if len(value) <= 6:
        return "***"
    return value[:3] + "..." + value[-3:]


def _report(settings) -> dict:
    provider = settings.AI_PROVIDER
    key_attr = {
        "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY", "ollama": "OLLAMA_BASE_URL",
        "bai": "BAI_API_KEY", "openai": "AI_API_KEY",
    }
    base_url_attr = {
        "groq": "GROQ_MODEL", "gemini": "GEMINI_MODEL",
        "openrouter": "OPENROUTER_MODEL", "ollama": "OLLAMA_MODEL",
        "bai": "BAI_MODEL", "openai": "AI_MODEL",
    }
    return {
        "provider": provider,
        "api_key": _mask(getattr(settings, key_attr.get(provider, ""), "")),
        "base_url": getattr(settings, "BAI_BASE_URL", ""),
        "endpoint": "https://api.b.ai/v1/chat/completions"
        if provider == "bai" else getattr(settings, "AI_API_BASE_URL", "") + "/chat/completions",
        "model": getattr(settings, base_url_attr.get(provider, ""), ""),
    }


async def main() -> None:
    from app.config.settings import get_settings
    settings = get_settings()
    info = _report(settings)
    print("=" * 60)
    print("ISOLATED PROVIDER TEST")
    print("=" * 60)
    for k, v in info.items():
        print(f"{k.upper():<12}: {v}")

    provider = info["provider"]
    if provider == "mock" or info["api_key"] == "MISSING" or info["api_key"] == "***":
        print("-" * 60)
        print("RESULT: no API key configured in this environment.")
        print("The app runs in AI_PROVIDER=mock (heuristic) — no external AI call is made.")
        print("To reproduce the live 400, set the provider key and re-run this script.")
        print("=" * 60)
        return

    # ------------------------------------------------------------------
    # Minimal valid request (no optional fields at all).
    # ------------------------------------------------------------------
    import httpx
    endpoint = info["endpoint"]
    model = info["model"]
    key = getattr(settings, "BAI_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    minimal_body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            res = await client.post(endpoint, headers=headers, json=minimal_body)
        except Exception as exc:  # noqa: BLE001
            print("-" * 60)
            print("MINIMAL REQUEST: NETWORK/CONFIG ERROR")
            print("error:", str(exc)[:200])
            print("=" * 60)
            return

    body_text = res.text[:400]
    print("-" * 60)
    print(f"MINIMAL REQUEST STATUS: {res.status_code}")
    print("RESPONSE BODY (truncated, redacted):")
    print(body_text if "authorization" not in body_text.lower() else "(redacted)")
    print("=" * 60)
    if res.status_code == 200:
        print("=> Minimal request SUCCEEDS: provider is working; app payload is the problem.")
    else:
        print(f"=> Minimal request FAILS ({res.status_code}): provider/config/model issue.")


if __name__ == "__main__":
    asyncio.run(main())
