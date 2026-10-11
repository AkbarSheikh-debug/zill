"""Concept: the provider is the ONE doorway to the model.

Everything vendor-specific (URL, wire format, retries, API key) lives here.
Zill itself speaks one neutral message format:
    {"role": "user", "text": ...}
    {"role": "assistant", "text": ..., "tool_calls": [{"name", "args", "extra"}]}
    {"role": "tool", "name": ..., "text": ...}
Swap vendors by rewriting this file only; the loop never notices.
"""
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "gemini-3.8-flash"
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUS = {429, 500, 502, 503}
MAX_ATTEMPTS = 5


def load_key():
    """Return ZILL_API_KEY from the environment, else from the project's .env file."""
    key = os.environ.get("ZILL_API_KEY")
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "ZILL_API_KEY":
                key = value.strip().strip("\"'")
    if not key:
        raise RuntimeError("ZILL_API_KEY is not set (environment or .env)")
    return key


def to_wire(messages):
    """Translate neutral messages into Gemini 'contents'."""
    contents = []
    for m in messages:
        if m["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": m["text"]}]})
        elif m["role"] == "assistant":
            parts = [{"text": m["text"]}] if m.get("text") else []
            for call in m.get("tool_calls", []):
                part = {"functionCall": {"name": call["name"], "args": call["args"]}}
                part.update(call.get("extra", {}))  # e.g. thoughtSignature: echo it back
                parts.append(part)
            contents.append({"role": "model", "parts": parts})
        else:  # tool result; consecutive results share one user turn
            part = {"functionResponse": {"name": m["name"], "response": {"result": m["text"]}}}
            if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0]:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
    return contents


def from_wire(data):
    """Translate a Gemini response into {"text", "tool_calls", "usage"}."""
    candidate = data["candidates"][0]
    text, calls = "", []
    for part in candidate.get("content", {}).get("parts", []):
        if "text" in part:
            text += part["text"]
        if "functionCall" in part:
            fc = part["functionCall"]
            extra = {"thoughtSignature": part["thoughtSignature"]} if "thoughtSignature" in part else {}
            calls.append({"name": fc["name"], "args": fc.get("args", {}), "extra": extra})
    usage = data.get("usageMetadata", {})
    return {"text": text, "tool_calls": calls,
            "usage": {"input": usage.get("promptTokenCount", 0),
                      "output": usage.get("candidatesTokenCount", 0)}}


def post(url, body, key, timeout=120):
    """POST JSON, retrying with exponential backoff on 429/500/502/503 and timeouts."""
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "x-goog-api-key": key})
    for attempt in range(MAX_ATTEMPTS):
        last = attempt == MAX_ATTEMPTS - 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or last:
                raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None
        except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
            reason = getattr(e, "reason", e)
            if not isinstance(reason, (TimeoutError, socket.timeout)) or last:
                raise
        time.sleep(2 ** attempt)


def complete(model, system, messages, tools):
    """Send one request; return {"text", "tool_calls", "usage": {"input", "output"}}."""
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": to_wire(messages)}
    if tools:
        body["tools"] = [{"functionDeclarations": [t["schema"] for t in tools]}]
    return from_wire(post(URL.format(model=model), body, load_key()))
