# Roles and coordination

Choose **what each agent is responsible for** with a role, and **how agents
communicate** with a coordination mode. These are separate settings: assigning the
`coordinator` role does not make an agent the leader in peer-to-peer mode.
Roles do not change models, providers, authentication, or tool permissions.

## Interactive choices

```bash
council --setup
```

The setup flow asks:

1. Peer-to-peer or hierarchical coordination?
2. If hierarchical, which enabled agent leads? If peer-to-peer, should turns after
   the first round be sequential or parallel?
3. Which role should each enabled agent have? Keep its existing role, select a
   premade/named role, or enter a custom description.

Enter a displayed name or its one-based menu position; Enter accepts the default.
Custom descriptions must be nonempty. Setup saves to the user configuration, or
the file selected by `--config` / `COUNCIL_CONFIG`. Existing files receive a unique
`agents.yaml.backup-*` backup before atomic replacement. YAML formatting/comments
are normalized; agent commands, models, environment values, and custom role definitions
are preserved. Cancelling before completion does not save changes or start agents.

Normal terminal runs print the effective choices on one line before starting, for
example `Council setup: peer-to-peer, sequential turns; claude=architect, codex=implementer, ...`,
and ask `Use these choices? [Y/n]`. Enter starts the run with them. `n` opens the
questionnaire above; those answers apply only to that session. Use `--setup` to save
defaults, or select `keep` to retain a role. `--quiet` hides discussion output but
does not suppress the question.

Piped/non-terminal invocations and prompts read using `-` do not ask questions.
They use the saved configuration (default mode: `peer-to-peer`). `--setup` itself
requires a terminal and fails promptly when invoked from a pipe.

## Premade roles

Run `council --roles` to list built-in roles and any named roles in your configuration.

| Role | Responsibility |
| --- | --- |
| `architect` | System structure, interfaces, constraints, and tradeoffs. |
| `implementer` | Working code, concrete implementation, and edge cases. |
| `researcher` | Primary sources, factual evidence, and uncertainty. |
| `reviewer` | Correctness, maintainability, scope, and focused fixes. |
| `tester` | Executed tests, failure cases, and coverage gaps. |
| `security` | Trust boundaries, permissions, sensitive data, and abuse cases. |
| `skeptic` | Counterexamples, hidden assumptions, and unsupported consensus. |
| `product` | User needs, acceptance criteria, usability, and scope. |
| `coordinator` | Assignments, reconciliation of evidence, and checklist tracking. |

The shipped roles are Claude → architect, Codex → implementer, agy → researcher,
and Hermes → skeptic. Older inline `role` descriptions remain supported. An agent
without a role receives the reviewer description. Existing user configs are never
silently replaced with the new presets.

## Custom roles and command-line overrides

```bash
council --mode peer-to-peer --role claude=architect --role codex=tester "Review this design."
council --mode hierarchical --leader claude \
  --custom-role 'hermes=Review numerical stability and statistical assumptions.' \
  "Design a data-processing tool."
```

`--role AGENT=NAME` selects a built-in or named custom role. `--custom-role
AGENT=DESCRIPTION` assigns free text. Repeat the flags for different enabled agents;
multiple assignments for the same agent in one invocation are rejected. CLI overrides
apply only to that run unless used with `--setup`, which saves the resulting choices.

To define a reusable custom role, add a top-level mapping to your configuration:

```yaml
roles:
  data-auditor: "Check data quality, statistical assumptions, and numerical stability."
```

In the existing agent entry, use either:

```yaml
role_preset: data-auditor
```

or a one-off description:

```yaml
role: "Review migration compatibility and rollback behavior."
```

Do not set both fields to nonempty values. Named custom roles cannot replace built-in
role names or the menu words `keep` and `custom`. Names use lowercase letters, digits,
hyphens, or underscores and must begin with a letter.

## Peer-to-peer: direct shared conversation

```bash
council --mode peer-to-peer "Debate the tradeoffs and resolve disagreements."
```

Round 1 runs independently in parallel. In round 2 and later, agents speak one at a
time in configuration order. Each agent receives the complete shared conversation,
including earlier turns in the current round, and is instructed to address relevant
peers by name. For example, Claude can question Codex, Codex can answer immediately
on its turn, and Claude can respond in the next round. No leader rewrites these
messages. Everyone sees the same public conversation; this is not private routing
or a live network connection between CLI processes.

The configured framer still writes the checklist and the synthesizer compiles a
final answer, but neither controls the exchange between peers. Synthesis can fall
back to another surviving peer if necessary. A question from the last permitted
round may remain unanswered; final synthesis must preserve unresolved issues.

### Sequential or parallel turns

The description above is the default, **sequential** turn style. Choose it with
`settings.peer_turns`, `--peer-turns`, or `council --setup`:

| `peer_turns` | Each turn in rounds 2+ sees | Round duration | Trade-off |
| --- | --- | --- | --- |
| `sequential` (default) | Every earlier round **and** earlier replies from the current round | Sum of all peers' response times | A question can be answered in the same round. |
| `parallel` | Every earlier round only | The slowest peer's response time | Faster; a question asked in round N is answered in round N+1. |

```bash
council --peer-turns parallel "Debate the tradeoffs quickly."
```

With four agents that each take about a minute, a sequential round 2 or 3 takes about
four minutes and a parallel one about one. Both styles send every peer the complete
history of finished rounds, record speaker order in `session.json`, and use the same
consensus rule. The prompt tells each agent which style is in effect.

## Hierarchical: leader and specialists

```bash
council --mode hierarchical --leader claude "Build and verify a small CLI."
```

The leader frames the request. Each discussion round then runs:

1. **Assignment:** the leader sees all earlier reports and assigns work by agent name.
2. **Specialist reports:** remaining agents work in parallel, seeing the leader's
   assignment plan and their own earlier reports, without raw peer reports.
3. **Leader review:** the leader receives those reports and evaluates evidence,
   disagreements, and remaining work.

The plan is visible to all specialists, including assignments to other specialists.
Role and assignment compliance are prompt instructions, not a mechanical proof that
an agent performed its assigned work. The leader relays relevant peer findings in
later assignment plans and writes the final synthesis. The hierarchy overrides
`settings.framer` and `settings.synthesizer` for that run.

The leader must be enabled and executable. An unavailable leader fails startup; a
failed leader framing, assignment, or review stops the run with `leader_failed`.
Final leader failure is `synthesis_failed`. Council does not silently replace the
leader. Specialist failures can be tolerated while quorum remains. Quorum includes
the leader, but consensus requires an explicit yes from every surviving specialist
and the leader in round 3 or later; the leader cannot vote on their behalf.

Hierarchy adds one leader assignment call per round compared with a peer round of
the same size: with four agents and three rounds, a normal run uses 17 calls
including framing and synthesis, versus 14 in peer mode. Hierarchical specialist
reports run in parallel, but every round adds two sequential leader calls (plan and
review). Sequential peer turns are usually the slowest option; parallel peer turns the
fastest. All modes keep the soft time cap and per-call timeouts.

## Noninteractive automation

```bash
council --non-interactive --prompt-file request.md
council --mode hierarchical --leader claude --prompt-file request.md
```

`--mode` selects the mode and skips all startup questions. `--non-interactive`
retains the configured mode and roles while skipping questions. These choices can
also be stored under settings:

```yaml
settings:
  mode: hierarchical
  leader: claude
```

Every session records the effective mode, leader (or `null` for peers), resolved role
descriptions, and per-round speaker order. Hierarchical `planN.<leader>.*` artifacts
contain assignments; `roundN.<leader>.*` contains the corresponding review.
