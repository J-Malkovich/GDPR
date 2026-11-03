"""promise-check: nudge an agent whose turn ended on "I'll do X".

post_llm_call fires once per turn with the final message. If that message looks like an announcement of work
still to do (Jev yes/no, P >= threshold) and does not wait for anyone, the plugin queues a short nudge into the
same session through ctx.inject_message, so the agent starts a new turn. Needs
plugins.entries.promise-check.allow_gateway_injection: true and a Jev key file (env PROMISE_CHECK_KEY_FILE,
default ~/.hermes/secrets/jev_api_key). Off by default: add `promise-check` to plugins.enabled to turn it on.

Safety: at most MAX_STREAK nudges in a row per session (reset by any turn that is not a promise); Jev errors,
bad answers or a missing key mean no nudge; the agent's text goes only into Jev's `state`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

JEV_URL = "https://api.typesafe.ai/v1/systemone"
THRESHOLD = 0.8
MAX_STREAK = 2
MIN_CHARS = 15
MENTION = re.compile(r"<@[!&]?\d+>")
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
QUESTIONS = {"promise": {
    "type": "noul",
    "instructions": ("Does this message announce work the author will do next that is not done yet "
                     "(for example 'I'll do X', 'next I will', 'starting now'), without waiting for anyone's "
                     "answer, approval or input first?"),
    "criteria": {
        "true": "The author promises to do something next and is not waiting for anyone.",
        "false": "The message reports finished work, asks a question, waits for a decision or input, hands off "
                 "to someone else, or is only chat.",
    },
}}
NUDGE = ("(automatic check) Your last reply said work was still to come, but the turn ended without doing it. "
         "Do it now, or say plainly what is blocking you.")

_lock = threading.Lock()
_streak: dict[str, int] = {}   # session id -> nudges in a row


class JevError(RuntimeError):
    pass


def read_key(path=None):
    path = path or os.environ.get("PROMISE_CHECK_KEY_FILE") or os.path.expanduser("~/.hermes/secrets/jev_api_key")
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def ask_jev(key, text, opener=urllib.request.urlopen, url=JEV_URL):
    """P(yes) that `text` announces pending work. Any failure is a JevError with a fixed message."""
    body = json.dumps({"model": "jev-latest", "state": {"message": text[-1500:]}, "questions": QUESTIONS}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=10) as r:
            data = json.loads(r.read())
        p = float(data["answers"]["promise"]["noul"])
    except urllib.error.HTTPError as e:
        raise JevError(f"jev http {e.code}") from None
    except (OSError, ValueError, KeyError, TypeError):
        raise JevError("jev: no usable answer") from None
    if not 0 <= p <= 1:
        raise JevError("jev: no usable answer")
    return p


def worth_asking(text):
    t = (text or "").strip()
    if len(t) < MIN_CHARS or MENTION.search(CODE.sub(" ", t)):
        return False  # a mention is a hand-off or a question: the agent is waiting on someone
    return not t.endswith("?")


def make_hook(ctx, ask=ask_jev, key_reader=read_key):
    def on_post_llm_call(session_id="", assistant_response=None, **_):
        try:
            text = assistant_response if isinstance(assistant_response, str) else ""
            if not worth_asking(text):
                _reset(session_id)
                return None
            key = key_reader()
            if not key:
                return None
            if _streak.get(session_id, 0) >= MAX_STREAK:
                logger.info("promise-check: streak cap reached for %s", session_id)
                return None
            if ask(key, text) < THRESHOLD:
                _reset(session_id)
                return None
            with _lock:
                _streak[session_id] = _streak.get(session_id, 0) + 1
            if not ctx.inject_message(NUDGE, session_key=session_key() or None):
                logger.warning("promise-check: nudge refused by the host (allow_gateway_injection set?)")
            else:
                logger.info("promise-check: nudged session %s", session_id)
        except JevError as e:
            logger.warning("promise-check: %s", e)
        except Exception as e:  # a plugin must never break the turn
            logger.warning("promise-check: %s", e.__class__.__name__)
        return None
    return on_post_llm_call


def session_key():
    """Gateway route key of the session this hook runs for ('' if none; hooks get the caller's context)."""
    try:
        from gateway.session_context import bound_session_key
        return bound_session_key()
    except Exception:
        return ""


def _reset(session_id):
    with _lock:
        _streak.pop(session_id, None)


def register(ctx):
    ctx.register_hook("post_llm_call", make_hook(ctx))
