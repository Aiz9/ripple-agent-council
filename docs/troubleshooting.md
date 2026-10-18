# Troubleshooting

Start with `council --doctor`. It catches configuration and executable discovery
problems without making model requests. For runtime problems, the session path is
printed before framing starts.

## Command not found after installation

Run `uv tool list` to check that `agent-council` is installed, then
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
subsequent rounds; it is not silently retried. In peer mode, synthesis can fall back
to another surviving participant. In hierarchical mode, a leader failure stops the
run; inspect its `planN.*` assignment or `roundN.*` review logs.

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

The installed `agy --help` exposes `--print` and `--sandbox`. Neither guarantees
that a tool request can be approved in a headless run. A denied shell command can
still end the call. Council records the failure and continues if quorum remains.

The protocol now asks agents to report unavailable tools and continue with available
evidence. This may help when the CLI permits a response after denial; it cannot
override the CLI's permission handling.

Test the exact configured invocation from a normal terminal:

```bash
agy --print "Reply with READY without calling tools." --sandbox
```

Use the installed CLI's supported permissions mechanism if you need tool access.
The previously suggested `permissions.allow` configuration was not verified for
this installed `agy`, so this project does not invent a rule or enable a blanket
bypass. To run the other three participants while investigating, disable `agy` in
`agents.local.yaml` and pass that file with `--config`.

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
