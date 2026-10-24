# Example setup: what GDPR makes possible

This page is a showcase, not a reference. It describes how one owner uses GDPR with a team of
[Hermes Agent](https://hermes-agent.nousresearch.com/docs) bots, to show what the combination
allows. Setup and commands are in the [README](../README.md).

Nothing here is required. GDPR only manages who is in which private thread. The rest comes from
Hermes Agent (skills, memory, a role description per agent) and from how the Discord server is
laid out.

## The idea

The owner talks to the agents in Discord. GDPR decides who is in which thread. The agents do the
work. Alerts come back into Discord. Most days the owner only sees alerts and results, not
routine maintenance.

## One thread, separate conversations

A private thread can hold several agents at once, and the owner can still talk to one of them
without the others joining in.

- `@agent ...` is for that agent. Agents that are not mentioned stay out of it.
- A message with no mention is read by every agent that has already taken part, and each may
  answer. Use it when you want everyone's view.
- Agents hand work to each other the same way, with a real mention.

The details, and what they cost in tokens, are in "Running Hermes Agent with GDPR" in the README.

## Working together

- **Bring an agent in to do a job.** Mention it, or `@GDPR add dev`. It reads the thread and
  does the task.
- **Pick the model per job.** A heavy model for hard or risky work and a light one for lookups,
  set for the session. The agent's default is not changed. If the session is moved to another
  thread, the model goes with it.
  Which agent may switch models is a Hermes setting; by default only the owner has admin
  rights and a few commands can be allowed for others.
- **Have another agent review.** A coordinating agent checks the result against what was asked
  and against the real state (files, API, containers), not against the other agent's
  self-report.
- **Hand over with a mention plus a note.** Agents share no memory, so a handover is a mention
  that says what is needed and where the result goes. The agent that was asked replies with a
  mention when it is done, and says what it verified.
- **Move or close a conversation.** Move a session to another thread (README), kick agents that
  are finished, and `/done` makes everyone save what they learned.

## Who does what

Each area has one owning agent, and a request outside an agent's area is handed to its owner
with a mention, not done on the side.

- A **sysadmin** agent runs the apps, monitoring, backups and the password manager.
- A **coordinating** agent triages work, reviews it and owns the agent containers.
- A **Discord** agent owns the server layout, roles and integrations.
- A **developer** agent owns code and pull requests.

Day-to-day operations on three physical machines are handled this way: a single-board
computer and two small servers, where the agent containers run. Work is tracked on a shared
kanban board (we use Planka). Each agent has its own login on the password manager (Vaultwarden),
and shared logins go through one shared collection. Metrics and uptime come from a monitoring hub
with an agent on every host (Beszel).

## Adding a new agent

Deploying a new agent is a written checklist that the agents run themselves, so it is nearly
fully automated. They create the container, set up the allow lists, register the agent with
GDPR, and set up its Discord roles and channels.

The owner's part is small: give the secrets (the bot token and the model token) through
`/secret`, never in chat, and grant the permissions the agents need. With those in place an
agent can do almost all of the rest. A few steps still need the owner, such as creating the
Discord application and its token.

## From alert to action

1. Monitoring raises an alert when a host, container or service crosses a threshold or goes down.
2. A small poller turns each new alert into a Discord message. The poller is plain code, so
   routine problems are handled by automation without an agent.
3. An alert that automation cannot fix is posted with a real mention of the owning agent. Only a
   real mention wakes an agent.
4. The agent opens a thread, investigates, fixes the problem or escalates it, and posts the
   result.
5. If the fix is something the team will meet again, the agent writes it into a shared skill.
   The next alert of that kind starts from a written runbook.
6. Anything left unfinished becomes a kanban card, so open work stays visible.

## Shared skills

Every agent runs in its own container with its own memory. What they share lives outside them:
a git repository of skills that all agents read. A procedure one agent learns (a runbook, a
pitfall, a preference of the owner) is available to the others after the next sync. Each agent
still decides which skills it enables for itself. Project knowledge stays in each repository, not
in an agent's memory.

## How Discord is laid out

- A **trigger channel** is where the owner starts a job: mention an agent there and GDPR opens a
  private thread for it.
- The threads all sit under one **parent channel**, so the owner has a single list of
  conversations, each with only the people and agents who belong in it.
- A **log category** holds one read-only channel per agent for its own activity, plus a shared
  log of GDPR actions.
- An **alerts channel** receives monitoring and deploy notifications through webhooks. On our
  setup a webhook can wake an agent when its ID is on that agent's allowlist.
- **Permissions keep it tidy.** Each agent gets only the Discord rights its job needs, and
  sensitive ones such as Manage Threads are held by one bot (GDPR), so removing an agent from a thread really cuts it off. A bot cannot add
  itself to a private thread, and channel access is set per role.

## Limits worth knowing

- Agents do not share context. Cross-agent work needs a handover.
- More agents in a thread means more tokens per unmentioned message.
- GDPR does not route work. The routing described here comes from mentions, roles, skills and
  Hermes settings.
