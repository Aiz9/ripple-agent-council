"""Built-in, opt-in adapters for widely used agent CLIs.

Each entry is a configuration template whose flags were checked against the CLI's
documentation or source; see docs/adapters.md. Discovery only looks for executables
on PATH: it never installs anything, tests authentication, or calls a model.
An empty model drops the `--model {model}` pair, so each CLI keeps its own default.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import shutil


@dataclass(frozen=True)
class Adapter:
    title: str
    argv: tuple[str, ...]
    output_mode: str = "stdout"
    prompt_via: str = "argv"
    role: str = "reviewer"
    env: dict[str, str] = field(default_factory=dict)
    source: str = ""
    note: str = ""

    def configuration(self, name: str) -> dict:
        config = {"name": name, "adapter": name, "enabled": True, "argv": list(self.argv),
                  "output_mode": self.output_mode, "prompt_via": self.prompt_via,
                  "model": "", "role_preset": self.role}
        if self.env:
            config["env"] = copy.deepcopy(self.env)
        return config


ADAPTERS = {
    "claude": Adapter(
        "Claude Code", ("claude", "-p", "--output-format", "stream-json", "--verbose",
                        "--include-partial-messages", "--model", "{model}"),
        "claude_stream_json", "stdin", "architect", {"CLAUDE_CONFIG_DIR": "{session}/claude-config"},
        "https://code.claude.com/docs/en/headless; stdin and stream-json tested 2026-10-18"),
    "codex": Adapter(
        "OpenAI Codex", ("codex", "exec", "--skip-git-repo-check", "--model", "{model}", "-o", "{outfile}", "-"),
        "file", "stdin", "implementer", source="codex exec --help; stdin tested 2026-10-18"),
    "agy": Adapter(
        "Google Antigravity", ("agy", "--print", "{prompt}", "--sandbox", "--model", "{model}"),
        role="researcher", source="agy --help; argv-only, tested 2026-10-18",
        note="--sandbox does not approve headless tool requests."),
    "hermes": Adapter(
        "Hermes Agent", ("hermes", "-z", "{prompt}", "-m", "{model}"), role="skeptic",
        source="https://hermes-agent.nousresearch.com/docs/user-guide/cli/; argv-only, tested 2026-10-18",
        note="An empty model keeps Hermes's configured provider and model."),
    "gemini": Adapter(
        "Gemini CLI", ("gemini", "--approval-mode", "default", "--output-format", "json", "--model", "{model}",
                       "-p", "{prompt}"),
        "gemini_json", role="researcher",
        source="https://geminicli.com/docs/cli/headless/ and /docs/cli/cli-reference",
        note="Replaced by Antigravity (agy) in June 2026; personal Google sign-in is no longer "
             "accepted, so use an API key or Vertex AI. Default approval mode: tools needing approval fail."),
    "qwen": Adapter(
        "Qwen Code", ("qwen", "--approval-mode", "default", "--output-format", "json", "--model", "{model}",
                      "-p", "{prompt}"),
        "qwen_json", role="implementer",
        source="https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/",
        note="A separate CLI from Hermes, even when Hermes uses a Qwen model."),
    "opencode": Adapter(
        "OpenCode", ("opencode", "run", "--agent", "plan", "--format", "json", "--model", "{model}"),
        "opencode_json", "stdin", "architect",
        source="https://opencode.ai/docs/cli/; stdin and JSON events from packages/cli/src/run (dev)",
        note="Built-in plan agent. Without --auto, run rejects permission requests instead of prompting."),
    "copilot": Adapter(
        "GitHub Copilot CLI", ("copilot", "-p", "{prompt}", "-s", "--no-ask-user",
                               "--available-tools=view,grep,glob", "--model", "{model}"),
        role="reviewer",
        source="https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference "
               "and .../copilot-cli/use-copilot-cli/allowing-tools",
        note="Only the read-only view/grep/glob tools are available: no shell or edit tools."),
    "cursor": Adapter(
        "Cursor CLI", ("agent", "-p", "--mode", "ask", "--output-format", "json", "--model", "{model}", "{prompt}"),
        "cursor_json", role="reviewer",
        source="https://cursor.com/docs/cli/reference/parameters and /output-format",
        note="Ask mode. The executable is named `agent`; set argv[0] if yours differs (e.g. legacy cursor-agent)."),
    "goose": Adapter(
        "Goose", ("goose", "run", "--no-session", "--quiet", "--output-format", "text", "--model", "{model}",
                  "-i", "-"),
        "stdout", "stdin", "researcher", {"GOOSE_MODE": "chat", "GOOSE_DISABLE_SESSION_NAMING": "true"},
        "https://goose-docs.ai/docs/guides/goose-cli-commands/ and /environment-variables/",
        "Chat Only mode: no extension use or file modifications."),
    "kiro": Adapter(
        "Kiro CLI", ("kiro-cli", "chat", "--v3", "--no-interactive", "--trust-tools=read_file,grep_search",
                     "--model", "{model}"),
        "stdout", "stdin", "reviewer", source="https://kiro.dev/docs/cli/headless/",
        note="Headless use requires KIRO_API_KEY (paid plans). Only read_file/grep_search are auto-approved."),
    "aider": Adapter(
        "Aider", ("aider", "--chat-mode", "ask", "--dry-run", "--no-git", "--no-auto-commits", "--no-stream",
                  "--no-pretty", "--no-check-update", "--llm-history-file", "{outfile}", "--model", "{model}",
                  "--message", "{prompt}"),
        "aider_history", role="reviewer",
        source="https://aider.chat/docs/config/options.html and /docs/usage/modes.html",
        note="Ask mode never edits; --dry-run, --no-git and --no-auto-commits guard against "
             "Aider's default auto-commit behavior."),
}


def expand_adapter(entry: dict) -> dict:
    """Expand an `adapter:` entry from its template; explicit fields in the entry win."""
    adapter_id = entry.get("adapter")
    if adapter_id is None:
        return copy.deepcopy(entry)
    if not isinstance(adapter_id, str) or adapter_id not in ADAPTERS:
        raise ValueError(f"unknown adapter {adapter_id!r}; use --adapters to list built-ins")
    result = ADAPTERS[adapter_id].configuration(adapter_id)
    if "role" in entry and "role_preset" not in entry:
        result.pop("role_preset", None)
    # Keep the template's safety environment when users add unrelated variables.
    if isinstance(entry.get("env"), dict):
        result["env"] = result.get("env", {}) | entry["env"]
    result.update({key: copy.deepcopy(value) for key, value in entry.items() if key != "env"})
    if "env" in entry and not isinstance(entry["env"], dict):
        result["env"] = entry["env"]  # Let configuration validation report the invalid type.
    return result


def select_agents(cfg: dict, requested: str) -> dict:
    """Return a copy of cfg whose roster is exactly the requested names or adapter IDs."""
    names = [name.strip() for name in requested.split(",")]
    if not names or any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError("--agents needs a comma-separated list of distinct agent names or adapter IDs")
    result = copy.deepcopy(cfg)
    existing = {entry["name"]: entry for entry in cfg["agents"]}
    chosen = []
    for name in names:
        if name in existing:
            entry = copy.deepcopy(existing[name])
            entry["enabled"] = True
        elif name in ADAPTERS:
            entry = ADAPTERS[name].configuration(name)
        else:
            raise ValueError(f"unknown agent or adapter {name!r}; use --adapters to list built-ins")
        chosen.append(entry)
    result["agents"] = chosen
    if len(chosen) < result["settings"].get("min_agents", 2):
        raise ValueError("--agents must select at least min_agents agents")
    # The synthesis prompt carries the whole debate, so prefer a stdin agent to write it.
    stdin_names = [entry["name"] for entry in chosen if entry.get("prompt_via") == "stdin"]
    for key in ("framer", "synthesizer", "leader"):
        if result["settings"].get(key) not in names:
            result["settings"][key] = (stdin_names or names)[0] if key == "synthesizer" else names[0]
    return result


def list_adapters() -> None:
    print(f"{'Adapter':<10} {'Executable':<10} {'Found':<6} {'Prompt':<7} CLI")
    for name, adapter in ADAPTERS.items():
        executable = adapter.argv[0]
        found = "yes" if shutil.which(executable) else "no"
        print(f"{name:<10} {executable:<10} {found:<6} {adapter.prompt_via:<7} {adapter.title}")
    print("Found means the executable is on PATH; authentication is not checked and no model is called.")
    print('Use one: council --agents claude,gemini "prompt"   Details: docs/adapters.md')
