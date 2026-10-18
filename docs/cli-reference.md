# CLI reference

```text
council [options] "prompt"
council [options] -                     # read the prompt from stdin
council [options] --prompt-file FILE
council --doctor | --setup | --init-config | --paths | --roles | --adapters | --version
```

## Prompt input

| Form | Behavior |
| --- | --- |
| `council "text"` | Use the argument as the prompt. |
| `council -` | Read the prompt from stdin. Never asks setup questions. |
| `--prompt-file FILE` | Read a UTF-8 prompt file. Cannot be combined with a positional prompt. |

An empty or whitespace-only prompt is rejected with exit 2. How the prompt then reaches
each agent (stdin or argument) is a per-agent setting: see
[prompt delivery](configuration.md#prompt-delivery).

## Run options

| Option | Default | Meaning |
| --- | --- | --- |
| `--agents NAMES` | enabled configured agents | Comma-separated roster for this run: configured agent names first, then built-in adapter IDs (see `--adapters`). Must name at least `min_agents` distinct agents. With `--setup`, the roster is saved. |
| `--type {auto,question,build}` | `auto` | `build` writes and tests the synthesized files. `auto` uses the framing agent's `TASK_TYPE`, then a keyword heuristic. |
| `--max-rounds N` | `settings.max_rounds` (3) | Discussion rounds, excluding framing and synthesis. Positive integer. |
| `--no-verify` | off | Skip build extraction and execution. A build then exits 1 with status `build_unverified`. |
| `--quiet` | off | Print status lines and the final answer only; transcripts are still saved. Does not suppress setup questions. |
| `--config PATH` | see [precedence](#environment-variables) | Configuration YAML for this run. |
| `--sessions-dir PATH` | `$XDG_STATE_HOME/agent-council/sessions` | Where session directories are created. |

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
| `--doctor` | Validate configuration and find each enabled agent's executable. Reports role, model, prompt delivery, and coordination. Exit 0 when at least `min_agents` executables (and the hierarchical leader) are found, else 2. No model calls; does not test authentication. |
| `--setup` | Ask for mode, leader or peer turn style, and each agent's role, then save the configuration atomically, keeping a backup of the previous file. Requires a terminal (fails with exit 2 otherwise, or with `--non-interactive`). Honors `--config`, `COUNCIL_CONFIG`, `--agents`, `--mode`, `--leader`, `--peer-turns`, `--role`, and `--custom-role`. |
| `--init-config` | Create the user configuration from the bundled defaults if it does not exist. Never overwrites. |
| `--paths` | Print the selected config, user config, and sessions paths as JSON. Does not validate configuration or create directories. |
| `--roles` | List built-in roles and named roles from the configuration. |
| `--adapters` | List the built-in agent adapters, each one's executable, whether it is on `PATH`, and its prompt delivery. Reads no configuration. See [agent adapters](adapters.md). |
| `--version` | Print the installed version. |

## Environment variables

| Variable | Effect |
| --- | --- |
| `COUNCIL_CONFIG` | Configuration file; used when `--config` is absent. |
| `COUNCIL_SESSIONS_DIR` | Sessions directory; used when `--sessions-dir` is absent. |
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
| `1` | `quorum_lost`, `leader_failed`, `synthesis_failed`, `time_limit`, `build_failed`, `build_unverified`, `error` | The run could not produce a verified result. |
| `2` | `insufficient_agents`, `leader_unavailable` | Startup failure. Also used for invalid arguments or configuration (no session is created). |
| `130` | `interrupted` | SIGINT or SIGTERM; partial transcripts and a summary are saved. Also returned when setup input is cancelled. |

## Session files

Session directories are named with a UTC timestamp and a random suffix, so concurrent
runs never collide. They are created with owner-only permissions.

```text
sessions/<id>/
  final.md                         answer, overall status, roles, drops, build status
  session.json                     machine-readable progress and results (schema_version 1)
  error.txt                        traceback, only if Council itself hit an internal error
  rounds/
    round0.<agent>.prompt.txt      exact input sent to the agent
    round0.<agent>.stdout.txt      unmodified captured stdout
    round0.<agent>.stderr.txt      captured diagnostics
    round0.<agent>.md              parsed answer, or the failure reason
    round1.*, round2.*, round3.*   discussion turns
    plan1.*, plan2.*, plan3.*      leader assignments (hierarchical mode)
    final.*                        synthesis attempts
    *.last.txt                     final answers from file-mode adapters (codex)
    build-verification.json        complete build verification report
    build-verification.md          readable build verification report
  agents/<agent>/                  separate working directory per participant
  workspace/                       extracted build files and test execution
  claude-config/                   session-specific copy of Claude's configuration
```

The `session.json` fields are described in [development](development.md#session-data).
Inspect a summary with `python -m json.tool sessions/<id>/session.json`.
