"""promise-check: nudge an agent whose turn ended on "I'll do X".

post_llm_call fires once per turn with the final message. If that message looks like an announcement of work
still to do (Jev yes/no, P >= threshold) and does not wait for anyone, the plugin queues a short nudge into the
same session through ctx.inject_message, so the agent starts a new turn. Needs
plugins.entries.promise-check.allow_gateway_injection: true and a Jev key file (env PROMISE_CHECK_KEY_FILE,
default ~/.hermes/secrets/jev_api_key). Off by default: add `promise-check` to plugins.enabled to turn it on. Even then it only LOGS (gate hits, Jev
score, 'would nudge') until env PROMISE_CHECK_MODE=nudge is set.

Gate: a promise or progressive phrase in the last sentence, no '?', no mention, no credential lookalike, not a
reply to a background event (tool calls in the turn do not exempt it). Only then Jev is asked.

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
# Gate (free, runs before any Jev call). Tuned to over-send: Jev, not the regex, decides waits vs stops.
# Wait words ("after", "once", "then"...) deliberately do NOT block: agents that said "I'll do X after Y" and
# stopped were nudged by the owner in the session history.
PROMISE = re.compile(
    r"\b(i['\u2019]ll|i will|i['\u2019]m going to|i am going to|let me|let['\u2019]s|now i|next[, ]+i|next up|"
    r"starting now|starting with|going to|will now)\b|(?:^|[.!?:;,]\s+)on it\b", re.I)
# Second pattern: "I'm continuing", "carry on", "I'll push"... Agents often do real work, then end on one of these
# and stop (found by replaying the five agents' session history), so a tool call in the turn does not exempt it.
PROGRESSIVE = re.compile(
    r"\b(i['\u2019]m (now )?(continuing|fixing|finishing|working|checking|running|writing|building|doing|starting|going)|"
    r"continuing with|carry on|i['\u2019]ll (continue|carry on|keep going|push|start|proceed))\b", re.I)
BACKGROUND_EVENT = re.compile(r"^\s*(?:\[[^\]\n]{1,40}\]\s*)?\[(?:IMPORTANT: Background process|ASYNC DELEGATION|SYSTEM: Background)", re.I)
# Hermes appends "⚠️ File-mutation verifier: ..." (plus bullet lines) to a final reply after failed writes; it is
# not the agent's own words, so it must not count as the last sentence.
VERIFIER_FOOTER = re.compile(r"\n*[ \t]*⚠️?\s*File-mutation verifier:.*\Z", re.S)
# Anything that looks like a credential never leaves the host: such a message is not sent to Jev at all.
SECRETISH = re.compile(
    r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)|\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|"
    r"\bgithub_pat_[A-Za-z0-9_]{20,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{30,}|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.|\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/_-]{40,}={0,2}(?![\w/.-])|"
    r"(?:password|passwd|secret|token|api[_-]?key|bearer)\s*[:=]\s*\S{6,}|"
    r"\b[MNO][A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
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


def last_sentence(text):
    parts = [p.strip() for p in SENTENCE_END.split(CODE.sub(" ", text).strip()) if p.strip()]
    return parts[-1] if parts else ""


def gate(text, user_message=""):
    """The free check before any Jev call. Returns which pattern matched ('promise' or 'progressive'), or None.
    Needs: a promise/progressive phrase in the last sentence, no trailing '?', no mention outside code, no
    credential lookalike, and the turn not a reply to a background-process/delegation event. Tool calls in the
    turn do NOT exempt it. Wait words do not block: Jev decides waits vs stops."""
    t = VERIFIER_FOOTER.sub("", text or "").strip()
    if len(t) < MIN_CHARS or t.endswith("?") or MENTION.search(CODE.sub(" ", t)):
        return None  # a mention is a hand-off or a question: the agent is waiting on someone
    if SECRETISH.search(t):
        return None
    if BACKGROUND_EVENT.match(user_message if isinstance(user_message, str) else ""):
        return None
    last = last_sentence(t)
    if PROMISE.search(last):
        return "promise"
    if PROGRESSIVE.search(last):
        return "progressive"
    return None


def worth_asking(text, user_message="", history=None):
    return gate(text, user_message) is not None  # history is accepted for compatibility and ignored


def make_hook(ctx, ask=ask_jev, key_reader=read_key):
    def on_post_llm_call(session_id="", assistant_response=None, user_message="", **_):
        try:
            text = assistant_response if isinstance(assistant_response, str) else ""
            kind = gate(text, user_message)
            if kind is None:
                _reset(session_id)
                return None
            key = key_reader()
            if not key:
                return None
            if _streak.get(session_id, 0) >= MAX_STREAK:
                logger.info("promise-check: streak cap reached for %s", session_id)
                return None
            p = ask(key, text)
            logger.info("promise-check: gate hit (%s), jev=%.2f session=%s", kind, p, session_id)
            if p < THRESHOLD:
                _reset(session_id)
                return None
            if os.environ.get("PROMISE_CHECK_MODE", "log").lower() != "nudge":
                logger.info("promise-check: LOG-ONLY would nudge session %s", session_id)
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
