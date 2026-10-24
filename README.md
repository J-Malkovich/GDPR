```text
   O
   |
   |                            ______________                              _______
   |_______              ______/              \______                ______/      |
   |       \____________/                            \______________/             |
   |                                                                              |
   |     ____ ____  ____  ____                                                    |
   |    / ___|  _ \|  _ \|  _ \                                                   |
   |   | |  _| | | | |_) | |_) |                                                  |
   |   | |_| | |_| |  __/|  _ <                                   *               |
   |    \____|____/|_|   |_| \_\                            *           *         |
   |                                                                              |
   |                                                    *       .-"-.       *     |
   |   Your Discord's privacy bureaucrat.                      (     )            |
   |                                                  *        |=====|        *   |
   |   - Orchestrates private threads: one                     | (o) |            |
   |     mention opens one, you stay in it                     '-----'      *     |
   |   - Adds and kicks agents, including               *                         |
   |     ones other agents call in                          *           *         |
   |   - Logs every action. No AI, no state                       *               |
   |                                                                              |
   |   PLEASE FILL IN A GDPR COMPLIANCE FORM FOR EVERY                            |
   |   PRIVATE CHAT WHILE BEING CARBON POSITIVE                                   |
   |                            ______________                              ______|
   |_______              ______/              \______                ______/
   |       \____________/                            \______________/
   |
   |
   |
   |
 _|_|_
[_____]
```

# GDPR

> **GDPR is a Discord bot that manages private threads for a team of AI agent bots. It has no
> AI of its own.**
>
> **What it does**
> - **Private threads:** mention an agent and GDPR opens a private thread with you and that
>   agent.
> - **Thread management:** adds, kicks and archives agents, so the agents themselves never need
>   thread moderation rights.
> - **Wrap-up:** `/done` asks every agent in a thread to finish up.
> - **Secrets inbox:** `/secret` sends a secret to your server through a hidden form, with
>   optional encryption on your own computer.
>
> **What it stores:** nothing, except the secrets you send it. Only you (the owner) and the
> agents you list can give it commands.
>
> **What you need:** Docker, a Discord server and about 20 minutes, of which about 5 need a
> person.

## How you use it

GDPR uses two channels:

- **HQ channel** (`#gdpr-hq`): where you talk to GDPR. It also posts a log line there for
  everything it does.
- **Threads channel** (`#gdpr-threads`): where GDPR creates the private threads you work in
  with your agents.

**Start a conversation.** In the HQ channel, write a normal message that mentions one or more
agents:

```
@dev can you look at the failing build?
```

GDPR opens a private thread in the threads channel, adds you and the mentioned agents, and posts
your message there with a mention, so the agents wake up. You then work in that thread.

**Bring in another agent.** Inside a thread, mention them (`@ops can you check this?`). Private
threads don't let members invite others, so GDPR adds the agent. It then pings them with a link
to your message, because Discord does not deliver messages from before someone joined a
private thread.

**Commands.** For more control, mention GDPR with a command, in the HQ channel or inside a
thread. Inside a thread, the thread is implied. In the HQ channel, name it with `<#thread>`.

- **Open with a topic:** `@GDPR open "topic" dev ops | message` opens a thread with that title.
- **Add an agent:** `@GDPR add dev`, or `@GDPR add <#thread> dev` from the HQ channel.
- **Remove an agent:** `@GDPR kick dev`. The owner can never be kicked.
- **Archive the thread:** `@GDPR archive`.

The same actions exist as slash commands for the owner: `/open`, `/add`, `/kick` and `/archive`.
Run `/add`, `/kick` and `/archive` inside the thread they are about.

**Wrap up.** `/done` (owner only, inside a thread) posts one message to every agent in the
thread. It asks them to check for unfinished work, save what they learned and reply with one
line.

**Hand over a secret.** `/secret` (owner only) opens a hidden form with a name and a value.
GDPR saves the value as a file on the server and replies only to you, with the file name.
Nothing appears in chat. Then tell the agent in a thread which file name to pick up. See
"Secrets for agents" below.

**Who can do what**

- **The owner:** everything, including all slash commands and `/secret`.
- **Agents listed in `AGENT_IDS`:** mentions, `open`, `add`, `kick` and `archive`, but no slash
  commands.
- **Restricted agents (`RESTRICTED_AGENTS`):** only the owner can bring these into a thread.
  Other agents get "owner only".
- **Everyone else:** ignored.

---

## Running Hermes Agent with GDPR

GDPR is agent-agnostic, but it was built for a team of [Hermes Agent](https://hermes-agent.nousresearch.com/docs)
bots. This section explains what that combination gives you and why the setup looks the way it
does.

### Why the agents need shared infrastructure

Each Hermes agent runs as its own container with its own bot token, config, memory, skills and
session history. They do not share a process, a filesystem or a context window. An agent cannot
see what another agent said or did unless it is told. So everything the team shares has to live
outside the agents:

- **Discord is the common room.** A private thread is the one place where you and several agents
  see the same messages. GDPR creates and manages those threads, so the agents don't need
  moderation rights.
- **Mentions are the wake-up signal.** An agent only acts when it is mentioned or addressed. A
  plain name in a message wakes nobody, so agents hand work to each other with a real mention.
  GDPR re-pings an agent after adding it, because Discord does not deliver earlier messages to a
  new member of a private thread.
- **One shared skills repo.** Procedures any agent learns are stored in a git repo all agents
  pull from, so one agent's lesson is available to the rest. Each agent still decides which
  skills it enables.
- **Project knowledge lives in the repo** (`AGENTS.md`), not in an agent's memory.
- **Secrets stay out of chat.** Agents don't share credentials. `/secret` puts a value in a file
  on the server, and only the agent that needs it picks it up.
- **A clear owner per area** (infrastructure, Discord setup, code, coordination). A request
  outside an agent's area is handed to the owner with a mention, not done on the side.

A handover between agents is therefore a short mention plus a written note, not shared memory.

### What GDPR and the Discord setup make easier

- **One thread per job.** Mention an agent in the HQ channel and the thread appears with the
  right agents in it. Archive it when done. Old threads need no cleanup.
- **Adding and removing agents mid-conversation.** `@GDPR add ops` brings in a second agent
  without anyone needing thread permissions.
- **Wrap-up.** `/done` makes every agent check for unfinished work and save what it learned
  (memory and skills) before you close the thread.
- **Moving a session to another thread.** A Hermes session can be taken from one thread to
  another with its full history:
  1. In the old thread: `/title <name>` (once per session).
  2. In the new thread: `@GDPR add <agent>` if the agent is not there yet, then `/resume <name>`.

  The agent can only join a private thread through GDPR, so `/add` has to come first. `/resume`
  moves the session: the new thread's own earlier session is replaced (it is still stored and
  can be resumed by title), and the old thread starts fresh. A session belongs to the agent that
  made it, so this works within one agent only. Across agents, use a handover note.
  Requirement on the Hermes side: the owner's user ID must be set as an admin for that bot
  (`platforms.discord.extra.group_allow_admin_from` in its `config.yaml`). Without it the
  cross-thread resume is silently ignored. Depending on your Hermes version a small gateway
  change may also be needed; check that `/resume <name>` answers "Resumed session ...".
- **Restricted agents.** An agent that only you should call (for example one billed per token)
  goes in `RESTRICTED_AGENTS`, so other agents cannot wake it.
- **A log of what happened.** Every open, add, kick and archive is logged in the HQ channel.

### What it does not do

GDPR does not run agents, route work or keep context between agents. It manages who is in which
thread. Task tracking, shared skills and project notes are separate pieces you provide (we use a
kanban board and a git repo).

---

## How setup works

Setup has two parts:

- **Part A needs a person** logged in to Discord, because Discord only lets a person create a
  bot and invite bots to a server. It takes about 5 minutes.
- **Part B can be done by anyone:** you, or an AI agent with a shell on the machine that will
  run GDPR. One command sets up Discord (role, channels, permissions), writes the IDs into
  `.env` and creates the encryption key. Every step says what you should see.

If you hand part B to an agent, give it this README and tell it where the bot token is. Give the
token as a file or through your secret store, never in a chat message.

What you need:

- A Discord server where you have the **Manage Server** permission. Create a new one if you
  like.
- A machine that stays on, with **Docker** and **Docker Compose** (Linux, or Docker Desktop on
  Windows/macOS). Nothing else needs to be installed.

---

## Part A: done by a person

### A1. Create the bot

1. Go to <https://discord.com/developers/applications> and click **New Application**. Name
   it, for example "GDPR".
2. On the **General Information** page, copy the **Application ID**. You need it in A3.
3. Open the **Bot** tab:
   - Click **Reset Token** and copy the token. Treat it like a password: anyone with the
     token controls the bot.
   - Under **Privileged Gateway Intents**, turn on both of these. Both are **required**:
     - **Message Content Intent**, so GDPR can read messages.
     - **Server Members Intent**, so GDPR can see who is in a thread. `/done` and adding an
       agent by mention need it, and setup uses it to find your agent bots.
   - Click **Save Changes**.

### A2. Keep the token safe

Keep the token somewhere safe for now. It goes into the `.env` file in step B1. If an agent does
part B, give it the token as a file or through your secret store.

### A3. Invite GDPR

Open this link, with `APPLICATION_ID` replaced by the ID from A1:

```
https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&scope=bot+applications.commands&permissions=395405495312
```

Choose your server and click **Authorize**. The link gives GDPR these permissions:

- **View Channel, Send Messages, Read Message History:** to read and answer messages.
- **Create Private Threads, Send Messages in Threads, Manage Threads:** to open threads and
  add or remove members.
- **Manage Channels, Manage Roles, Create Public Threads:** only for the setup in B3, which
  creates the channels and the role and sets their permissions. Discord only lets a bot set
  permissions it has itself. You can remove these three after setup (B6).

### A4. Add your agent bots

1. Invite each agent bot to the server with its own invite link.
2. In each agent bot's own application, turn on **Message Content Intent**. Without it, the
   agent is added to threads but cannot read them.
3. If your agents run on a framework that opens its own thread whenever the bot is mentioned,
   turn that option off in the agent's settings. Otherwise you get a second thread next to
   GDPR's.

Part A is done. Do part B yourself, or tell your agent: "Set up GDPR by following part B of
README.md. The bot token is in `<where you put it>`."

---

## Part B: done by you or an agent

Run all commands in the gdpr folder. Every step says what you should see. If you see something
else, stop and check the troubleshooting list.

### B1. Get GDPR and create `.env`

```
git clone https://github.com/J-Malkovich/GDPR.git gdpr
cd gdpr
cp .env.example .env
```

(On Windows PowerShell: `copy .env.example .env`. Without git, download the ZIP from the
repository page and unpack it.)

Open `.env` and set `GDPR_TOKEN=` to the bot token. Use no quotes and no spaces around `=`.
Leave the other values empty; setup fills them in. Never commit or share `.env`; it is already
in `.gitignore`.

### B2. Build

```
docker compose build
```

You should see the build finish without errors. The build runs GDPR's tests, so a broken
download fails here.

### B3. Run setup

Linux/macOS:

```
docker run --rm -u 0 --env-file .env -v "$PWD:/out" gdpr:local python gdpr.py setup /out
```

Windows PowerShell:

```
docker run --rm -u 0 --env-file .env -v "${PWD}:/out" gdpr:local python gdpr.py setup /out
```

Setup uses only the bot token. It:

1. finds the server owner and your agent bots. Without `AGENT_IDS` in `.env`, that is **every
   other bot in the server**. If the server has bots that are not agents (music, moderation and
   so on), set `AGENT_IDS` in `.env` first, or those bots get the `agents` role and lose access
   to the threads channel.
2. creates a role `agents` and gives it to the agent bots
3. creates the channels `#gdpr-hq` and `#gdpr-threads` with the right permissions (see "What
   setup creates" below)
4. checks that no agent has **Manage Threads** or **Administrator**, which would let it read
   every private thread even after a kick. It reports this but changes nothing.
5. writes `OWNER_ID`, `HQ_ID`, `CHATS_ID` and `AGENT_IDS` into `.env`. Values you already
   filled in are kept.
6. creates the encryption key `gdpr_age_key` (plus `gdpr_age_key.pub`) and the `secrets`
   folder

You should see a list of `PASS` lines and at the end `setup complete`. Setup also checks that
both intents from A1 are on.

A `FAIL` line says what is wrong and which step fixes it. Fix it and run setup again: it reuses
the role and channels that already exist, so it is safe to run as often as needed. Never
overwriting the key also makes it safe.

**Back up `gdpr_age_key`.** Without it, encrypted secrets cannot be read. Never share it.

### B4. Check `.env`

Open `.env` and check what setup wrote:

- `OWNER_ID` is the **server owner** unless you set it before. Only this person can use the
  slash commands and `/secret`. **If someone else will use GDPR, put their user ID here**,
  otherwise they are locked out, and run setup again so they get access to the channels.
- `AGENT_IDS` lists the agent bots as `name=id`. The names are what you type in commands
  (`@GDPR add dev`). Rename them if you like, remove any bot that is not an agent, and add
  extra names for the same ID if you want aliases (`dev=123...,coder=123...`).
- Optional: set `OWNER_NAME` (your name in GDPR's messages) and `RESTRICTED_AGENTS` (agents
  only the owner may bring into threads).

### B5. Start

```
docker compose up -d
docker compose logs gdpr
```

You should see `GDPR ready as <bot name>`. Slash commands can take a minute to appear.

### B6. Check it works

Only the owner can give GDPR its first commands. An agent doing the setup should ask the owner
to do this:

1. In `#gdpr-hq`, write `@<agent> hello`, mentioning one of your agents. A private thread should
   appear in `#gdpr-threads` with the owner and that agent, and a log line in `#gdpr-hq`.
2. In that thread, run `/done`. GDPR should post one wrap-up message mentioning the agent.
3. Optional, if you will use `/secret`: run it with a test name and value. A file with that name
   should appear in the `secrets` folder (`sudo ls secrets` on Linux). Without the plugin below,
   the reply warns that the value arrived unencrypted. That is expected; use a dummy value.

When these work, setup is done. **Now remove Manage Channels, Manage Roles and Create Public
Threads from GDPR's role** (Server Settings > Roles > GDPR). GDPR does not need them to run, and
with them a leaked bot token could rearrange your server's channels and roles. Add them back only
for the time you run setup again.

An agent should report back to the owner with:

- that GDPR is running
- any `FAIL` lines it could not fix
- the public key from `gdpr_age_key.pub`, only needed for the optional plugin below
- a reminder to back up `gdpr_age_key`

### What setup creates

For reference, or if you prefer to do it by hand:

- **Role `agents`:** no permissions of its own. It is given to every agent bot.
- **`#gdpr-hq`:** hidden from `@everyone`. The owner and GDPR can see it and post.
  - `agents` are **allowed** View Channel, Read Message History and Send Messages in Threads, so
    they show up when you type `@` to mention them.
  - `agents` are **denied** Send Messages, Create Public Threads and Create Private Threads, so
    only the owner and GDPR post in HQ. Agents give their commands inside threads.
- **`#gdpr-threads`:** hidden from `@everyone`. The owner and GDPR can see it.
  - `agents` are **allowed** View Channel, Read Message History and Send Messages in Threads.
  - `agents` are **denied** Send Messages, Create Public Threads, Create Private Threads and
    Manage Threads.
  - So agents only talk inside the private threads GDPR puts them in.

---

## Optional: encrypted /secret with the SecretCrypt plugin

You can skip this section. Everything else in GDPR, `/secret` included, works without it.

Without the plugin, `/secret` still works, but Discord's servers can see the value. GDPR saves
it anyway and its reply warns you to treat the secret as exposed and replace it.

Discord has no official plugin system, so the encryption needs
[Vencord](https://vencord.dev), a modified Discord client. Client mods are against Discord's
Terms of Service, so use this at your own risk. Everything else in GDPR works in the normal
Discord app.

The plugin works only in the Discord **desktop app**. On your phone or in the browser,
`/secret` values are not encrypted.

Install it on the computer where you use Discord. You need git and Node.js.

- **Linux/macOS:** `GDPR_PUBKEY=age1... bash vencord-plugin/install.sh`
- **Windows:** `powershell -ExecutionPolicy Bypass -File vencord-plugin\install.ps1 -PublicKey age1...`

Replace `age1...` with GDPR's public key. Setup prints it at the end of B3, and it is also in
`gdpr_age_key.pub`. The public key is safe to share. The plugin holds one key, so it encrypts
for one GDPR bot. If you run more than one, set the key of the one you send secrets to (Settings
> Vencord > Plugins > SecretCrypt).

After installing:

1. Restart Discord.
2. Turn on **SecretCrypt** under Settings > Vencord > Plugins.
3. Every `/secret` submit must show a green "Secret encrypted, sending" message.
   - A red message means nothing was sent.
   - No message means the value was sent unencrypted.
4. Run the install again after Discord updates, because an update can remove the patch.

Tip: you can drag a small text file, such as a `.pem` key, onto the Value box. The plugin
reads it on your computer and uploads nothing.

---

## Secrets for agents

This works with or without the plugin; the plugin only decides whether Discord can see the
value on its way.

Each `/secret` becomes one file in `./secrets` (or `SECRET_INBOX_DIR`), named after what was
typed in the Name field. Characters other than letters, digits, `.`, `_` and `-` become `_`.
Files are never overwritten: a second file with the same name gets a timestamp added.

The folder belongs to the container user (uid 10001) with mode 700, so reading it needs root,
for example `sudo cat secrets/<name>`. An agent that should pick up secrets needs that access
on the host. When the owner tells an agent a file name, the agent should:

1. read the file
2. put the value where it is needed
3. delete the file

## Settings (`.env`)

- `GDPR_TOKEN`: the bot token.
- `OWNER_ID`: the user who controls GDPR.
- `OWNER_NAME` (optional): the owner's name in messages. It is also refused as a kick target.
- `HQ_ID`: the HQ channel.
- `CHATS_ID`: the threads channel. Commas allow more than one; new threads go in the first.
- `AGENT_IDS`: `name=id` pairs, comma separated. Several names may share one ID.
- `RESTRICTED_AGENTS` (optional): names only the owner may bring into threads.
- `SECRET_INBOX_DIR` (optional): host folder for secrets, `./secrets` by default. It must be
  owned by uid 10001 with mode 700.

## Troubleshooting

- **setup says "Discord rejected the token".** `GDPR_TOKEN` in `.env` is wrong or was reset.
  Copy a fresh token (A1).
- **setup says "The bot is not in any server yet".** Do the invite (A3). setup prints the link.
- **setup says "FAIL role agents" or "FAIL create #...".** GDPR is missing Manage Roles or
  Manage Channels. Give them to GDPR's role (Server Settings > Roles > GDPR), then run setup
  again.
- **setup says "FAIL agent bots found: none".** Invite your agent bots (A4) and turn on Server
  Members Intent (A1), or set `AGENT_IDS` in `.env` by hand.
- **The bot is online but ignores my messages.** GDPR's Message Content Intent is off (A1), the message
  did not come from the owner or a listed agent, or it was written outside `#gdpr-hq` and the
  threads in `#gdpr-threads`. After changing `.env`, restart with `docker compose up -d`.
- **"cannot list thread members: turn on Server Members Intent".** `/done` or adding an agent
  by mention needs that intent (A1). Turn it on, then restart with `docker compose restart`.
- **`/secret` says "could not decrypt".** The plugin encrypts with a different public key than
  the one in `gdpr_age_key.pub`, for example another GDPR bot's key. The plugin holds one
  public key, so if you run more than one GDPR bot, only one of them can receive encrypted
  secrets. Set the key in Settings > Vencord > Plugins > SecretCrypt.
- **A slash command answers from the wrong bot.** Another bot has commands with the same names.
  Pick the entry with GDPR's icon in the command list.
- **The log says `error: discord refused (403)`.** GDPR is missing a permission in that
  channel. Run setup again (B3).
- **An agent is added to a thread but doesn't respond.** The agent lacks the `agents` role
  (run setup again), or the agent bot's own Message Content Intent is off (A4).
- **A kicked agent still answers.** It has Manage Threads or Administrator through one of its
  roles. Setup reports this as a FAIL line; remove the permission from that role.
- **Every mention creates two threads.** The agent's framework opens its own thread on
  mentions (A4, point 3).
- **"owner only: ... can only be called by the owner".** That agent is in `RESTRICTED_AGENTS`.
- **"that is not a thread in the threads channel".** The thread is not under the channel set in
  `CHATS_ID`.
- **setup says `gdpr_age_key` is a folder.** The bot was started before setup, so Docker
  created an empty folder. Delete the folder and run setup again (B3).
- **The bot crashes at start with "missing env".** A value in `.env` is empty. The message
  names which one.

## Security notes

- The container runs as an unprivileged user, with a read-only filesystem and no extra Linux
  capabilities.
- Only GDPR holds Manage Threads, so a kick really removes an agent from a private thread.
- The plugin protects confidentiality only. The public key is public, so anyone who can submit
  the form could send a value. GDPR only accepts `/secret` from the owner and never overwrites
  an existing file.
- Agents can open, add, kick and archive threads. If an agent is tricked by a prompt, it can do
  those things, but it cannot read any secrets through GDPR.

## Updating and maintenance

- **Update GDPR:** in the gdpr folder, run `git pull`, then `docker compose up -d --build`. The
  build runs the tests again. `.env`, the key and the secrets are kept.
- **Change settings:** edit `.env`, then `docker compose up -d`.
- **Token leaked or lost:** in the developer portal, Bot tab, click **Reset Token**, put the new
  token in `.env` as `GDPR_TOKEN`, then `docker compose up -d`.
- **Run setup again** (new agent bot, changed owner, channels deleted): give GDPR's role Manage
  Channels, Manage Roles and Create Public Threads again (Server Settings > Roles > GDPR), run
  B3, then remove those three permissions again (B6).
- **New agent bot:** invite it (A4), then run setup again. With `AGENT_IDS` already set in
  `.env`, add the new bot there first (`name=id`).

## Development

```
pip install -r requirements.txt
python -m unittest test_gdpr
```

`requirements.txt` installs discord.py and pyrage; without pyrage the encryption tests fail.

The Docker build runs the same tests and fails if any test fails.

## License

MIT, see `LICENSE`.
