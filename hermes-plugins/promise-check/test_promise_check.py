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


import os
os.environ["PROMISE_CHECK_MODE"] = "nudge"   # most tests exercise the nudge path


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

    def test_gate_blocks_before_jev(self):
        for text in ("<@123> I'll do it once you answer.", "Shall I start now?", "ok", "", None,
                     "Tests pass and the PR is up."):
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

    def test_streak_caps_loops_and_a_non_promise_turn_resets(self):
        c = Ctx()
        h = hook(c, opener_for(0.99))
        for _ in range(5):
            h(session_id="s", assistant_response=PROMISE)
        self.assertEqual(len(c.sent), P.MAX_STREAK)
        h(session_id="s", assistant_response="Pushed. The PR is up and all tests pass.")  # gate says no: reset
        h(session_id="s", assistant_response=PROMISE)
        self.assertEqual(len(c.sent), P.MAX_STREAK + 1)

    def test_streaks_are_per_session(self):
        c = Ctx()
        h = hook(c, opener_for(0.99))
        for s in ("a", "a", "a", "b"):
            h(session_id=s, assistant_response=PROMISE)
        self.assertEqual(len(c.sent), 3)  # a: 2 (capped), b: 1

    def test_host_refusing_the_nudge_does_not_raise(self):
        c = Ctx(accept=False)
        self.assertIsNone(hook(c, opener_for(0.99))(session_id="s", assistant_response=PROMISE))

    def test_register_hooks_post_llm_call_only(self):
        c = Ctx()
        P.register(c)
        self.assertEqual(list(c.hooks), ["post_llm_call"])


# Real final messages from the agents' session history, hand-labelled by Hermes. STOP = the turn ended on an
# immediate promise and nothing followed; WAIT = the agent was legitimately waiting. The gate must pass every
# one of these (wait cases are for Jev to separate), and must drop non-promises.
STOPS = ["I'll create them now.", "I'll build the Vencord plugin on arch-pc in the meantime.",
         "I'll update the card to say `max_turns` is a deliberate per-agent value.",
         "I'll check that the new numbers fix the KvK result.", "On it, starting with the prep."]
WAITS = ["I'll check the name as soon as he says it's saved.", "I'll set up whichever you pick.",
         "I'll run the harness against that push.", "I'll do that after the review comes back."]
NOT_PROMISES = ["Done. Tests pass and the PR is up.", "The service is healthy, nothing to change.",
                "I checked the logs and found the cause: a stale token."]


class Gate(unittest.TestCase):
    def test_labelled_stops_and_waits_pass_the_gate(self):
        for t in STOPS + WAITS:
            self.assertTrue(P.worth_asking(t), t)

    def test_non_promises_do_not(self):
        for t in NOT_PROMISES:
            self.assertFalse(P.worth_asking(t), t)

    def test_only_the_last_sentence_counts(self):
        self.assertFalse(P.worth_asking("I'll be honest, the fix was small. It is merged and verified."))
        self.assertTrue(P.worth_asking("Found the bug in the parser. I'll patch it now."))

    def test_on_it_only_counts_as_an_opener(self):
        self.assertFalse(P.worth_asking("PR #4 is merged and I have nothing more to do on it."))
        self.assertTrue(P.worth_asking("On it, starting with the prep."))
        self.assertTrue(P.worth_asking("Understood. On it."))

    def test_hermes_verifier_footer_is_not_the_last_sentence(self):
        foot = "\n\n\u26a0\ufe0f File-mutation verifier: 1 write failed\n  \u2022 /tmp/x.py: could not find old_string"
        self.assertTrue(P.worth_asking("Found it. I'll patch it now." + foot))
        self.assertFalse(P.worth_asking("Patched and verified." + foot))

    def test_credential_lookalikes_never_reach_jev(self):
        bad = ["I'll use ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4 to push now.",
               "I'll deploy with sk-" + "abcdef0123456789abcdef now.",
               "I'll paste -----BEGIN OPENSSH PRIVATE KEY----- next.",
               "I'll set password=hunter22secret and restart.",
               "I'll curl with Bearer: abcdefghij1234 now.",
               "I'll check hash " + "0123456789abcdef" * 2 + " next.",
               "I'll use eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig now.",
               "I'll store " + "A" * 20 + "b1" * 12 + " in the vault now."]
        for t in bad:
            self.assertFalse(P.worth_asking(t), t)
            o, c = opener_for(0.99), Ctx()
            hook(c, o)(session_id="s", assistant_response=t)
            self.assertEqual((o.seen, c.sent), ([], []), t)

    def test_normal_text_with_paths_and_ids_still_passes(self):
        for t in ("I'll edit /root/.hermes/work/gdpr-public/gdpr.py now.", "I'll ssh to dev@192.168.178.230 next.",
                  "I'll open PR #14 and ping <@1> later".replace("<@1>", "Hermes"),
                  "I'll check commit b6737ea next.", "I'll restart the gdpr container now."):
            self.assertTrue(P.worth_asking(t), t)

    def test_trailing_question_blocks(self):
        self.assertFalse(P.worth_asking("I'll start now, ok?"))

    def test_tool_use_this_turn_blocks_but_an_earlier_turn_does_not(self):
        used = [{"role": "user", "content": "go"}, {"role": "assistant", "tool_calls": [{"id": "1"}]},
                {"role": "tool", "content": "ok"}, {"role": "assistant", "content": "I'll do the rest now."}]
        self.assertFalse(P.worth_asking("I'll do the rest now.", "go", used))
        earlier = [{"role": "assistant", "tool_calls": [{"id": "1"}]}, {"role": "tool", "content": "ok"},
                   {"role": "user", "content": "go"}, {"role": "assistant", "content": "I'll do it now."}]
        self.assertTrue(P.worth_asking("I'll do it now.", "go", earlier))

    def test_reply_to_a_background_event_is_skipped(self):
        for um in ("[IMPORTANT: Background process proc_1 completed normally (exit code 0).",
                   "[GDPR] [IMPORTANT: Background process proc_1 completed normally]",
                   "[ASYNC DELEGATION COMPLETE \u2014 deleg_1]"):
            self.assertFalse(P.worth_asking("I'll check the output next.", um), um)
        self.assertTrue(P.worth_asking("I'll check the output next.", "please go ahead"))

    def test_hook_passes_turn_context_to_the_gate(self):
        c, o = Ctx(), opener_for(0.99)
        hook(c, o)(session_id="s", assistant_response=PROMISE,
                   user_message="[IMPORTANT: Background process proc_1 completed normally]")
        self.assertEqual((o.seen, c.sent), ([], []))


class LogOnly(unittest.TestCase):
    def test_default_mode_logs_and_never_nudges(self):
        old = os.environ.pop("PROMISE_CHECK_MODE")
        try:
            c = Ctx()
            with self.assertLogs("promise_check", "INFO") as cm:
                hook(c, opener_for(0.97))(session_id="s", assistant_response=PROMISE)
            self.assertEqual(c.sent, [])
            self.assertTrue(any("LOG-ONLY would nudge" in m for m in cm.output))
        finally:
            os.environ["PROMISE_CHECK_MODE"] = old


class KeyFile(unittest.TestCase):
    def test_missing_file_is_empty(self):
        self.assertEqual(P.read_key("/nonexistent/key"), "")


if __name__ == "__main__":
    unittest.main()
