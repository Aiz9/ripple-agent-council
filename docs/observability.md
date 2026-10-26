# Observability

What a run shows while it happens, and how to look at it afterwards: live output,
usage, traces (optionally exported to OpenTelemetry), HTML reports, and a local
session viewer.

## Live output

Agents whose CLI writes the answer to standard output as it is produced stream live,
one labelled line at a time:

```text
round1 · claude │ The workload is read-heavy, so [VERIFIED: query log] ...
round1 · hermes │ CHALLENGE: the benchmark cited is for version 12 ...
```

| Output mode | Shown |
| --- | --- |
| `claude_stream_json` (the bundled `claude` agent) | live, as text deltas arrive |
| `stdout` (for example `agy`, `hermes`, most `--add-agent` CLIs) | live, line by line |
| `opencode_json` | live, one text part at a time |
| `file`, `aider_history`, `claude_json`, `gemini_json`, `qwen_json`, `cursor_json` | as one block when the call finishes |

In parallel rounds, lines from different agents interleave; the label says whose each
line is. Streamed text is for display only: the recorded answer is always parsed from
the complete output afterwards. For Claude, text from subagents is not streamed, and
narration before tool calls may appear live but is not part of the final answer.
`--no-stream` restores whole-turn blocks; `--quiet` prints only status and the final
answer. A configuration saved before 0.8.0 that uses `claude_json` keeps working but
does not stream; to switch, set `output_mode: claude_stream_json` and
`argv: ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]`.

## Usage

Each call's usage is stored with the call, and `session.json` has a `usage` summary:

```json
{"by_agent": {"claude": {"calls": 7, "metered_calls": 7, "total_tokens": 182344, "cost_usd": 0.8312},
              "codex": {"calls": 7, "metered_calls": 7, "total_tokens": 96000, "cost_usd": null},
              "hermes": {"calls": 7, "metered_calls": 0, "total_tokens": 0, "cost_usd": null}},
 "total_tokens": 278344, "total_cost_usd": 0.8312, "unmetered_agents": ["hermes"]}
```

Only formats seen in real CLI output are parsed: Claude Code's token counts and
`total_cost_usd`, and Codex's closing `tokens used` line. Every other CLI is listed in
`unmetered_agents` instead of being estimated. Failed and retried attempts count.
Budgets are described in [steering](steering.md#budgets).

## Traces

Every session writes `trace.jsonl`: one JSON line per finished span, with
`trace_id`, `span_id`, `parent_id`, `name`, `start` and `end` (Unix seconds),
`duration_ms`, `status`, and `attributes`. Each invocation, including each resume, is
a new trace.

| Span | Attributes |
| --- | --- |
| `council session` | session, mode, resumed, agents; at the end exit_code, consensus, rounds |
| `framing`, `round1`..., `ranking`, `synthesis`, `build` | one per phase |
| `agent call` | agent, tag, attempt, prompt_bytes, reused (replayed from a checkpoint), seconds, failure_kind, cost_usd, tokens |
| `build verification` | command, exit_code |

### OpenTelemetry

Install the optional extra, then point the standard variables at a collector:

```bash
uv tool install --reinstall '.[otel]'      # from a checkout; from PyPI: 'ripple-agent-council[otel]'
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # OTLP over HTTP
export OTEL_SERVICE_NAME=agent-council                      # the default
council "your prompt"
```

`OTEL_TRACES_EXPORTER=console` prints spans to standard output instead, and `none`
disables export. Council configures a tracer provider only when one of these is set and
no provider is installed yet, so a program that embeds Council keeps its own. Span
attributes are prefixed `council.`. Prompts and answers are never attached to spans.
Without the extra, `trace.jsonl` is still written.

## Reports

```bash
council --report                 # the latest session
council --report 20261018T101500Z-abc123 --open
```

`--report` writes `report.html` into the session and prints its path. The page is self-
contained (no scripts or external requests) and shows: status, consensus, and usage;
the council and its roles; the definition of done; a timeline of agent calls from
`trace.jsonl`; the anonymous peer ranking; guardrail, human-checkpoint, and leader
events; the debate by phase with challenges and consensus lines highlighted; build
verification; and the final answer.

## Session viewer

```bash
council --serve                  # http://127.0.0.1:8765/
council --serve --port 0 --open  # any free port, opened in your browser
```

A read-only viewer for every session in the sessions directory. The index refreshes
every 5 seconds, and a running session's page every 3. It listens on 127.0.0.1 only, answers just `/` and
`/session/<id>` for existing session IDs, escapes all text, and never serves raw files,
agent working directories, or the copied Claude configuration. Stop it with Ctrl+C.
