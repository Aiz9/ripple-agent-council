# Configuration reference

Run `council --init-config` to create `~/.config/agent-council/agents.yaml`, respecting
`XDG_CONFIG_HOME`. The command never overwrites an existing configuration.

Configuration precedence is `--config PATH`, then `COUNCIL_CONFIG`, then the user
configuration, then bundled `agent_council/agents.yaml`. Configuration in the current
directory is not loaded automatically. Files are complete configurations, not merge
layers. An invalid user or explicit configuration fails rather than silently falling
back to defaults. Use `council --paths` to inspect selected locations.

For a project-specific variant, create `agents.local.yaml` and pass
`--config agents.local.yaml`; that filename is ignored by Git. Session location
precedence is `--sessions-dir`, then `COUNCIL_SESSIONS_DIR`, then
`$XDG_STATE_HOME/agent-council/sessions` (default `~/.local/state/agent-council/sessions`).
Relative or empty XDG base paths are ignored. Explicit CLI and `COUNCIL_*` paths may
be relative to the caller's working directory and support `~` expansion.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `mode` | `peer-to-peer` | Direct shared conversation or `hierarchical` leader/specialist workflow. CLI `--mode` overrides it. Terminal runs confirm it unless skipped. |
| `peer_turns` | `sequential` | Peer-to-peer rounds 2+: `sequential` turns see same-round replies; `parallel` turns run concurrently and see the conversation through the previous round. CLI `--peer-turns` overrides it. Ignored in hierarchical mode. |
| `leader` | configured framer | Enabled agent that frames, delegates, reviews, and synthesizes in hierarchical mode. |
| `max_rounds` | `3` | Discussion rounds, excluding framing and synthesis. Positive integer; CLI `--max-rounds` takes precedence. |
| `per_agent_timeout_sec` | `420` | Timeout for each agent call unless overridden by the agent. |
| `session_wallclock_cap_sec` | `2400` | Soft limit: stop starting discussion rounds once elapsed. Framing, in-flight calls, synthesis retries, and verification may extend total runtime. |
| `build_timeout_sec` | `300` | Maximum duration of the generated build verification command. |
| `min_agents` | `2` | Minimum enabled, available, and responding participants. Must be at least two. |
| `verify_build` | `true` | Extract and execute final builds. If false, build results are unverified and return exit 1. |
| `framer` | first enabled agent | Preferred peer-mode framing agent; must name an enabled agent. Hierarchical mode uses the leader. |
| `synthesizer` | first enabled agent | Preferred peer-mode final writer; alternatives are tried on failure. Hierarchical mode uses only the leader. |

Unknown settings, duplicate/unsafe agent names, invalid types, invalid timeouts,
unsupported output modes, and unusable placeholders are rejected before model calls.
In peer mode, an unavailable preferred framer is replaced by the first available
participant; framing call failure falls back to a generic checklist and task-type
heuristic. Hierarchical mode requires the selected leader and stops on its failure.

The wall-clock limit is not a token or money budget. Reported Claude costs are summed
over **all** calls, including framing, debate, and synthesis attempts. Missing cost
data is `null`; actual reported zero is `0`. Other adapters have no cost normalization.

## Agent fields

Use `role_preset` for a built-in or named custom role, or `role` for an inline
description. See [roles and coordination](roles-and-modes.md) for presets, reusable
custom definitions, `--setup`, and per-run overrides. Roles change instructions,
not models or permissions.

```yaml
agents:
  - name: reviewer
    enabled: true
    role: "Review assumptions and test edge cases."
    model: ""
    timeout_sec: 300
    output_mode: stdout
    env:
      EXAMPLE_SETTING: "a string value"
    argv: ["reviewer-cli", "--prompt", "{prompt}"]
```

Instead of writing every field, an entry can name a built-in template with
`adapter: <id>` (for example `adapter: opencode`); fields you set override the
template. See [agent adapters](adapters.md) for the catalog and merge rules.

An `argv` list is passed directly to the executable without a shell. Quotes and shell
metacharacters in the prompt remain part of one argument. The executable must be on
`PATH` or an absolute executable path. Use absolute paths for custom scripts too:
agent calls run from their own session working directory.

Names allow letters, digits, underscores, and hyphens. `enabled` defaults to true;
`model` defaults to an empty string; `output_mode` defaults to `stdout`;
`prompt_via` defaults to `argv`; `timeout_sec` inherits
`settings.per_agent_timeout_sec`. Environment keys and values must be strings.
Environment values expand `~` and the `{session}` placeholder.

| Standalone argv placeholder | Substitution |
| --- | --- |
| `{prompt}` | The complete prompt, including protocol and prior debate. Required with `prompt_via: argv`; rejected with `prompt_via: stdin`. |
| `{outfile}` | Absolute destination for a file-mode adapter's final answer. |
| `{model}` | Agent's `model` string. If empty, the token and its immediately preceding flag are removed. |

Placeholders must occupy their own list elements; `--prompt={prompt}` is rejected.
Only set `model` when the command includes `{model}`. An override that would be
silently ignored is rejected. For a CLI whose installed help confirms `--model`:

```yaml
model: "your-exact-model-id"
argv: ["reviewer-cli", "--model", "{model}", "--prompt", "{prompt}"]
```

The shipped commands omit model flags entirely. Model changes belong in the CLI's
own configuration unless you intentionally add a supported override.

## Prompt delivery

`prompt_via` chooses how each prompt reaches the agent:

| Value | Delivery | Size limit |
| --- | --- | --- |
| `argv` | The `{prompt}` token is replaced by the prompt, as one argument. | Linux caps one argument at 131,071 bytes (`MAX_ARG_STRLEN`, 32 pages), regardless of the 2 MiB total `ARG_MAX`. |
| `stdin` | The prompt is written to the process's standard input; `argv` must not contain `{prompt}`. | None. |

The limit matters because prompts grow during a run: every turn after the first, and
the final synthesis, include the complete debate history. Several verbose agents over
three or more rounds can pass 128 KiB, and the synthesis prompt is the largest of all.
Before launching an `argv` agent, Council checks the prompt size. An oversize prompt
fails that call immediately with an explanation instead of the kernel's
`Argument list too long`; the run continues while quorum holds. Council never
truncates the history to fit.

Use `stdin` whenever the CLI reads its prompt from standard input. Each shipped
agent was checked against the installed CLI on 2026-10-18:

| Agent | Delivery | Evidence |
| --- | --- | --- |
| `claude` | `stdin` | `claude -p --output-format json` with the prompt piped in answered normally. |
| `codex` | `stdin` | `codex exec -` reads its instructions from stdin (documented in `codex exec --help`, and tested). |
| `agy` | `argv` | `--print` takes the prompt as its value; `agy --print -` answered the literal `-`. |
| `hermes` | `argv` | `hermes -z -` answered the literal `-`; its CLI has no stdin prompt option. |

`council --doctor` reports each agent's delivery. To check a CLI yourself:

```bash
printf '%s' 'Reply with exactly READY.' | your-cli --its-noninteractive-flags
```

Configurations are complete files, not merged with the defaults, so a user
configuration created before 0.4.0 keeps argument delivery. To adopt stdin, edit
the two agents:

```yaml
  - name: claude
    prompt_via: stdin
    argv: ["claude", "-p", "--output-format", "json"]
  - name: codex
    prompt_via: stdin
    argv: ["codex", "exec", "--skip-git-repo-check", "-o", "{outfile}", "-"]
```

## Hermes model selection

The shipped Hermes adapter is:

```yaml
name: hermes
model: ""
output_mode: stdout
argv: ["hermes", "-z", "{prompt}"]
```

Hermes chooses its model from its own configuration (`model.default` and
`model.provider` in `~/.hermes/config.yaml`), which may point at a hosted provider or a
local model such as Qwen. Council deliberately leaves this choice to Hermes, which
preserves your provider, endpoint, authentication, and future model changes. Council
stores no provider URL or credentials and does not resolve provider aliases. To force
a model for Council runs only, add `"-m", "{model}"` to `argv` and set `model`.

To check the installed Hermes independently in a normal terminal:

```bash
hermes -z "Reply with exactly READY; do not use tools."
```

This makes a real model request. `./council --doctor` does not.

## Output adapters

| Mode | Success contract |
| --- | --- |
| `stdout` | Exit 0 and nonempty stdout. Used by `agy` and Hermes. |
| `claude_json` | Exit 0, valid JSON object, non-error envelope, and a nonempty string `result`. Optional finite nonnegative `total_cost_usd`. |
| `file` | Exit 0 and a nonempty file at `{outfile}`. Stdout is retained as diagnostics and never substituted for a missing answer. |
| `gemini_json` | One JSON object with a nonempty string `response` and no `error` field (Gemini CLI). |
| `qwen_json` | A JSON array whose last event is `type: result`, `subtype: success`, `is_error: false`, with a nonempty `result`; any error event fails (Qwen Code). |
| `opencode_json` | JSON lines; the answer is the text of the message ending in `step_finish` with reason `stop`, never text from earlier tool steps; any `error` event fails (OpenCode). |
| `cursor_json` | One JSON object with `type: result`, `subtype: success`, `is_error: false`, and a nonempty `result` (Cursor CLI). |
| `aider_history` | A complete final `LLM RESPONSE` record in the Aider history file written to `{outfile}`; requires `{outfile}` in argv. |

All modes retain stdout and stderr, including partial output on timeout. A stdout
adapter cannot distinguish a textual error printed with exit 0 from a real answer;
add a structured adapter if a CLI supports a suitable documented response schema.

## Claude isolation

The default environment uses `CLAUDE_CONFIG_DIR: "{session}/claude-config"`.
Each run gets its own directory, avoiding shared council configuration writes.
Council copies the following files when they exist:

- `~/.claude/.credentials.json`
- `~/.claude/settings.json`
- `~/.claude.json`, falling back to `~/.claude/.claude.json`

New copied files have owner-only permissions. Copies remain in the session and are
not synchronized back. Authentication refreshes may still interact because the
underlying account credentials are shared. Council does not guarantee full isolation
of CLI services, plugins, external directories, or account state.

Remove the `env` block if you want Claude to use its usual configuration directly.
Do not set a common copy destination for concurrent council sessions.

## Tool permissions

Each CLI keeps its own tool permissions. No permission-bypass flags are added.
`agy --sandbox` enables terminal restrictions but does not approve denied tools.
The orchestrator makes no automatic changes to user-wide permission settings.

Working directories are separated for file organization, not access control:
`agents/<name>/` for each participant and `workspace/` for final verification.
Agents can access other paths if their CLI permissions allow it. `--no-verify`
controls only the final extraction/execution stage.
