"""Concept: context management, the compaction fold.

One fold, two halves: when the tape (the message array) is over budget, the OLD
half becomes ONE model-written summary and the RECENT half (the last
KEEP_RECENT messages) is kept word for word. The system prompt and the tool
definitions are not in the messages list, so they are never folded.
Design rules: (1) compaction may fail, it may never loop: one fold per call.
(2) A broken summarizer degrades to a deterministic fallback, never a crash.
(3) The original request survives every fold. (4) Never mutate the input.
Simplifications: provider.py pairs tool results to calls by position and name,
not by id, so the seam only drops leading tool messages; the original request is
recovered from an earlier summary up to its first blank line.
"""
from collections import Counter

from zill import provider

CHARS_PER_TOKEN = 4   # a budget meter, not a tokenizer
KEEP_RECENT = 6       # never summarize the freshest messages
CLIP_CHARS = 600      # per message, in the transcript we summarize
CLIP_TOOL_CHARS = 300  # tool results are clipped harder: they are the bulk
MARKER = "[Conversation so far, compacted]"
LABEL = "Original request (verbatim):"
FALLBACK_NOTE = "(fallback summary: the summarizer was unavailable)"
SUMMARY_INSTRUCTION = (
    "You compress agent transcripts into a handover note. Use exactly these headings: Goal; "
    "Constraints and preferences; Progress (done / in progress / blocked); Key decisions and why; "
    "Files created or edited and their purpose; Unresolved errors; Next steps. Be dense and factual, "
    "and stay under 250 words, because the summary itself rides on every later call. "
    "Never copy secrets such as API keys or passwords: write [REDACTED].")


def estimate_tokens(messages):
    """Rough token count (characters // 4). Good enough: we fold long before the hard ceiling."""
    return sum(len(str(m)) for m in messages) // CHARS_PER_TOKEN


def _clip(text, limit):
    return text if len(text) <= limit else f"{text[:limit]} …[clipped {len(text) - limit} chars]"


def render(messages):
    """Turn messages into a plain-text transcript with long texts clipped."""
    blocks = []
    for m in messages:
        text = m.get("text") or ""
        label = f"[tool: {m.get('name')}]" if m["role"] == "tool" else f"[{m['role']}]"
        if text.startswith(MARKER):  # an earlier summary must survive into the next one
            limit = 4000
        else:
            limit = CLIP_TOOL_CHARS if m["role"] == "tool" else CLIP_CHARS
        lines = [label, _clip(text, limit)]
        lines += [f"-> called {c['name']}({_clip(str(c['args']), 120)})" for c in m.get("tool_calls", [])]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def original_request(messages):
    """The first real user request, even if an earlier fold already summarized it away."""
    for m in messages:
        if m["role"] != "user":
            continue
        if not m["text"].startswith(MARKER):
            return m["text"]
        _, found, rest = m["text"].partition(LABEL)
        return rest.strip().split("\n\n")[0] if found else None
    return None


def _mend(kept):
    """The seam: drop leading tool results whose calls were summarized away (orphans)."""
    start = 0
    while start < len(kept) and kept[start]["role"] == "tool":
        start += 1  # never invent a fake call; the meaning is already in the summary
    return kept[start:]


def _fallback_summary(old):
    """Deterministic summary with no model at all: the last resort."""
    calls = [c for m in old for c in m.get("tool_calls", [])]
    paths = [str(c["args"].get("path")) for c in calls if c["name"] in ("write_file", "edit_file")]
    errors = [_clip(m["text"], 200) for m in old if m["role"] == "tool" and "ERROR" in m["text"]][-3:]
    last = next((m["text"] for m in reversed(old) if m["role"] == "assistant" and m.get("text")), "")
    text = "\n".join([FALLBACK_NOTE,
                      "Tools used: " + (", ".join(f"{n} x{k}" for n, k in Counter(c["name"] for c in calls).items()) or "none"),
                      "Files written or edited: " + (", ".join(paths) or "none"),
                      "Recent errors: " + (" | ".join(errors) or "none"),
                      "Last assistant text: " + _clip(last, 500)])
    return text[:3000]


def compact(model, messages, budget_tokens, complete=None, on_compact=None):
    """Fold the old half into one summary if over budget; return a NEW list (or the same one)."""
    complete = complete or provider.complete  # injectable, so tests need no network
    before = estimate_tokens(messages)
    if before <= budget_tokens or len(messages) <= KEEP_RECENT + 1:
        return messages
    old, kept = messages[:-KEEP_RECENT], _mend(messages[-KEEP_RECENT:])
    try:
        ask = [{"role": "user", "text": "Summarize this transcript:\n\n" + render(old)}]
        summary = (complete(model, SUMMARY_INSTRUCTION, ask, [])["text"] or "").strip()
    except Exception:
        summary = ""
    summary = summary or _fallback_summary(old)
    request = original_request(messages)
    head = MARKER + (f"\n{LABEL} {request[:2000]}" if request else "") + "\n\n" + summary
    if kept and kept[0]["role"] == "user":  # keep roles alternating: merge instead of stacking users
        result = [{"role": "user", "text": head + "\n\n---\n\n" + kept[0]["text"]}] + kept[1:]
    else:
        result = [{"role": "user", "text": head}] + kept
    if on_compact:
        on_compact(before, estimate_tokens(result), head)
    return result  # maybe still over budget: that is fine, the next turn can fold again
