# Troubleshooting

Start with `council --doctor`. It catches configuration and executable discovery
problems without making model requests. For runtime problems, the session path is
printed before framing starts.

## Command not found after installation

Run `uv tool list` to check that `ripple-agent-council` (before 0.8.0: `agent-council`) is installed, then
`uv tool update-shell` and open a new terminal if the tool executable directory is
missing from PATH. `command -v council` identifies the active executable;
`council --version` identifies its version. `council --paths` shows where it reads
configuration and writes sessions. See [installation](installation.md) for migration
from the earlier symlink-based setup.

## An agent is dropped

Open its `rounds/<phase>.<agent>.stderr.txt`, `.stdout.txt`, and `.md` files.
`session.json` has every failure in `drop_history`; `dropped` retains the last failure
per agent, even if a later call succeeds. Preflight failures have metadata entries
but no per-call logs because no process was launched.

An agent can fail because of authentication, permissions, missing binaries, invalid
structured output, an empty final message, or a timeout. Council continues only
while the configured quorum remains. A failed discussion agent is excluded from
subsequent rounds in that invocation. Transient failures get bounded retries; capacity
limits rotate immediately to avoid hammering an exhausted account. Peer synthesis
and hierarchical leadership can fall back to another participant. If no viable council
remains, the task pauses. Inspect its `planN.*` assignment or `roundN.*` review logs,
then run `council --resume latest` after restoring availability.

## Continue a paused or abruptly terminated task

Use `council --sessions` to inspect saved tasks and `council --resume <id>` (or
`--resume latest`) to continue. The task's saved configuration is used even if the
original YAML moved or changed. No new prompt is needed. Resume retries unfinished
work and reuses completed calls with identical input. Sessions created before 0.7.0
lack the necessary `run.json` snapshot and cannot be resumed automatically.

An uncertain build command requires `--retry-build`; it is never repeated just by
resuming. Failed verification can be retried explicitly after repairing the workspace.
See [recovery](recovery.md) for process cleanup and the in-flight request limitation.

## Startup asks questions in a script

In a terminal, Council asks one `Use these choices? [Y/n]` question before each run.
Use `--non-interactive` to retain saved choices or `--mode peer-to-peer` /
`--mode hierarchical` to select a mode without asking. `--quiet` controls the
discussion display, not the question. Piped input automatically skips it.
`--setup` requires a terminal; use the config file or role flags for automation.

## "Prompt is N bytes but Linux limits one command-line argument"

That agent receives its prompt as a command-line argument (`prompt_via: argv`), and
the prompt — which includes the complete debate so far — has passed Linux's
131,071-byte limit for a single argument. Council refused to launch the call rather
than let it fail with `Argument list too long`. The run continues while quorum holds.

- If the CLI can read its prompt from stdin, set `prompt_via: stdin` and remove
  `{prompt}` from its `argv`. See [prompt delivery](configuration.md#prompt-delivery).
- Otherwise, lower `--max-rounds`, ask for shorter answers in your prompt, or use
  `--peer-turns parallel`, whose turns do not include same-round replies.
- Keep a stdin-capable agent as `synthesizer`: the final synthesis prompt is the largest.

The shipped `agy` and Hermes commands are argument-only. A user configuration created
before 0.4.0 also sends Claude and Codex prompts as arguments; `council --doctor` shows
each agent's delivery.

## Claude: "OAuth session expired and could not be refreshed"

Council copies your Claude credentials into a fresh, session-specific
`CLAUDE_CONFIG_DIR` at the start of every run. If the source credentials in `~/.claude`
are expired or signed out, the copy fails the same way. Start `claude` in a normal
terminal, sign in again if asked (or use `/login`), then rerun Council.

The same message appears when a copied configuration directory is reused across runs:
once your interactive Claude session refreshes its credentials, the old copy's refresh
token stops working. Keep the default `{session}/claude-config` path rather than a
fixed shared directory. A very long run can still be affected if your interactive
session refreshes its credentials mid-run; rerunning creates a fresh copy.

## An adapter agent is missing or fails

`council --adapters` shows whether each adapter's executable is on `PATH`; `--doctor`
does the same for the current roster. Neither checks credentials. Common causes:

- **Not signed in, or no API key.** Gemini CLI no longer accepts personal Google
  sign-in (use an API key or Vertex AI); Kiro needs `KIRO_API_KEY`; Cursor accepts
  `CURSOR_API_KEY`. Run the CLI once yourself in a terminal to confirm it works.
- **Wrong executable.** Cursor's CLI is named `agent`; make sure `command -v agent` is
  Cursor's, or set the agent's `argv[0]` to the right path.
- **"…output…" or "…result is not successful" errors.** The CLI returned an error
  envelope or output in an unexpected format. Read that call's `.stdout.txt` and
  `.stderr.txt`; a CLI update may have changed its flags or format. See the
  verification notes in [agent adapters](adapters.md#verification-status).
- **Denied tools.** Presets use ask, plan, chat, or read-only modes, so an agent that
  tries to edit files or run commands is refused. That is intended; the agent should
  answer from what it can read.

## agy works interactively but fails in Council

Start with `council --workspace /path/to/your/projects -f request.md`. Council now
passes `--add-dir` explicitly, so Antigravity can register the project and Claude
can access files outside its otherwise empty session directory. Regular runs use
the invoking directory by default.

Headless CLIs cannot display their usual native interactive approval dialogs.
Council now gives every agent a shared fallback: return a `council-tool` request,
then Council asks you to approve that exact operation in its own terminal. A
missing terminal saves and pauses for `council --resume SESSION`. Choose `y` to
allow once, `n` to deny, or `q` to save and stop. See
[Council tool access](configuration.md#council-tool-access).

Test the exact configured invocation from a normal terminal:

```bash
agy --print "Reply with READY without calling tools." --sandbox
```

Antigravity documents scoped `permissions.allow` rules in its
[headless guide](https://www.antigravity.google/docs/cli/headless/), while reports in
its [permission issue](https://github.com/google-antigravity/antigravity-cli/issues/548)
show version-dependent differences in which settings layer applies. Council does
not rewrite those global settings or add blanket bypass flags. If the CLI exits
before it can issue a fallback request, use its interactive `/permissions` UI to
approve the specific operation and retry, or select another configured agent.

Recognized native tool-permission errors get one additional model turn instructing
the agent to return a Council fallback request instead of retrying the native tool.
Authentication failures and model refusals are not reinterpreted as tool permissions.
This retry is subject to the same model-call budgets and tool-turn limit.

## Hermes reports an installation-lock error

If even `hermes --help` fails because the launcher cannot write its `.install.lock`
beneath `~/.hermes/installs/`, the process lacks write access to Hermes's own
installation directory, for example inside a restricted sandbox. That is a filesystem
restriction, not evidence of a bad model configuration or broken installation.

Run Hermes from a terminal where its own installation directories are writable.
If the same error persists there, investigate its launcher and installation before
changing the provider configuration. Council leaves Hermes's model selection alone
and cannot repair directory access by changing its adapter.

## Doctor passes but a live run fails

Doctor does not start the binaries. An executable launcher can exist while its
runtime, login, provider, or filesystem access is unavailable. Use the exact command
from `agents.yaml` with a short prompt in a normal terminal and inspect the runtime
logs from the failed Council session. Avoid sharing credentials or full configuration
files when reporting the issue.

## Build verification failed or was skipped

Read `rounds/build-verification.md` or `.json`. They contain the extracted filenames,
test command, actual exit code, output, and error. The final deliverable is under
`workspace/`; an agent's scratch files are under `agents/<name>/`.

Verification does not automatically install dependencies. Tell agents your runtime
and dependency constraints in the request. The `RUN:` command uses the inherited
environment and `/bin/sh` on Linux. It must be one line; if it needs a multiline
script, include that script in a `FILE:` block and run it.

Malformed or missing blocks, duplicate paths, path escapes, multiple `RUN:` lines,
and nonzero test exits fail the build. Paths with spaces and empty files are supported.
For Markdown files containing triple backticks, use a longer outer fence.

`--no-verify` and `settings.verify_build: false` mark the build as unverified, return
exit 1, and skip both extraction and execution. Question tasks are unaffected.

## Timeouts, interruption, or long prompts

Set `timeout_sec` on the slow agent or adjust `per_agent_timeout_sec`. The default
per-agent setting now actually applies when no override is present. On Linux,
timeouts and SIGINT/SIGTERM kill the launched process group, including normal child
processes. Processes that deliberately detach into a new session are outside that
group. Commands receive closed stdin to avoid waiting for terminal input.

The session wall-clock cap is soft. Synthesis and build verification may extend a
run after new debate rounds are stopped. It does not bound provider token costs.

Each later turn receives the full history, on stdin or as an argument depending on the
agent's `prompt_via`. Argument delivery has a per-argument size limit (see above); stdin
does not. Every CLI still has its provider's context limit. These failures are
recorded, and history is never silently truncated. Reduce the round count or ask for
concise responses. `--prompt-file` affects how Council reads your prompt, not how
prompts are delivered to the agents.

## A session has an error.txt

Council itself hit an unexpected error, as opposed to an agent failing. The session
still finishes with status `error` and a `final.md`; `error.txt` holds the traceback,
which is also printed to stderr. That is a Council bug: keep the traceback when
reporting it, but remove prompts and credentials from anything you share.

## Reading a session summary

```bash
python -m json.tool sessions/<id>/session.json
```

Look at `status`, `exit_code`, `consensus`, `rounds_run`, `drop_history`, and `build`.
A completed question can have `consensus: false`: the synthesis should explain any
remaining disagreements. `total_cost_usd_claude` includes only reported Claude costs,
not the total bill for all providers.
