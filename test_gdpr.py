import asyncio
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
except ImportError:  # minimal stand-in for the few names gdpr.py uses
    discord = types.ModuleType("discord")

    class AllowedMentions:
        def __init__(self, users=False, roles=False, everyone=False, replied_user=False):
            self.users, self.roles, self.everyone = users, roles, everyone

        @classmethod
        def none(cls):
            return cls(users=False)

    class Object:
        def __init__(self, id):
            self.id = id

    class HTTPException(Exception):
        def __init__(self, status=500):
            self.status = status

    discord.AllowedMentions, discord.Object, discord.HTTPException = AllowedMentions, Object, HTTPException
    discord.ChannelType = types.SimpleNamespace(private_thread="private_thread")
    sys.modules["discord"] = discord

import gdpr as W

OWNER, HQ, CHATS, BOT = 1, 100, 200, 9
AGENTS = {"dev": 11, "ops": 12, "qa": 13}
CFG = dict(token="x", owner_id=OWNER, hq_id=HQ, chats_id=CHATS, agents=AGENTS,
           owner_name="Alex", owner_aliases={"owner", "alex"})
CFGR = dict(CFG, agents=dict(AGENTS, boss=14), restricted={"boss"})
ENV = dict(GDPR_TOKEN="t", OWNER_ID="1", HQ_ID="100", CHATS_ID="200",
           AGENT_IDS="dev=11,ops=12,qa=13")


def run(c):
    return asyncio.run(c)


def make_guild():
    g = MagicMock()
    hq, chats, thread = MagicMock(), MagicMock(), MagicMock()
    hq.send = AsyncMock()
    thread.mention, thread.parent_id = "<#300>", CHATS
    for m in ("send", "add_user", "remove_user", "edit"):
        setattr(thread, m, AsyncMock())
    chats.create_thread = AsyncMock(return_value=thread)
    g.get_channel.side_effect = lambda i: {HQ: hq, CHATS: chats}.get(i)
    g.get_thread.side_effect = lambda i: thread if i == 300 else None
    g.fetch_channel = AsyncMock(return_value=None)  # uncached ids: Discord has nothing either
    g.hq, g.chats, g.thread = hq, chats, thread
    return g


def make_msg(g, uid, content, channel_id=HQ, parent_id=None):
    m = MagicMock()
    m.channel.parent_id = parent_id
    m.author.id, m.content, m.guild = uid, content, g
    m.channel.id = channel_id
    return m


def handle(g, uid, content, channel_id=HQ, parent_id=None):
    w = W.Gdpr(CFG, BOT)
    run(w.handle_message(make_msg(g, uid, content, channel_id, parent_id)))
    return g.hq.send.call_args[0][0] if g.hq.send.called else None


class MentionAdd(unittest.TestCase):
    def test_agent_mention_in_thread_adds(self):
        g = make_guild()
        m = make_msg(g, 12, "hey <@11> take over", channel_id=300, parent_id=CHATS)
        m.channel.add_user = AsyncMock()
        m.channel.fetch_members = AsyncMock(return_value=[])
        m.channel.send = AsyncMock()
        run(W.Gdpr(CFG, BOT).handle_message(m))
        self.assertEqual([c.args[0].id for c in m.channel.add_user.call_args_list], [11])
        # newly added agent gets a fresh ping, since the original message was not delivered to it
        self.assertIn("<@11>", m.channel.send.call_args[0][0])
        self.assertFalse(g.hq.send.called)

    def test_no_mention_no_add(self):
        g = make_guild()
        m = make_msg(g, 12, "plain chat", channel_id=300, parent_id=CHATS)
        m.channel.add_user = AsyncMock()
        run(W.Gdpr(CFG, BOT).handle_message(m))
        self.assertFalse(m.channel.add_user.called)


class Parsing(unittest.TestCase):
    def test_open(self):
        self.assertEqual(W.parse_command('<@9> open "Fix DNS" dev ops | please look', 9),
                         ("open", "Fix DNS", ["dev", "ops"], "please look"))

    def test_open_commas_and_at(self):
        self.assertEqual(W.parse_command('<@!9> open "x" @Dev, ops | hi', 9)[2], ["dev", "ops"])

    def test_kick_add_archive(self):
        self.assertEqual(W.parse_command("<@9> kick <#300> Dev", 9), ("kick", 300, "dev"))
        self.assertEqual(W.parse_command("<@9> add <#300> ops", 9), ("add", 300, "ops"))
        self.assertEqual(W.parse_command("<@9> archive <#300>", 9), ("archive", 300))

    def test_not_addressed(self):
        self.assertIsNone(W.parse_command("open \"x\" dev | hi", 9))
        self.assertIsNone(W.parse_command("<@10> kick <#300> dev", 9))

    def test_garbage_gives_help(self):
        self.assertEqual(W.parse_command("<@9> dance", 9), ("help",))

    def test_slug(self):
        self.assertEqual(W.slug("Fix DNS!! now"), "fix-dns-now")
        self.assertEqual(W.slug("???"), "chat")
        self.assertLessEqual(len(W.slug("a" * 100)), 40)

    def test_config(self):
        c = W.load_config(ENV)
        self.assertEqual(c["agents"], AGENTS)
        self.assertEqual(c["owner_id"], 1)

    def test_config_missing(self):
        with self.assertRaises(SystemExit) as e:
            W.load_config({k: v for k, v in ENV.items() if k != "GDPR_TOKEN"})
        self.assertIn("GDPR_TOKEN", str(e.exception))
        with self.assertRaises(SystemExit):
            W.load_config(dict(ENV, OWNER_ID="abc"))


class Open(unittest.TestCase):
    def test_agent_open(self):
        g = make_guild()
        out = handle(g, 11, '<@9> open "Fix DNS" dev ops | please look')
        kw = g.chats.create_thread.call_args.kwargs
        self.assertEqual(kw["name"], "fix-dns")
        self.assertEqual(kw["type"], discord.ChannelType.private_thread)
        self.assertFalse(kw["invitable"])
        self.assertEqual(kw["auto_archive_duration"], 4320)
        self.assertNotIn("message", kw)  # no anchor message
        added = {c.args[0].id for c in g.thread.add_user.call_args_list}
        self.assertEqual(added, {1, 11, 12})  # owner always in + requester + ops
        g.thread.send.assert_called_once()
        text, am = g.thread.send.call_args[0][0], g.thread.send.call_args.kwargs["allowed_mentions"]
        self.assertIn("From <@11>: please look", text)
        self.assertEqual({u.id for u in am.users}, {11, 12})
        self.assertFalse(am.everyone)
        self.assertFalse(am.roles)
        self.assertIn("<#300>", out)
        self.assertNotIn("<@", out)  # pointer uses plain names
        self.assertEqual(g.hq.send.call_args.kwargs["allowed_mentions"].users, False)

    def test_relay_does_not_ping_other_agents_or_requester_non_target(self):
        g = make_guild()
        handle(g, 11, '<@9> open "t" ops | hey <@13> @everyone')
        am = g.thread.send.call_args.kwargs["allowed_mentions"]
        self.assertEqual({u.id for u in am.users}, {12})  # only target agent may ping
        self.assertFalse(am.everyone)

    def test_owner_added_not_pinged(self):
        g = make_guild()
        handle(g, OWNER, "<@11> <@12> discuss backups")
        self.assertEqual({c.args[0].id for c in g.thread.add_user.call_args_list}, {1, 11, 12})
        am = g.thread.send.call_args.kwargs["allowed_mentions"]
        self.assertEqual({u.id for u in am.users}, {11, 12})
        self.assertIn("From <@1>: discuss backups", g.thread.send.call_args[0][0])
        self.assertEqual(g.chats.create_thread.call_args.kwargs["name"], "discuss-backups")

    def test_unknown_name(self):
        g = make_guild()
        out = handle(g, 11, '<@9> open "t" bob | hi')
        self.assertIn("unknown agent", out)
        g.chats.create_thread.assert_not_called()

    def test_mention_style_names_rejected(self):
        g = make_guild()
        out = handle(g, 11, '<@9> open "t" <@12> | hi')
        self.assertTrue(out.startswith("error"))
        g.chats.create_thread.assert_not_called()

    def test_no_agents_or_empty_message(self):
        g = make_guild()
        self.assertTrue(handle(g, 11, '<@9> open "t" | hi').startswith("error"))
        g.chats.create_thread.assert_not_called()


class Kick(unittest.TestCase):
    def test_kick(self):
        g = make_guild()
        out = handle(g, 11, "<@9> kick <#300> ops")
        self.assertEqual(g.thread.remove_user.call_args.args[0].id, 12)
        self.assertIn("ops removed", out)

    def test_refuse_owner(self):
        g = make_guild()
        for n in ("alex", "owner", "Alex"):
            out = handle(g, 11, f"<@9> kick <#300> {n}")
            self.assertIn("refused", out)
        g.thread.remove_user.assert_not_called()

    def test_refuse_owner_even_if_in_agent_map(self):
        w = W.Gdpr(dict(CFG, agents={"boss": OWNER, "dev": 11}), BOT)
        g = make_guild()
        with self.assertRaises(W.GdprError):
            run(w.kick(g, 11, 300, "boss"))

    def test_unknown_name_and_thread(self):
        g = make_guild()
        self.assertIn("unknown agent", handle(g, 11, "<@9> kick <#300> bob"))
        self.assertIn("not a thread", handle(g, 11, "<@9> kick <#999> dev"))
        g.thread.remove_user.assert_not_called()

    def test_thread_outside_chats_refused(self):
        g = make_guild()
        g.thread.parent_id = 555
        self.assertIn("not a thread", handle(g, 11, "<@9> kick <#300> dev"))
        g.thread.remove_user.assert_not_called()


class AddArchive(unittest.TestCase):
    def test_add(self):
        g = make_guild()
        out = handle(g, OWNER, "<@9> add <#300> qa")
        self.assertEqual(g.thread.add_user.call_args.args[0].id, 13)
        self.assertIn("qa added", out)

    def test_archive(self):
        g = make_guild()
        out = handle(g, 12, "<@9> archive <#300>")
        g.thread.edit.assert_called_once_with(archived=True)
        self.assertIn("archive", out)
        self.assertEqual(g.hq.send.call_args.kwargs["allowed_mentions"].users, False)


class Ignored(unittest.TestCase):
    def test_non_allowlisted(self):
        g = make_guild()
        self.assertIsNone(handle(g, 777, '<@9> open "t" dev | hi'))
        self.assertIsNone(handle(g, 777, "<@9> kick <#300> dev"))
        g.chats.create_thread.assert_not_called()
        g.thread.remove_user.assert_not_called()

    def test_own_messages(self):
        g = make_guild()
        self.assertIsNone(handle(g, BOT, '<@9> open "t" dev | hi'))

    def test_other_channel_and_threads(self):
        g = make_guild()
        self.assertIsNone(handle(g, OWNER, "<@11> hi", channel_id=300))
        self.assertIsNone(handle(g, 11, '<@9> open "t" dev | hi', channel_id=555))
        g.chats.create_thread.assert_not_called()

    def test_thread_under_chats_commands(self):
        g = make_guild()
        out = handle(g, 11, "<@9> kick ops", channel_id=300, parent_id=CHATS)
        g.thread.remove_user.assert_called_once()
        self.assertIn("kick: ops removed", out)
        handle(g, OWNER, "<@9> add dev", channel_id=300, parent_id=CHATS)
        g.thread.add_user.assert_called()
        handle(g, 12, "<@9> archive", channel_id=300, parent_id=CHATS)
        g.thread.edit.assert_called_once_with(archived=True)

    def test_thread_owner_kick_refused_and_plain_ignored(self):
        g = make_guild()
        out = handle(g, 11, "<@9> kick alex", channel_id=300, parent_id=CHATS)
        self.assertIn("owner cannot be kicked", out)
        g.thread.remove_user.assert_not_called()
        g2 = make_guild()
        m = make_msg(g2, OWNER, "<@11> hi", channel_id=300, parent_id=CHATS)
        m.channel.add_user = AsyncMock()
        m.channel.fetch_members = AsyncMock(return_value=[])
        m.channel.send = AsyncMock()
        run(W.Gdpr(CFG, BOT).handle_message(m))
        self.assertFalse(g2.hq.send.called)  # mention in a thread adds, never opens a thread
        g2.chats.create_thread.assert_not_called()

    def test_thread_elsewhere_and_hq_needs_ref(self):
        g = make_guild()
        self.assertIsNone(handle(g, 11, "<@9> archive", channel_id=300, parent_id=777))
        out = handle(g, 11, "<@9> archive")  # in the HQ channel without a thread ref
        self.assertIn("name the thread", out)
        g.thread.edit.assert_not_called()

    def test_dm(self):
        w = W.Gdpr(CFG, BOT)
        m = make_msg(None, OWNER, "<@11> hi")
        run(w.handle_message(m))  # no crash

    def test_owner_plain_chat_and_agent_plain_mention(self):
        g = make_guild()
        self.assertIsNone(handle(g, OWNER, "just talking"))
        self.assertIsNone(handle(g, 11, "no mention here"))
        g.chats.create_thread.assert_not_called()

    def test_agent_plain_mention_opens_thread_with_owner(self):
        g = make_guild()
        out = handle(g, 11, "<@12> <@11> discuss X")  # self-mention ignored
        self.assertEqual({c.args[0].id for c in g.thread.add_user.call_args_list}, {1, 11, 12})
        self.assertIn("From <@11>: discuss X", g.thread.send.call_args[0][0])
        self.assertIn("open:", out)
        self.assertNotIn("<@", out)

    def test_agent_mentioning_only_self_or_non_agent_ignored(self):
        g = make_guild()
        self.assertIsNone(handle(g, 11, "<@11> note to self"))
        self.assertIsNone(handle(g, 11, "<@555> hi"))
        g.chats.create_thread.assert_not_called()

    def test_owner_mentions_non_agent(self):
        g = make_guild()
        self.assertIsNone(handle(g, OWNER, "<@555> hello"))

    def test_hq_replies_do_not_loop(self):
        # the bot's own pointer/log lines contain no <@id> and are ignored as own messages
        g = make_guild()
        out = handle(g, 11, '<@9> open "t" dev ops | hi')
        self.assertNotRegex(out, r"<@")


class Discord(unittest.TestCase):
    def test_http_error_reported(self):
        g = make_guild()
        exc = discord.HTTPException.__new__(discord.HTTPException)
        exc.status = 403
        g.chats.create_thread.side_effect = exc
        self.assertIn("403", handle(g, 11, '<@9> open "t" dev | hi'))


class DecryptValue(unittest.TestCase):
    def setUp(self):
        try:
            import pyrage
        except ImportError:
            self.skipTest("pyrage not installed")
        import base64
        import tempfile
        self.pyrage, self.b64 = pyrage, base64
        self.ident = pyrage.x25519.Identity.generate()
        self.tmp = tempfile.NamedTemporaryFile("w", delete=False)
        self.tmp.write("# comment\n" + str(self.ident) + "\n")
        self.tmp.close()
        self.addCleanup(os.unlink, self.tmp.name)

    def enc(self, s, ident=None):
        ident = ident or self.ident
        return W.SECRET_PREFIX + self.b64.b64encode(
            self.pyrage.encrypt(s.encode(), [ident.to_public()])).decode()

    def test_roundtrip(self):
        v = "tok_\u00e9" * 50
        self.assertEqual(W.decrypt_value(self.enc(v), self.tmp.name), v)

    def enc_gz(self, s):
        import gzip
        return W.SECRET_PREFIX_GZ + self.b64.b64encode(
            self.pyrage.encrypt(gzip.compress(s.encode()), [self.ident.to_public()])).decode()

    def test_roundtrip_gzip(self):
        v = "-----BEGIN PRIVATE KEY-----\n" + "QUJD" * 1000 + "\n-----END PRIVATE KEY-----\n"
        self.assertEqual(W.decrypt_value(self.enc_gz(v), self.tmp.name), v)

    def test_gzip_bomb_rejected(self):
        with self.assertRaises(W.GdprError):
            W.decrypt_value(self.enc_gz("A" * (W.MAX_SECRET_BYTES + 10)), self.tmp.name)

    def test_plain_passthrough(self):
        self.assertEqual(W.decrypt_value("plainvalue", "/nonexistent"), "plainvalue")

    def test_bad_ciphertext_errors_without_leak(self):
        with self.assertRaises(W.GdprError) as cm:
            W.decrypt_value(W.SECRET_PREFIX + "AAAA", self.tmp.name)
        self.assertNotIn("AAAA", str(cm.exception))

    def test_wrong_key_errors(self):
        other = self.pyrage.x25519.Identity.generate()
        with self.assertRaises(W.GdprError):
            W.decrypt_value(self.enc("x", other), self.tmp.name)

    def test_missing_key_file_errors(self):
        with self.assertRaises(W.GdprError):
            W.decrypt_value(self.enc("x"), "/nonexistent")


class SecretStore(unittest.TestCase):
    def test_writes_600_and_no_overwrite(self):
        import os, stat, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = W.store_secret(d, "dev_token", "abc123\n")
            self.assertEqual(open(p).read(), "abc123\n")
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            p2 = W.store_secret(d, "dev_token", "other")
            self.assertNotEqual(p, p2)
            self.assertEqual(open(p).read(), "abc123\n")

    def test_sanitizes_names_and_rejects_empty(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            for raw, want in [("my token!", "my_token_"), ("../x", "x"), ("a/b", "a_b"), ("-y", "y")]:
                p = W.store_secret(d, raw, "v")
                self.assertEqual(os.path.dirname(p), d)
                self.assertEqual(os.path.basename(p), want)
            for bad in ["", "   ", "///", "..."]:
                with self.assertRaises(W.GdprError):
                    W.store_secret(d, bad, "v")
            with self.assertRaises(W.GdprError):
                W.store_secret(d, "ok", "  \n")


class Macro(unittest.TestCase):
    def members(self, g, *ids):
        g.thread.fetch_members = AsyncMock(return_value=[types.SimpleNamespace(id=i) for i in ids])

    def test_done_mentions_only_agents_in_thread(self):
        g = make_guild()
        self.members(g, OWNER, 11, 13, 777)  # ops (12) not in thread, 777 is a stranger
        run(W.Gdpr(CFG, BOT).macro(g, OWNER, 300, "done"))
        text, am = g.thread.send.call_args[0][0], g.thread.send.call_args.kwargs["allowed_mentions"]
        self.assertEqual({u.id for u in am.users}, {11, 13})
        self.assertIn("<@11>", text)
        self.assertIn("<@13>", text)
        self.assertNotIn("<@12>", text)
        self.assertNotIn("<@1>", text)
        self.assertNotIn("<@777>", text)
        self.assertIn("Wrap-up requested by Alex", text)
        self.assertFalse(am.everyone)
        self.assertFalse(am.roles)
        self.assertIn("done: sent in <#300>", g.hq.send.call_args[0][0])

    def test_owner_in_agent_map_never_pinged(self):
        w = W.Gdpr(dict(CFG, agents={"boss": OWNER, "dev": 11}), BOT)
        g = make_guild()
        self.members(g, OWNER, 11)
        run(w.macro(g, OWNER, 300, "done"))
        self.assertEqual({u.id for u in g.thread.send.call_args.kwargs["allowed_mentions"].users}, {11})

    def test_no_agents_errors(self):
        g = make_guild()
        self.members(g, OWNER)
        with self.assertRaises(W.GdprError):
            run(W.Gdpr(CFG, BOT).macro(g, OWNER, 300, "done"))
        g.thread.send.assert_not_called()

    def test_outside_thread_errors(self):
        g = make_guild()
        with self.assertRaises(W.GdprError):
            run(W.Gdpr(CFG, BOT).macro(g, OWNER, HQ, "done"))
        g.thread.send.assert_not_called()

    def test_unknown_macro_and_custom_macros(self):
        g = make_guild()
        self.members(g, 11)
        w = W.Gdpr(dict(CFG, macros={"ship": "ship it"}), BOT)
        with self.assertRaises(W.GdprError):
            run(w.macro(g, OWNER, 300, "done"))
        run(w.macro(g, OWNER, 300, "ship"))
        self.assertIn("ship it", g.thread.send.call_args[0][0])


class ArchivedThreads(unittest.TestCase):
    def test_archived_thread_is_fetched(self):
        g = make_guild()
        g.get_thread.side_effect = lambda i: None  # not cached (archived)
        g.fetch_channel = AsyncMock(return_value=g.thread)
        out = handle(g, OWNER, "<@9> archive <#300>")
        g.fetch_channel.assert_awaited_once_with(300)
        g.thread.edit.assert_called_once_with(archived=True)
        self.assertIn("archive", out)

    def test_fetched_thread_outside_chats_refused(self):
        g = make_guild()
        g.get_thread.side_effect = lambda i: None
        g.fetch_channel = AsyncMock(return_value=types.SimpleNamespace(parent_id=555))
        self.assertIn("not a thread", handle(g, OWNER, "<@9> archive <#300>"))
        g.thread.edit.assert_not_called()


class OwnerName(unittest.TestCase):
    def test_from_env(self):
        cfg = W.load_config(dict(ENV, OWNER_NAME="Alex"))
        self.assertEqual((cfg["owner_name"], cfg["owner_aliases"]), ("Alex", {"owner", "alex"}))
        cfg = W.load_config(ENV)
        self.assertEqual((cfg["owner_name"], cfg["owner_aliases"]), ("the owner", {"owner"}))

    def test_macro_uses_owner_name(self):
        g = make_guild()
        g.thread.fetch_members = AsyncMock(return_value=[types.SimpleNamespace(id=11)])
        run(W.Gdpr(dict(CFG, owner_name="Sam"), BOT).macro(g, OWNER, 300, "done"))
        self.assertIn("Wrap-up requested by Sam.", g.thread.send.call_args[0][0])


class Init(unittest.TestCase):
    TOKEN_ID = "123456789012345678"

    def env(self):
        import base64
        tok = base64.urlsafe_b64encode(self.TOKEN_ID.encode()).decode().rstrip("=") + ".x.y"
        return dict(ENV, GDPR_TOKEN=tok)

    def test_creates_key_and_inbox_and_keeps_key(self):
        import contextlib
        import io
        import stat
        import tempfile
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as out:
            pub = W.init(d, self.env(), chown=False)
            key = os.path.join(d, "gdpr_age_key")
            self.assertTrue(pub.startswith("age1"))
            self.assertEqual(stat.S_IMODE(os.stat(key).st_mode), 0o400)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "secrets")).st_mode), 0o700)
            with open(key + ".pub") as f:
                self.assertEqual(f.read().strip(), pub)
            self.assertEqual(W.init(d, self.env(), chown=False), pub)  # second run keeps the key
            self.assertIn(pub, out.getvalue())

    def test_key_decrypts(self):
        import base64
        import contextlib
        import io
        import tempfile
        import pyrage
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            pub = W.init(d, self.env(), chown=False)
            ct = pyrage.encrypt(b"hunter2", [pyrage.x25519.Recipient.from_str(pub)])
            self.assertEqual(W.decrypt_value(W.SECRET_PREFIX + base64.b64encode(ct).decode(),
                                             os.path.join(d, "gdpr_age_key")), "hunter2")

    def test_refuses_bad_env_and_key_folder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                W.init(d, dict(ENV, OWNER_ID=""), chown=False)
            os.mkdir(os.path.join(d, "gdpr_age_key"))
            with self.assertRaises(SystemExit):
                W.init(d, self.env(), chown=False)

    def test_invite_url(self):
        self.assertEqual(W.bot_id_from_token(self.env()["GDPR_TOKEN"]), self.TOKEN_ID)
        self.assertIsNone(W.bot_id_from_token("garbage"))
        self.assertIn(f"permissions={W.INVITE_PERMISSIONS}", W.invite_url("1"))


class FakeDiscord:
    """In-memory stand-in for the few REST endpoints `setup` uses."""
    def __init__(self, members_ok=True, perms_ok=True, guilds=None, agent_perms=0,
                 app_flags=W.FLAG_MEMBERS & (1 << 14) | (1 << 18)):
        self.app_flags = app_flags
        self.guilds = [{"id": "77", "name": "Test"}] if guilds is None else guilds
        self.members_ok, self.perms_ok = members_ok, perms_ok
        self.roles = [{"id": "77", "name": "@everyone", "permissions": "0"},
                      {"id": "80", "name": "power", "permissions": str(agent_perms)}]
        self.members = {"9": {"user": {"id": "9", "username": "gdpr", "bot": True}, "roles": []},
                        "1": {"user": {"id": "1", "username": "alex"}, "roles": []},
                        "11": {"user": {"id": "11", "username": "Dev Bot", "bot": True}, "roles": ["80"]}}
        self.channels = [{"id": "500", "name": "general", "type": 0}]
        self.calls = []

    def __call__(self, token, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/users/@me":
            return 200, {"id": "9", "username": "gdpr"}
        if path == "/applications/@me":
            return 200, {"flags": self.app_flags}
        if path == "/users/@me/guilds":
            return 200, self.guilds
        if path == "/guilds/77":
            return 200, {"owner_id": "1"}
        if path.startswith("/guilds/77/members?"):
            return (200, list(self.members.values())) if self.members_ok else (403, {})
        if path == "/guilds/77/roles":
            if method == "POST":
                if not self.perms_ok:
                    return 403, {}
                self.roles.append({"id": "90", "name": body["name"], "permissions": "0"})
                return 200, self.roles[-1]
            return 200, self.roles
        if method == "PUT" and "/roles/" in path:
            uid, rid = path.split("/")[4], path.split("/")[6]
            self.members[uid]["roles"].append(rid)
            return 204, None
        if path == "/guilds/77/channels":
            if method == "POST":
                if not self.perms_ok:
                    return 403, {}
                ch = {"id": str(600 + len(self.channels)), "name": body["name"], "type": 0,
                      "permission_overwrites": body["permission_overwrites"]}
                self.channels.append(ch)
                return 201, ch
            return 200, self.channels
        if method == "PUT" and "/permissions/" in path:
            return (204, None) if self.perms_ok else (403, {})
        raise AssertionError((method, path))


class Setup(unittest.TestCase):
    TOKEN = "dG9rZW4.x.y"

    def run_setup(self, fake, env_text=None, **kw):
        import contextlib
        import io
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d)
        with open(os.path.join(d, ".env"), "w") as f:
            f.write(env_text or f"# comment\nGDPR_TOKEN={self.TOKEN}\nOWNER_ID=\nHQ_ID=\nCHATS_ID=\nAGENT_IDS=\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = W.setup(d, env={}, call=fake, say=print, run_init=False, **kw)
        return ok, W.read_env_file(os.path.join(d, ".env")), out.getvalue(), d

    def test_fresh_server(self):
        fake = FakeDiscord()
        ok, env, out, d = self.run_setup(fake)
        self.assertTrue(ok, out)
        self.assertEqual(env["OWNER_ID"], "1")
        self.assertEqual(env["AGENT_IDS"], "dev-bot=11")
        hq = next(c for c in fake.channels if c["name"] == "gdpr-hq")
        th = next(c for c in fake.channels if c["name"] == "gdpr-threads")
        self.assertEqual((env["HQ_ID"], env["CHATS_ID"]), (hq["id"], th["id"]))
        self.assertIn("90", fake.members["11"]["roles"])  # agents role given to the agent bot
        ow = {o["id"]: o for o in th["permission_overwrites"]}
        self.assertEqual(int(ow["90"]["allow"]), W.AGENT_THREADS_ALLOW)
        self.assertTrue(int(ow["90"]["deny"]) & W.MANAGE_THREADS)
        self.assertEqual(int(ow["77"]["deny"]), W.VIEW)  # @everyone cannot see it
        self.assertEqual(int(ow["9"]["allow"]), W.BOT_ALLOW)
        hq_agents = {o["id"]: o for o in hq["permission_overwrites"]}["90"]
        self.assertTrue(int(hq_agents["allow"]) & W.VIEW)  # agents appear in the HQ mention picker
        self.assertEqual(int(hq_agents["deny"]), W.SEND | W.CREATE_PUBLIC | W.CREATE_PRIVATE)  # but cannot post
        with open(os.path.join(d, ".env")) as f:
            self.assertTrue(f.read().startswith("# comment\n"))  # comments kept

    def test_second_run_reuses_everything(self):
        fake = FakeDiscord()
        self.run_setup(fake)
        n_channels, n_roles = len(fake.channels), len(fake.roles)
        fake.calls.clear()
        ok, _, out, _ = self.run_setup(fake)
        self.assertTrue(ok, out)
        self.assertEqual((len(fake.channels), len(fake.roles)), (n_channels, n_roles))
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])

    def test_existing_env_values_kept(self):
        fake = FakeDiscord()
        env_text = f"GDPR_TOKEN={self.TOKEN}\nOWNER_ID=5\nAGENT_IDS=coder=11\n"
        ok, env, out, _ = self.run_setup(fake, env_text)
        self.assertTrue(ok, out)
        self.assertEqual((env["OWNER_ID"], env["AGENT_IDS"]), ("5", "coder=11"))
        th = next(c for c in fake.channels if c["name"] == "gdpr-threads")
        self.assertIn("5", {o["id"] for o in th["permission_overwrites"]})  # configured owner gets access

    def test_missing_permissions_fail(self):
        ok, env, out, _ = self.run_setup(FakeDiscord(perms_ok=False))
        self.assertFalse(ok)
        self.assertIn("FAIL  role agents", out)
        self.assertEqual(env["HQ_ID"], "")

    def test_agent_with_manage_threads_reported(self):
        ok, _, out, _ = self.run_setup(FakeDiscord(agent_perms=W.MANAGE_THREADS))
        self.assertFalse(ok)
        self.assertIn("FAIL  dev-bot has no Manage Threads/Administrator", out)

    def test_members_intent_off(self):
        ok, _, out, _ = self.run_setup(FakeDiscord(members_ok=False))
        self.assertFalse(ok)
        self.assertIn("FAIL  agent bots found: none", out)
        fake = FakeDiscord(members_ok=False)
        ok, _, out, _ = self.run_setup(fake, f"GDPR_TOKEN={self.TOKEN}\nAGENT_IDS=dev=11\n")
        self.assertIn("PUT", {c[0] for c in fake.calls})  # still assigns the role from AGENT_IDS
        self.assertTrue(ok, out)  # skipped role check is a warning, not a failure
        self.assertIn("WARN  could not list members", out)
        self.assertIn("setup complete", out)

    def test_not_invited_or_many_servers(self):
        for guilds in ([], [{"id": "77", "name": "a"}, {"id": "78", "name": "b"}]):
            with self.assertRaises(SystemExit):
                self.run_setup(FakeDiscord(guilds=guilds))
        with self.assertRaises(SystemExit):
            self.run_setup(FakeDiscord(), "GDPR_TOKEN=\n")

    def test_init_runs_with_new_values(self):
        from unittest import mock
        import contextlib, io, tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d)
        with open(os.path.join(d, ".env"), "w") as f:
            f.write(f"GDPR_TOKEN={self.TOKEN}\nOWNER_ID=\nHQ_ID=\nCHATS_ID=\nAGENT_IDS=\n")
        with mock.patch.object(W, "init") as init, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(W.setup(d, env={}, call=FakeDiscord()))
        W.load_config(init.call_args[0][1])  # the merged values form a complete config

    def test_intents_checked(self):
        ok, _, out, _ = self.run_setup(FakeDiscord(app_flags=1 << 18))  # members intent off
        self.assertFalse(ok)
        self.assertIn("FAIL  Server Members Intent", out)
        ok, _, out, _ = self.run_setup(FakeDiscord(app_flags=(1 << 15) | (1 << 19)))  # "limited" variants
        self.assertTrue(ok, out)
        self.assertIn("PASS  Message Content Intent is on", out)

    def test_invite_permissions(self):
        self.assertEqual(W.INVITE_PERMISSIONS, 360777321472 | W.CREATE_PUBLIC | W.MANAGE_CHANNELS | W.MANAGE_ROLES)
        for p in (W.AGENT_HQ_ALLOW, W.AGENT_HQ_DENY, W.AGENT_THREADS_ALLOW, W.AGENT_THREADS_DENY,
                  W.BOT_ALLOW, W.OWNER_ALLOW):
            self.assertEqual(p & ~W.INVITE_PERMISSIONS, 0)  # the bot only sets permissions it holds


class Restricted(unittest.TestCase):
    def hq(self, uid, text):
        g = make_guild()
        run(W.Gdpr(CFGR, BOT).handle_message(make_msg(g, uid, text)))
        return g

    def test_agent_open_refused(self):
        g = self.hq(11, '<@9> open "x" boss | hi')
        self.assertFalse(g.chats.create_thread.called)
        self.assertIn("owner only", g.hq.send.call_args[0][0])

    def test_agent_plain_mention_refused(self):
        g = self.hq(11, "<@14> do this")
        self.assertFalse(g.chats.create_thread.called)
        self.assertIn("owner only", g.hq.send.call_args[0][0])

    def test_owner_plain_mention_opens(self):
        g = self.hq(OWNER, "<@14> do this")
        self.assertTrue(g.chats.create_thread.called)
        self.assertIn(14, {c.args[0].id for c in g.thread.add_user.call_args_list})

    def test_owner_open_with_others(self):
        g = self.hq(OWNER, '<@9> open "x" boss dev | hi')
        self.assertEqual({c.args[0].id for c in g.thread.add_user.call_args_list}, {1, 14, 11})

    def test_agent_add_refused(self):
        g = make_guild()
        m = make_msg(g, 12, "<@9> add <#300> boss", channel_id=300, parent_id=CHATS)
        run(W.Gdpr(CFGR, BOT).handle_message(m))
        self.assertFalse(g.thread.add_user.called)
        self.assertIn("owner only", g.hq.send.call_args[0][0])

    def test_agent_mention_in_thread_does_not_add(self):
        g = make_guild()
        m = make_msg(g, 12, "hey <@14> help", channel_id=300, parent_id=CHATS)
        m.channel.add_user = AsyncMock(); m.channel.fetch_members = AsyncMock(return_value=[]); m.channel.send = AsyncMock()
        run(W.Gdpr(CFGR, BOT).handle_message(m))
        self.assertFalse(m.channel.add_user.called)

    def test_owner_mention_in_thread_adds(self):
        g = make_guild()
        m = make_msg(g, OWNER, "hey <@14> help", channel_id=300, parent_id=CHATS)
        m.channel.add_user = AsyncMock(); m.channel.fetch_members = AsyncMock(return_value=[]); m.channel.send = AsyncMock()
        run(W.Gdpr(CFGR, BOT).handle_message(m))
        self.assertEqual([c.args[0].id for c in m.channel.add_user.call_args_list], [14])

    def test_member_already_in_thread_not_repinged(self):
        g = make_guild()
        m = make_msg(g, OWNER, "hey <@11> again", channel_id=300, parent_id=CHATS)
        m.channel.add_user = AsyncMock(); m.channel.send = AsyncMock()
        m.channel.fetch_members = AsyncMock(return_value=[types.SimpleNamespace(id=11)])
        run(W.Gdpr(CFG, BOT).handle_message(m))
        self.assertFalse(m.channel.send.called)

    def test_config_restricted(self):
        c = W.load_config(dict(ENV, AGENT_IDS="dev=11,boss=14", RESTRICTED_AGENTS="Boss"))
        self.assertEqual(c["restricted"], {"boss"})


def http_error(status):
    try:  # real discord.py: HTTPException(response, message)
        return discord.HTTPException(types.SimpleNamespace(status=status, reason="x"), "x")
    except TypeError:  # stand-in
        return discord.HTTPException(status)


class MembersIntent(unittest.TestCase):
    def test_done_explains_missing_intent(self):
        g = make_guild()
        g.thread.fetch_members = AsyncMock(side_effect=http_error(403))
        with self.assertRaises(W.GdprError) as e:
            run(W.Gdpr(CFG, BOT).macro(g, OWNER, 300, "done"))
        self.assertIn("Server Members Intent", str(e.exception))

    def test_mention_add_explains_missing_intent(self):
        g = make_guild()
        m = make_msg(g, OWNER, "hey <@11>", channel_id=300, parent_id=CHATS)
        m.channel.add_user = AsyncMock(); m.channel.send = AsyncMock()
        m.channel.fetch_members = AsyncMock(side_effect=http_error(403))
        run(W.Gdpr(CFG, BOT).handle_message(m))
        self.assertIn("Server Members Intent", g.hq.send.call_args[0][0])
        self.assertFalse(m.channel.add_user.called)

    def test_other_errors_pass_through(self):
        g = make_guild()
        g.thread.fetch_members = AsyncMock(side_effect=http_error(500))
        with self.assertRaises(discord.HTTPException):
            run(W.Gdpr(CFG, BOT).macro(g, OWNER, 300, "done"))


if __name__ == "__main__":
    unittest.main()
