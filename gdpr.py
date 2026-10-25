"""GDPR: AI-free Discord bot that manages private threads for a team of agent bots. No model calls, no state files.

Optional card watcher: PLANKA_URL, PLANKA_BOARD_ID, PLANKA_API_KEY[_FILE] (README).

Env: GDPR_TOKEN, OWNER_ID, HQ_ID, CHATS_ID, AGENT_IDS (name=id,name=id); optional OWNER_NAME,
RESTRICTED_AGENTS. `python gdpr.py setup <dir>` prepares Discord, .env, key and secrets folder (README B3).
"""
import asyncio
import base64
import json
import os
import re
import traceback
import sys
import time

import discord

USAGE = ('usage: `open "topic" dev ops | message`, `kick <#thread> name`, '
         '`add <#thread> name`, `archive <#thread>`')
OWNER_ALIASES = {"owner"}  # plus OWNER_NAME, see load_config
MENTION = re.compile(r"<@[!&]?(\d+)>")
OPEN_RE = re.compile(r'^open\s+"([^"]+)"\s+([^|]*?)\s*\|\s*(.+)$', re.S | re.I)
REF_RE = re.compile(r"^(kick|add)\s+(?:<#(\d+)>\s+)?@?(\S+)\s*$", re.I)
ARCH_RE = re.compile(r"^archive(?:\s+<#(\d+)>)?\s*$", re.I)


CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
ATTEMPT_RE = re.compile(r"\b(open|add|kick|archive)\b", re.I)
CORRECTION_GAP = 60  # seconds: max one correction per sender per thread

MACROS = {
    "done": (
        "Wrap-up requested by {owner}. "
        "1) Re-check this thread: did you finish every task, review or hand-off you took on? "
        "If something is still open, reply now with what is left and who/what blocks it. Do not claim done early. "
        "2) If you are done: save what you learned where your setup keeps it (notes, docs, memory), "
        "and record any open ends where your team tracks work. "
        "3) Reply with ONE short line: done + what you updated, or what is still open. "
        "Do not mention other agents unless you need something from them."
    ),
}


class GdprError(Exception):
    pass


def load_config(env=None):
    env = os.environ if env is None else env
    need = ["GDPR_TOKEN", "OWNER_ID", "HQ_ID", "CHATS_ID", "AGENT_IDS"]
    missing = [k for k in need if not env.get(k, "").strip()]
    if missing:
        raise SystemExit("gdpr: missing env: " + ", ".join(missing))
    try:
        agents = {}
        for part in env["AGENT_IDS"].split(","):
            if part.strip():
                name, _, aid = part.partition("=")
                agents[name.strip().lower()] = int(aid)
        cfg = dict(token=env["GDPR_TOKEN"], owner_id=int(env["OWNER_ID"]),
                   hq_id=int(env["HQ_ID"]), chats_id=int(env["CHATS_ID"].split(",")[0]),
                   chats_ids={int(x) for x in env["CHATS_ID"].split(",") if x.strip()}, agents=agents,
                   restricted={n.strip().lower() for n in env.get("RESTRICTED_AGENTS", "").split(",") if n.strip()})
        owner_name = env.get("OWNER_NAME", "").strip()
        cfg["owner_name"] = owner_name or "the owner"
        if env.get("DONE_TEXT", "").strip():  # optional: your own /done wrap-up prompt ({owner} is replaced)
            cfg["macros"] = {"done": env["DONE_TEXT"].strip()}
        cfg["owner_aliases"] = OWNER_ALIASES | ({owner_name.lower()} if owner_name else set())
    except ValueError:
        raise SystemExit("gdpr: OWNER_ID/HQ_ID/CHATS_ID/AGENT_IDS must be numeric ids")
    if not agents:
        raise SystemExit("gdpr: AGENT_IDS has no name=id pairs")
    return cfg


SECRET_PREFIX = "WSEC1:"
SECRET_PREFIX_GZ = "WSEC2:"   # base64(age(gzip(text)))
MAX_SECRET_BYTES = 65536


def decrypt_value(value, key_path):
    """Decrypt a client-side encrypted value (age, base64; WSEC1: plain, WSEC2: gzip inside).

    Values without a prefix pass through unchanged (plugin off); the caller logs that.
    Raises GdprError if a prefix is present but decryption is impossible.
    Plaintext and ciphertext are never put into an error message."""
    v = (value or "").strip()
    gz = v.startswith(SECRET_PREFIX_GZ)
    if not (gz or v.startswith(SECRET_PREFIX)):
        return value
    try:
        import base64
        import pyrage
        with open(key_path) as f:
            ids = [pyrage.x25519.Identity.from_str(ln.strip()) for ln in f
                   if ln.strip().startswith("AGE-SECRET-KEY-")]
        if not ids:
            raise ValueError("no identity")
        raw = pyrage.decrypt(base64.b64decode(v[len(SECRET_PREFIX):], validate=True), ids)
        if gz:
            import zlib
            d = zlib.decompressobj(31)  # gzip container
            raw = d.decompress(raw, MAX_SECRET_BYTES)
            if d.unconsumed_tail:
                raise ValueError("too large")
        return raw.decode("utf-8")
    except Exception as e:
        raise GdprError(f"could not decrypt the value ({e.__class__.__name__}); nothing stored, check the key and resend")


def clean_name(name):
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip()).lstrip("._-")[:64]
    if not name:
        raise GdprError("give it a name with at least one letter or digit")
    return name


class VaultDeposit:
    """Deposits secrets into one Vaultwarden collection through the bw CLI.

    - The secret goes to bw on stdin (base64 JSON), never on a command line.
    - bw state lives in `appdata` (a tmpfs in the container); nothing else is written to disk.
    - API key and master password are read from root-only files and handed to the child process env only.
    - bw output and command lines are never logged; failures are reported as a fixed string.
    - No fallback: any failure raises GdprError and nothing is stored anywhere else."""

    TIMEOUT = 45

    def __init__(self, bw="bw", server="", org_id="", collection_id="", appdata="/tmp/bw",
                 clientid_file="/run/secrets/vw_client_id", clientsecret_file="/run/secrets/vw_client_secret",
                 password_file="/run/secrets/vw_master", ca_file=""):
        self.bw, self.server, self.org_id, self.collection_id = bw, server, org_id, collection_id
        self.appdata, self.ca_file = appdata, ca_file
        self.clientid_file, self.clientsecret_file, self.password_file = clientid_file, clientsecret_file, password_file
        self.lock = asyncio.Lock()

    def _env(self, **extra):
        env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": self.appdata,
               "BITWARDENCLI_APPDATA_DIR": self.appdata, "BW_NOINTERACTION": "true",
               "NODE_OPTIONS": "--experimental-global-webcrypto"}  # needed by bw against this Vaultwarden
        if self.ca_file:
            env["NODE_EXTRA_CA_CERTS"] = self.ca_file
        env.update(extra)
        return env

    async def _bw(self, args, stdin=None, **env):
        """Run bw; returns stdout text. Raises GdprError('vault unavailable') without leaking any output."""
        try:
            p = await asyncio.create_subprocess_exec(
                self.bw, *args, "--nointeraction", env=self._env(**env),
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(stdin), self.TIMEOUT)
        except (OSError, asyncio.TimeoutError) as e:
            try:
                p.kill()
            except Exception:
                pass
            print(f"vault: bw {args[0]} failed ({e.__class__.__name__})", flush=True)
            raise GdprError(VAULT_ERR)
        if p.returncode != 0:
            print(f"vault: bw {args[0]} failed (rc={p.returncode})", flush=True)
            raise GdprError(VAULT_ERR)
        return out.decode("utf-8", "replace")

    def _read(self, path):
        try:
            with open(path) as f:
                return f.readline().strip()
        except OSError:
            print("vault: credential file unreadable", flush=True)
            raise GdprError(VAULT_ERR)

    async def _session(self):
        try:
            await self._bw(["login", "--check"])
        except GdprError:
            if self.server:
                await self._bw(["config", "server", self.server])
            await self._bw(["login", "--apikey"], BW_CLIENTID=self._read(self.clientid_file),
                           BW_CLIENTSECRET=self._read(self.clientsecret_file))
        return (await self._bw(["unlock", "--passwordfile", self.password_file, "--raw"])).strip()

    async def deposit(self, name, value):
        """Create one secure note in the Deposits collection. Returns the item name.

        Vaultwarden allows equal names, so a repeated name becomes a second item and nothing is overwritten.
        No sync/list round-trip: each bw call costs seconds."""
        name = clean_name(name)
        value = (value or "").strip("\r\n")
        if not value.strip():
            raise GdprError("empty value")
        async with self.lock:
            session = await self._session()
            item = {"type": 2, "name": name, "notes": value, "secureNote": {"type": 0},
                    "organizationId": self.org_id, "collectionIds": [self.collection_id], "folderId": None}
            await self._bw(["create", "item"], stdin=base64.b64encode(json.dumps(item).encode()), BW_SESSION=session)
            return name


VAULT_ERR = "the vault did not accept it; nothing stored, try again later"


def vault_from_env(env):
    """VaultDeposit when VW_* is configured, None for the file inbox. A half-set config is an error (fail closed)."""
    keys = ("VW_URL", "VW_ORG_ID", "VW_COLLECTION_ID")
    if not any(env.get(k) for k in keys):
        return None
    missing = [k for k in keys if not env.get(k)]
    if missing:
        raise SystemExit(f"Vaultwarden deposit is half configured, missing: {', '.join(missing)}")
    return VaultDeposit(server=env["VW_URL"], org_id=env["VW_ORG_ID"], collection_id=env["VW_COLLECTION_ID"],
                        bw=env.get("BW_BIN", "bw"), appdata=env.get("BW_APPDATA", "/tmp/bw"), ca_file=env.get("VW_CA_FILE", ""))


def store_secret(inbox, name, value):
    """Write one secret to <inbox>/<name> (mode 600, never overwrites). Returns the path.

    The value is never logged or returned in any message."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip()).lstrip("._-")[:64]
    if not name:
        raise GdprError("give it a name with at least one letter or digit")
    value = (value or "").strip("\r\n")
    if not value.strip():
        raise GdprError("empty value")
    path = os.path.join(inbox, name)
    if os.path.exists(path):
        path = f"{path}.{int(time.time())}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")
    return path


def split_names(s):
    return [n.lstrip("@").lower() for n in re.split(r"[\s,]+", s) if n.strip("@")]


def slug(topic):
    s = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40].strip("-")
    return s or "chat"


def parse_command(content, bot_id):
    """None if not addressed to the bot; else a tuple describing the command."""
    m = re.match(rf"^\s*<@!?{bot_id}>\s*(.*)$", content, re.S)
    if not m:
        return None
    body = m.group(1).strip()
    if (o := OPEN_RE.match(body)):
        return ("open", o.group(1).strip(), split_names(o.group(2)), o.group(3).strip())
    if (r := REF_RE.match(body)):
        return (r.group(1).lower(), int(r.group(2)) if r.group(2) else None, r.group(3).lower())
    if (a := ARCH_RE.match(body)):
        return ("archive", int(a.group(1)) if a.group(1) else None)
    return ("help",)


class Gdpr:
    def __init__(self, cfg, bot_id=0):
        self.cfg, self.bot_id = cfg, bot_id
        self.agent_ids = set(cfg["agents"].values())
        self.last_correction = {}  # (sender id, thread id) -> time.monotonic() of the last correction

    async def member_ids(self, thread):
        """IDs of the thread's members. Discord answers 403 unless Server Members Intent is on."""
        try:
            return {m.id for m in await thread.fetch_members()}
        except discord.HTTPException as e:
            if getattr(e, "status", None) == 403:
                raise GdprError("cannot list thread members: turn on Server Members Intent "
                                "for this bot in the Discord developer portal (README A1)")
            raise

    def is_restricted(self, aid):
        r = self.cfg.get("restricted", set())
        return any(self.cfg["agents"].get(n) == aid for n in r)

    def resolve(self, names, by=None):
        """by: requester id; restricted agents can only be targeted by the owner (by=None skips the check)."""
        if not names:
            raise GdprError("no agent named. " + USAGE)
        out, bad = [], []
        for n in names:
            if n in self.cfg["agents"]:
                aid = self.cfg["agents"][n]
                if by is not None and by != self.cfg["owner_id"] and self.is_restricted(aid):
                    raise GdprError(f"owner only: {n} can only be called by the owner")
                if (n, aid) not in out:
                    out.append((n, aid))
            else:
                bad.append(n)
        if bad:
            raise GdprError("unknown agent(s): " + ", ".join(bad) + ". known: " + ", ".join(self.cfg["agents"]))
        return out

    def who(self, uid):
        for n, i in self.cfg["agents"].items():
            if i == uid:
                return n
        return "owner" if uid == self.cfg["owner_id"] else str(uid)

    async def log(self, guild, text):
        hq = guild.get_channel(self.cfg["hq_id"])
        await hq.send(text, allowed_mentions=discord.AllowedMentions.none())

    async def thread_of(self, guild, thread_id):
        t = guild.get_thread(thread_id)
        if t is None:  # archived threads are not in the cache
            try:
                t = await guild.fetch_channel(thread_id)
            except discord.HTTPException:
                t = None
        if getattr(t, "parent_id", None) is None or t.parent_id not in self.cfg.get("chats_ids", {self.cfg["chats_id"]}):
            raise GdprError("that is not a thread in the threads channel (CHATS_ID)")
        return t

    async def open(self, guild, requester_id, topic, names, text):
        agents = self.resolve(names, requester_id)
        chats = guild.get_channel(self.cfg["chats_id"])
        if chats is None:
            raise GdprError("chats channel not found")
        if not text.strip():
            raise GdprError("empty message")
        t = await chats.create_thread(name=slug(topic), type=discord.ChannelType.private_thread,
                                      invitable=False, auto_archive_duration=4320)
        for uid in dict.fromkeys([self.cfg["owner_id"], requester_id] + [i for _, i in agents]):
            await t.add_user(discord.Object(id=uid))
        ids = [i for _, i in agents]
        await t.send(f"From <@{requester_id}>: {text[:1700]}\n" + " ".join(f"<@{i}>" for i in ids),
                     allowed_mentions=discord.AllowedMentions(
                         users=[discord.Object(id=i) for i in ids], roles=False, everyone=False,
                         replied_user=False))
        await self.log(guild, f"open: {t.mention} by {self.who(requester_id)} with "
                              + ", ".join(n for n, _ in agents))

    async def kick(self, guild, by, thread_id, name):
        name = name.lstrip("@").lower()
        if name in self.cfg.get("owner_aliases", OWNER_ALIASES) or self.cfg["agents"].get(name) == self.cfg["owner_id"]:
            raise GdprError("refused: the owner cannot be kicked")
        (n, aid), = self.resolve([name])
        t = await self.thread_of(guild, thread_id)
        await t.remove_user(discord.Object(id=aid))
        await self.log(guild, f"kick: {n} removed from {t.mention} by {self.who(by)}")

    async def add(self, guild, by, thread_id, name):
        (n, aid), = self.resolve([name.lstrip("@").lower()], by)
        t = await self.thread_of(guild, thread_id)
        await t.add_user(discord.Object(id=aid))
        await self.log(guild, f"add: {n} added to {t.mention} by {self.who(by)}")

    async def archive(self, guild, by, thread_id):
        t = await self.thread_of(guild, thread_id)
        await t.edit(archived=True)
        await self.log(guild, f"archive: {t.mention} by {self.who(by)}")

    async def macro(self, guild, by, thread_id, name):
        """Post one macro text in a thread, mentioning only the agents currently in it."""
        text = self.cfg.get("macros", MACROS).get(name)
        if not text:
            raise GdprError(f"unknown macro {name}")
        text = text.replace("{owner}", self.cfg.get("owner_name", "the owner"))
        t = await self.thread_of(guild, thread_id)
        members = await self.member_ids(t)
        ids = [i for i in dict.fromkeys(self.cfg["agents"].values())
               if i in members and i != self.cfg["owner_id"]]
        if not ids:
            raise GdprError("no agents in this thread")
        await t.send(" ".join(f"<@{i}>" for i in ids) + "\n" + text[:1800],
                     allowed_mentions=discord.AllowedMentions(
                         users=[discord.Object(id=i) for i in ids], roles=False, everyone=False,
                         replied_user=False))
        await self.log(guild, f"{name}: sent in {t.mention} by {self.who(by)} to "
                              + ", ".join(self.who(i) for i in ids))

    def mentions_me(self, content):
        """True if the text mentions the bot outside code spans/blocks (a quoted example doesn't count)."""
        return re.search(rf"<@!?{self.bot_id}>", CODE_RE.sub(" ", content)) is not None

    def looks_like_attempt(self, content):
        """A command attempt = a bot mention followed (anywhere after it) by open/add/kick/archive."""
        m = re.search(rf"<@!?{self.bot_id}>(.*)$", CODE_RE.sub(" ", content), re.S)
        return bool(m and ATTEMPT_RE.search(m.group(1)))

    async def correct(self, message, why, first=True):
        """Answer IN the thread so the sender sees what was wrong. Pings the sender only if it is an agent
        (so it wakes and resends), never the owner. Max one per sender per thread per CORRECTION_GAP seconds.
        The reply never starts with the bot's own mention, so it cannot trigger itself."""
        uid, ch = message.author.id, message.channel
        now = time.monotonic()
        last = self.last_correction.get((uid, ch.id))
        if last is not None and now - last < CORRECTION_GAP:
            return
        self.last_correction[(uid, ch.id)] = now
        rule = " The command must be the FIRST thing in its own message." if first else ""
        text = (f"GDPR did not act on your message: {why}{rule} "
                f"Resend it alone, like this: `<@{self.bot_id}> open \"topic\" dev | text`. {USAGE}")
        ping = uid in self.agent_ids
        if ping:
            text = f"<@{uid}> " + text
        await ch.send(text, reference=message, allowed_mentions=discord.AllowedMentions(
            users=[discord.Object(id=uid)] if ping else False, roles=False, everyone=False, replied_user=False))

    async def handle_message(self, message):
        uid, ch, guild = message.author.id, message.channel, message.guild
        if uid == self.bot_id or guild is None:
            return
        in_hq = ch.id == self.cfg["hq_id"]
        in_chat = getattr(ch, "parent_id", None) in self.cfg.get("chats_ids", {self.cfg["chats_id"]})
        if not (in_hq or in_chat):
            return  # only the HQ channel and threads in the threads channel
        is_owner = uid == self.cfg["owner_id"]
        if not is_owner and uid not in self.agent_ids:
            return
        try:
            cmd = parse_command(message.content, self.bot_id)
            if cmd is not None and cmd[0] in ("kick", "add", "archive") and cmd[1] is None:
                if not in_chat:
                    raise GdprError("name the thread, e.g. `kick <#thread> name`")
                cmd = (cmd[0], ch.id) + cmd[2:]  # default to the thread it was typed in
            if cmd is None:
                if in_chat:
                    # a real @mention of an agent inside a thread adds it (threads are
                    # invitable=false, so Discord itself only lets Manage Threads holders add)
                    ids = {int(i) for i in MENTION.findall(message.content)}
                    for n, i in self.cfg["agents"].items():
                        if i in ids and i != uid:
                            if not is_owner and self.is_restricted(i):
                                raise GdprError(f"owner only: {n} can only be called by the owner")
                            # events of a private thread only reach members, so the message that
                            # triggered the add was never delivered: re-ping with a fresh message
                            joined = await self.member_ids(ch)
                            await ch.add_user(discord.Object(id=i))
                            if i not in joined:
                                await ch.send(f"<@{i}> {self.who(uid)} mentioned you above: {message.jump_url}",
                                              allowed_mentions=discord.AllowedMentions(
                                                  users=[discord.Object(id=i)], roles=False,
                                                  everyone=False, replied_user=False))
                    if self.mentions_me(message.content) and (not is_owner or self.looks_like_attempt(message.content)):
                        await self.correct(message, "it has my mention, but not at the start, so I read it as plain chat.")
                    return
                ids = [int(i) for i in MENTION.findall(message.content)]
                targets = [n for n, i in self.cfg["agents"].items() if i in ids and i != uid]
                if not targets:
                    return
                text = re.sub(r"\s+", " ", MENTION.sub("", message.content)).strip()
                await self.open(guild, uid, text, targets, text)
            elif cmd[0] == "open":
                await self.open(guild, uid, cmd[1], cmd[2], cmd[3])
            elif cmd[0] == "kick":
                await self.kick(guild, uid, cmd[1], cmd[2])
            elif cmd[0] == "add":
                await self.add(guild, uid, cmd[1], cmd[2])
            elif cmd[0] == "archive":
                await self.archive(guild, uid, cmd[1])
            else:
                await self.log(guild, USAGE)
                if in_chat and not is_owner:
                    await self.correct(message, "I could not read the command.")
        except GdprError as e:
            await self.log(guild, f"error: {e}")
            if in_chat and not is_owner:
                try:
                    await self.correct(message, f"{e}.", first=False)
                except discord.HTTPException:
                    pass
        except discord.HTTPException as e:
            await self.log(guild, f"error: discord refused ({e.status})")


async def handle_secret_submit(inter, owner_id, name, raw, key_path, vault, inbox):
    """/secret modal submit. Defers FIRST: Discord drops an interaction not answered within 3 s and a
    Vaultwarden deposit takes several seconds (bw login/unlock). All replies go through followup."""
    if inter.user.id != owner_id:
        return await inter.response.send_message("owner only", ephemeral=True)
    await inter.response.defer(ephemeral=True)
    try:
        plain_arrival = not raw.strip().startswith((SECRET_PREFIX, SECRET_PREFIX_GZ))
        if plain_arrival:
            print("warning: /secret value arrived without the encryption marker (plugin off?)", flush=True)
        value = decrypt_value(raw, key_path)
        if vault:
            where = f"`{await vault.deposit(name, value)}` in the Vaultwarden Deposits collection"
        else:
            where = f"`{os.path.basename(store_secret(inbox, name, value))}` in the secrets inbox"
    except GdprError as e:
        return await inter.followup.send(f"error: {e}", ephemeral=True)
    except OSError as e:
        return await inter.followup.send(f"error: could not write ({e.__class__.__name__})", ephemeral=True)
    except Exception:
        traceback.print_exc()  # traceback never contains the value: it is not in any exception text
        return await inter.followup.send("error: unexpected failure, nothing stored, see the GDPR logs", ephemeral=True)
    await inter.followup.send(
        f"stored as {where} ({len(value.strip())} chars). "
        "Tell the agent who should handle it; the value is not in chat."
        + (" WARNING: it arrived UNENCRYPTED (Vencord plugin off?). Discord saw it: rotate this secret." if plain_arrival else ""),
        ephemeral=True)


class CardWatcher:
    """Polls a Planka board (read-only API key) and, for cards that carry the custom fields notify_thread /
    notify_agent, produces ONE line when a card enters Reviewing, gets the label blocked, or sits In Progress
    without activity for stall_hours. No model calls. State is in memory; the first scan only records the
    current state (so a restart never repeats old notices)."""

    def __init__(self, cfg, now=time.time):
        self.cfg, self.now = cfg, now
        self.cards = {}      # card id -> dict(list, blocked, stalled, comments, active)
        self.seeded = False

    @staticmethod
    def _ts(s):
        from datetime import datetime
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except (AttributeError, ValueError):
            return 0.0

    def scan(self, board):
        """board = Planka GET /api/boards/<id> JSON. Returns [(thread_id, agent_id or None, text)]."""
        inc = board.get("included", {})
        lists = {l["id"]: (l.get("name") or "").strip().lower() for l in inc.get("lists", [])}
        labels = {l["id"]: (l.get("name") or "").strip().lower() for l in inc.get("labels", [])}
        fields = {f["id"]: f["name"] for f in inc.get("customFields", [])}
        blocked_by_card, values, agent_label = {}, {}, {}
        for cl in inc.get("cardLabels", []):
            name = labels.get(cl["labelId"])
            if name == "blocked":
                blocked_by_card[cl["cardId"]] = True
            elif name.startswith("agent:") and name[6:] in self.cfg.get("agents", {}):
                agent_label.setdefault(cl["cardId"], str(self.cfg["agents"][name[6:]]))
        for v in inc.get("customFieldValues", []):
            values.setdefault(v["cardId"], {})[fields.get(v["customFieldId"])] = (v.get("content") or "").strip()
        review, progress = self.cfg["review_list"], self.cfg["progress_list"]
        stall = self.cfg["stall_hours"] * 3600
        now, out, seen = self.now(), [], set()
        for c in inc.get("cards", []):
            if c.get("isClosed"):
                continue
            v = values.get(c["id"], {})
            thread, agent = v.get("notify_thread", ""), v.get("notify_agent", "")
            if not thread.isdigit():
                continue
            if not agent.isdigit():   # no notify_agent override: mention the agent named by the card's agent:<name> label
                agent = agent_label.get(c["id"], "")
            seen.add(c["id"])
            lst = lists.get(c["listId"], "")
            blocked = bool(blocked_by_card.get(c["id"]))
            comments = c.get("commentsTotal") or 0
            prev = self.cards.get(c["id"])
            active = max(self._ts(c.get("updatedAt")), self._ts(c.get("listChangedAt")), self._ts(c.get("createdAt")))
            if prev and prev["comments"] != comments:
                active = max(active, now)
            if prev:
                active = max(active, prev["active"])
            stalled = bool(stall) and lst == progress and now - active >= stall
            st = dict(list=lst, blocked=blocked, stalled=stalled, comments=comments, active=active)
            if self.seeded:
                url = f"{self.cfg['url']}/cards/{c['id']}"
                name = " ".join(str(c.get("name", "")).split())[:100]
                why = None
                if lst == review and (not prev or prev["list"] != review):
                    why = "is now in Reviewing"
                elif blocked and not (prev and prev["blocked"]):
                    why = "is blocked"
                elif stalled and not (prev and prev["stalled"]):
                    why = f"has had no activity for {self.cfg['stall_hours']:g}h while In Progress"
                if why:
                    mention = f"<@{agent}> " if agent.isdigit() else ""
                    out.append((int(thread), int(agent) if agent.isdigit() else None,
                                f"{mention}Card \"{name}\" {why}: {url}"))
            self.cards[c["id"]] = st
        for cid in set(self.cards) - seen:   # field removed, card closed or deleted: forget it
            del self.cards[cid]
        self.seeded = True
        return out


def watcher_from_env(env):
    """None unless PLANKA_URL, PLANKA_BOARD_ID and a key (PLANKA_API_KEY or PLANKA_API_KEY_FILE) are set."""
    url, board = env.get("PLANKA_URL", "").strip().rstrip("/"), env.get("PLANKA_BOARD_ID", "").strip()
    key = env.get("PLANKA_API_KEY", "").strip()
    if not key and env.get("PLANKA_API_KEY_FILE", "").strip():
        key = open(env["PLANKA_API_KEY_FILE"].strip()).read().strip()
        key = key.split("=", 1)[1].strip() if key.startswith("planka_api_key=") else key
    if not (url and board and key):
        return None
    if not url.startswith(("http://", "https://")):
        raise SystemExit("gdpr: PLANKA_URL must start with http:// or https://")
    agents = {}   # AGENT_IDS (name=id,...): the card's agent:<name> label picks who is mentioned
    for part in env.get("AGENT_IDS", "").split(","):
        n, _, i = part.partition("=")
        if n.strip() and i.strip().isdigit():
            agents[n.strip().lower()] = int(i)
    try:
        cfg = dict(url=url, board=board, key=key, agents=agents, poll=max(30, int(env.get("PLANKA_POLL_SECONDS", "180"))),
                   stall_hours=float(env.get("PLANKA_STALL_HOURS", "24")),
                   review_list=env.get("PLANKA_REVIEW_LIST", "Reviewing").strip().lower(),
                   progress_list=env.get("PLANKA_PROGRESS_LIST", "In Progress").strip().lower())
    except ValueError:
        raise SystemExit("gdpr: PLANKA_POLL_SECONDS / PLANKA_STALL_HOURS must be numbers")
    return CardWatcher(cfg)


def fetch_board(cfg):
    import urllib.request
    req = urllib.request.Request(f"{cfg['url']}/api/boards/{cfg['board']}", headers={"x-api-key": cfg["key"]})
    with urllib.request.urlopen(req, timeout=20) as r:   # GET only: the key is read-only on purpose
        return json.load(r)


async def watch_cards(watcher, client, fetch=fetch_board):
    """Poll forever; one failed poll or post never stops the loop. The key is never logged."""
    while True:
        try:
            board = await asyncio.to_thread(fetch, watcher.cfg)
            for thread_id, agent_id, text in watcher.scan(board):
                try:
                    ch = client.get_channel(thread_id) or await client.fetch_channel(thread_id)
                    await ch.send(text, allowed_mentions=discord.AllowedMentions(
                        users=[discord.Object(agent_id)] if agent_id else False))
                except Exception as e:
                    print(f"card watcher: could not post to thread {thread_id}: {e.__class__.__name__}", flush=True)
        except Exception as e:
            print(f"card watcher: poll failed: {e.__class__.__name__}", flush=True)
        await asyncio.sleep(watcher.cfg["poll"])


def main():
    from discord import app_commands
    cfg = load_config()
    intents = discord.Intents.none()
    intents.guilds = intents.guild_messages = intents.message_content = True
    client = discord.Client(intents=intents)
    w = Gdpr(cfg)
    watcher = watcher_from_env(os.environ)  # optional Planka card watcher
    tree = app_commands.CommandTree(client)

    async def run(inter, coro_fn, *args):
        if inter.user.id != cfg["owner_id"]:
            return await inter.response.send_message("owner only", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        try:
            await coro_fn(inter.guild, inter.user.id, *args)
            await inter.followup.send("done (see the HQ channel)", ephemeral=True)
        except GdprError as e:
            await inter.followup.send(f"error: {e}", ephemeral=True)
        except discord.HTTPException as e:
            await inter.followup.send(f"error: discord refused ({e.status})", ephemeral=True)
        except Exception:
            traceback.print_exc()
            await inter.followup.send("error: unexpected failure, see the GDPR logs", ephemeral=True)

    inbox = os.environ.get("SECRET_INBOX", "/inbox")
    vault = vault_from_env(os.environ)  # None = file inbox (no VW_* set); never a fallback at runtime

    key_path = os.environ.get("GDPR_AGE_KEY", "/run/secrets/gdpr_age_key")

    class SecretModal(discord.ui.Modal, title="Store a secret"):
        # fixed custom_ids: the Vencord SecretCrypt plugin matches on them
        def __init__(self):
            super().__init__(custom_id="wsec:v1")

        sname = discord.ui.TextInput(label="Name (file name, e.g. dev_discord_token)", max_length=64, custom_id="wsec:name")
        svalue = discord.ui.TextInput(label="Value", style=discord.TextStyle.paragraph, max_length=4000, custom_id="wsec:value")

        async def on_submit(self, inter: discord.Interaction):
            await handle_secret_submit(inter, cfg["owner_id"], str(self.sname), str(self.svalue), key_path, vault, inbox)

    @tree.command(name="secret", description="Store a secret in the GDPR inbox (hidden form, not in chat)")
    async def secret_(inter: discord.Interaction):
        if inter.user.id != cfg["owner_id"]:
            return await inter.response.send_message("owner only", ephemeral=True)
        await inter.response.send_modal(SecretModal())

    @tree.command(name="open", description="Open a private thread")
    async def open_(inter: discord.Interaction, topic: str, agents: str, message: str):
        await run(inter, lambda g, u, *a: w.open(g, u, topic, split_names(agents), message))

    @tree.command(name="kick", description="Remove an agent from this thread")
    async def kick_(inter: discord.Interaction, agent: str):
        await run(inter, w.kick, inter.channel_id, agent)

    @tree.command(name="add", description="Add an agent to this thread")
    async def add_(inter: discord.Interaction, agent: str):
        await run(inter, w.add, inter.channel_id, agent)

    @tree.command(name="archive", description="Archive this thread")
    async def archive_(inter: discord.Interaction):
        await run(inter, w.archive, inter.channel_id)

    @tree.command(name="done", description="Ask all agents in this thread to wrap up")
    async def done_(inter: discord.Interaction):
        await run(inter, w.macro, inter.channel_id, "done")

    @client.event
    async def on_ready():
        w.bot_id = client.user.id
        await tree.sync()
        if watcher and not getattr(client, "_watching", False):
            client._watching = True
            asyncio.create_task(watch_cards(watcher, client))
        print(f"GDPR ready as {client.user}", flush=True)

    @client.event
    async def on_message(message):
        await w.handle_message(message)

    client.run(cfg["token"])


BOT_UID = 10001
# application flags: privileged intents enabled in the developer portal (full or "limited" variant)
FLAG_MEMBERS, FLAG_CONTENT = (1 << 14) | (1 << 15), (1 << 18) | (1 << 19)
ADMIN, MANAGE_CHANNELS, VIEW, SEND, HISTORY, MANAGE_ROLES = 1 << 3, 1 << 4, 1 << 10, 1 << 11, 1 << 16, 1 << 28
MANAGE_THREADS, CREATE_PUBLIC, CREATE_PRIVATE, SEND_IN_THREADS = 1 << 34, 1 << 35, 1 << 36, 1 << 38
# Manage Channels + Manage Roles are only for `setup` (create channels, role, overwrites); a bot can
# only allow/deny permissions it holds itself, hence Create Public Threads (it is denied to agents).
INVITE_PERMISSIONS = (VIEW | SEND | HISTORY | MANAGE_THREADS | CREATE_PUBLIC | CREATE_PRIVATE | SEND_IN_THREADS
                      | MANAGE_CHANNELS | MANAGE_ROLES)
HQ_NAME, THREADS_NAME, ROLE_NAME = "gdpr-hq", "gdpr-threads", "agents"
BOT_ALLOW = VIEW | SEND | HISTORY | MANAGE_THREADS | CREATE_PRIVATE | SEND_IN_THREADS
OWNER_ALLOW = VIEW | SEND | HISTORY | SEND_IN_THREADS
AGENT_HQ_ALLOW = VIEW | HISTORY | SEND_IN_THREADS          # agents see HQ (mention picker) but cannot post there
AGENT_HQ_DENY = SEND | CREATE_PUBLIC | CREATE_PRIVATE
AGENT_THREADS_ALLOW = VIEW | HISTORY | SEND_IN_THREADS
AGENT_THREADS_DENY = SEND | CREATE_PUBLIC | CREATE_PRIVATE | MANAGE_THREADS
API = "https://discord.com/api/v10"


def bot_id_from_token(token):
    """The first part of a bot token is the base64-encoded bot user ID; None if unreadable."""
    import base64
    first = (token or "").strip().split(".")[0]
    try:
        bid = base64.urlsafe_b64decode(first + "=" * (-len(first) % 4)).decode()
    except Exception:
        return None
    return bid if bid.isdigit() else None


def invite_url(bot_id):
    return (f"https://discord.com/oauth2/authorize?client_id={bot_id}"
            f"&scope=bot+applications.commands&permissions={INVITE_PERMISSIONS}")


def public_key(key_path):
    import pyrage
    with open(key_path) as f:
        for ln in f:
            if ln.strip().startswith("AGE-SECRET-KEY-"):
                return str(pyrage.x25519.Identity.from_str(ln.strip()).to_public())
    raise SystemExit(f"{key_path} has no age key; move it away and run setup again")


def init(out, env=None, chown=True):
    """One-time setup, run as root in the image: age key + secrets folder, then print what to do.

    Never overwrites an existing key. Returns the public key."""
    import pyrage
    say = print
    cfg = load_config(env)  # fails early with a clear message if .env is incomplete
    key_path, inbox = os.path.join(out, "gdpr_age_key"), os.path.join(out, "secrets")
    if os.path.isdir(key_path):
        raise SystemExit(f"{key_path} is a folder (Docker made it because the key was missing). "
                         "Delete it and run setup again.")
    if os.path.exists(key_path):
        say("keeping existing gdpr_age_key")
    else:
        ident = pyrage.x25519.Identity.generate()
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, "w") as f:
            f.write(str(ident) + "\n")
        say("created gdpr_age_key (back it up; without it encrypted secrets cannot be read)")
    pub = public_key(key_path)
    with open(key_path + ".pub", "w") as f:
        f.write(pub + "\n")
    os.makedirs(inbox, exist_ok=True)
    os.chmod(inbox, 0o700)
    if chown:
        for p in (key_path, inbox):
            os.chown(p, BOT_UID, BOT_UID)
    say("secrets folder ready: ./secrets")
    say("public key for the optional SecretCrypt plugin (also in gdpr_age_key.pub):\n  " + pub)
    say("next: check .env, then docker compose up -d (README steps B4 and B5)")
    return pub


def api(token, method, path, body=None):
    """Minimal Discord REST call with the bot token. Returns (status, json-or-None)."""
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(API + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bot " + token, "Content-Type": "application/json",
                                          "User-Agent": "DiscordBot (gdpr, 1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


def env_name(username):
    return re.sub(r"[^a-z0-9_-]+", "-", username.lower()).strip("-") or "agent"


def overwrites(guild_id, bot_id, owner_id, role_id):
    """Wanted permission overwrites per channel name: {name: [(id, type, allow, deny)]} (type 0 role, 1 member)."""
    base = [(guild_id, 0, 0, VIEW), (bot_id, 1, BOT_ALLOW, 0), (owner_id, 1, OWNER_ALLOW, 0)]
    return {HQ_NAME: base + [(role_id, 0, AGENT_HQ_ALLOW, AGENT_HQ_DENY)],
            THREADS_NAME: base + [(role_id, 0, AGENT_THREADS_ALLOW, AGENT_THREADS_DENY)]}


def read_env_file(path):
    vals = {}
    with open(path) as f:
        for ln in f:
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, _, v = ln.partition("=")
                vals[k.strip()] = v.strip()
    return vals


def update_env_file(path, values):
    """Set KEY=value lines in place (keeps comments, order and file owner); appends missing keys."""
    with open(path) as f:
        lines = f.read().splitlines()
    done = set()
    for n, ln in enumerate(lines):
        k = ln.partition("=")[0].strip()
        if "=" in ln and not ln.lstrip().startswith("#") and k in values:
            lines[n] = f"{k}={values[k]}"
            done.add(k)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def setup(out, env=None, call=api, say=print, run_init=True):
    """Discord side of the setup with only the bot token, safe to run again (reuses by name):
    role `agents` for the agent bots, channels gdpr-hq and gdpr-threads with permissions, IDs into .env,
    then init (key + secrets folder). Prints a PASS/FAIL list; returns True if nothing failed."""
    env_path = os.path.join(out, ".env")
    if not os.path.exists(env_path):
        raise SystemExit(f"{env_path} not found: copy .env.example to .env first (README step B1)")
    fileenv = read_env_file(env_path)
    env = dict(os.environ if env is None else env, **{k: v for k, v in fileenv.items() if v})
    token = env.get("GDPR_TOKEN", "").strip()
    if not token:
        raise SystemExit("GDPR_TOKEN is empty in .env (README step B1)")
    results = []

    def check(ok, what):
        results.append(ok)
        say(("PASS  " if ok else "FAIL  ") + what)
        return ok

    st, me = call(token, "GET", "/users/@me")
    if st != 200:
        raise SystemExit(f"Discord rejected the token (HTTP {st}). Check GDPR_TOKEN in .env (README step A1).")
    st, app = call(token, "GET", "/applications/@me")
    flags = int((app or {}).get("flags") or 0) if st == 200 else None
    for bit, name, why in ((FLAG_CONTENT, "Message Content Intent", "GDPR cannot read messages"),
                           (FLAG_MEMBERS, "Server Members Intent", "/done and adding agents by mention fail")):
        check(flags is not None and bool(flags & bit), f"{name} is on" + ("" if flags is not None and flags & bit
              else f": turn it on in the developer portal, Bot tab (README A1); without it {why}"))
    st, guilds = call(token, "GET", "/users/@me/guilds")
    if st != 200 or not guilds:
        raise SystemExit("The bot is not in any server yet. Open the invite link (README step A3): "
                         + invite_url(me["id"]))
    if len(guilds) > 1:
        raise SystemExit("The bot is in more than one server: " + ", ".join(g["name"] for g in guilds)
                         + ". Remove it from the others (GDPR serves one server).")
    gid = guilds[0]["id"]
    say(f"bot {me['username']} in server {guilds[0]['name']}")
    st, guild = call(token, "GET", f"/guilds/{gid}")
    owner = env.get("OWNER_ID") or (guild or {}).get("owner_id")
    check(bool(owner), f"owner: {owner} ({'from .env' if env.get('OWNER_ID') else 'server owner'})")

    # agents: AGENT_IDS from .env, otherwise every other bot in the server
    st, members = call(token, "GET", f"/guilds/{gid}/members?limit=1000")
    members = members if st == 200 else []
    if env.get("AGENT_IDS"):
        agents = {}
        for part in env["AGENT_IDS"].split(","):
            n, _, i = part.partition("=")
            if i.strip():
                agents.setdefault(i.strip(), n.strip())
    else:
        agents = {m["user"]["id"]: env_name(m["user"]["username"]) for m in members
                  if m["user"].get("bot") and m["user"]["id"] != me["id"]}
    if not check(bool(agents), f"agent bots found: {', '.join(agents.values()) or 'none'}"):
        say("      invite your agent bots (A4) and turn on Server Members Intent (A1), or set AGENT_IDS in .env")

    # role
    st, roles = call(token, "GET", f"/guilds/{gid}/roles")
    roles = roles if st == 200 else []
    role = next((r for r in roles if r["name"] == ROLE_NAME), None)
    if role is None:
        st, role = call(token, "POST", f"/guilds/{gid}/roles", {"name": ROLE_NAME, "permissions": "0"})
        role = role if st in (200, 201) else None
    if not check(role is not None, f"role {ROLE_NAME}" + (f" (id {role['id']})" if role else
                                                          ": could not create it (needs Manage Roles, A3)")):
        return False
    by_id = {m["user"]["id"]: m for m in members}
    for aid, name in agents.items():
        if role["id"] in by_id.get(aid, {}).get("roles", []):
            check(True, f"{name} has role {ROLE_NAME}")
            continue
        st, _ = call(token, "PUT", f"/guilds/{gid}/members/{aid}/roles/{role['id']}")
        check(st in (200, 204), f"give {name} role {ROLE_NAME}" + ("" if st in (200, 204) else
                                                                 f" (HTTP {st}; is the bot in the server?)"))

    # report agents that could read every private thread (never changed silently)
    perms = {r["id"]: int(r.get("permissions", 0)) for r in roles}
    if not members:
        say("WARN  could not list members, so agent roles were not checked for Manage Threads/Administrator "
            "(turn on Server Members Intent, A1, and run setup again to check)")
    for aid, name in agents.items():
        p = perms.get(gid, 0)
        for rid in by_id.get(aid, {}).get("roles", []):
            p |= perms.get(rid, 0)
        if aid in by_id:
            check(not p & (MANAGE_THREADS | ADMIN), f"{name} has no Manage Threads/Administrator"
                  + ("" if not p & (MANAGE_THREADS | ADMIN) else ": remove it from that role (A4), or kick will not work"))

    # channels
    st, chans = call(token, "GET", f"/guilds/{gid}/channels")
    by_name = {c["name"]: c for c in (chans or []) if c.get("type") == 0}
    ids = {}
    for name, ows in overwrites(gid, me["id"], owner, role["id"]).items():
        body = [{"id": i, "type": t, "allow": str(a), "deny": str(d)} for i, t, a, d in ows if i]
        ch = by_name.get(name)
        if ch is None:
            st, ch = call(token, "POST", f"/guilds/{gid}/channels", {"name": name, "type": 0, "permission_overwrites": body})
            if not check(st in (200, 201), f"create #{name}" + ("" if st in (200, 201) else f" (HTTP {st}; needs Manage Channels, A3)")):
                continue
        else:
            bad = [o for o in body if call(token, "PUT", f"/channels/{ch['id']}/permissions/{o['id']}",
                                            {"type": o["type"], "allow": o["allow"], "deny": o["deny"]})[0] not in (200, 204)]
            if not check(not bad, f"permissions on existing #{name}" + (f" ({len(bad)} failed; needs Manage Roles, A3)" if bad else "")):
                continue
        ids[name] = ch["id"]
        check(True, f"#{name} ready (id {ch['id']})")

    values = {}
    if not fileenv.get("OWNER_ID") and owner:
        values["OWNER_ID"] = owner
    if HQ_NAME in ids:
        values["HQ_ID"] = ids[HQ_NAME]
    if THREADS_NAME in ids:
        values["CHATS_ID"] = ids[THREADS_NAME]
    if not fileenv.get("AGENT_IDS") and agents:
        values["AGENT_IDS"] = ",".join(f"{n}={i}" for i, n in agents.items())
    if values:
        update_env_file(env_path, values)
        check(True, "wrote to .env: " + ", ".join(values))
    if all(results) and run_init:
        init(out, dict(env, **values))
    say("\nsetup " + ("complete" if all(results) else "INCOMPLETE: fix the FAIL lines and run setup again"))
    return all(results)


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["init"]:
        init(sys.argv[2] if len(sys.argv) > 2 else "/out")
        sys.exit(0)
    if cmd == ["invite"]:
        bid = bot_id_from_token(os.environ.get("GDPR_TOKEN", ""))
        sys.exit(print(invite_url(bid)) if bid else "GDPR_TOKEN is empty or not a bot token")
    if cmd == ["setup"]:
        sys.exit(0 if setup(sys.argv[2] if len(sys.argv) > 2 else "/out") else 1)
    sys.exit(main())
