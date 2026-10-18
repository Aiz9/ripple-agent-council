# Changes

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
