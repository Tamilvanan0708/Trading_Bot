"""
BAI HTTP 400 differential diagnostic — determines the EXACT failing field/model.

Bypasses the application's provider abstraction and talks to B.AI directly.

Flow:
  1. Reports the configured endpoint / model / key presence (key never printed).
  2. GET /v1/models  -> lists the models ACTUALLY available for this credential.
  3. Sends the MINIMAL request:  {"model": <model>, "messages": [{"role":"user","content":"ping"}]}
  4. Branches:
       - minimal 200 -> differential tests add ONE field at a time
                        (temperature -> max_tokens -> stream -> response_format)
                        and report the first request that flips 200 -> 400.
       - minimal 400 -> tries each model returned by /v1/models (and common
                        GPT-5.x aliases) to find a working model name, and
                        reports which model fails.
  5. For every request it prints the exact wire body length + parsed JSON type
     (detects double-encoded JSON, e.g. JSON-as-string) WITHOUT printing the key.

Usage:
    python scripts/diagnose_bai_400.py

Requirements: BAI_API_KEY + BAI_MODEL (+ optional BAI_BASE_URL) in the
environment (or .env at the project root).
"""

import asyncio
import json
import os
import sys

import httpx

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Sanitize wire body: keep JSON structure, redact long message text.
def _wire_preview(serialized: str, limit: int = 800) -> str:
    if len(serialized) <= limit:
        return serialized
    return serialized[:limit] + f"...(truncated, total_len={len(serialized)})"


def _body_report(body: dict) -> dict:
    """Structural report of the outgoing body (no message content)."""
    serialized = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    parsed_type = "unknown"
    try:
        parsed = json.loads(serialized)
        parsed_type = type(parsed).__name__
    except (TypeError, ValueError) as exc:
        parsed_type = f"INVALID_JSON({exc})"
    return {
        "wire_length": len(serialized),
        "parsed_type": parsed_type,
        "double_encoded": serialized.lstrip().startswith(('"', "'")) or parsed_type == "str",
        "top_level_keys": sorted(body.keys()),
        "value_types": {k: type(v).__name__ for k, v in body.items()},
        "messages_count": len(body.get("messages", [])),
        "message_roles": [m.get("role") for m in body.get("messages", [])],
        "content_types": [type(m.get("content")).__name__ for m in body.get("messages", [])],
        "optional_fields": sorted(k for k in body if k not in ("model", "messages")),
        "wire_preview": _wire_preview(serialized),
    }


async def _send(client: httpx.AsyncClient, endpoint: str, headers: dict, body: dict, label: str):
    print("-" * 70)
    print(f"[{label}]")
    for k, v in _body_report(body).items():
        print(f"    {k}: {v}")
    try:
        res = await client.post(endpoint, headers=headers, json=body)
    except Exception as exc:  # noqa: BLE001
        print(f"    => NETWORK ERROR: {str(exc)[:200]}")
        return None
    err = res.text[:300]
    if "authorization" in err.lower() or "api-key" in err.lower():
        err = "(redacted)"
    rid = res.headers.get("x-oneapi-request-id", "") or res.headers.get("request-id", "")
    print(f"    => HTTP {res.status_code}" + (f"  x-oneapi-request-id={rid}" if rid else ""))
    if res.status_code != 200:
        print(f"    error body: {err}")
    return res.status_code


async def main() -> None:
    from app.config.settings import get_settings

    settings = get_settings()
    base_url = (getattr(settings, "BAI_BASE_URL", "") or "https://api.b.ai/v1").rstrip("/")
    endpoint = f"{base_url}/chat/completions"
    key = getattr(settings, "BAI_API_KEY", "") or os.getenv("BAI_API_KEY", "")
    configured_model = (getattr(settings, "BAI_MODEL", "") or "gpt-5-4-mini").strip()

    print("=" * 70)
    print("BAI HTTP 400 DIFFERENTIAL DIAGNOSTIC")
    print("=" * 70)
    print(f"endpoint: {endpoint}")
    print(f"configured model: {configured_model}")
    print(f"api key: {'PRESENT' if key else 'MISSING'}")
    if not key:
        print("-" * 70)
        print("No BAI_API_KEY configured. Set BAI_API_KEY + BAI_MODEL and re-run.")
        return

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "bai-400-diagnostic/1.0",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # ---------------------------------------------------------------
        # 1. List models actually available for this credential.
        # ---------------------------------------------------------------
        try:
            mres = await client.get(f"{base_url}/models", headers=headers)
        except Exception as exc:  # noqa: BLE001
            mres = None
            print("-" * 70)
            print(f"GET /models -> NETWORK ERROR: {str(exc)[:200]}")
        if mres is not None and mres.status_code == 200:
            try:
                data = mres.json()
                models = [m.get("id") for m in data.get("data", [])] or []
                print("-" * 70)
                print(f"GET /models -> HTTP {mres.status_code}")
                print(f"available model IDs for this credential ({len(models)}):")
                for m in models:
                    print(f"    {m}")
                print(f"configured model '{configured_model}' in list: {configured_model in models}")
            except Exception as exc:  # noqa: BLE001
                print(f"GET /models parse error: {str(exc)[:200]} body={mres.text[:300]}")
        else:
            status = mres.status_code if mres is not None else "ERR"
            print(f"GET /models -> HTTP {status} (auth not needed for later tests)")

        # ---------------------------------------------------------------
        # 2. Minimal request with the configured model.
        # ---------------------------------------------------------------
        minimal = {"model": configured_model, "messages": [{"role": "user", "content": "ping"}]}
        status = await _send(client, endpoint, headers, minimal, "TEST A: minimal (model + messages)")

        # ---------------------------------------------------------------
        # 3. Branch.
        # ---------------------------------------------------------------
        if status == 200:
            print("-" * 70)
            print("MINIMAL REQUEST = 200  => B.AI works; the app payload adds the problem field.")
            print("Differential testing (add ONE field at a time):")
            candidates = [
                ("TEST B: + temperature", {**minimal, "temperature": 0.1}),
                ("TEST C: + max_tokens", {**minimal, "temperature": 0.1, "max_tokens": 1024}),
                ("TEST D: + stream", {**minimal, "temperature": 0.1, "stream": False}),
                ("TEST E: + response_format", {**minimal, "temperature": 0.1, "response_format": {"type": "json_object"}}),
                ("TEST F: + system role", {
                    "model": configured_model,
                    "messages": [
                        {"role": "system", "content": "You are a validator. Output JSON only."},
                        {"role": "user", "content": "ping"},
                    ],
                    "temperature": 0.1,
                }),
            ]
            for label, body in candidates:
                s = await _send(client, endpoint, headers, body, label)
                if s is not None and s != 200:
                    print("-" * 70)
                    print(f"FIRST FAILING FIELD FOUND: {label} -> HTTP {s}")
                    break
        else:
            print("-" * 70)
            print(f"MINIMAL REQUEST = HTTP {status}  => model/endpoint/config problem, not app fields.")
            # 4. Try every model returned by /v1/models, then GPT-5.x aliases.
            candidates = []
            try:
                mres = await client.get(f"{base_url}/models", headers=headers)
                if mres.status_code == 200:
                    candidates = [m.get("id") for m in mres.json().get("data", []) if m.get("id")]
            except Exception as exc:  # noqa: BLE001 - diagnostic must not crash
                print(f"    (could not list models: {str(exc)[:120]})")
            aliases = ["gpt-5-4-mini", "gpt-5-mini", "gpt-5-nano", "gpt-4o-mini"]
            for m in candidates + [a for a in aliases if a not in candidates]:
                print("-" * 70)
                print(f"Trying model: {m}")
                s = await _send(client, endpoint, headers, {
                    "model": m, "messages": [{"role": "user", "content": "ping"}],
                }, f"model probe: {m}")
                if s == 200:
                    print("-" * 70)
                    print(f"WORKING MODEL FOUND: {m}")
                    print(f"Set BAI_MODEL={m} in the deployment .env.")
                    break


if __name__ == "__main__":
    asyncio.run(main())
