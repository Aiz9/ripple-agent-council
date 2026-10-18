# Development

## Layout

| File | Responsibility |
| --- | --- |
| `council` | Bash launcher: resolve symlinks and run the project with uv's lockfile. |
| `council.py` | Compatibility entrypoint for source checkouts. |
| `agent_council/cli.py` | Configuration discovery/validation, CLI adapters, subprocess lifecycle, prompts, orchestration, artifacts, and build verification. |
| `agent_council/adapters.py` | Built-in adapter catalog, `adapter:` expansion, `--agents` roster selection, and `--adapters` listing. |
| `agent_council/outputs.py` | Parsers for Gemini, Qwen Code, OpenCode, and Cursor structured output. |
| `agent_council/aider.py` | Parser for Aider's LLM history file. |
| `agent_council/roles.py` | Built-in/custom role catalog, validation, and assignment. |
| `agent_council/setup.py` | Interactive choices and atomic configuration save with backups. |
| `agent_council/agents.yaml` | Bundled default participants and settings; no account credentials. |
| `agents.yaml` | Checkout example for explicit `--config agents.yaml` use. |
| `pyproject.toml` | Package metadata, console entrypoint, dependency and build configuration. |
| `tests/fake_agent.py` | Deterministic executable fixture supporting stdout, JSON, and file output, with the prompt as an argument or on stdin (`-`). |
| `tests/coordination_agent.py` | Fixture that asserts each turn received exactly the conversation its coordination mode and turn style allow. |
| `tests/test_council.py` | Unit and subprocess integration tests. |
| `tests/test_global.py` | User configuration precedence, paths, setup commands, and entrypoints. |
| `tests/test_roles_coordination.py` | Role overrides, direct peer delivery, hierarchy, leader failures, and terminal setup. |
| `tests/test_adapters.py` | Catalog validity, no permission-bypass flags, default/catalog agreement, overrides, rosters, and full debates through every output format. |
| `tests/test_adapter_outputs.py`, `tests/test_aider_output.py` | Structured-output and Aider-history parsers, including error and partial outputs. |

The runtime dependency surface is Python's standard library plus PyYAML. Setuptools
builds the package. Tests use `unittest`; no extra test runner is required.

## Setup and validation

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
./council --doctor
bash -n council
uv build
```

If an existing Python environment already contains PyYAML, the same test suite can
also run as `python -m unittest discover -s tests -v`. Do not install packages globally
to work around an unavailable project environment.

The suite runs local fixture CLIs through real subprocesses. It covers every output
mode (including the Gemini, Qwen Code, OpenCode, Cursor, and Aider formats), the
built-in adapter catalog and roster selection, both prompt deliveries (including a 1 MB stdin prompt and the pre-launch
argument-size check), model passthrough, timeout inheritance, configuration errors,
complete debate history, sequential and parallel peer turns, hierarchy, consensus
quorum, drops, synthesis fallback, atomic summary paths, build extraction and
execution, cancellation, descendant cleanup, terminal setup and confirmation, and
launcher symlinks. Fixture requests never contact providers or read live credentials.

For an installation smoke check, build the wheel and use a temporary uv tool prefix:

```bash
tool_check_dir="$(mktemp -d)"
UV_TOOL_DIR="$tool_check_dir/tools" UV_TOOL_BIN_DIR="$tool_check_dir/bin" \
  uv tool install dist/agent_council-0.5.0-py3-none-any.whl
(cd /tmp && "$tool_check_dir/bin/council" --version && "$tool_check_dir/bin/council" --paths)
```

This checks the packaged console entrypoint outside the checkout. Set temporary
`XDG_CONFIG_HOME` and `XDG_STATE_HOME` directories for tests that initialize
configuration or run fixture debates. Never run real agents just to verify packaging.

Live validation is separate: execute a short question and a small build from a
normal terminal with authenticated CLIs, then inspect every participant's status.
Passing fixture tests does not establish that a CLI version, provider, or permission
configuration works live.

## Control flow

1. Validate CLI input and YAML. Check enabled agents and discover executable files.
2. Create a private, uniquely named session and separate agent/build directories.
3. Seed session-specific Claude configuration if configured.
4. Frame the goal and definition of done; peer mode can fall back to a generic checklist.
5. In peer mode, run independent answers concurrently, then either sequential direct
   turns with the full conversation so far, or (`peer_turns: parallel`) concurrent
   turns that share the conversation through the previous round. In hierarchy, run
   leader assignments, parallel specialist reports, then leader review for each round.
6. Require quorum, exclude failed discussion agents, and stop on consensus, round
   limit, or soft cap. A failed hierarchical leader stops the run.
7. Synthesize with the leader in hierarchy, or the preferred surviving peer with fallback.
8. For builds, validate all file blocks and destinations before writing; execute the
   single test command with a timeout. A skipped or failed verification is not success.
9. Write final status, answer, costs, and failure details; return the documented exit code.

Agent calls use `Popen` in a new process session on Linux. Stdin is closed, or, for
`prompt_via: stdin`, carries the prompt and is then closed. Before an argument-delivery
call, the prompt is checked against Linux's per-argument limit (32 pages, computed from
the page size). The parent polls completion, timeout, and a cancellation event; when a
poll times out mid-write, later polls continue the same stdin write. Cancellation and timeout
kill the process group and retain captured output. Parallel result lists follow
configuration order, regardless of completion order; console blocks print as each
call finishes.

## Session data

`session.json` has `schema_version: 1`. The principal fields are:

- `goal`, `session`, `agents`, `is_build`, `checklist`.
- `mode`, `leader`, `roles`: effective workflow, leader name or null, and resolved role names/descriptions.
- `peer_turns`: `sequential` or `parallel` in peer-to-peer mode; null in hierarchical mode.
- `status`, `exit_code`, `elapsed_sec`, `rounds_run`, `consensus`.
- `rounds`: discussion round number, successful participant names, and response order.
- `calls`: parsed answers, errors, return codes, elapsed times, and reported costs.
- `drop_history`: every failed call or missing executable with its phase.
- `dropped`: last failure per agent, including failures followed by recovery.
- `synthesizer`: agent whose final synthesis succeeded, when available.
- `build`: verification report, skipped status, or `null`.
- `total_cost_usd_claude`: sum of reported values, or `null` when unavailable.

Early failures may omit fields for phases that never ran. `round_cap_reached: true`
indicates the soft time cap stopped new discussion rounds. Writes use a temporary
JSON file and atomic replacement. A process killed with SIGKILL cannot finalize a
summary; the last saved snapshot and already-written per-call logs remain.

## Extending an adapter

For a new CLI with plain stdout or a final-message file, add an agent entry and use
an existing output mode. Confirm its noninteractive command and model flag locally.
Pipe a short prompt into it to learn whether it reads stdin; if it does, prefer
`prompt_via: stdin`, which avoids the argument-size limit. Do not introduce
permission-bypass flags merely to make a smoke test pass.

To add a CLI to the built-in catalog, add an `Adapter` to `ADAPTERS` in
`agent_council/adapters.py`. Take every flag from the CLI's documentation, source, or
installed `--help`, and record that evidence in `source`. Include a `--model {model}`
pair, choose the most restrictive noninteractive mode the CLI offers, and never a
blanket approval flag; `tests/test_adapters.py` validates every entry and rejects known
bypass flags. Then list it in `docs/adapters.md`, including how it was verified.

For a new structured format, add a parser to `agent_council/outputs.py` (it then
becomes a valid `output_mode`), and add tests for valid answers, explicit error
envelopes, malformed output, and empty answers.
Only normalize cost fields whose meaning and units are known. Never use diagnostic
stdout as a successful file-mode answer.

## Limits

Council checks process success, quorum, vote syntax, artifact structure, and build
command results. It does not mechanically verify claims, source credibility, checklist
completion, or test quality. Separate directories are not a security boundary.
Argument-delivered prompts are capped by the OS per-argument limit, and every
prompt by its provider's context window. Session time is a soft
budget. Model calls may use and modify files permitted by each CLI's own settings.
