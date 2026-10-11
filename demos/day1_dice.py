"""Concept: watch one full agent cycle with a deliberately silly tool.

user -> assistant (tool call) -> tool result -> assistant (answer).
Run from the project root:  python -m demos.day1_dice
"""
import json
import random
from types import SimpleNamespace

from zill.loop import run_loop
from zill.provider import DEFAULT_MODEL


def roll(count):
    """Roll `count` six-sided dice; return the rolls and total as JSON."""
    rolls = [random.randint(1, 6) for _ in range(int(count))]
    return json.dumps({"rolls": rolls, "total": sum(rolls)})


ROLL_DICE = SimpleNamespace(
    spec={"schema": {
        "name": "roll_dice",
        "description": "Roll some six-sided dice and return the rolls and their total.",
        "parameters": {"type": "object",
                       "properties": {"count": {"type": "integer", "description": "How many dice to roll"}},
                       "required": ["count"]}}},
    run=roll,
)


def on_event(message):
    """Print one message in a readable one-line form."""
    calls = [f"{c['name']}({c['args']})" for c in message.get("tool_calls", [])]
    label = message["role"] + (f":{message['name']}" if "name" in message else "")
    print(f"[{label}] {message['text']} {' '.join(calls)}".rstrip())


if __name__ == "__main__":
    messages = [{"role": "user", "text": "Roll 3 dice and tell me whether the total beats 10"}]
    on_event(messages[0])
    run_loop(DEFAULT_MODEL, "You are a helpful assistant.", messages, {"roll_dice": ROLL_DICE}, on_event)
