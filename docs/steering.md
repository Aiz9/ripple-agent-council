# Steering a run

Options that shape how a council works: presets, automatic rosters, budgets, human
checkpoints, anonymous peer review, guardrails, and follow-up questions. Each one is
off unless you ask for it, and each is recorded in the session.

- [Presets](#presets)
- [Automatic roster](#automatic-roster)
- [Budgets](#budgets)
- [Human checkpoints](#human-checkpoints)
- [Anonymous peer review](#anonymous-peer-review)
- [Guardrails](#guardrails)
- [Follow-up questions](#follow-up-questions)

## Presets

A preset is a named bundle of settings. Explicit command-line options win over the
preset, and the preset wins over saved settings.

```bash
council --presets                         # list built-in and configured presets
council --preset quick "Explain this error"
council --preset deep --max-rounds 5 "Pick a database for this workload"
```

| Built-in | Effect |
| --- | --- |
| `quick` | 2 rounds, parallel peer turns. |
| `deep` | 4 rounds, then an anonymous peer ranking. |
| `blind` | Parallel turns plus anonymous peer ranking. |
| `safe-build` | Build task; you approve the test command before it runs (needs a terminal). |
| `hierarchy` | Hierarchical mode: a leader assigns work and reviews reports. |

Define your own under a top-level `presets` key. Besides `description`, a preset may
set `mode`, `peer_turns`, `leader`, `max_rounds`, `anonymous_review`, `human_review`,
`max_cost_usd`, `max_tokens`, `type`, `agents` (a list or comma string), and `roles`
(agent → built-in role, named role, or free text). Unknown keys are a configuration
error. A configured preset with a built-in's name replaces it.

```yaml
presets:
  security-review:
    description: Two reviewers and a skeptic, blind-ranked.
    agents: [claude, codex, hermes]
    roles: {claude: security, codex: reviewer, hermes: "Look for missing threat models."}
    anonymous_review: true
    max_cost_usd: 2
```

## Automatic roster

`--agents auto` picks the roster for you; `auto:N` caps it at N agents (default 4).

Candidates are your configured agents first, then installed built-in adapters. An
agent is skipped when its executable is not on `PATH`, or when a session in the last
three hours recorded it as rate- or usage-limited or signed out — those skips are
printed. The rest are ordered by how well their role suits the task (implementer,
tester, and reviewer for builds; researcher, skeptic, and architect for questions),
then by whether they are enabled in your configuration. The run stops with exit 2 if
fewer than `min_agents` remain. The chosen roster is printed as `Auto roster: ...`.

## Budgets

```bash
council --max-cost 1.50 "your prompt"        # USD, as reported by the CLIs
council --max-tokens 400000 "your prompt"
council --resume latest --max-cost 3         # raise the cap and continue
```

Council records each call's reported usage. Claude Code reports tokens and cost; Codex
reports total tokens; other CLIs report nothing reliable and are listed as unmetered
rather than estimated. Once a cap is reached, no new agent call starts (calls already
running finish), and the session pauses with exit 75 and `pause_reason:
budget_exhausted`. Usage appears in `final.md`, `session.json` (`usage`), and reports.

A cap only counts metered usage: with unmetered agents in the roster, real spend is
higher than the total shown. Settings: `max_cost_usd`, `max_tokens` (default: none).

## Human checkpoints

```bash
council --human-review framing,build "Build a log parser with tests"
council --human-review all "your prompt"
```

| Point | When | Choices |
| --- | --- | --- |
| `framing` | after the definition of done is written | continue, add guidance, replace the definition of done, stop |
| `rounds` | after each discussion round | continue, add guidance, synthesize now, stop |
| `synthesis` | before the final synthesis | continue, add guidance, stop |
| `build` | before the test command runs | approve or decline the exact `RUN:` command and file list (default: decline) |

Guidance you add is included in every later prompt as a note from the user. Decisions
are saved in the session, so `--resume` replays them instead of asking again; *stop*
is never saved, so a resumed session asks again. Checkpoints need a terminal: without
one, a new run is refused, and a resumed run pauses with `pause_reason:
awaiting_human`. Setting: `human_review` (a list, default empty).

## Anonymous peer review

```bash
council --anonymous-review "your prompt"
```

After the discussion, each surviving agent ranks every final position under shuffled
labels (Response A, B, ...), with agent names scrubbed from the text, then ends with a
`RANKING: A > B > C` line. Valid ballots are combined with a Borda count; malformed
ballots are ignored, and the number of valid ballots is reported. The ranking goes to the synthesizer as one more piece
of evidence (not a vote that overrides the debate) and appears in `final.md` and the
report. Labels are shuffled with a seed derived from the session, so a resume rebuilds
identical prompts and reuses saved ballots. The approach follows
[karpathy/llm-council](https://github.com/karpathy/llm-council). Setting:
`anonymous_review` (default false).

## Guardrails

Guardrails check every answer before it enters the debate. They live in a top-level
`guardrails` list:

```yaml
guardrails:
  - rule: no_secrets                 # API keys, tokens, private keys
  - rule: require_evidence_tags      # at least one [VERIFIED: ...]/[ASSUMPTION]/[UNVERIFIED]
    phases: [discussion]
    action: flag
  - rule: forbid
    pattern: "(?i)rm -rf /"
    message: "suggests deleting the filesystem"
  - rule: require
    pattern: "(?m)^RUN: "
    phases: [final]
  - rule: max_chars
    value: 20000
    agents: [hermes]
  - python: my_rules:no_shouting     # module:function on the Python path
```

| Field | Meaning |
| --- | --- |
| `rule` | `forbid` / `require` (a regular expression in `pattern`), `max_chars` (`value`), `require_evidence_tags`, `no_secrets`, or `python`. |
| `action` | `reject` (default): the agent gets one revision with the problems explained; if the revision still violates, the answer is dropped like a failed call. `flag`: keep the answer and record the violation. |
| `phases` | Any of `framing`, `discussion`, `ranking`, `final` (default: all). |
| `agents` | Only check these agents (default: all). |
| `message` | Replaces the default explanation for `forbid` and `require`. |

A `python` validator is called as `function(answer, context)` with `context` holding
`agent`, `tag`, and `phase`; it returns a message, a list of messages, or nothing. A
validator that raises is recorded as a *flag*, never a rejection, so a bug in a rule
cannot silently drop every answer. Every event is stored in `session.json`
(`guardrail_events`) and counted in `final.md`.

Guardrails check text. They do not restrict what an agent's own tools can do; that is
still each CLI's permission configuration.

## Follow-up questions

```bash
council "Which database fits this workload?"
council --continue "And how should we migrate from MySQL?"
council --continue-from 20261018T101500Z-abc123 "What about backups?"
```

`--continue` follows up on the latest completed session; `--continue-from SESSION`
names one. The new session's prompt carries every earlier question and council answer
in the chain, oldest first and in full, and its metadata links to its parent
(`parent_session`). Unless you pass `--config` or set `COUNCIL_CONFIG`, the follow-up
uses the parent's saved configuration, so the same council continues the conversation.
`--sessions` shows which session each one continues.
