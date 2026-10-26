# Agent Council

Ask several authenticated coding-agent CLIs one question or build request. They answer
independently, challenge each other's evidence, and converge on a final synthesis.
For build requests, Council writes the final files and runs the agreed test command,
so "done" means the tests actually executed.

Council is a Python orchestrator, not a model API client. Authentication, model
selection, tool permissions, and provider charges all belong to the individual CLIs.

- [Quick start](#quick-start)
- [Participants](#participants)
- [How a run works](#how-a-run-works)
- [Usage](#usage)
- [Beyond a single run](#beyond-a-single-run)
- [Builds and execution safety](#builds-and-execution-safety)
- [Results and exit codes](#results-and-exit-codes)
- [Documentation](#documentation)
- [Development](#development)

## Quick start

Requires Linux, Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and at least two of
the agent CLIs below on `PATH`, each already authenticated.

```bash
git clone https://github.com/Aiz9/ripple-agent-council.git
cd ripple-agent-council
uv tool install .
council --version
council --doctor        # checks config and executables; makes no model calls
council --doctor --probe  # optional: one short request per agent to test sign-in
council --setup         # choose coordination mode, turn style, and agent roles once
council "Compare REST and GraphQL for a small team. Cite evidence."
```

`uv tool install` puts `council` on your user PATH in an isolated environment — no
`sudo`, no changes to system Python, and it works from any directory. If the shell
cannot find it, run `uv tool update-shell` and open a new terminal. Run
`uv tool install --reinstall .` after updating the source. See
[installation](docs/installation.md) for updates, migration, and uninstalling.

`--doctor` validates configuration and finds executables. It does **not** start the
agents, test credentials, or prove a provider is reachable; `--probe` does, with one
real model request per agent.

## Participants

| Agent | CLI and invocation | Default role | Prompt delivery | Model |
| --- | --- | --- | --- | --- |
| `claude` | Claude Code, `claude -p` | architect | stdin | CLI default |
| `codex` | OpenAI Codex, `codex exec -` | implementer | stdin | CLI default |
| `agy` | Google Antigravity, `agy --print` | researcher | argument (≤128 KiB) | CLI default |
| `hermes` | Nous Research Hermes Agent, `hermes -z` | skeptic | argument (≤128 KiB) | Hermes's configured provider and model |

Council never overrides a model: each CLI uses its own configured default, which is
not necessarily the newest model. Hermes keeps whichever provider and model you set up
for it (for example, a local Qwen model), and Council does not copy Hermes credentials.

**Prompt delivery matters for long debates.** Later turns and the final synthesis
carry the complete debate history. Linux limits a single command-line argument to
128 KiB, so Claude and Codex receive prompts on stdin, which has no such limit. `agy`
and Hermes only accept the prompt as an argument; if a prompt grows past the limit,
that call fails before launch with an explanation, and the run continues while quorum
holds. See [configuration](docs/configuration.md#prompt-delivery).

Roles change each agent's instructions only — never its model, authentication, or
permissions. Use `council --roles` to list the nine built-in roles.

### More agents

Council also ships opt-in adapters for **Gemini CLI, Qwen Code, OpenCode, GitHub
Copilot CLI, Cursor CLI, Goose, Kiro CLI, and Aider**. Install and sign in to any of
them, then pick the roster per run:

```bash
council --adapters                                     # what exists, and which executables are installed
council --agents claude,codex,opencode "your prompt"   # any mix of configured agents and adapters
council --setup --agents claude,codex,goose            # save a roster as your default
```

Each preset is conservative — ask, plan, chat, or read-only tool modes, never a blanket
approval flag. [Agent adapters](docs/adapters.md) lists every command, its permissions,
the credentials it needs, and how it was verified.

To add any other CLI, or a built-in adapter, without editing YAML:

```bash
council --add-agent
```

It asks for the command as you would type it (for example `ollama run llama3.2 {prompt}`),
how the CLI returns its answer, and a role. It validates every answer, can send one
test request through the real CLI, and saves the entry with a backup of your config.

## How a run works

```text
 your prompt
     │
     ▼
 0  Framing          one agent: task type + definition-of-done checklist
     │
     ▼
 1  Independent      every agent answers alone, in parallel, in its own directory
     │
     ▼
 2  Cross-exam       agents reply to each other by name and CHALLENGE weak claims
     │
     ▼
 3+ Resolution       evidence-based resolution; stops when every surviving agent
     │               ends with "CONSENSUS: yes", or at max_rounds (default 3)
     ▼
 (Peer ranking)      optional: agents blind-rank the anonymized final positions
     │
     ▼
 Synthesis           answer, agreements, unresolved dissent, confidence, checklist
     │
     ▼ build requests only
 Verification        write the FILE: blocks to a clean directory, run the RUN: command
```

Two coordination modes control how rounds 2+ are exchanged:

- **Peer-to-peer** (default): a shared conversation without a leader. Turns are
  **sequential** by default — agents speak one at a time and later speakers see
  earlier replies from the same round. Set **parallel** turns for speed: everyone
  answers at once, seeing the conversation through the previous round, so a round
  lasts as long as its slowest agent instead of the sum of all agents.
- **Hierarchical**: a leader frames the task, assigns work each round, reviews the
  specialists' parallel reports, and writes the synthesis.

Consensus requires at least `min_agents` successful responses and an unambiguous final
`CONSENSUS: yes` line from every surviving participant. A failed agent is recorded and
is skipped while unavailable; losing quorum saves and pauses the task. Explicit resume can retry recovered agents. Consensus is agreement among surviving
agents, **not proof that their claims are true** — evidence tags and checklist judgments
are agent-authored. Details: [roles and coordination](docs/roles-and-modes.md).

## Usage

```bash
council "your question or build request"
council --type build "Build a CSV-to-JSON Python CLI with unittest tests."
council --peer-turns parallel "Debate this design quickly."
council --agents claude,gemini,goose "Compare these two approaches."
council --mode hierarchical --leader claude "Build and verify a small CLI."
council --role hermes=security --custom-role 'codex=Benchmark every claim.' "Review this plan."
council --max-rounds 4 --quiet "your question"
council --prompt-file request.md
printf '%s\n' 'Explain this tradeoff' | council -
council --config agents.local.yaml --sessions-dir ./my-sessions "your question"
council --type build --no-verify "Draft a Python tool and its tests."
```

In a terminal, each run first shows your saved choices in one line — press Enter to
use them, or `n` to change them for this run (`council --setup` saves defaults).
`--mode` or `--non-interactive` skips the question; piped input never asks.

Agent text streams live as it is written, one labelled line at a time, for Claude,
`agy`, Hermes, and other plain-output CLIs; agents that write their answer to a file
(such as Codex) appear as a block when they finish. `--no-stream` shows whole turns
only, and `--quiet` keeps only status lines and the final answer; every transcript is
saved either way. See [observability](docs/observability.md#live-output).

`--type auto` (default) uses the framing agent's classification and falls back to a
keyword heuristic if framing fails. Pass `--type` when the distinction matters.

Every flag, environment variable, and exit code: [CLI reference](docs/cli-reference.md).

### Repository access and tool approvals

Council now gives agents your invoking directory as project context. Select another
directory, or several repositories, with `--workspace`:

```bash
council --workspace /path/to/projects -f request.md
council --workspace /path/to/first --workspace /path/to/second "Compare these repositories."
```

Claude and Antigravity receive explicit `--add-dir` arguments for these paths,
their own session directory, and the saved skills. Agents keep separate working
directories. With `--repo`, the default project context is each agent's worktree.

If a headless agent cannot obtain native tool permission, it can request a Council
tool instead. Council displays the exact file operation or command, working
directory, and reason, then asks in your terminal:

```text
Allow this exact operation? [y/N/q]:
```

`y` approves that operation once, `n` or Enter denies it, and `q` saves and pauses.
File reading, directory listing, and explicit command arguments are supported.
Completed operations are saved and reused on resume. An interrupted command with
an unknown outcome needs fresh approval before a retry. Without an interactive
terminal, Council saves and pauses until you resume in one. Use `--tool-approval
deny` to reject fallback requests in unattended runs.

Native tools retain their own permissions. This fallback does not intercept every
native tool call or disable any CLI sandbox. Approved commands run as your user
under the existing OS restrictions. See [tool access](docs/configuration.md#council-tool-access).

### Prompts from files

Save your request in a UTF-8 Markdown (`.md`) or text (`.txt`) file, then pass its
path with `--prompt-file` or `-f`:

```bash
council --prompt-file request.md
council -f request.txt
council -f "my request.md"              # quote paths containing spaces
council --type build --repo . -f spec.md
council --continue -f follow-up.md
```

Council reads the whole file as your prompt, including headings, lists, and code
blocks. Relative paths start from your current directory. UTF-8 files with a
byte-order mark are supported; empty files and invalid text are rejected before a
session starts. Use either a file or a typed prompt in one command.

The prompt text is saved with the session, so `council --resume` works even if you
later edit or delete the original file.

### Shared global skills for every agent

Install all four configured sources once in Council's global library:

- [Orchestra Research's AI Research Skills](https://github.com/Orchestra-Research/AI-Research-SKILLs)
- [Ponytail](https://github.com/DietrichGebert/ponytail)
- [Caveman](https://github.com/JuliusBrussee/caveman)
- [Graphify](https://github.com/Graphify-Labs/graphify)

```bash
council --install-skills
council --skills
council -f research-request.md
```

To install just one source, use `council --install-skills ponytail` (or
`orchestra-research`, `caveman`, `graphify`, or one of their GitHub URLs).
Repeating the all-source command keeps existing libraries and installs missing ones.

Every new run automatically gives every participant the shared skill catalog and
instructions to read and apply relevant skills. This works for the configured
Claude, Codex, Antigravity, and Hermes agents, built-in adapters, and custom CLIs.
Council supplies the instructions through each agent's prompt.

Ponytail and Caveman's core instructions are included directly for every agent when
installed. Domain workflows, including Graphify, are loaded as relevant. Style
instructions preserve Council's evidence, consensus markers, JSON, and full build
outputs. Native plugin UI, hooks, and proxy services are separate from the portable
skills; they are not installed by this command.

Council adapts agent-specific conventions into a shared workflow: native skill
paths point to the session's copies, tool names map to each runtime's available
tools, delegation uses Council peers, and `/loop` or OpenClaw continuity becomes
bounded work within Council's rounds. Upstream originals, references, scripts,
and licenses are retained. Tools and dependencies mentioned by a skill still need
to be available to the agent.

To always include particular skills' full instructions, add their names (shown by
`--skills`) to the top-level `skills` section of your user configuration:

```yaml
skills:
  enabled: true
  names: [peft-fine-tuning, vllm]
```

Use `--skill NAME` (repeatable) to override that selection for one run, or
`--no-skills` to disable it. The global library defaults to
`~/.local/share/agent-council/skills`, respecting `XDG_DATA_HOME`;
`--skills-dir PATH`, `skills.directory`, or `COUNCIL_SKILLS_DIR` can select another
location. With an existing local checkout, use `council --install-skills /path/to/AI-Research-SKILLs`.
Installation requires git and network access when downloading from GitHub. An
existing installation is never overwritten.

See [shared skills](docs/configuration.md#shared-skills) for portability rules,
selection, and session snapshots. After updating Council's source, reinstall it
with `uv tool install --reinstall .` before using the new flags on the global command.

## Beyond a single run

| Want to... | Use | Guide |
| --- | --- | --- |
| Share global skills across every agent | `--install-skills`, then ordinary runs | [shared skills](docs/configuration.md#shared-skills) |
| Pick a ready-made setup | `--preset quick\|deep\|blind\|safe-build\|hierarchy`, or your own | [presets](docs/steering.md#presets) |
| Let Council choose the agents | `--agents auto` (skips agents that are missing or recently rate-limited) | [automatic roster](docs/steering.md#automatic-roster) |
| Cap spending | `--max-cost 2` or `--max-tokens 500000`; pauses resumably | [budgets](docs/steering.md#budgets) |
| Weigh in during the run | `--human-review framing,rounds,synthesis,build` | [human checkpoints](docs/steering.md#human-checkpoints) |
| Reduce favoritism between agents | `--anonymous-review`: blind ranking before synthesis | [peer review](docs/steering.md#anonymous-peer-review) |
| Enforce rules on every answer | `guardrails:` in the configuration | [guardrails](docs/steering.md#guardrails) |
| Ask a follow-up | `--continue "and then?"` | [follow-ups](docs/steering.md#follow-up-questions) |
| See what happened | `--report`, `--serve`, `trace.jsonl`, OpenTelemetry | [observability](docs/observability.md) |
| Work on a real repository | `--repo .`: a worktree per agent, the verified build on a `council/<id>` branch | [git worktrees](docs/integrations.md#git-worktrees) |
| Call a council from another agent | `council --mcp` (MCP server with `ask_council`) | [MCP server](docs/integrations.md#mcp-server) |
| Check it beats one agent | `--eval cases.yaml --baseline claude --judge codex` | [evaluation](docs/integrations.md#evaluation) |

## Builds and execution safety

The synthesizer emits the deliverable in this form:

````text
FILE: test_example.py
```python
import unittest

class Example(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(2 + 2, 4)

if __name__ == "__main__":
    unittest.main()
```
RUN: python test_example.py
````

Council validates every path and fence **before** writing anything, then runs the single
`RUN:` line as a shell command from a build directory separate from every agent's working
directory. Empty files are supported. Missing files or commands, malformed fences, paths
escaping the build directory, duplicate paths, test failures, and timeouts all fail
verification, and the full command output is saved.

**Build verification executes generated code with your user permissions.** Separate
directories prevent routine collisions; they are not a security sandbox. `--no-verify`
skips the orchestrator's extraction and execution (the build then exits 1 as
`build_unverified`), but agents may still use whatever tools their own CLI
configuration allows. The shipped configuration adds no permission-bypass flags. A
zero exit code from the test command is recorded evidence, not proof of good coverage.

## Results and exit codes

### Continue after limits or interruptions

```bash
council --sessions
council --resume latest
# or: council --resume /path/to/sessions/<id>
```

Completed responses are checkpointed immediately, including responses from a parallel
round that is still running. Resume restores the saved goal, configuration, workspace,
and debate; matching completed calls are reused. Temporary network failures receive
bounded retries. A limited agent is skipped while the others continue, and an
unavailable leader is replaced with a recorded successor. If quorum cannot be met,
the task pauses with exit 75 and a resume command instead of being marked complete.

An interrupted build command is not silently repeated. Inspect its outcome, then use
`council --resume <id> --retry-build` if it should run again. See
[recovery](docs/recovery.md) for crash behavior and guarantees.

Each run prints its session directory, by default under
`~/.local/state/agent-council/sessions/` (`council --paths` shows yours). It holds
`final.md` (answer, status, drops, build result), `session.json` (machine-readable
progress), the exact prompt, stdout, stderr, and parsed answer for every call, and the
build directory. The full layout is in the [CLI reference](docs/cli-reference.md#session-files).

**Sessions can contain private prompts, command output, and copied Claude credentials.**
They are created owner-only; do not publish them wholesale.

| Exit | Meaning |
| --- | --- |
| `0` | Question synthesis completed, or a build's test command passed. Consensus may still be false. For `--doctor`: enough executables were found. |
| `1` | Build verification failed or was skipped; the saved task remains resumable. |
| `2` | Invalid input/configuration, an invalid checkpoint, or a session already running elsewhere. |
| `75` | Task paused: too few available agents, exhausted synthesis/leadership candidates, a reached budget, a human checkpoint, runtime error, or uncertain build outcome. Work is saved. |
| `130` | Interrupted by SIGINT or SIGTERM; available transcripts and a summary are saved. |

## Documentation

| Guide | Covers |
| --- | --- |
| [Installation](docs/installation.md) | Global install, updates, user paths, migration from the old checkout, uninstall. |
| [CLI reference](docs/cli-reference.md) | Every flag, environment variable, exit code, and session file. |
| [Recovery](docs/recovery.md) | Limits, retries, leader failover, durable checkpoints, abrupt exits, and resuming work. |
| [Agent adapters](docs/adapters.md) | Opt-in Gemini CLI, Qwen Code, OpenCode, Copilot, Cursor, Goose, Kiro, and Aider adapters. |
| [Configuration](docs/configuration.md) | Settings, agent fields, prompt delivery, output adapters, Hermes/Qwen, Claude isolation, permissions. |
| [Steering a run](docs/steering.md) | Presets, automatic roster, budgets, human checkpoints, anonymous peer review, guardrails, follow-ups. |
| [Observability](docs/observability.md) | Live streaming, usage, traces and OpenTelemetry, HTML reports, the session viewer. |
| [Integrations](docs/integrations.md) | MCP server, git worktrees, evaluation against single agents. |
| [Roles and coordination](docs/roles-and-modes.md) | Built-in and custom roles, setup, peer turn styles, hierarchy, automation. |
| [Troubleshooting](docs/troubleshooting.md) | Dropped agents, argument-size limits, Claude sign-in, `agy` tool denials, build failures. |
| [Development](docs/development.md) | Architecture, control flow, offline tests, CI and releases, adding an adapter. |
| [Changelog](CHANGELOG.md) | Changes and behavior changes by version. |

## Development

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
./council --doctor          # source launcher; same user paths as the installed command
```

The tests drive deterministic fixture CLIs through real subprocesses. They need no
credentials, make no model calls, and therefore do not validate live provider behavior.
See [development](docs/development.md).
