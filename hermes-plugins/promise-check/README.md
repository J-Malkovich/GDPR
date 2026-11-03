# promise-check (Hermes plugin)

When a turn ends on "I'll do X" and nothing follows, this plugin asks TypeSafe Jev one yes/no question about the
final message and, if the answer is yes (P >= 0.8), queues a short automatic nudge into the same session.

- Hook: `post_llm_call` (once per turn, no waiting period).
- Skipped without calling Jev: messages under 15 chars, ending in `?`, or mentioning someone outside code.
- Fails closed: no key, Jev error or odd answer = no nudge. At most 2 nudges in a row per session.
- Agent text goes only into Jev's `state`, never into question text.
- Key: file `~/.hermes/secrets/jev_api_key` (or env `PROMISE_CHECK_KEY_FILE`).

Turn on, per agent (Hermes owns the config):
    hermes plugins enable promise-check
    hermes config set plugins.entries.promise-check.allow_gateway_injection true
Turn off: `hermes plugins disable promise-check`.

Test: `PYTHONPATH=. python3 -m unittest test_promise_check`
