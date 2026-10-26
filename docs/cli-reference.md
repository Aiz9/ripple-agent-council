# CLI reference

```text
council [options] "prompt"
council [options] -                     # read the prompt from stdin
council [options] --prompt-file FILE
council --continue "follow-up"          # or --continue-from SESSION
council --sessions
council --resume [latest|SESSION_ID|/path/to/session]
council --report [SESSION] | --serve
council --eval CASES [--baseline AGENT]... [--judge AGENT]
council --mcp
council --doctor [--probe] | --adapters [--probe] | --setup | --add-agent | --init-config | --paths
council --roles | --presets | --version
council --install-skills [SOURCE] | --skills
```

## Prompt input

| Form | Behavior |
| --- | --- |
| `council "text"` | Use the argument as the prompt. |
| `council -` | Read the prompt from stdin. Never asks setup questions. |
| `-f FILE`, `--prompt-file FILE` | Read a UTF-8 text or Markdown file, including `.txt` and `.md`. Cannot be combined with a positional prompt (including `-`). |

```bash
council -f request.md
council --prompt-file "path/to/my request.txt"
council --agents auto --type build -f specification.md
council --continue -f follow-up.md
```

Files are read in full as prompt text; Markdown headings, lists, code fences, and
indentation are preserved. Any extension is accepted for UTF-8 text, with or without
a byte-order mark. Relative paths use the current working directory; `~` is expanded.
Automatic roster selection uses the loaded text for all three input forms.

An empty or whitespace-only prompt, NUL bytes, a missing/unreadable file, a directory,
or a file that is not UTF-8 is rejected with exit 2 before any session or model call.
The loaded text is saved in the session snapshot: resume does not reread the file.
Maintenance commands such as `--doctor` do not read prompt input.

How the prompt then reaches each agent (stdin or argument) is a per-agent setting: see
[prompt delivery](configuration.md#prompt-delivery).

## Run options

| Option | Default | Meaning |
| --- | --- | --- |
| `--skill NAME` | `skills.names` (`["*"]`) | Select and inline a shared skill for every agent; repeatable. Overrides the saved selection and enables shared skills. See [shared skills](configuration.md#shared-skills). |
| `--no-skills` | off | Disable shared skills for this run; mutually exclusive with `--skill`. |
| `--skills-dir PATH` | config, environment, XDG data | Override the shared library root. Also applies to installation and listing. |
| `--workspace PATH` | invoking directory (worktrees with `--repo`) | Project directory to inspect. Repeat for multiple roots. Paths are passed to supported native CLIs and bound Council fallback file access. Saved for resume. |
| `--tool-approval {ask,deny}` | `settings.tool_approval` (`ask`) | Ask in the Council terminal before fallback tool execution, or reject requests without execution. Without a terminal, `ask` pauses for approval. Native CLI permissions are unchanged. |
| `--agents NAMES` | enabled configured agents | Comma-separated roster for this run: configured agent names first, then built-in adapter IDs (see `--adapters`). Must name at least `min_agents` distinct agents. With `--setup`, the roster is saved. `auto` or `auto:N` chooses for you: see [automatic roster](steering.md#automatic-roster). |
| `--preset NAME` | none | Apply a built-in or configured [preset](steering.md#presets). Explicit options win. |
| `--type {auto,question,build}` | `auto` | `build` writes and tests the synthesized files. `auto` uses the framing agent's `TASK_TYPE`, then a keyword heuristic. |
| `--max-rounds N` | `settings.max_rounds` (3) | Discussion rounds, excluding framing and synthesis. Positive integer. |
| `--no-verify` | off | Skip build extraction and execution. A build then exits 1 with status `build_unverified`. |
| `--max-cost USD` | `settings.max_cost_usd` (none) | Pause resumably once reported cost reaches USD. Allowed with `--resume` to raise the cap. See [budgets](steering.md#budgets). |
| `--max-tokens N` | `settings.max_tokens` (none) | Pause resumably once metered tokens reach N. Allowed with `--resume`. |
| `--human-review POINTS` | `settings.human_review` (none) | Pause for you at `framing`, `rounds`, `synthesis`, `build` (comma list), or `all`. Needs a terminal. See [human checkpoints](steering.md#human-checkpoints). |
| `--anonymous-review` | `settings.anonymous_review` (off) | Before synthesis, agents blind-rank the anonymized final positions. See [anonymous peer review](steering.md#anonymous-peer-review). |
| `--continue` | off | Ask a follow-up to the latest completed session, with the same council. See [follow-ups](steering.md#follow-up-questions). |
| `--continue-from SESSION` | none | Ask a follow-up to a specific completed session (ID or path). |
| `--repo PATH` | none | Give each agent its own git worktree of this repository; a verified build is committed on a new `council/<session>` branch, never merged or pushed. See [git worktrees](integrations.md#git-worktrees). |
| `--quiet` | off | Print status lines and the final answer only; transcripts are still saved. Does not suppress setup questions. |
| `--no-stream` | off | Show each agent turn as one block when it finishes, instead of live. See [live output](observability.md#live-output). |
| `--config PATH` | see [precedence](#environment-variables) | Configuration YAML for this run. |
| `--sessions-dir PATH` | `$XDG_STATE_HOME/agent-council/sessions` | Where session directories are created. |
| `--resume [SESSION]` | latest incomplete session | Restore a checkpointed session. Saved goal, agents, roles, settings, and repository are used; do not combine with new task/configuration options (only `--max-cost`, `--max-tokens`, and `--retry-build` are allowed). Does not ask startup questions. |
| `--sessions` | off | List saved sessions, status, stage, and goal. No model calls. |
| `--retry-build` | off | With `--resume`, explicitly retry failed/interrupted verification. Known failed builds retain repaired workspace files; unknown/interrupted builds rematerialize generated files. |

## Coordination and roles

These change the run only; combine them with `--setup` to save them.

| Option | Meaning |
| --- | --- |
| `--mode {peer-to-peer,hierarchical}` | Coordination mode. Also skips the startup question. |
| `--peer-turns {sequential,parallel}` | Peer-to-peer rounds 2+: one agent at a time seeing same-round replies, or all at once seeing the conversation through the previous round (faster). Ignored in hierarchical mode. |
| `--leader AGENT` | Enabled agent that leads hierarchical mode. |
| `--role AGENT=ROLE` | Assign a built-in or named custom role. Repeat for different agents. |
| `--custom-role AGENT=DESCRIPTION` | Assign a free-text role. Repeat for different agents. |
| `--non-interactive` | Use the configured choices without asking. |

Assigning two roles to the same agent in one invocation is rejected. Roles never change
an agent's model, command, or permissions.

**Startup question.** When stdin and stderr are both a terminal and none of `--mode`,
`--non-interactive`, or a stdin prompt (`-`) is used, Council prints a one-line summary
of the effective choices and asks `Use these choices? [Y/n]`. Enter or `y` starts the
run; `n` opens the full questionnaire for this run only.

## Maintenance commands

These are mutually exclusive and never start a council session.

| Command | Behavior |
| --- | --- |
| `--install-skills [SOURCE]` | Install all four configured sources (Orchestra Research, Ponytail, Caveman, Graphify) globally. `SOURCE` can be `all`, a source name, its GitHub URL, or a local checkout path. The all-source command keeps existing libraries and installs missing ones; explicit single-source installs refuse overwrites. Retains resources, licenses, notices, and provenance. Requires git/network for GitHub. No model calls. |
| `--skills` | List installed shared skill names and descriptions, marking configured skills with `*`. No model calls. |
| `--doctor` | Validate configuration and find each enabled agent's executable. Reports role, model, prompt delivery, and coordination. Exit 0 when at least `min_agents` executables (and the hierarchical leader) are found, else 2. No model calls; does not test authentication. With `--probe`, sends each installed enabled agent one short `READY` request in parallel and counts only agents that answer. |
| `--setup` | Ask for mode, leader or peer turn style, and each agent's role, then save the configuration atomically, keeping a backup of the previous file. Requires a terminal (fails with exit 2 otherwise, or with `--non-interactive`). Honors `--config`, `COUNCIL_CONFIG`, `--agents`, `--mode`, `--leader`, `--peer-turns`, `--role`, and `--custom-role`. |
| `--add-agent` | Interactively add one agent: a built-in adapter, or any CLI described as a command line with `{prompt}` (argument delivery), no `{prompt}` (stdin delivery), and optional `{outfile}` / `{model}`. Asks for the output style, role, optional model, and whether it joins every run; validates each answer; optionally sends one real test request; then saves atomically with a backup to the same file `--setup` would. Requires a terminal. Exit 1 if you discard an agent whose test failed. |
| `--init-config` | Create the user configuration from the bundled defaults if it does not exist. Never overwrites. |
| `--paths` | Print the selected config, user config, and sessions paths as JSON. Does not validate configuration or create directories. |
| `--roles` | List built-in roles and named roles from the configuration. |
| `--adapters` | List the built-in agent adapters, each one's executable, whether it is on `PATH`, and its prompt delivery. Reads no configuration. With `--probe`, also sends every installed adapter one short `READY` request (a real model call each) and reports READY or the failure kind (for example `authentication`); exit 1 if none answers. See [agent adapters](adapters.md). |
| `--presets` | List built-in and configured presets with their settings. |
| `--report [SESSION]` | Write a self-contained `report.html` into a session (default: the latest) and print its path. `--open` opens it. See [reports](observability.md#reports). |
| `--serve` | Run the read-only session viewer on 127.0.0.1 (`--port`, default 8765; `0` picks a free port; `--open` opens a browser). |
| `--mcp` | Serve the Model Context Protocol on stdio, with the `ask_council`, `list_sessions`, and `get_session` tools. See [MCP server](integrations.md#mcp-server). |
| `--eval CASES` | Run every case in a YAML/JSON case file through the council and score it; `--baseline AGENT` (repeatable) also sends each case to one agent alone, `--judge AGENT` grades answers against case rubrics, `--eval-dir DIR` chooses the output directory. See [evaluation](integrations.md#evaluation). |
| `--version` | Print the installed version. |

## Environment variables

| Variable | Effect |
| --- | --- |
| `COUNCIL_CONFIG` | Configuration file; used when `--config` is absent. |
| `COUNCIL_SESSIONS_DIR` | Sessions directory; used when `--sessions-dir` is absent. |
| `COUNCIL_SKILLS_DIR` | Shared library root, used without `--skills-dir` or `skills.directory`. |
| `XDG_DATA_HOME` | Base of the default shared library (`agent-council/skills`). Empty or relative values are ignored. |
| `XDG_CONFIG_HOME` | Base of the user configuration (`agent-council/agents.yaml`). Empty or relative values are ignored. |
| `XDG_STATE_HOME` | Base of the default sessions directory. Empty or relative values are ignored. |

Configuration precedence: `--config`, then `COUNCIL_CONFIG`, then
`~/.config/agent-council/agents.yaml`, then the bundled defaults. A selected file that
is invalid fails the run; it never silently falls back to defaults. An `agents.yaml` in
the current directory is not loaded automatically.

Agents inherit Council's environment plus their configured `env` values.

## Exit codes

| Exit | Status values | Meaning |
| --- | --- | --- |
| `0` | `completed` | Question synthesis completed, or a build's test command passed. Check `consensus` separately. |
| `1` | `build_failed`, `build_unverified` | Build is not verified; session remains resumable. |
| `2` | no new status | Invalid arguments/configuration/checkpoint, or another process owns the session. |
| `75` | `paused` | Work saved; `pause_reason` describes unavailable agents, leader/synthesis exhaustion, a reached budget (`budget_exhausted`), a human checkpoint (`awaiting_human`, `stopped_by_user`), a runtime error, or an uncertain build outcome. |
| `130` | `interrupted` | SIGINT or SIGTERM; partial transcripts and a summary are saved. Also returned when setup input is cancelled. |

## Session files

Session directories are named with a UTC timestamp and a random suffix, so concurrent
runs never collide. They are created with owner-only permissions.

```text
sessions/<id>/
  final.md                         answer, overall status, roles, drops, build status
  session.json                     machine-readable progress and results (schema_version 1)
  run.json                         immutable goal/configuration/options snapshot for resume
  skills.json                      shared skill selection, portable document paths, and hashes
  skills/                          portable SKILL.md copies, originals, references, scripts, licenses
  progress.json                    phases already committed
  calls/                           durable latest result per logical agent call
  attempts/                        immutable attempt records, including failures/retries
  tool-calls/                      pending approvals and durable results of Council fallback tools
  processes/                       PID/start-time identity for interrupted subprocess recovery
  session.lock                     exclusive process lock; stale file does not mean stale lock
  final-answer.json                durable final markdown/text before completion is committed
  build-step.json                  build outcome and synthesis fingerprint, when applicable
  trace.jsonl                      one span per session, phase, and agent call (observability.md)
  report.html                      written by --report
  error.txt                        traceback, only if Council itself hit an internal error
  rounds/
    round0.<agent>.prompt.txt      exact input sent to the agent
    round0.<agent>.stdout.txt      unmodified captured stdout
    round0.<agent>.stderr.txt      captured diagnostics
    round0.<agent>.md              parsed answer, or the failure reason
    round1.*, round2.*, round3.*   discussion turns
    plan1.*, plan2.*, plan3.*      leader assignments (hierarchical mode)
    final.*                        synthesis attempts
    ranking.*                      anonymous peer ballots (--anonymous-review)
    <tag>-revision.*               revisions requested by a rejecting guardrail
    *.last.txt                     final answers from file-mode adapters (codex)
    build-verification.json        complete build verification report
    build-verification.md          readable build verification report
  agents/<agent>/                  separate working directory per participant (a git worktree with --repo)
  workspace/                       extracted build files and test execution
  build/                           with --repo: build worktree on branch council/<id>, instead of workspace/
  claude-config/                   session-specific copy of Claude's configuration
```

The `session.json` fields are described in [development](development.md#session-data).
Inspect a summary with `python -m json.tool sessions/<id>/session.json`.
