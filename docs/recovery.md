# Resume saved work and handle unavailable agents

Council saves each finished agent call as it arrives, including calls that finish
while other participants in the same parallel round are still running. A stopped
session can continue in the same directory with its saved goal, agents, roles,
coordination mode, settings, transcripts, and build workspace.

## Find and resume a session

```bash
council --sessions
council --resume
council --resume latest
council --resume 20261018T090000Z-example
council --resume /absolute/path/to/sessions/20261018T090000Z-example
```

`--sessions` lists saved session IDs, statuses, stages, and goals. `--resume` without
a value means `--resume latest`: choose the newest incomplete session that has a
valid `run.json` checkpoint. A crash can leave the displayed status as `running`;
that session can still be resumed after its previous process has exited.

IDs and `latest` use the session root selected by `--sessions-dir`, then
`COUNCIL_SESSIONS_DIR`, then `$XDG_STATE_HOME/agent-council/sessions` (default
`~/.local/state/agent-council/sessions`). An explicit path locates that directory
directly. For a nondefault root:

```bash
council --sessions-dir ./saved-sessions --sessions
council --sessions-dir ./saved-sessions --resume latest
```

Resume reads the configuration and task options captured in `run.json`. It works
even if the original configuration file has moved or been deleted. Use a separate
new run to change the goal, agent roster, roles, round limit, or coordination mode;
Council rejects those task and configuration overrides with `--resume`. Display
options such as `--quiet` remain available. Resume runs without setup questions.

The snapshot covers Council's configuration. Authentication and provider defaults
managed by an external agent CLI still come from that CLI's current setup; Council
does not snapshot a provider account or its in-progress request.

An explicit ID or path can name a completed session. Council shows its saved final
answer and exits successfully without repeating agent calls or build commands.
`latest` skips completed sessions. Sessions from versions that did not write
`run.json` remain readable as transcripts but cannot be resumed.

## What Council reuses

Each call cache includes the full prompt's SHA-256 hash, the agent name, and the
logical turn. Council reuses a successful result only when all of these match.
Prompts include the task protocol, role, and conversation history, so changed
history can require a new call even when the visible turn name is the same.

Finished phases are recorded in `progress.json`. When replaying a finished phase,
Council also restores its failed calls so earlier drop and leader-failover
decisions remain consistent. At the unfinished frontier, failed calls can be tried
again. This lets a resumed session use an agent whose rate limit has cleared while
retaining successful calls whose prompts still match.

For example, if two agents finish a parallel round and the third is still running
when Council is killed, the two durably saved results remain available. Resume
reuses them when their prompts match and runs the unfinished call again. The
transcript is reconstructed in the same agent order, independent of completion
order.

| Artifact | Purpose |
| --- | --- |
| `run.json` | Original validated configuration, goal, task type, and verification option. |
| `progress.json` | Finished phases used to replay earlier decisions. |
| `calls/*.json` | Current result and exact prompt hash for each logical agent turn. |
| `attempts/*.json` | Separate result records for every attempted call, including retries. |
| `rounds/*` | Prompts, stdout, stderr, answer transcripts, and adapter output files. Retried calls get distinct artifact names. |
| `session.json` | Current status, stage, resumed-call count, unavailable agents, leader changes, and reported costs. |
| `final-answer.json`, `final.md` | Durable final answer and its readable Markdown presentation. A completed resume can restore the Markdown file. |
| `build-step.json` | Build verification's saved state and finished report. |
| `processes/*.json` | Identity records used to recover surviving child process groups. |
| `session.lock` | Exclusive session ownership while Council is running. |

Checkpoint JSON files use atomic replacement, file and directory flushes, and
owner-only file permissions. The guarantee applies to results durably saved before
a crash. A request still in flight, or completed by a provider just before Council
saved its result, may be sent again. Council resumes its own orchestration and
conversation history; it cannot restore an external CLI's interrupted provider
request or guarantee exactly-once provider calls or side effects.

## Rate limits, retries, and leader changes

Council recognizes common capacity notices, authentication errors, and temporary
network or service failures. Recognized account-limit notices printed with exit
code zero are treated as failures too. Raw output remains in the session so the
classification can be inspected.

Temporary network and service failures receive a bounded retry. Configure this in
the normal YAML file before starting a new session:

```yaml
settings:
  agent_retries: 1
  retry_delay_sec: 1
```

`agent_retries` counts additional attempts: the default permits two attempts in
total. Set it to `0` to disable automatic retries. Delays start at
`retry_delay_sec`, double for later retries, and are capped at 30 seconds. These
settings are part of the saved configuration used on resume.

Capacity failures rotate to other available agents immediately instead of
repeatedly calling the exhausted account. In peer mode, the discussion continues
while enough agents respond; final synthesis can use another available peer. In
hierarchical mode, Council promotes another available participant when the leader
fails, updates the team instructions, and records the transition in
`session.json` under `leader_changes`. Framing, delegation, review, and synthesis
can each trigger this handoff.

If Council cannot maintain quorum or obtain a final answer, it saves the session
with status `paused`, a `pause_reason`, and exit code `75`. Restore the affected
CLI's login, quota, or service availability, then run `council --resume`. There is
no background retry daemon; an explicit resume starts the next attempt.

## Interruptions and exclusive ownership

Ctrl+C requests a stop, terminates active command groups, saves available results,
and returns exit code `130`. Resume the same session to continue unfinished work.
SIGKILL cannot run a cleanup handler, but results already checkpointed by individual
workers survive it.

Only one Council process can own a session at a time. Concurrent resume attempts
fail immediately instead of running duplicate work. The operating system releases
the lock when its owning process exits, including after SIGKILL. The retained
`session.lock` file is normal and does not mean the session is still locked.

On Linux, resume checks recorded child process groups before continuing. It kills
a group only when its live leader's PID, process-group ID, start time, and boot
identity still match the saved record. Reused PIDs and unverifiable identities are
skipped. Groups whose leader is already gone cannot be safely identified from that
record. The CLI and generated build command retain their configured host
permissions; process-group cleanup and separate workspaces provide no system
sandbox.

## Resume build verification

Build verification runs the final answer's `RUN:` command in the session's
`workspace/` directory. A saved, finished verification result is reused on ordinary
resume, including a failed result. Repeating the command requires an explicit
choice:

```bash
council --resume /path/to/session --retry-build
```

For a finished failed build, inspect `rounds/build-verification.json` and the files
in `workspace/`, repair them, then use `--retry-build`. Council preserves those
repaired files and reruns the original verification command; matching saved agent
calls and final synthesis are reused.

If the previous build step was marked `running`, its command may have executed
partially or finished without its result being saved. Ordinary resume pauses with
`pause_reason: build_outcome_unknown`, exit code `75`, and no repeated build
command. Inspect the workspace, command logs, and any external effects before
using `--retry-build`.

For this unknown-outcome case, the current runner writes the generated `FILE:`
blocks again before running the command. Preserve any manual edits you need before
retrying. This differs from retrying a saved, finished failed verification, which
keeps repaired files.

Council also binds a saved build step to the synthesized answer's hash. If resumed
work produces a different build answer, it pauses with
`pause_reason: build_artifacts_changed`. `--retry-build` explicitly accepts the new
generated files and runs their verification command; those file blocks are written
again, so inspect or preserve existing edits first.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Completed successfully, including an explicit resume of an already completed session. |
| `1` | Build verification failed or was skipped; inspect its saved report. |
| `2` | Input/configuration/session selection error, or the session is already locked by another process. |
| `75` | Work is saved and paused; inspect `session.json` and `pause_reason` before resuming. |
| `130` | Interrupted; saved work can be resumed. |
