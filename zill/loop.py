"""Concept: the agent loop, the heartbeat of every harness.

Ask the model. If it wants tools, run them, show it the results, ask again.
When it answers without tool calls, the task is done. The messages list is the
agent's entire memory.
"""
from zill import provider

WRAP_UP = "You are out of turns. Wrap up now: summarise what you did and what is left."


def run_tool(tools, call):
    """Run one tool call and return its result text; never raises."""
    tool = tools.get(call["name"])
    if tool is None:
        return f"ERROR: unknown tool {call['name']}"
    try:
        return str(tool.run(**call["args"]))
    except Exception as e:  # a broken tool must not kill the loop
        return f"ERROR: {type(e).__name__}: {e}"


def run_loop(model, system, messages, tools, on_event, max_turns=80, before_turn=None):
    """Drive the model/tool cycle until it answers in plain text; return that text."""
    specs = [t.spec for t in tools.values()]
    for _ in range(max_turns):
        if before_turn:  # context socket: may fold old messages; replaced IN PLACE so the caller sees it
            try:
                messages[:] = before_turn(messages)
            except Exception as e:  # a broken compactor must never kill the run
                print(f"before_turn failed ({type(e).__name__}: {e}); continuing unchanged")
        reply = provider.complete(model, system, messages, specs)
        assistant = {"role": "assistant", "text": reply["text"], "tool_calls": reply["tool_calls"]}
        messages.append(assistant)
        on_event(assistant)
        if not reply["tool_calls"]:
            return reply["text"]
        for call in reply["tool_calls"]:
            result = {"role": "tool", "name": call["name"], "text": run_tool(tools, call)}
            messages.append(result)
            on_event(result)
    # Out of turns: ask for a summary, with no tools so the model cannot stall.
    messages.append({"role": "user", "text": WRAP_UP})
    on_event(messages[-1])
    reply = provider.complete(model, system, messages, [])
    final = {"role": "assistant", "text": reply["text"], "tool_calls": []}
    messages.append(final)
    on_event(final)
    return reply["text"]
