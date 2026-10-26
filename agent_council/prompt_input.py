"""Load and validate a prompt before selecting agents or starting a session."""
from __future__ import annotations

from pathlib import Path
import stat
import sys


def read_prompt(prompt: str | None, prompt_file: Path | None = None) -> str:
    """Read literal text, a UTF-8 file, or stdin (``prompt == '-'``).

    Keep the prompt's whitespace and Markdown intact. File extensions are not
    restricted; UTF-8 files with a byte-order mark are accepted as well.
    """
    if prompt_file is not None and prompt is not None:
        raise ValueError("use either a positional prompt or --prompt-file")
    source = "prompt"
    if prompt_file is not None:
        path = prompt_file.expanduser()
        source = f"prompt file {path}"
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError(f"{source} must be a regular text file")
            prompt = path.read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            raise ValueError(f"{source} must be UTF-8 text") from exc
        except OSError as exc:
            raise ValueError(f"cannot read {source}: {exc.strerror or exc}") from exc
    elif prompt == "-":
        source = "stdin prompt"
        prompt = sys.stdin.read()
    if prompt is None:
        raise ValueError("provide a nonempty prompt, --prompt-file FILE (-f FILE), or '-' for stdin")
    if not prompt.strip():
        raise ValueError(f"{source} is empty or contains only whitespace")
    if "\x00" in prompt:
        raise ValueError(f"{source} contains NUL bytes; provide a text prompt")
    return prompt
