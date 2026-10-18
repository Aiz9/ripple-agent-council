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
- [Builds and execution safety](#builds-and-execution-safety)
- [Results and exit codes](#results-and-exit-codes)
- [Documentation](#documentation)
- [Development](#development)

## Quick start

Requires Linux, Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and at least two of
the agent CLIs below on `PATH`, each already authenticated.

```bash
git clone https://github.com/Aiz9/Agent-council.git
cd Agent-council
uv tool install .
council --version
council --doctor        # checks config and executables; makes no model calls
council --setup         # choose coordination mode, turn style, and agent roles once
council "Compare REST and GraphQL for a small team. Cite evidence."
```

`uv tool install` puts `council` on your user PATH in an isolated environment — no
`sudo`, no changes to system Python, and it works from any directory. If the shell
cannot find it, run `uv tool update-shell` and open a new terminal. Run
`uv tool install --reinstall .` after updating the source. See
[installation](docs/installation.md) for updates, migration, and uninstalling.

`--doctor` validates configuration and finds executables. It does **not** start the
agents, test credentials, or prove a provider is reachable.

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
does not rejoin; losing quorum stops the run. Consensus is agreement among surviving
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

Each completed agent response prints as a block as soon as it finishes, followed by the
final answer. This is response-level output, not token streaming. `--quiet` keeps only
status lines and the final answer; every transcript is saved either way.

`--type auto` (default) uses the framing agent's classification and falls back to a
keyword heuristic if framing fails. Pass `--type` when the distinction matters.

Every flag, environment variable, and exit code: [CLI reference](docs/cli-reference.md).

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
| `1` | Lost quorum, leader failure, synthesis failure, runtime error, time limit before discussion, or failed/skipped build verification. |
| `2` | Invalid input or configuration, too few executable agents, or an unavailable hierarchical leader at startup. |
| `130` | Interrupted by SIGINT or SIGTERM; available transcripts and a summary are saved. |

## Documentation

| Guide | Covers |
| --- | --- |
| [Installation](docs/installation.md) | Global install, updates, user paths, migration from the old checkout, uninstall. |
| [CLI reference](docs/cli-reference.md) | Every flag, environment variable, exit code, and session file. |
| [Agent adapters](docs/adapters.md) | Opt-in Gemini CLI, Qwen Code, OpenCode, Copilot, Cursor, Goose, Kiro, and Aider adapters. |
| [Configuration](docs/configuration.md) | Settings, agent fields, prompt delivery, output adapters, Hermes/Qwen, Claude isolation, permissions. |
| [Roles and coordination](docs/roles-and-modes.md) | Built-in and custom roles, setup, peer turn styles, hierarchy, automation. |
| [Troubleshooting](docs/troubleshooting.md) | Dropped agents, argument-size limits, Claude sign-in, `agy` tool denials, build failures. |
| [Development](docs/development.md) | Architecture, control flow, offline tests, adding an adapter. |
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
