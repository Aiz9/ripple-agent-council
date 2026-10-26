"""Shared skills for every Council CLI, without agent-specific installation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

from .checkpoints import atomic_json

ORCHESTRA_REPO = "https://github.com/Orchestra-Research/AI-Research-SKILLs.git"
SOURCES = {
    "orchestra-research": ORCHESTRA_REPO,
    "ponytail": "https://github.com/DietrichGebert/ponytail.git",
    "caveman": "https://github.com/JuliusBrussee/caveman.git",
    "graphify": "https://github.com/Graphify-Labs/graphify.git",
}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
IGNORE = {".git", "node_modules", "__pycache__", ".venv"}
DEFAULT_INLINE = {"ponytail", "caveman"}
COMPATIBILITY = """COUNCIL SKILL COMPATIBILITY (applies to skills and their references):
Use the relevant configured skills by default. Your user goal, assigned Council
role, coordination protocol, permissions, and budgets take precedence over skill
examples. The CLI names in upstream examples do not restrict who can use a skill.
- Read/Write/Edit: use your available file tools; Bash: use your available shell.
- Skill(name), slash-skill commands, and native skill activation: read the matching
  SKILL.md from the shared catalog and follow it within this task.
- WebSearch/WebFetch/Exa MCP: use an available search/browser tool or permitted HTTP
  client. If unavailable, report the limitation; never invent searches or evidence.
- Task/Agent/subagents: request help from the configured Council peers in your
  response, following the current coordination mode. Do not launch another council
  or assume native subagent tools exist.
- TodoWrite/planning: use your available planning tool or a Markdown checklist.
- AskUserQuestion: report the question to Council; human checkpoints handle input.
- /loop, OpenClaw heartbeat, cron.add, and recurring jobs: perform bounded work in
  this turn and report state and next steps. Council controls rounds, timeouts,
  checkpoints, and resume. Do not install schedulers or start background loops.
- Resolve skill-relative references/scripts from that skill's directory. Use the
  catalog for cross-skill paths instead of ~/.claude, ~/.codex, or other native
  skill directories. Scripts, dependencies, MCP servers, and services are not
  installed or executed merely because a skill mentions them.
- If a Caveman workflow refers to Native Core, read ../native-core.md relative
  to its skill directory when present. Shared library resources remain alongside
  the skill folders in the session snapshot.
State which skills you applied. If tools or dependencies prevent a step, say so
and complete the useful work you can. Never claim an unavailable tool ran.
Prose compression in Ponytail and Caveman must preserve technical content:
retain evidence,
TASK_TYPE, CONSENSUS, RANKING, FILE, RUN, complete build files, JSON schemas,
commands, and code exactly as Council requires. A style preference cannot remove
required validation, test results, or unresolved disagreements.
Native UI bands, hooks, proxies, persistent mode files, and slash-command handlers
are optional host integrations, not prerequisites. Follow their portable skill
instructions directly; do not claim that these host integrations are installed.
For vision requests, use available image tools; report unsupported images or PDFs.
Use an existing project or isolated environment for skill dependencies, retaining
the runtime's normal installation permissions.
"""


def directory(override: str | None = None) -> Path:
    value = override or os.environ.get("COUNCIL_SKILLS_DIR")
    if value:
        return Path(value).expanduser().resolve()
    base = os.environ.get("XDG_DATA_HOME", "")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".local/share"
    return root / "agent-council/skills"


def validate(value) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict) or value.keys() - {"enabled", "directory", "names"}:
        raise ValueError("skills must be a mapping with enabled, directory, and names")
    result = {"enabled": True, "directory": None, "names": ["*"]} | value
    if not isinstance(result["enabled"], bool):
        raise ValueError("skills.enabled must be true or false")
    if result["directory"] is not None and (not isinstance(result["directory"], str) or not result["directory"].strip()):
        raise ValueError("skills.directory must be a nonempty path")
    names = result["names"]
    if not isinstance(names, list) or any(not isinstance(n, str) or (n != "*" and not NAME.fullmatch(n)) for n in names):
        raise ValueError("skills.names must be a list of skill names, or ['*'] for all")
    if len(set(names)) != len(names) or ("*" in names and len(names) != 1):
        raise ValueError("skills.names must be distinct; '*' must be used alone")
    return result


def metadata(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    parts = re.split(r"(?m)^---\s*$", text, maxsplit=2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f"skill needs YAML frontmatter: {path}")
    # Some upstream descriptions have unindented continuation lines. Read fields
    # independently so these human-readable descriptions still work.
    fields = dict(re.findall(r"(?ms)^([\w-]+):[ \t]*(.*?)(?=^[\w-]+:|\Z)", parts[1]))
    try:
        name = yaml.safe_load(fields.get("name", ""))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid skill name in {path}") from exc
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(f"invalid skill name in {path}")
    description = fields.get("description", "").strip()
    try:
        parsed = yaml.safe_load("description: " + description)
        description = parsed["description"]
    except yaml.YAMLError:
        pass
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"skill needs a description: {path}")
    return {"name": name, "description": " ".join(description.split()), "path": str(path)}


def discover(root: Path, *, missing_ok: bool = False) -> list[dict]:
    if not root.exists():
        if missing_ok:
            return []
        raise ValueError(f"skills directory does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"skills path is not a directory: {root}")
    found = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE)
        for name in dirs + files:
            if (Path(current) / name).is_symlink():
                raise ValueError(f"skill libraries must not contain symlinks: {Path(current) / name}")
        document = "SKILL.md" if "SKILL.md" in files else "skill.md" if "skill.md" in files else None
        if document is None or any(part.startswith(".") for part in Path(current).relative_to(root).parts):
            continue
        item = metadata(Path(current) / document)
        if item["name"] in found:
            raise ValueError(f"duplicate skill name: {item['name']}")
        found[item["name"]] = item
    return [found[name] for name in sorted(found)]


def select(config: dict) -> tuple[Path, list[dict]]:
    root = directory(config["directory"])
    if not config["enabled"] or not config["names"]:
        return root, []
    optional = config["directory"] is None and not os.environ.get("COUNCIL_SKILLS_DIR") and config["names"] == ["*"]
    items = discover(root, missing_ok=optional)
    if config["names"] == ["*"]:
        return root, items
    by_name = {item["name"]: item for item in items}
    unknown = set(config["names"]) - by_name.keys()
    if unknown:
        raise ValueError(f"unknown skills: {', '.join(sorted(unknown))}; use --skills to list installed names")
    return root, [by_name[name] for name in config["names"]]


def source_name(source: str) -> str:
    if source in SOURCES:
        return source
    for name, url in SOURCES.items():
        if source.removesuffix(".git").rstrip("/") == url.removesuffix(".git"):
            return name
    path = Path(source).expanduser()
    if not path.is_dir():
        raise ValueError("--install-skills accepts a configured repository name/URL or a local checkout directory")
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", path.resolve().name).strip(".-") or "local-skills"


def install(root: Path, source: str = ORCHESTRA_REPO) -> tuple[Path, int]:
    """Install one immutable library atomically, including references and license.

    Only the configured upstream URLs or an existing local checkout are accepted. No
    upstream installer, dependency, hook, or skill script is executed.
    """
    root = root.expanduser().resolve()
    name = source_name(source)
    remote = SOURCES.get(name)
    # A local directory named after a source remains an offline installation.
    local = Path(source).expanduser().resolve() if Path(source).expanduser().is_dir() else None
    target = root / name
    if target.exists():
        raise ValueError(f"skill library already exists: {target}; use --skills to inspect it")
    if local is not None and (local == root or local in root.parents):
        raise ValueError("the installation destination must be outside the source checkout")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as work:
        checkout, stage = Path(work) / "checkout", Path(work) / "library"
        if local is None:
            try:
                proc = subprocess.run(["git", "clone", "--depth", "1", "--", remote, str(checkout)],
                                      capture_output=True, text=True, timeout=180)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError(f"could not download skills: {exc}") from exc
            if proc.returncode:
                raise ValueError(f"could not download skills: {proc.stderr.strip()}")
            revision = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                                      capture_output=True, text=True, check=True).stdout.strip()
            local = checkout
        else:
            revision = None
        # Copy canonical skills, not duplicate native adapters, translations,
        # benchmark fixtures, UI integrations, or dependency trees.
        if (local / "skills").is_dir():
            entries = [local / "skills"]
        else:
            entries = sorted(p for p in local.iterdir() if p.is_dir() and re.match(r"^\d", p.name))
        if entries:
            stage.mkdir()
            def ignore(directory, names):
                omitted = set(names) & IGNORE
                if Path(directory) == local / "skills" and (local / "skills/caveman/SKILL.md").is_file():
                    omitted.add("generated")  # native copies duplicate the canonical skills
                return omitted
            for entry in entries:
                if entry.is_symlink():
                    raise ValueError(f"skill source must not be a symlink: {entry}")
                shutil.copytree(entry, stage / entry.name, ignore=ignore, symlinks=True)
            for path in local.iterdir():
                if path.is_file() and (path.name.startswith(("LICENSE", "NOTICE")) or path.name == "README.md"):
                    if path.is_symlink():
                        raise ValueError(f"skill source must not be a symlink: {path}")
                    shutil.copyfile(path, stage / path.name)
        else:
            discover(local)
            shutil.copytree(local, stage, ignore=shutil.ignore_patterns(*IGNORE), symlinks=True)
        items = discover(stage)
        if not items:
            raise ValueError("source contains no SKILL.md or skill.md files")
        atomic_json(stage / "council-source.json", {"source": source, "revision": revision,
                                                   "skills": len(items)})
        # mkdir is exclusive even when another installer wins the race.
        target.mkdir()
        try:
            stage.replace(target)
        except BaseException:
            target.rmdir()
            raise
    return target, len(items)


def portable_document(text: str, paths: dict[str, str]) -> str:
    """Adapt known native paths/continuity while preserving original API examples."""
    for name, path in sorted(paths.items(), key=lambda item: len(item[0]), reverse=True):
        home = r"(?:(?:~|\$HOME)/|/(?:home/[^/]+|Users/[^/]+|root)/)?"
        native = (r"(?:\.(?:claude|codex|gemini|hermes|agents|cursor|qwen|kiro|goose|copilot|aider)/skills"
                  r"|\.config/opencode/skills|\.gemini/antigravity/skills)")
        pattern = home + native + rf"/{re.escape(name)}(?=/|[\s`\"')]|$)"
        text = re.sub(pattern, lambda _: path, text)
    text = re.sub(r"(?ms)^## Agent Continuity[^\n]*\n.*?(?=^## |\Z)",
                  "## Agent Continuity in Council\n\n"
                  "Council owns continuation. Read the current research state, perform bounded work "
                  "within this turn, save progress in the permitted workspace, and return findings "
                  "and next steps to the council. Resume uses Council checkpoints.\n\n", text)
    # Graphify's native Agent requirement would otherwise prevent non-Claude
    # runtimes from performing semantic extraction at all.
    text = re.sub(r"\*\*MANDATORY: You MUST use the Agent tool here\.[^\n]*\*\*",
                  "Coordinate semantic extraction chunks with the configured Council peers. "
                  "Use available tools for your assigned chunk; if peer results are unavailable, "
                  "process a bounded chunk yourself and report remaining work.", text)
    text = re.sub(r'^python3 -c "import graphify"[^\n]*--break-system-packages[^\n]*$',
                  'python3 -c "import graphify"  # If missing, use a project or isolated environment for graphifyy.',
                  text, flags=re.MULTILINE)
    # Retain valid frontmatter at the top of the adapted document.
    parts = re.split(r"(?m)^---\s*$", text, maxsplit=2)
    if len(parts) == 3 and not parts[0].strip():
        return "---" + parts[1] + "---\n\n" + COMPATIBILITY + "\n" + parts[2].lstrip()
    return COMPATIBILITY + "\n" + text


def snapshot(config: dict, session: Path, selection: tuple[Path, list[dict]]) -> None:
    root, items = selection
    records, paths = [], {}
    for item in items:
        source = Path(item["path"])
        relative = source.relative_to(root).with_name("SKILL.md")
        target = session / "skills" / relative
        paths[item["name"]] = str(target.parent)
        paths.setdefault(source.parent.name, str(target.parent))
        records.append({**item, "path": str(target.relative_to(session)),
                        "inline": config["names"] != ["*"] or item["name"] in DEFAULT_INLINE})
    copied = set()
    for item in items:
        source = Path(item["path"])
        library = next((parent for parent in source.parents if parent.is_relative_to(root)
                        and (parent / "council-source.json").is_file()), source.parent)
        if library in copied:
            continue
        if session.is_relative_to(library):
            raise ValueError("session storage must be outside the shared skill directories")
        shutil.copytree(library, session / "skills" / library.relative_to(root), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*IGNORE), symlinks=True)
        copied.add(library)
    for item, record in zip(items, records):
        source, target = Path(item["path"]), session / record["path"]
        original = source.read_text(encoding="utf-8-sig")
        shutil.copyfile(source, target.with_name("SKILL.original.md"))
        adapted = portable_document(original, paths)
        target.write_text(adapted, encoding="utf-8")
        if source.name != "SKILL.md":
            target.with_name(source.name).write_text(adapted, encoding="utf-8")
        record["sha256"] = hashlib.sha256(adapted.encode("utf-8")).hexdigest()
    if items:
        for path in [*root.rglob("LICENSE*"), *root.rglob("NOTICE*")]:
            if path.is_file() and not any(part in IGNORE for part in path.relative_to(root).parts):
                target = session / "skills" / path.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
    atomic_json(session / "skills.json", {"schema_version": 1, "skills": records})


def context(session: Path) -> tuple[str, list[str]]:
    manifest = session / "skills.json"
    if not manifest.exists():  # sessions created before shared skills support
        return "", []
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or saved.get("schema_version") != 1 or not isinstance(saved.get("skills"), list):
        raise ValueError("invalid skills.json checkpoint")
    if not saved["skills"]:
        return "", []
    lines = ["SHARED COUNCIL SKILLS", COMPATIBILITY, "Available skills (name, description, SKILL.md path):"]
    names, inline = [], []
    for item in saved["skills"]:
        if (not isinstance(item, dict)
                or any(not isinstance(item.get(key), str) for key in ("name", "description", "path", "sha256"))
                or not NAME.fullmatch(item["name"]) or not isinstance(item.get("inline"), bool)):
            raise ValueError("invalid skill record in skills.json checkpoint")
        path = (session / item["path"]).resolve()
        if not path.is_relative_to((session / "skills").resolve()):
            raise ValueError("skill checkpoint path escapes the session skills directory")
        text = path.read_text(encoding="utf-8")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != item["sha256"]:
            raise ValueError(f"skill snapshot changed: {item['name']}")
        names.append(item["name"])
        lines.append(json.dumps({"name": item["name"], "description": item["description"],
                                 "path": str(path)}, ensure_ascii=False))
        if item["inline"]:
            inline.append(f"\nCONFIGURED SKILL: {item['name']}\n{text}\nEND CONFIGURED SKILL")
    lines += ["Before answering, read and apply the skills relevant to your assigned work. "
              "Explicitly configured skill instructions are also included below.", *inline]
    return "\n".join(lines) + "\n", names
