import io
import json
import unittest
import urllib.error

import promise_check as P


class Ctx:
    def __init__(self, accept=True):
        self.sent, self.accept, self.hooks = [], accept, {}

    def inject_message(self, text, *a, **k):
        self.sent.append(text)
        return self.accept

    def register_hook(self, name, fn):
        self.hooks[name] = fn


def opener_for(p=None, status=200, body=None, boom=None):
    seen = []

    def opener(req, timeout=0):
        seen.append(json.loads(req.data))
        if boom:
            raise boom
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(b"secret-body"))
        raw = body if body is not None else json.dumps({"answers": {"promise": {"type": "noul", "noul": p}}})
        return io.BytesIO(raw.encode())
    opener.seen = seen
    return opener


def hook(ctx, opener, key="k"):
    return P.make_hook(ctx, ask=lambda k, t: P.ask_jev(k, t, opener=opener), key_reader=lambda: key)


PROMISE = "Got it, I'll write the tests and open the PR now."


class AskJev(unittest.TestCase):
    def test_probability_and_text_only_in_state(self):
        o = opener_for(0.93)
        self.assertEqual(P.ask_jev("k", PROMISE, opener=o), 0.93)
        sent = o.seen[0]
        self.assertEqual(sent["state"], {"message": PROMISE})
        self.assertNotIn(PROMISE, json.dumps(sent["questions"]))

    def test_every_failure_is_a_jev_error_without_leaking(self):
        cases = [opener_for(status=500), opener_for(status=429), opener_for(boom=OSError("http://x?key=k")),
                 opener_for(body="not json"), opener_for(body='{"answers": []}'), opener_for(p=1.7),
                 opener_for(body='{"answers": {"promise": {"noul": "high"}}}')]
        for o in cases:
            with self.assertRaises(P.JevError) as cm:
                P.ask_jev("k", PROMISE, opener=o)
            self.assertNotIn("secret-body", str(cm.exception))
            self.assertNotIn("key=k", str(cm.exception))


class Hook(unittest.TestCase):
    def setUp(self):
        P._streak.clear()

    def test_promise_then_stop_is_nudged(self):
        c = Ctx()
        hook(c, opener_for(0.95))(session_id="s", assistant_response=PROMISE)
        self.assertEqual(len(c.sent), 1)
        self.assertIn("Do it now", c.sent[0])

    def test_done_report_is_not_nudged(self):
        c = Ctx()
        hook(c, opener_for(0.05))(session_id="s", assistant_response="Done. Tests pass, PR is up.")
        self.assertEqual(c.sent, [])

    def test_just_under_threshold_is_not_nudged(self):
        c = Ctx()
        hook(c, opener_for(0.79))(session_id="s", assistant_response=PROMISE)
        self.assertEqual(c.sent, [])

    def test_mentions_questions_short_and_empty_skip_jev(self):
        for text in ("<@123> I'll do it once you answer.", "Shall I start now?", "ok", "", None):
            o, c = opener_for(0.99), Ctx()
            hook(c, o)(session_id="s", assistant_response=text)
            self.assertEqual((o.seen, c.sent), ([], []), text)

    def test_mention_inside_code_does_not_count(self):
        c = Ctx()
        hook(c, opener_for(0.99))(session_id="s", assistant_response="I'll run `echo <@123>` next and report back.")
        self.assertEqual(len(c.sent), 1)

    def test_jev_outage_means_no_nudge_and_no_exception(self):
        for o in (opener_for(status=503), opener_for(boom=OSError("down")), opener_for(body="junk")):
            c = Ctx()
            self.assertIsNone(hook(c, o)(session_id="s", assistant_response=PROMISE))
            self.assertEqual(c.sent, [])

    def test_no_key_means_no_call(self):
        o, c = opener_for(0.99), Ctx()
        hook(c, o, key="")(session_id="s", assistant_response=PROMISE)
        self.assertEqual((o.seen, c.sent), ([], []))

    def test_streak_caps_loops_and_a_normal_turn_resets(self):
        c, o = Ctx(), opener_for(0.99)
        h = hook(c, o)
        for _ in range(5):
            h(session_id="s", assistant_response=PROMISE)
        self.assertEqual(len(c.sent), P.MAX_STREAK)
        h(session_id="s", assistant_response="All done, nothing left to do here.")  # still asks jev -> promise 0.99...
        # a short/hand-off turn resets the streak
        h(session_id="s", assistant_response="<@1> done, over to you")
        h(session_id="s", assistant_response=PROMISE)
        self.assertEqual(len(c.sent), P.MAX_STREAK + 1)

    def test_streaks_are_per_session(self):
        c, h = Ctx(), hook(Ctx(), opener_for(0.99))
        c = Ctx(); h = hook(c, opener_for(0.99))
        for s in ("a", "a", "a", "b"):
            h(session_id=s, assistant_response=PROMISE)
        self.assertEqual(len(c.sent), 3)

    def test_host_refusing_the_nudge_does_not_raise(self):
        c = Ctx(accept=False)
        self.assertIsNone(hook(c, opener_for(0.99))(session_id="s", assistant_response=PROMISE))

    def test_register_hooks_post_llm_call_only(self):
        c = Ctx()
        P.register(c)
        self.assertEqual(list(c.hooks), ["post_llm_call"])


class KeyFile(unittest.TestCase):
    def test_missing_file_is_empty(self):
        self.assertEqual(P.read_key("/nonexistent/key"), "")


if __name__ == "__main__":
    unittest.main()
