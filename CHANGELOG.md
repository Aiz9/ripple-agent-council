# Changes

## Unreleased

- Fix empty project context: new sessions use the invoking directory or repeatable
  `--workspace` paths, and register directory access with Claude and Antigravity.
- Add terminal approvals for shared fallback file/list/command requests. Agents
  whose native tool approval is unavailable can ask Council, which displays the
  exact operation and waits for y/N/q. Missing terminals pause resumably; recorded
  results prevent duplicate execution. Native permissions and sandboxes are kept.
- Add `--tool-approval ask|deny` and settings `tool_approval`, `max_tool_requests`,
  and `tool_timeout_sec`. Additional tool-response model calls count toward budgets.
- Add shared global skills: `--install-skills [SOURCE]` installs Orchestra Research's
  AI Research Skills, Ponytail, Caveman, and Graphify; `--skills` lists the catalog. Every
  configured agent receives installed skills by default, including custom CLIs.
- Add `skills.enabled`, `skills.directory`, and `skills.names`, plus `--skill`,
  `--skills-dir`, and `--no-skills` overrides. Explicit skill selections are inlined;
  the full library uses a catalog with relevant skills read as needed.
- Inline Ponytail and Caveman's core guidance by default while preserving Council
  protocol and complete build outputs. Support lowercase Graphify skill documents,
  exclude duplicate native adapters, and retain Caveman's shared core resources.
- Adapt native skill paths, tool conventions, delegation, and autoresearch
  continuity for Council's configured agents. Preserve originals and resources in
  session snapshots so resume uses the same portable skill instructions.
- Add `-f FILE` as a shortcut for `--prompt-file FILE`, with examples for Markdown
  and text prompts, builds, and follow-up questions.
- Accept UTF-8 prompt files with a byte-order mark. Report empty files, invalid
  UTF-8, NUL bytes, unreadable paths, and non-regular files before starting a session.
- Load file and stdin prompts before automatic roster selection so build requests
  receive the same role preferences as typed prompts.

## 0.8.0 — Steering, observability, and integrations

Steering a run ([steering](docs/steering.md)):

- Budgets: `--max-cost USD` and `--max-tokens N` (settings `max_cost_usd`,
  `max_tokens`) stop starting calls once reported usage reaches the cap and pause the
  session resumably; raise the cap with `--resume ... --max-cost`. Usage is parsed from
  Claude Code (tokens and cost) and Codex (tokens); other CLIs are listed as unmetered.
- Human checkpoints: `--human-review framing,rounds,synthesis,build` (or `all`) to add
  guidance, replace the definition of done, end the debate early, approve the build
  command, or stop. Decisions are saved and replayed on resume.
- Anonymous peer review: `--anonymous-review` has agents blind-rank the anonymized
  final positions (Borda count) before synthesis.
- Guardrails as code: a top-level `guardrails` list (`forbid`, `require`, `max_chars`,
  `require_evidence_tags`, `no_secrets`, or a Python `module:function`) that flags an
  answer or asks for one revision and then drops it.
- Presets: `--preset quick|deep|blind|safe-build|hierarchy` or your own under
  `presets`; `--presets` lists them.
- `--agents auto[:N]` chooses an installed, recently usable, role-appropriate roster.
- Follow-ups: `--continue` and `--continue-from SESSION` carry the earlier questions
  and answers, with the same council.

Observability ([observability](docs/observability.md)):

- Live streaming: agent text appears as it is written for `stdout`, OpenCode, and the
  new `claude_stream_json` mode, now used by the bundled `claude` agent and adapter.
  `--no-stream` restores whole-turn blocks.
- Every session writes `trace.jsonl` (session, phase, and agent-call spans); the
  optional `otel` extra exports them to OpenTelemetry via the standard `OTEL_*`
  variables.
- `--report [SESSION]` writes a self-contained HTML report; `--serve` runs a local,
  read-only session viewer on 127.0.0.1.

Integrations ([integrations](docs/integrations.md)):

- `council --mcp`: an MCP server on stdio with `ask_council`, `list_sessions`, and
  `get_session`; verified with the official MCP Python SDK client.
- `--repo PATH`: one git worktree per agent at the session's base commit; a verified
  build is committed on a new `council/<session>` branch, never merged or pushed.
- `--eval CASES`: score the council on a case file with deterministic checks, single-
  agent `--baseline`s, and an optional blind `--judge`.
- `--adapters --probe` and `--doctor --probe` send one short READY request per agent to
  test sign-in.

Project:

- The package is now `ripple-agent-council` (the PyPI name `agent-council` is taken);
  the module (`agent_council`) and command (`council`) are unchanged. Reinstalling
  over 0.7.0 needs `uv tool uninstall agent-council` first.
- GitHub Actions: CI on Python 3.11–3.13, and a tag-triggered PyPI release using
  trusted publishing. Project URLs point to `Aiz9/ripple-agent-council`.

Fixes and behavior changes:

- Fix: on Python 3.11, a prompt sent on stdin that exceeded the pipe buffer, to a CLI
  that did not read it immediately, stalled until the call timed out. Pipes are now
  served by threads instead of `communicate()`.
- A command that exits while a descendant still holds its output pipes now has that
  process group ended after five seconds, instead of waiting for the timeout.
- Configurations created before 0.8.0 keep `claude_json` and do not stream; see
  [live output](docs/observability.md#live-output) to switch.

## 0.7.0 — Durable recovery and agent failover

- Add `--sessions` and `--resume [latest|ID|path]`; restore the saved goal, agent
  configuration, roles, workspace, and phase history without starting a new session.
- Checkpoint each response immediately, including completed members of a parallel
  round; fsync and atomically publish state. Reuse successful calls when inputs match.
- Keep immutable attempt records and distinct raw artifacts for retries.
- Classify account limits (including exit-zero notices), retry transient failures
  with bounded backoff, and rotate exhausted agents without retry storms.
- Elect and record successor leaders, including during review/final synthesis.
  Preserve quorum; pause with exit 75 when work cannot currently proceed.
- Use session locks and Linux PID/start-time/boot identity checks to recover safely
  after abrupt termination. Recover committed final answers without more model calls.
- Checkpoint build outcomes separately. Require `--retry-build` for an uncertain
  command; known failed-build retries preserve repaired workspace files.
- Add real SIGKILL/SIGINT, checkpoint durability, failover, resume, and build tests.

## 0.6.0 — Add agents without editing YAML

- Add `council --add-agent`, an interactive way to add a built-in adapter or any CLI.
  Describe the CLI as you would type it: `{prompt}` marks where the question goes, and
  leaving it out sends the question on stdin. Choose the output style, role, optional
  model, and whether the agent joins every run.
- Check each answer as it is given: unique names, placeholders written as separate
  words, no shell operators (commands run without a shell), and full configuration
  validation. Warn when the executable is not on `PATH`.
- Optionally send one short test request through the real CLI from a throwaway
  directory; if it fails, choose whether to keep the agent.
- Save atomically with a backup to the same file as `--setup`.

## 0.5.0 — Adapters for widely used agent CLIs

- Add opt-in adapters for Gemini CLI, Qwen Code, OpenCode, GitHub Copilot CLI, Cursor
  CLI, Goose, Kiro CLI, and Aider, plus templates for the four default agents. Each
  preset uses the CLI's most restrictive noninteractive mode (ask, plan, chat, or
  read-only tools) and no blanket approval flag; a test enforces this.
- Add `--agents NAMES` to choose a run's roster from configured agents and adapter IDs
  (saved when combined with `--setup`), `--adapters` to list the catalog and which
  executables are installed, and `adapter:` configuration entries that expand a
  template while keeping explicit field overrides.
- Add strict output modes `gemini_json`, `qwen_json`, `opencode_json`, `cursor_json`,
  and `aider_history`. Error envelopes, incomplete streams, and earlier messages are
  never accepted as the answer.
- When `--agents` leaves out the configured synthesizer, prefer an agent that receives
  its prompt on stdin, since the synthesis prompt is the largest.
- The four default agents were checked by running the installed CLIs. The eight new
  CLIs were checked against official documentation (OpenCode also against its source)
  but not run, because they were not installed; `docs/adapters.md` records this.
- Add `docs/adapters.md`; update the README, configuration, CLI reference,
  development, and troubleshooting guides.

### Behavior changes

Configuration validation accepts the new output modes and `adapter:` entries; an
unknown adapter ID is an error. The default roster and its commands are unchanged.

## 0.4.0 — Long debates, faster peer rounds, one-question startup

- Fix every run with the shipped configuration crashing before framing. Seeding
  Claude's session-specific configuration called `Path.open(..., opener=...)`, but
  pathlib's `Path.open()` has no `opener` parameter, so it raised `TypeError`. No test
  set `CLAUDE_CONFIG_DIR`; tests now cover seeding, its owner-only permissions, and a
  full run that seeds.
- Finish the session on an unexpected internal error instead of leaving it `running`:
  status `error`, a `final.md`, and the traceback in `error.txt` and on stderr.
- Fix long debates failing on Linux's per-argument limit. Later turns and the final
  synthesis carry the complete debate, and one command-line argument cannot exceed
  131,071 bytes. Add `prompt_via: stdin`, which writes the prompt to the agent's
  standard input with no size limit.
- Ship Claude Code (`claude -p`) and Codex (`codex exec -`) with stdin delivery, verified
  against the installed CLIs. `agy` and Hermes remain argument-only: neither reads its
  prompt from stdin (also verified).
- Refuse an oversize argument prompt before launch, with an explanation and remedies,
  instead of surfacing the kernel's `Argument list too long`. History is still never
  truncated.
- Add `peer_turns: parallel` and `--peer-turns`. Parallel peer rounds run every turn
  concurrently against the conversation through the previous round, so a round lasts
  as long as its slowest agent. The default stays `sequential`.
- Replace the per-run questionnaire with a one-line summary and `Use these choices?
  [Y/n]`. Enter runs with the saved choices; `n` opens the full questionnaire.
- Ask for the turn style in `--setup` and the questionnaire when peer-to-peer is chosen.
- Report each agent's prompt delivery and the peer turn style in `--doctor`; record
  `peer_turns` in `session.json` and `final.md`.
- Add a CLI reference and restructure the README around a quick start; document prompt
  delivery, turn styles, and Claude credential-copy failures.
- Remove stale 0.2.0 files from `build/`.

### Behavior changes

Terminal runs now ask one confirmation question instead of the full questionnaire;
answer `n` for the previous behavior. Configuration validation accepts `argv` without
`{prompt}` only with `prompt_via: stdin`, and rejects `{prompt}` together with stdin
delivery. User configurations are never rewritten by an upgrade: a configuration
created before 0.4.0 keeps argument delivery for Claude and Codex until edited (see
`docs/configuration.md#prompt-delivery`).

## 0.3.0 — Roles and coordination modes

- Add nine premade roles, reusable custom role definitions, and inline custom roles.
- Add `--roles`, per-agent `--role` / `--custom-role`, and a `--setup` wizard that saves
  coordination and role choices with a backup of the previous configuration.
- Ask terminal users for peer-to-peer or hierarchical coordination and role choices
  before execution. `--mode`, `--non-interactive`, and piped input support automation.
- Run peer conversations as independent first answers followed by direct sequential
  turns that include earlier replies from the same round without a leader intermediary.
- Add hierarchical leader assignment → specialist reports → leader review rounds;
  the selected leader frames and synthesizes. Preserve specialist dissent and quorum.
- Record mode, leader, effective roles, and speaker order in session results.
- Preserve inline roles from older configs and Hermes's model/provider configuration.

## 0.2.0 — Global tool installation

- Package the application with a `council` console entrypoint for `uv tool install`.
- Bundle the default agents configuration so installed copies work without a checkout.
- Use XDG user configuration and state directories, with explicit CLI and environment overrides.
- Add `--init-config`, `--paths`, and `--version`; preserve existing user configuration.
- Retain the source launcher and `council.py` compatibility entrypoint.
- Add installation, migration, update, and uninstall instructions plus global-tool tests.
- Move implementation to `agent_council/cli.py` and defaults to `agent_council/agents.yaml`.
  Source runs now use the same user paths as installed runs; use `--config` and
  `--sessions-dir` to keep using earlier checkout-local files.

## Reliability and documentation update — 2026-10-18

- Preserve Hermes's configured model and provider by leaving model selection to Hermes.
- Validate configuration before calls, including duplicate names, model overrides,
  output adapters, timeouts, quorum, and preferred agents.
- Apply the shared agent timeout when an individual timeout is omitted.
- Require quorum after every discussion round and for consensus. Read consensus
  only from an unambiguous final-line declaration.
- Preserve the complete debate for subsequent rounds and final synthesis.
- Use separate agent working directories and a separate final build directory.
- Retry failed synthesis with another surviving participant.
- Keep every failure, stdout/stderr, partial timeout output, and all reported costs.
- Save progress atomically and produce summaries for failures and handled interrupts.
- Kill process groups on timeout/interruption; close subprocess stdin.
- Reject malformed JSON success envelopes and missing file-mode final answers.
- Validate all generated paths before writing; reject malformed fences, duplicate
  paths, path escapes, missing artifacts, and multiple test commands. Keep empty files.
- Return failure for failed/skipped build verification and failed synthesis.
- Add `--doctor`, `--prompt-file`, stdin prompts, `--sessions-dir`, and `--no-verify`.
- Resolve launcher symlinks and require uv's lockfile.
- Add a deterministic unit/integration suite plus configuration, architecture,
  development, and troubleshooting documentation.
- Correct earlier claims that default models necessarily mean the latest models,
  `agy --sandbox` grants headless permissions, and agents without bypass flags cannot
  execute tools. Leave `agy` permission configuration unchanged.

### Behavior changes

`min_agents` now applies throughout discussion, not only at startup. Failed
discussion agents are not reintroduced. Build tasks with verification disabled
return exit 1 (`build_unverified`) and do not extract files. Missing file-mode output
and malformed structured output are failures. Claude configuration copies are now
session-specific and remain inside the private session directory. A run can complete
without consensus; inspect `consensus` separately from `status`.
