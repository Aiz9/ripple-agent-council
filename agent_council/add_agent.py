"""Interactive `council --add-agent`: describe a CLI once and Council writes its entry.

Nothing is called unless the user accepts the optional test request, and nothing is
saved here; the caller persists the returned configuration.
"""
import copy
import re
import shlex
import shutil

from .adapters import ADAPTERS
from .roles import role_catalog
from .setup import choose

OUTPUT_STYLES = {
    "stdout": "everything the CLI prints on stdout is the answer (most CLIs)",
    "file": "the CLI writes its final answer to a file; put {outfile} in the command",
    "claude_json": "one JSON object with a `result` string (Claude Code style)",
    "claude_stream_json": "JSON lines ending in a `result` event (Claude Code stream-json; streams live)",
    "gemini_json": "one JSON object with a `response` string (Gemini CLI style)",
    "qwen_json": "a JSON array ending in a successful `result` event (Qwen Code style)",
    "opencode_json": "JSON lines of text and step events (OpenCode style)",
    "cursor_json": "one `type: result` JSON object (Cursor CLI style)",
    "aider_history": "Aider's LLM history file; put {outfile} in the command",
}
PLACEHOLDERS = ("{prompt}", "{outfile}", "{model}")
SHELL_OPERATORS = {"|", "||", "&", "&&", ";", ">", ">>", "<"}
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*")


def ask(question: str, default: str = "", check=None) -> str:
    """Ask until `check` (returning an error message or None) accepts the answer."""
    while True:
        shown = f" [{default}]" if default else ""
        answer = input(f"{question}{shown}: ").strip() or default
        problem = check(answer) if check else None
        if problem is None:
            return answer
        print(problem)


def confirm(question: str, default: bool = True) -> bool:
    while True:
        answer = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Answer y or n.")


def check_command(command: str) -> str | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return "Couldn't parse the quotes in that command; check they are balanced."
    if not argv:
        return "Enter the command that runs the CLI."
    if SHELL_OPERATORS & set(argv):
        return "Council runs commands without a shell, so pipes, redirects, and && can't be used."
    for token in argv:
        for placeholder in PLACEHOLDERS:
            if placeholder in token and token != placeholder:
                return (f"Write {placeholder} as a separate word, e.g. `--flag {placeholder}`, "
                        f"not `{token}`.")
    return None


def describe_command(cfg: dict, name: str, validate) -> dict:
    print("\nType the command as you would in a terminal. Council fills in:")
    print("  {prompt}   where the question goes; leave it out to send the question on stdin")
    print("  {outfile}  where the CLI writes its answer file (file-based output only)")
    print("  {model}    after the CLI's model flag, to allow a model override")
    print("Example: ollama run llama3.2 {prompt}")
    while True:
        argv = shlex.split(ask("Command", check=check_command))
        if shutil.which(argv[0]) is None:
            print(f"Note: {argv[0]!r} is not on PATH right now; the test below will fail until it is.")
        prompt_via = "argv" if "{prompt}" in argv else "stdin"
        if prompt_via == "stdin":
            print("No {prompt} in the command, so the question will be sent on standard input.")
        print("\nHow does the CLI return its answer?")
        for mode, text in OUTPUT_STYLES.items():
            print(f"  {mode}: {text}")
        output = choose("Output", list(OUTPUT_STYLES), "file" if "{outfile}" in argv else "stdout")
        entry = {"name": name, "argv": argv, "prompt_via": prompt_via, "output_mode": output, "model": ""}
        try:
            validate(copy.deepcopy(cfg) | {"agents": [*copy.deepcopy(cfg["agents"]), entry | {"enabled": False}]})
            return entry
        except ValueError as exc:
            print(f"That command can't be used: {exc}. Let's try again.")


def add_agent(cfg: dict, *, validate, probe) -> dict | None:
    """Return a validated copy of cfg with one new agent appended, or None if the
    user declines to keep an agent whose test failed. `validate` checks a whole
    configuration; `probe` sends one test request and returns run_agent's result."""
    taken = {agent["name"] for agent in cfg["agents"]}

    def check_name(name):
        if not NAME.fullmatch(name):
            return "Use letters, digits, hyphens, or underscores, starting with a letter or digit."
        return f"An agent named {name!r} already exists." if name in taken else None

    print("\nAdd an agent to the council.")
    print("Built-in adapters: " + ", ".join(ADAPTERS))
    template = ask("Start from a built-in adapter (Enter for a custom command)",
                   check=lambda v: None if not v or v in ADAPTERS
                   else f"Unknown adapter {v!r}; leave it empty for a custom command.")
    name = ask("Name for this agent", default=template if template and template not in taken else "",
               check=check_name)
    if template:
        entry = {"adapter": template, "name": name}
        argv, default_role = list(ADAPTERS[template].argv), ADAPTERS[template].role
    else:
        entry = describe_command(cfg, name, validate)
        argv, default_role = entry["argv"], "reviewer"

    catalog = role_catalog(cfg)
    print("\nRoles: " + ", ".join(catalog))
    role = choose(f"Role for {name}", [*catalog, "custom"], default_role)
    if role == "custom":
        entry["role"] = ask(f"Describe {name}'s responsibilities",
                            check=lambda v: None if v else "A custom role needs a description.")
    else:
        entry["role_preset"] = role
    if "{model}" in argv:
        entry["model"] = ask("Model to use (Enter keeps the CLI's own default)")
    entry["enabled"] = confirm("Include it in every council run? (No: only when named with --agents)")

    result = validate(copy.deepcopy(cfg) | {"agents": [*copy.deepcopy(cfg["agents"]), entry]})
    agent = next(a for a in result["agents"] if a["name"] == name)
    if confirm("Test it now? This sends one short request through the real CLI"):
        outcome = probe(agent)
        if outcome["ok"]:
            print(f"Test passed in {outcome['seconds']}s. It answered: {outcome['answer'][:300]}")
        else:
            print(f"Test failed: {outcome['error'][:800]}")
            if not confirm("Save it anyway?", default=False):
                return None
    return result
