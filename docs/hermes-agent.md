# Running Hermes Agent with GDPR

GDPR is agent-agnostic, but it was built for a team of [Hermes Agent](https://hermes-agent.nousresearch.com/docs)
bots. This page explains what that combination gives you and why the setup looks the way it
does. Install and commands are in the [README](../README.md). A showcase of a full day-to-day
setup is in [example-setup.md](example-setup.md).

## Why the agents need shared infrastructure

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

## What GDPR and the Discord setup make easier

- **One thread per job.** Mention an agent in the HQ channel and the thread appears with the
  right agents in it. Archive it when done. Old threads need no cleanup.
- **Adding and removing agents mid-conversation.** `@GDPR add ops` brings in a second agent
  without anyone needing thread permissions. See "Removing an agent" below.
- **Wrap-up.** `/done` makes every agent check for unfinished work and save what it learned
  (memory and skills) before you close the thread.
- **Restricted agents.** An agent that only you should call (for example one billed per token)
  goes in `RESTRICTED_AGENTS`, so other agents cannot wake it.
- **A log of what happened.** Every open, add, kick and archive is logged in the HQ channel.

### How group threads behave

When several agents are in one thread, who answers depends on the mention. This comes from the
Hermes Discord gateway, not from GDPR (read in the gateway code, not tested live with two
agents):

- A message that mentions one or more agents but not agent X is ignored by X. Mention only the
  agents you want to answer.
- A message with no mention at all is read by every agent that has already taken part in the
  thread, and each may answer. That costs tokens for every agent, so remove the ones you no
  longer need (see "Removing an agent").
- An agent that has not taken part yet needs a mention to start.
- Agent-to-agent messages only wake the target if they contain a real mention of it.
- An optional Hermes setting, `discord.thread_require_mention: true`, makes threads require a
  mention even after an agent has taken part. It is off by default.

### Removing an agent

`@GDPR kick dev` (or `/kick`) takes the agent out of the private thread, so it no longer
receives that thread. Each agent is a separate bot with its own session, so asking it to stay
quiet does not stop it reading and spending tokens in the background; removing it does.

- This only holds while the agent has no Manage Threads or Administrator permission, which is
  why only GDPR holds Manage Threads (setup warns about roles that break this).
- It pairs with resume: after a resume an agent answers everything in the thread, and a kick is
  how you stop that.
- This says the agent no longer receives the thread, not that it forgets it. `@GDPR add dev`
  brings it back.

### Moving a session to another thread

A Hermes session can be taken from one thread to another with its full history:

1. In the old thread: `/title <name>` (once per session).
2. In the new thread: `@GDPR add <agent>` if the agent is not there yet, then `/resume <name>`.

The agent can only join a private thread through GDPR (a bot cannot add itself to a private
thread, Discord answers 403 Missing Access), so `/add` has to come first or `/resume` fails.
`/resume` moves the session: the new thread's own earlier session is replaced (it is still
stored and can be resumed by title), and the old thread starts fresh. A session belongs to the
agent that made it, so this works within one agent only. Across agents, use a handover note.

On the Hermes side, the owner's user ID must be set as an admin for that bot
(`platforms.discord.extra.group_allow_admin_from` in its `config.yaml`, then restart the
gateway). Without it a cross-thread resume is silently ignored. Side effects to know about:

- Non-admin humans can then only run `/help` and `/whoami` on that bot in Discord.
- After a resume the agent is a participant in the new thread and answers every message there
  without an @mention. The `thread_require_mention` setting (see "How group threads behave")
  stops that.

On stock Hermes, use `/resume <name> --all` to move a session across threads. These steps were
checked on our own setup, not on a stock install.
