"""Concept: prove the fold. A fake complete stands in for the model, so there is
no network. Each test pins one promise: under budget nothing changes, the seam
never leaves an orphan tool result, the original request survives every fold,
a dead summarizer degrades to the fallback, and the loop socket is safe.
"""
import copy
import unittest
from types import SimpleNamespace
from unittest import mock

from zill import loop, provider
from zill.context import KEEP_RECENT, MARKER, compact, estimate_tokens

BIG = "y" * 400


def tape(n, first="Build the ORIGINAL-REQUEST-42 thing"):
    """n alternating user/assistant messages, the first being the request."""
    return [{"role": "user" if i % 2 == 0 else "assistant", "text": first if i == 0 else BIG} for i in range(n)]


class Recorder:
    """Fake complete: records what the summarizer receives, answers 'SUMMARY'."""

    def __init__(self, text="SUMMARY"):
        self.text, self.sent = text, []

    def __call__(self, model, system, messages, tools):
        self.sent.append(messages[0]["text"])
        return {"text": self.text, "tool_calls": [], "usage": {}}


def no_user_pairs(msgs):
    return not any(a["role"] == b["role"] == "user" for a, b in zip(msgs, msgs[1:]))


class TestFold(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertTrue(100 <= estimate_tokens([{"role": "user", "text": "x" * 400}]) <= 115)

    def test_unchanged_when_under_budget_or_too_short(self):
        msgs = tape(21)
        self.assertIs(compact("m", msgs, 10**6, Recorder()), msgs)
        short = tape(KEEP_RECENT + 1)
        self.assertIs(compact("m", short, 1, Recorder()), short)

    def test_fold_shape_and_no_mutation(self):
        msgs, backup = tape(21), copy.deepcopy(tape(21))  # 21 messages: the kept slice starts on an assistant
        seen = []
        out = compact("m", msgs, 100, Recorder(), on_compact=lambda b, a, h: seen.append((b, a, h)))
        self.assertTrue(out[0]["text"].startswith(MARKER))
        self.assertEqual(out[1:], msgs[-KEEP_RECENT:])
        self.assertLessEqual(len(out), 1 + KEEP_RECENT)
        self.assertEqual(msgs, backup)
        self.assertEqual(len(seen), 1)
        self.assertLess(seen[0][1], seen[0][0])

    def test_seam_drops_orphan_tool_results(self):
        old = tape(4)
        kept = [{"role": "tool", "name": "bash", "text": "r1"}, {"role": "tool", "name": "bash", "text": "r2"},
                {"role": "assistant", "text": BIG}, {"role": "user", "text": BIG}, {"role": "assistant", "text": BIG},
                {"role": "user", "text": BIG}]
        out = compact("m", old + kept, 100, Recorder())
        self.assertNotIn("tool", [m["role"] for m in out])
        self.assertEqual(out[1:], kept[2:])
        only_tools = [{"role": "tool", "name": "bash", "text": BIG}] * KEEP_RECENT
        out = compact("m", old + only_tools, 100, Recorder())
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["text"].startswith(MARKER))

    def test_original_request_survives_two_folds(self):
        rec = Recorder()
        first = compact("m", tape(21), 100, rec)
        grown = first + [{"role": "assistant" if i % 2 == 0 else "user", "text": BIG} for i in range(8)]
        second = compact("m", grown, 100, rec)
        self.assertIsNot(second, grown)
        self.assertIn("Build the ORIGINAL-REQUEST-42 thing", second[0]["text"])

    def test_dead_summarizer_uses_fallback(self):
        def boom(*args):
            raise RuntimeError("summarizer down")
        msgs = tape(21)
        for complete in (boom, Recorder(text="")):
            out = compact("m", msgs, 100, complete)
            self.assertIn("fallback summary", out[0]["text"])
            self.assertLess(estimate_tokens(out), estimate_tokens(msgs))

    def test_long_tool_result_is_clipped_for_the_summarizer(self):
        msgs = tape(21)
        msgs[2] = {"role": "tool", "name": "bash", "text": "z" * 50000}
        rec = Recorder()
        compact("m", msgs, 100, rec)
        self.assertIn("[clipped", rec.sent[0])
        self.assertLess(len(rec.sent[0]), 8000)

    def test_head_merges_into_a_user_message(self):
        out = compact("m", tape(20), 100, Recorder())  # kept slice starts on a user message
        self.assertEqual(len(out), KEEP_RECENT)
        self.assertTrue(no_user_pairs(out))
        self.assertIn(BIG, out[0]["text"])


class TestLoopSocket(unittest.TestCase):
    def run_with(self, before_turn):
        replies = iter([{"text": "", "tool_calls": [{"name": "t", "args": {}, "extra": {}}], "usage": {}},
                        {"text": "done", "tool_calls": [], "usage": {}}])
        seen = []
        tools = {"t": SimpleNamespace(spec={}, run=lambda: "ok")}
        msgs = [{"role": "user", "text": "go"}]
        with mock.patch.object(provider, "complete", lambda model, system, m, t: seen.append(list(m)) or next(replies)):
            result = loop.run_loop("m", "s", msgs, tools, lambda e: None, before_turn=before_turn)
        return result, msgs, seen

    def test_called_each_turn_and_replaces_in_place(self):
        calls = []
        result, msgs, seen = self.run_with(lambda m: calls.append(len(m)) or [{"role": "user", "text": "FOLDED"}])
        self.assertEqual((result, len(calls)), ("done", 2))
        self.assertEqual(seen[0], [{"role": "user", "text": "FOLDED"}])
        self.assertEqual(msgs[0]["text"], "FOLDED")  # the caller's own list shows the fold

    def test_survives_a_broken_compactor(self):
        def broken(m):
            raise ValueError("boom")
        result, msgs, seen = self.run_with(broken)
        self.assertEqual(result, "done")
        self.assertEqual(seen[0], [{"role": "user", "text": "go"}])


if __name__ == "__main__":
    unittest.main()
