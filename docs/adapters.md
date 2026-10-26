# Agent adapters

Besides the four default agents, Council ships **opt-in adapters** for widely used
agent CLIs. An adapter is a configuration template: the noninteractive command, how
the prompt is delivered, how the answer is parsed, and a conservative permission
setup. Install and sign in to a CLI yourself — Council never installs software,
tests credentials, or changes a CLI's own settings.

```bash
council --adapters                                     # the catalog, and which executables are installed
council --agents claude,gemini,opencode "your prompt"  # choose the roster for one run
council --setup --agents claude,codex,goose            # save a roster, then choose roles and mode
council --adapters --probe                             # also send each installed one a READY request
```

`--probe` is the only adapter command that calls models: one short request per
installed adapter, in parallel, reporting `READY` or why it failed (for example
`[authentication]` when a CLI is signed out). `council --doctor --probe` does the same
for the agents in your configuration.

## Catalog

| ID | CLI | Command (prompt, output) | Default role | Permission posture |
| --- | --- | --- | --- | --- |
| `claude` | Claude Code | `claude -p --output-format stream-json --verbose --include-partial-messages`; stdin; JSON lines, streamed live | architect | CLI's own permissions; isolated config copy |
| `codex` | OpenAI Codex | `codex exec … -o {outfile} -`; stdin; final-message file | implementer | CLI's own sandbox and approvals |
| `agy` | Google Antigravity | `agy --print {prompt} --sandbox`; argument; text | researcher | `--sandbox` restricts the terminal; tool requests can be denied headlessly |
| `hermes` | Hermes Agent | `hermes -z {prompt}`; argument; text | skeptic | Hermes's configured provider, model, and tools |
| `gemini` | Gemini CLI | `gemini --approval-mode default --output-format json -p {prompt}`; argument; JSON object | researcher | Default approval: tools that need approval fail |
| `qwen` | Qwen Code | `qwen --approval-mode default --output-format json -p {prompt}`; argument; JSON event array | implementer | Default approval: tools that need approval fail |
| `opencode` | OpenCode | `opencode run --agent plan --format json`; stdin; JSONL events | architect | Built-in `plan` agent; permission requests are rejected, not prompted |
| `copilot` | GitHub Copilot CLI | `copilot -p {prompt} -s --no-ask-user --available-tools=view,grep,glob`; argument; text | reviewer | Only read-only `view`, `grep`, `glob` tools exist for the model |
| `cursor` | Cursor CLI | `agent -p --mode ask --output-format json {prompt}`; argument; JSON result | reviewer | Ask mode |
| `goose` | Goose | `goose run --no-session --quiet --output-format text -i -`, `GOOSE_MODE=chat`; stdin; text | researcher | Chat Only mode: no extensions or file modifications |
| `kiro` | Kiro CLI | `kiro-cli chat --v3 --no-interactive --trust-tools=read_file,grep_search`; stdin; text | reviewer | Only `read_file` and `grep_search` are auto-approved |
| `aider` | Aider | `aider --chat-mode ask --dry-run --no-git --no-auto-commits --no-stream … --message {prompt}`; argument; LLM history file | reviewer | Ask mode never edits; dry run, no Git, no auto-commits |

Every adapter also carries a `--model {model}` (Hermes: `-m {model}`) pair. With the
default empty `model`, that pair is removed and the CLI uses its own configured model;
set `model` to override it. No adapter passes a blanket approval flag (`--yolo`,
`--allow-all-tools`, `--trust-all-tools`, `--force`, `--auto`, or similar); a test
enforces this.

"Argument" adapters receive the prompt as one command-line argument, which Linux caps
at 131,071 bytes; see [prompt delivery](configuration.md#prompt-delivery). When
`--agents` picks the roster and the configured synthesizer is not in it, Council makes
the first stdin adapter the synthesizer, because the synthesis prompt is the largest.

## Using adapters in configuration

An agent entry with `adapter:` expands to the template before validation. Any field you
set wins over the template:

```yaml
agents:
  - adapter: opencode                 # the full built-in template
  - adapter: goose
    name: goose-local                 # names must still be unique
    model: llama3
    role: "Check numerical claims."   # replaces the template's role_preset
    env:
      GOOSE_PROVIDER: ollama          # merged; GOOSE_MODE=chat is kept unless you set it
  - name: claude                      # explicit entries without adapter: still work
    argv: ["claude", "-p", "--output-format", "json"]
    prompt_via: stdin
    output_mode: claude_json
```

An unknown adapter ID is a configuration error. `--agents` accepts configured agent
names first and built-in adapter IDs second, so a configured `claude` entry takes
precedence over the `claude` template. `--agents` changes only that run; combined with
`--setup`, the selected roster is saved as complete, expanded entries.

## Notes per adapter

**Gemini CLI.** Google replaced Gemini CLI with Antigravity (`agy`) in June 2026, and
the CLI no longer accepts a personal Google sign-in, so this adapter needs an API key or
Vertex AI credentials configured for Gemini CLI. Its JSON is one object; Council takes
`response` and treats any `error` field as a failure.

**Qwen Code.** A separate CLI from Hermes, even though Hermes here also uses a Qwen
model. Its JSON is an array of events; only a final successful `result` event counts.

**OpenCode.** Uses the built-in `plan` agent. In `opencode run`, permission requests
are rejected instead of prompted unless `--auto` is passed, which this adapter never
does. Council accepts only text from the message that ends with
`step_finish` reason `stop`. A run whose last message is empty fails rather than
reusing earlier text.

**GitHub Copilot CLI.** `-s` prints only the agent's response. `--available-tools`
limits the model to the read-only `view`, `grep`, and `glob` tools, so it has no shell
or edit tools. Sign in through Copilot CLI first.

**Cursor CLI.** The executable is named `agent`, a generic name: check that
`command -v agent` is Cursor's before enabling it, and set `argv[0]` if yours has a
different name (older installs used `cursor-agent`). Headless use accepts an API key
through `CURSOR_API_KEY`. Success is one `result` object; on failure Cursor prints no
JSON, which Council records as a failure.

**Goose.** `GOOSE_MODE=chat` is Goose's Chat Only mode: it answers without extensions
or file modifications. `--quiet` prints only the model response. The provider and model
come from Goose's configuration unless you set `model` or `GOOSE_PROVIDER`.

**Kiro CLI.** Headless mode requires a `KIRO_API_KEY` environment variable, available on
Kiro's paid plans. Only the `read_file` and `grep_search` tools are auto-approved. This
is a stdout adapter, so everything Kiro prints on stdout becomes the answer.

**Aider.** Aider auto-commits changes by default; this adapter runs ask mode ("never
make changes"), `--dry-run`, `--no-git`, and `--no-auto-commits`, and disables
streaming. Council does not treat Aider's console output as the answer, since it mixes
in status and warning messages; it reads the answer from `--llm-history-file` and
accepts only a complete final response record, never an earlier one.

## Verification status

| Adapters | How the command was verified (2026-10-18) |
| --- | --- |
| `claude`, `codex`, `agy`, `hermes` | Run against the installed CLIs during development: prompt delivery, output, and `--model` flags checked. |
| `gemini`, `qwen`, `copilot`, `cursor`, `goose`, `kiro`, `aider` | Flags, prompt input, and output formats checked against each project's official documentation. The CLIs were not installed, so these commands have not been run. |
| `opencode` | Documentation plus OpenCode's source (`packages/cli/src/run`), for stdin input, JSON events, and permission handling. Not run. |

Each template's `source` field in `agent_council/adapters.py` names its evidence.
Council's parsers are tested against fixtures that reproduce each documented format,
but a CLI release can change its flags or output. Before relying on an adapter, run a
short check and read its round files:

```bash
council --agents claude,gemini --max-rounds 1 --non-interactive "Reply with one sentence about SQLite."
```

To add a CLI that is not in the catalog, run `council --add-agent`, which asks for the
command and can test it, or write an explicit agent entry; see
[configuration](configuration.md#agent-fields) and [development](development.md#extending-an-adapter).
