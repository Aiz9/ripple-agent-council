"""Session reports (self-contained HTML) and a local, read-only session viewer.

Rendering reads only known files: session.json, final-answer.json,
build-verification.json, trace.jsonl, and rounds/*.md. It never serves raw files,
copied Claude configuration, or agent working directories. All text is
HTML-escaped. The viewer binds to 127.0.0.1 and only accepts existing session IDs.
"""
from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import unquote

from .telemetry import read_trace

CSS = """
:root{--bg:#f7f7f8;--card:#fff;--ink:#1d1d1f;--muted:#6b6b75;--line:#e3e3e8;--accent:#3b5bdb;
--ok:#2b8a3e;--bad:#c92a2a;--warn:#e67700;--mark:#fff3bf}
@media (prefers-color-scheme:dark){:root{--bg:#141417;--card:#1d1d21;--ink:#ececf1;--muted:#a0a0ab;
--line:#2e2e35;--accent:#91a7ff;--ok:#69db7c;--bad:#ff8787;--warn:#ffc078;--mark:#5c4b00}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 6px}
h2{font-size:17px;margin:28px 0 10px}a{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
.meta{color:var(--muted);font-size:13px}.badge{display:inline-block;padding:1px 9px;border-radius:99px;
font-size:12px;font-weight:600;border:1px solid currentColor}.ok{color:var(--ok)}.bad{color:var(--bad)}
.warn{color:var(--warn)}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
pre{white-space:pre-wrap;word-wrap:break-word;margin:0;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
mark{background:var(--mark);color:inherit;padding:0 2px;border-radius:3px}
details{margin:6px 0}summary{cursor:pointer;font-weight:600}
.lane{display:grid;grid-template-columns:150px 1fr;gap:8px;align-items:center;font-size:12px;margin:3px 0}
.track{position:relative;height:16px;background:var(--line);border-radius:4px}
.bar{position:absolute;top:0;height:16px;border-radius:4px;background:var(--accent);opacity:.8}
.bar.error{background:var(--bad)}.bar.reused{background:var(--muted)}
"""


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _highlight(text: str) -> str:
    out = []
    for line in _esc(text).splitlines():
        if re.match(r"\s*(CHALLENGE:|CONSENSUS:|RANKING:|\[UNVERIFIED\])", line):
            line = f"<mark>{line}</mark>"
        else:  # challenges are usually written mid-sentence
            line = re.sub(r"(CHALLENGE:[^\n]*)", r"<mark>\1</mark>", line)
        out.append(line)
    return "\n".join(out)


def _phase_key(tag: str) -> tuple:
    base = tag.split(".")[0].removesuffix("-revision")
    if base == "round0":
        return (0, 0)
    if match := re.fullmatch(r"plan(\d+)", base):
        return (int(match[1]), 0)
    if match := re.fullmatch(r"round(\d+)", base):
        return (int(match[1]), 1)
    return (10_000, 0) if base == "ranking" else (10_001, 0)


def transcript(session: Path) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """[(phase, [(agent, artifact tag, body)])] in debate order."""
    phases: dict[str, list] = {}
    for path in sorted((session / "rounds").glob("*.md"), key=lambda p: p.stat().st_mtime_ns):
        if path.name == "build-verification.md":
            continue
        artifact, _, agent = path.stem.rpartition(".")
        if not artifact or not agent:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        body = body.split("\n\n", 1)[1] if body.startswith("# ") and "\n\n" in body else body
        phase = artifact.split(".")[0].removesuffix("-revision")
        phases.setdefault(phase, []).append((agent, artifact, body.strip()))
    return sorted(phases.items(), key=lambda item: _phase_key(item[0]))


def _timeline(spans: list[dict]) -> str:
    calls = [s for s in spans if s.get("name") == "agent call" and isinstance(s.get("start"), (int, float))]
    if not calls:
        return "<p class='meta'>No trace recorded.</p>"
    start = min(s["start"] for s in calls)
    end = max(s["end"] for s in calls)
    width = max(end - start, 1e-6)
    lanes: dict[str, list] = {}
    for span in calls:
        lanes.setdefault(span.get("attributes", {}).get("agent", "?"), []).append(span)
    rows = []
    for agent, items in sorted(lanes.items()):
        bars = []
        for span in items:
            attrs = span.get("attributes", {})
            kind = "reused" if attrs.get("reused") else ("error" if span.get("status") != "ok" else "")
            left = 100 * (span["start"] - start) / width
            size = max(100 * (span["end"] - span["start"]) / width, 0.4)
            tip = f"{attrs.get('tag')} · {span.get('duration_ms', 0) / 1000:.1f}s · {span.get('status')}"
            if attrs.get("tokens"):
                tip += f" · {attrs['tokens']:,} tokens"
            bars.append(f"<div class='bar {kind}' style='left:{left:.2f}%;width:{size:.2f}%' title='{_esc(tip)}'></div>")
        rows.append(f"<div class='lane'><span>{_esc(agent)}</span><div class='track'>{''.join(bars)}</div></div>")
    return (f"<p class='meta'>{len(calls)} agent calls over {width:.1f}s of wall time "
            "(grey = reused checkpoint, red = failed). Hover a bar for details.</p>" + "".join(rows))


def render_session(session: Path, *, refresh: bool = False) -> str:
    session = Path(session)
    meta = _read_json(session / "session.json") or {}
    final = _read_json(session / "final-answer.json") or {}
    build = _read_json(session / "rounds" / "build-verification.json")
    status = meta.get("status", "unknown")
    tone = "ok" if status == "completed" else ("warn" if status in ("running", "paused") else "bad")
    usage = meta.get("usage") or {}
    head = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            + ("<meta http-equiv='refresh' content='3'>" if refresh and status == "running" else "")
            + f"<title>Council {_esc(session.name)}</title><style>{CSS}</style></head><body><main>")
    parts = [head, f"<h1>{_esc(meta.get('question') or meta.get('goal') or session.name)}</h1>",
             f"<p class='meta'>Session {_esc(session.name)} · <span class='badge {tone}'>{_esc(status)}</span>"
             + (f" ({_esc(meta.get('pause_reason'))})" if meta.get("pause_reason") else "")
             + f" · consensus {_esc(meta.get('consensus'))} · {_esc(meta.get('mode'))}"
             + (f" ({_esc(meta.get('peer_turns'))} turns)" if meta.get("peer_turns") else "")
             + (f" · leader {_esc(meta.get('leader'))}" if meta.get("leader") else "")
             + f" · {meta.get('rounds_run', 0)} rounds · {meta.get('elapsed_sec', 0)}s"
             + (f" · continues {_esc(meta.get('parent_session'))}" if meta.get("parent_session") else "") + "</p>"]
    if usage:
        cost = f"${usage['total_cost_usd']:.4f}" if usage.get("total_cost_usd") is not None else "no reported cost"
        unmetered = (" · no usage reported by " + ", ".join(usage.get("unmetered_agents", []))
                     if usage.get("unmetered_agents") else "")
        parts.append(f"<p class='meta'>{usage.get('total_tokens', 0):,} metered tokens · {cost}{_esc(unmetered)}</p>")

    roles = meta.get("roles") or {}
    if roles:
        parts.append("<h2>Council</h2><div class='card'><table><tr><th>Agent</th><th>Role</th><th>Status</th></tr>")
        for name, role in roles.items():
            dropped = (meta.get("dropped") or {}).get(name)
            parts.append(f"<tr><td>{_esc(name)}</td><td>{_esc(role.get('name'))} — {_esc(role.get('description'))}</td>"
                         f"<td>{'<span class=bad>' + _esc(dropped[:160]) + '</span>' if dropped else '<span class=ok>ok</span>'}</td></tr>")
        parts.append("</table></div>")
    if meta.get("checklist"):
        parts.append("<h2>Definition of done</h2><div class='card'><ul>"
                     + "".join(f"<li>{_esc(item)}</li>" for item in meta["checklist"]) + "</ul></div>")

    parts.append("<h2>Timeline</h2><div class='card'>" + _timeline(read_trace(session / "trace.jsonl")) + "</div>")

    review = meta.get("peer_review")
    if review and review.get("ranking"):
        parts.append(f"<h2>Anonymous peer ranking</h2><div class='card'><p class='meta'>Borda count over "
                     f"{review['valid_ballots']} blind ballots</p><table><tr><th>#</th><th>Agent</th><th>Points</th></tr>"
                     + "".join(f"<tr><td>{i}</td><td>{_esc(n)}</td><td>{review['scores'][n]}</td></tr>"
                               for i, n in enumerate(review["ranking"], 1)) + "</table></div>")
    events = [("Guardrail", e) for e in meta.get("guardrail_events", [])] + \
             [("Human", d) for d in meta.get("human_decisions", [])] + \
             [("Leader change", c) for c in meta.get("leader_changes", [])]
    if events:
        parts.append("<h2>Events</h2><div class='card'><table><tr><th>Kind</th><th>Details</th></tr>"
                     + "".join(f"<tr><td>{_esc(kind)}</td><td>{_esc(', '.join(f'{k}: {v}' for k, v in item.items()))}</td></tr>"
                               for kind, item in events) + "</table></div>")

    parts.append("<h2>Debate</h2>")
    for phase, answers in transcript(session):
        cards = []
        for agent, artifact, body in answers:
            failed = body.startswith("[DROPPED")
            note = "" if artifact == phase else f" <span class='meta'>({_esc(artifact)})</span>"
            cards.append(f"<details {'open' if phase == 'final' else ''}><summary><span class='{'bad' if failed else ''}'>"
                         f"{_esc(agent)}</span>{note}</summary><pre>{_highlight(body)}</pre></details>")
        parts.append(f"<div class='card'><strong>{_esc(phase)}</strong>{''.join(cards)}</div>")

    if build:
        tone = "ok" if build.get("status") == "passed" else "bad"
        parts.append(f"<h2>Build verification</h2><div class='card'><p><span class='badge {tone}'>"
                     f"{_esc(build.get('status'))}</span> exit {_esc(build.get('run_rc'))} · "
                     f"<code>{_esc(build.get('run_cmd'))}</code></p><p class='meta'>Files: "
                     f"{_esc(', '.join(build.get('files_written', [])))}</p><details><summary>Output</summary>"
                     f"<pre>{_esc(build.get('run_output'))}</pre></details></div>")
    if final.get("text"):
        parts.append(f"<h2>Final answer</h2><div class='card'><pre>{_highlight(final['text'])}</pre></div>")
    parts.append("</main></body></html>")
    return "".join(parts)


def write_report(session: Path) -> Path:
    path = Path(session) / "report.html"
    path.write_text(render_session(session), encoding="utf-8")
    return path


def render_index(root: Path) -> str:
    rows = []
    for session in sorted((root.iterdir() if root.is_dir() else []), reverse=True):
        meta = _read_json(session / "session.json") if session.is_dir() else None
        if not isinstance(meta, dict):
            continue
        status = meta.get("status", "unknown")
        tone = "ok" if status == "completed" else ("warn" if status in ("running", "paused") else "bad")
        rows.append(f"<tr><td><a href='/session/{_esc(session.name)}'>{_esc(session.name)}</a></td>"
                    f"<td><span class='badge {tone}'>{_esc(status)}</span></td><td>{_esc(meta.get('consensus'))}</td>"
                    f"<td>{_esc((meta.get('question') or meta.get('goal') or '')[:140])}</td></tr>")
    table = ("<table><tr><th>Session</th><th>Status</th><th>Consensus</th><th>Question</th></tr>" + "".join(rows)
             + "</table>") if rows else "<p class='meta'>No sessions yet.</p>"
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta http-equiv='refresh' content='5'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>Council sessions</title>"
            f"<style>{CSS}</style></head><body><main><h1>Council sessions</h1>"
            f"<p class='meta'>{_esc(root)} · refreshes every 5 s</p><div class='card'>{table}</div></main></body></html>")


def build_server(root: Path, port: int = 8765) -> ThreadingHTTPServer:
    root = Path(root).expanduser().resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            path = unquote(self.path.split("?", 1)[0])
            if path == "/":
                return self._send(200, render_index(root))
            match = re.fullmatch(r"/session/([A-Za-z0-9_.-]+)/?", path)
            if match and match[1] not in (".", "..") and (root / match[1]).is_dir() \
                    and (root / match[1]).resolve().parent == root:
                return self._send(200, render_session(root / match[1], refresh=True))
            self._send(404, "<!doctype html><title>Not found</title><p>Not found.</p>")

        def _send(self, code: int, body: str):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # keep the terminal quiet
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
