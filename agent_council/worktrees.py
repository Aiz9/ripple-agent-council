"""Git worktree isolation (``--repo PATH``).

Every agent works in its own detached worktree of the repository at the
session's base commit, so agents can read and change real project files without
seeing or overwriting each other's edits. The verified build is written into a
separate worktree on a new branch, ``council/<session>``, and committed there
only when its RUN command passes. Nothing is merged, pushed, or checked out in
the user's own working tree.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

FALLBACK_IDENTITY = {"user.name": "Agent Council", "user.email": "council@localhost"}


def git(*args, cwd: Path, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env=os.environ | {"GIT_TERMINAL_PROMPT": "0"})
    if check and proc.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()[-600:]}")
    return proc.stdout.strip()


def repository(path) -> tuple[Path, str]:
    """(top-level directory, HEAD commit) of the repository containing path."""
    path = Path(path).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"--repo is not a directory: {path}")
    try:
        top = Path(git("rev-parse", "--show-toplevel", cwd=path))
    except (OSError, ValueError):
        raise ValueError(f"--repo is not inside a git repository: {path}") from None
    try:
        head = git("rev-parse", "--verify", "HEAD^{commit}", cwd=top)
    except ValueError:
        raise ValueError(f"--repo needs at least one commit: {top}") from None
    return top, head


def is_worktree(path: Path) -> bool:
    return (path / ".git").is_file()


def add_detached(repo: Path, path: Path, base: str) -> None:
    """A worktree at base for one agent; kept as-is when resuming."""
    if is_worktree(path):
        return
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"cannot create a worktree in nonempty {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "--detach", str(path), base, cwd=repo)


def add_branch(repo: Path, path: Path, branch: str, base: str) -> None:
    if is_worktree(path):
        return
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=repo, check=False):
        raise ValueError(f"branch {branch} already exists in {repo}")
    path.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "-b", branch, str(path), base, cwd=repo)


def commit(path: Path, files: list[str], message: str) -> str | None:
    """Commit exactly the build's files; None when they match the base already."""
    git("add", "--", *files, cwd=path)
    if not git("diff", "--cached", "--name-only", cwd=path):
        return None
    identity = []
    for key, fallback in FALLBACK_IDENTITY.items():
        if not git("config", key, cwd=path, check=False):
            identity += ["-c", f"{key}={fallback}"]
    git(*identity, "commit", "-m", message, cwd=path)  # the repository's own hooks still run
    return git("rev-parse", "HEAD", cwd=path)


def remove(repo: Path, path: Path) -> None:
    """Remove an agent worktree; its edits are discarded (transcripts keep the debate)."""
    if is_worktree(path):
        git("worktree", "remove", "--force", str(path), cwd=repo, check=False)
    git("worktree", "prune", cwd=repo, check=False)
