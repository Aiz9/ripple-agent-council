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
| `agent_retries` | `1` | Additional attempts for transient network/timeout failures. Nonnegative integer. Capacity/authentication failures rotate immediately instead. |
| `retry_delay_sec` | `1` | Positive initial exponential retry delay, capped at 30 seconds per wait. Interruptible with SIGINT/SIGTERM. |
| `session_wallclock_cap_sec` | `2400` | Soft limit: stop starting discussion rounds once elapsed. Framing, in-flight calls, synthesis retries, and verification may extend total runtime. |
| `build_timeout_sec` | `300` | Maximum duration of the generated build verification command. |
| `min_agents` | `2` | Minimum enabled, available, and responding participants. Must be at least two. |
| `verify_build` | `true` | Extract and execute final builds. If false, build results are unverified and return exit 1. |
| `framer` | first enabled agent | Preferred peer-mode framing agent; must name an enabled agent. Hierarchical mode uses the leader. |
| `synthesizer` | first enabled agent | Preferred peer-mode final writer; alternatives are tried on failure. Hierarchy uses the effective leader, which can fail over. |
| `max_cost_usd` | none | Stop starting calls and pause once reported cost reaches this many USD. CLI `--max-cost`. See [budgets](steering.md#budgets). |
| `max_tokens` | none | The same for metered tokens. CLI `--max-tokens`. |
| `anonymous_review` | `false` | Blind peer ranking of final positions before synthesis. CLI `--anonymous-review`. See [anonymous peer review](steering.md#anonymous-peer-review). |
| `human_review` | `[]` | Checkpoints that pause for you: any of `framing`, `rounds`, `synthesis`, `build`. CLI `--human-review`. See [human checkpoints](steering.md#human-checkpoints). |
| `tool_approval` | `ask` | Council fallback tools ask for explicit terminal approval. `deny` rejects requests without execution. CLI `--tool-approval` overrides it. Native tools retain their own policies. |
| `max_tool_requests` | `8` | Maximum fallback tool requests per agent turn, excluding the final answer. Intermediate model calls count toward reported usage and budgets. Positive integer. |
| `tool_timeout_sec` | `60` | Timeout for an approved Council command. Cancellation and timeout terminate its process group. Positive number. |

Unknown settings, duplicate/unsafe agent names, invalid types, invalid timeouts,
unsupported output modes, and unusable placeholders are rejected before model calls.
In peer mode, an unavailable preferred framer is replaced by the first available
participant; framing call failure falls back to a generic checklist and task-type
heuristic. Hierarchical mode records a successor when its leader becomes unavailable.
If no viable council remains, the task pauses for later recovery rather than lowering
the required quorum. See [recovery](recovery.md).

The wall-clock limit is not a token or money budget; use `max_cost_usd` or
`max_tokens` for that. Reported costs and tokens are summed over **all** calls,
including framing, debate, retries, and synthesis attempts. Missing cost data is
`null`; actual reported zero is `0`. Only Claude Code (tokens and cost) and Codex
(tokens) report usage; see [usage](observability.md#usage).

## Other top-level keys

Besides `settings`, `agents`, and `roles` (named custom roles, see
[roles](roles-and-modes.md)), a configuration may contain:

| Key | Contents | Reference |
| --- | --- | --- |
| `presets` | named bundles of settings, roster, and roles | [presets](steering.md#presets) |
| `guardrails` | rules every answer is checked against | [guardrails](steering.md#guardrails) |
| `skills` | shared skill library and the selection every agent receives | [shared skills](#shared-skills) |

## Shared skills

Run `council --install-skills` once to install these sources into separate folders
under `$XDG_DATA_HOME/agent-council/skills` (default `~/.local/share/agent-council/skills`):

| Source name | Repository |
| --- | --- |
| `orchestra-research` | [Orchestra Research / AI-Research-SKILLs](https://github.com/Orchestra-Research/AI-Research-SKILLs) |
| `ponytail` | [DietrichGebert / ponytail](https://github.com/DietrichGebert/ponytail) |
| `caveman` | [JuliusBrussee / caveman](https://github.com/JuliusBrussee/caveman) |
| `graphify` | [Graphify-Labs / graphify](https://github.com/Graphify-Labs/graphify) |

`--install-skills all` has the same behavior. A source name, its GitHub URL, or a
local checkout path installs only that source. Repeating the all-source command
validates and keeps existing sources, then installs any missing ones. An explicit
single-source installation never overwrites an existing source.

Canonical skill documents, references, scripts, shared resources, licenses, and
notices are preserved, with source/revision provenance for downloaded repositories.
Native duplicate adapters, translations, and Caveman's generated per-host copies
are excluded from the installed catalog. Both `SKILL.md` and `skill.md` are supported.
The installer uses git, with no model calls or execution of upstream installers or
skill scripts. `--install-skills /path/to/checkout` supports offline installation.

Shared skills are enabled by default, including in older agent configurations
that omit `skills`. An absent default library permits ordinary runs without skills.
An explicitly configured path or named skill must exist; errors occur before any
session or model call. `council --skills` shows the installed names and descriptions;
`*` marks configured skills.

```yaml
# Top-level section in your complete user configuration:
skills:
  enabled: true
  directory: null                 # optional override of the global library path
  names: ["*"]                     # make every installed skill available
```

| Field / option | Behavior |
| --- | --- |
| `enabled` | Boolean, default `true`. `--no-skills` disables shared skills for one run. |
| `directory` | Library root, supporting `~` and paths relative to the caller's directory. Precedence: `--skills-dir`, configuration, `COUNCIL_SKILLS_DIR`, XDG data default. |
| `names` | Distinct frontmatter names from `--skills`, or `["*"]` (default) for every skill. `[]` selects none. |
| `--skill NAME` | Repeat to select specific skills for one run, overriding `names` and enabling skills. Mutually exclusive with `--no-skills`. Combine with `--setup` to save the selection. |

With `["*"]`, each agent gets a catalog containing names, descriptions, and absolute
session paths, and instructions to read and apply the skills relevant to its work.
This avoids including the entire research library in every turn. Explicitly named
skills are also included in full in every prompt, so their instructions are visible
even when a CLI cannot read files. Supporting resources still require file tools.
Ponytail and Caveman's core skills are included in full with the wildcard selection
as well, so their general coding and prose guidance applies by default. Selecting
specific names overrides that default. Their style rules cannot compress protocol
markers, evidence, JSON, code, or complete generated build files.
Large explicit selections can exceed argument-delivery agents' limits; Council's
normal prompt-size checks apply. Use the catalog or stdin delivery for large libraries.

### Portability across agents

Council generates portable session copies of `SKILL.md`, keeps `SKILL.original.md`,
and includes the same compatibility instructions for every configured agent, including
custom adapters. Domain code examples remain intact. Known native paths under
`.claude`, `.codex`, `.gemini`, `.hermes`, `.agents`, `.cursor`, `.qwen`, `.kiro`,
`.goose`, `.copilot`, `.aider`, `.config/opencode`, and `.gemini/antigravity` are
translated to the selected skills' session paths. Skill names and directory aliases
are both recognized. Other cross-skill paths are resolved using the shared catalog.

Native `Skill` or slash-skill activation becomes reading the shared document;
`Read`/`Write`/`Edit`/`Bash`, planning, web search, and fetch names are interpreted
using the current agent's actual tools. `Task` and native subagents become requests
to configured Council peers under the chosen coordination mode. Human questions
go through Council's human checkpoints. The upstream autoresearch continuity section
is replaced with bounded work and saved progress; other `/loop`, cron, and heartbeat
examples are interpreted under that same rule. Council retains control of rounds,
timeouts, budgets, and resume.

Graphify's native-only semantic extraction requirement becomes chunked work with
Council peers, with bounded local processing when peer results are unavailable.
Its system-Python installation example becomes an import check and a project or
isolated environment dependency step. Image processing uses the current agent's
vision tools where available. The Graphify Python package is a runtime dependency
of graph workflows and is not installed by the skill installer.

Ponytail's UI bands, Caveman's proxy and hooks, persistent native modes, and other
host integrations are optional: the portable documents work without claiming those
services are present. Caveman workflows can read the preserved shared
`skills/native-core.md` from their library snapshot.

The adapter instructions apply to supporting references as well. They cannot supply
a missing browser, MCP server, dependency, credential, or denied permission. Agents
must report these limits. Skill instructions stay subordinate to the user's request,
Council's protocol, and the runtime's permissions.

Each new session copies the configured skills and their resources into `skills/`
and records paths, selected names, and document hashes in `skills.json`. Every
phase and agent receives that saved context, including blind ranking and synthesis.
Evaluation baselines and judges receive the configured skills too. A resumed
session uses its adapted copies even after the original library changes or is
removed; changing skill flags during `--resume` is rejected. Old sessions without
`skills.json` retain their original prompts. Libraries must use regular directories
and files rather than symlinks.

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

The easiest way to add an agent is `council --add-agent`, which asks for the command,
writes a valid entry, and can test it ([CLI reference](cli-reference.md#maintenance-commands)).

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
| `claude_stream_json` | The same contract applied to the last `type: result` event of Claude Code's `--output-format stream-json --verbose` JSON lines. Text deltas (with `--include-partial-messages`) are shown live. Used by the bundled `claude` agent. |
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
`agents/<name>/` for each participant and `workspace/` for final verification (with
`--repo`, git worktrees and a `build/` worktree; see [git worktrees](integrations.md#git-worktrees)).
Agents can access other paths if their CLI permissions allow it. `--no-verify`
controls only the final extraction/execution stage.

## Council tool access

New sessions use the invoking directory as their project context, or the repeatable
`--workspace PATH` directories. Every selected path must exist and is stored as an
absolute path in `run.json`. Agents are told where to start inspection rather than
searching the user's home directory. Each agent still runs in its own session
directory. `--repo` uses the separate worktrees by default; explicit `--workspace`
paths can add other source directories.

Council supplies the installed Claude and Antigravity CLIs with documented
`--add-dir` flags for the project, the agent's own directory, and saved skills.
Codex's `--add-dir` grants write access, so Council does not add that flag for source
inspection. Unsupported/custom CLIs get the same explicit project paths in their
prompts and can use the fallback below.

An agent whose native tools are denied can return a single `council-tool` fenced
JSON request. Only an entire response consisting of that block is recognized;
examples quoted inside prose are not executed. Supported operations are `shell`
with explicit `argv` and `cwd`, `read_file` with `path`, `start_line`, and
`max_lines`, and `list_dir` with `path`. Every request needs a reason. Paths must
be absolute and resolve inside the selected workspaces, agent directories, saved
skills, or build workspace. Outside paths are rejected, including symlink escapes.

With `tool_approval: ask`, Council serializes requests and displays the requesting
agent, phase, reason, exact operation, and command timeout. It waits for `y`/`yes`,
`n`/`no`/Enter, or `q`. No response or missing terminal grants no permission.
Approvals are for one exact operation in one turn; repeating an identical request
reuses the recorded result. `q` stops further approvals and saves the session.
Ctrl+C cancels pending input or an executing command and preserves recoverable state.

Commands are passed directly to a subprocess, with no implicit shell. Shell
scripts must explicitly request the shell and complete script as argv, which the
user reviews. Approval executes as Council's user under the existing OS limits;
the directory check is not a shell sandbox or a network isolation boundary.
Native CLI tool policies are unchanged. This feature is a shared fallback, not a
replacement for each CLI's native approval protocol.

Requests and results are checkpointed under `tool-calls/`. Completed operations
are never automatically repeated on resume. An operation saved as running has an
uncertain outcome and requires a new approval before retry. A missing terminal
or closed input pauses resumably with exit 75 and `awaiting_tool_approval`;
`--tool-approval deny` returns a denied result instead. MCP and piped invocations
therefore pause rather than silently approving tools. Resume from a normal terminal.
`--non-interactive` skips startup configuration questions; it does not approve
tools or suppress a tool-approval question in an available terminal. Use
`--tool-approval deny` when no fallback tool execution is wanted.

Tool stdout/stderr are limited to 20,000 characters each; file reads are bounded to
1 MB and at most 1,000 lines, and directory results to 1,000 entries. Results mark
truncation. Known secret environment values and common credential prefixes are
scrubbed from broker results; this is not comprehensive secret detection. Never
embed credentials in requests. Existing native transcripts still follow the
runtime's output behavior.

Each tool result goes back to the requesting agent as untrusted evidence in an
additional model turn, subject to the normal Council budgets. These turns have
`-toolN` transcript tags. Old sessions without saved tool/workspace options retain
their original prompts. A currently running process does not pick up code changes;
reinstall Council and start a new run to use this feature.
