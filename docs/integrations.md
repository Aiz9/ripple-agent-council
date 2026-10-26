# Integrations

Council as a tool for other agents (MCP), Council working on a real repository (git
worktrees), and Council measured against single agents (evaluation).

- [MCP server](#mcp-server)
- [Git worktrees](#git-worktrees)
- [Evaluation](#evaluation)

## MCP server

`council --mcp` serves the [Model Context Protocol](https://modelcontextprotocol.io)
over stdio, so an MCP client — Claude Code, Codex, Cursor, or your own program — can
consult a council.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `ask_council` | `prompt` (required); optional `agents` (names, adapters, or `auto[:N]`), `preset`, `type`, `max_rounds`, `max_cost_usd`, `anonymous_review`, `continue_from` (session ID) | the final answer, plus status, consensus, agents, usage, build status, and session ID |
| `list_sessions` | optional `limit` (1–200, default 20) | recent sessions with status and question |
| `get_session` | `session` (an ID from `list_sessions`) | the same summary and final answer as `ask_council` |

Register it with Claude Code (user scope, so it works in every project):

```bash
claude mcp add --scope user council -- council --mcp
```

or in any client that takes a JSON server list:

```json
{"mcpServers": {"council": {"command": "council", "args": ["--mcp"]}}}
```

`--config` and `--sessions-dir` given to `council --mcp` apply to every council it
runs. Each `ask_council` call is a full, non-interactive session in a child process
(the prompt goes over stdin, so it cannot be mistaken for an option), and takes as long
as a normal run — usually minutes; raise your client's tool timeout if it gives up
sooner. While it runs, the server keeps answering other requests. Paused sessions come
back with a resume command in the text and `pause_reason` in the data.

Only summaries and final answers leave the server. Transcripts, prompts, agent working
directories, and copied credentials stay in the session directory, and `get_session`
accepts only plain session IDs. Supported protocol versions: 2025-06-18, 2025-03-26,
and 2024-11-05. Tested with the official MCP Python SDK client.

**A council run started by another agent still executes with your permissions**, and
build verification still runs the generated `RUN:` command. Prefer `type: question`
for tools you expose broadly.

## Git worktrees

```bash
council --repo . --type build "Add input validation to the CSV importer, with tests"
```

With `--repo PATH`, each agent works in its own **detached git worktree** of that
repository at its current `HEAD` commit, under `sessions/<id>/agents/<agent>/`.
Agents can read and run the real project, and changes one agent makes are invisible to
the others and to your checkout. Prompts tell agents where they are, and that only the
final build is applied.

For a build, the synthesized `FILE:` blocks are written into a separate worktree on a
new branch, `council/<session-id>`, starting from the same base commit. The `RUN:`
command runs there. Only if it passes are exactly the build's files committed on that
branch (your repository's hooks run as usual; if `user.name`/`user.email` are not
configured, the commit is authored by `Agent Council <council@localhost>`).

```bash
git diff <base> council/<session-id>      # review; final.md prints the exact command
git merge council/<session-id>            # when you are satisfied
git worktree remove ~/.local/state/agent-council/sessions/<session-id>/build
git branch -D council/<session-id>        # or discard it
```

Council never merges, pushes, rebases, or touches your current branch or working
tree. The base commit is fixed when the session starts, so a resume continues from the
same commit even if your branch has moved. Agent worktrees are removed when the session
finishes (their transcripts remain) and kept while it is paused, for `--resume`. The
build worktree is kept for inspection. If the branch name already exists, the build
stops with an error rather than overwriting it.

A question session with `--repo` gives agents the worktrees and creates no branch.
`--repo` needs a repository with at least one commit; uncommitted changes in your
checkout are not visible to agents.

## Evaluation

Does the council actually beat one agent on your tasks? `--eval` measures it.

```yaml
# cases.yaml
cases:
  - id: db-choice
    prompt: "Which database fits 10k writes/s with strict consistency? Cite evidence."
    type: question
    expect:
      contains: ["PostgreSQL"]           # case-insensitive substring
      not_contains: ["MongoDB is ACID by default"]
      regex: ['\[VERIFIED: ']
    judge: "Recommends a database whose consistency model is explained correctly."
  - id: slugify
    prompt: "Write slugify(text) in Python with unittest tests."
    type: build
    expect:
      build_passes: true
```

```bash
council --eval cases.yaml --agents claude,codex,hermes --baseline claude --baseline codex --judge claude
```

For each case, Council runs a normal non-interactive session (each in its own process)
with your roster, preset, and settings, then sends the same request to each
`--baseline` agent alone in one call. Baseline builds are verified the same way.
Every answer is scored with the case's checks; a case passes when every check passes
and the answer is nonempty. With `--judge`, that agent grades each answer that has a
`judge` rubric from 0 to 10, without being told which system wrote it. Baselines and
the judge may be any configured agent or built-in adapter, including agents outside
the run's roster.

```text
System                    Passed    Rate   Judge     Time
council                    2/2      100%     8.5    412.3s
baseline:claude            1/2       50%     6.0     71.0s
baseline:codex             1/2       50%     7.0     88.2s
```

Results go to `--eval-dir` (default: a new directory under `evals/` beside your
sessions directory): `results.json` (every check, score, time, cost, and the council's
session path), `report.md`, and the council sessions themselves. Human checkpoints are
disabled during evaluation.

The judge is a model, so its scores are opinions with the judge's biases (including
toward its own style); prefer deterministic checks where possible, and do not use one
of the compared agents as the only judge for conclusions that matter. Evaluation runs
real sessions and costs what they cost.
