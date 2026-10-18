# Global installation

## Install

From a source checkout, run:

```bash
uv tool install .
council --version
council --doctor
```

Or supply the checkout's absolute path from any directory:

```bash
uv tool install /path/to/agent-council
```

uv creates a dedicated environment for the tool and exposes its `council` executable
in the user executable directory, usually `~/.local/bin`. No `sudo` is needed.
If the executable directory is absent from PATH, run `uv tool update-shell` and open
a new terminal. Inspect `command -v council` to confirm which executable is active.
If a legacy symlink occupies that name, move it aside before installing.

This is a regular, non-editable package installation. It includes Python code and
the default agent configuration, so the checkout can move after installation.
The existing authenticated `claude`, `codex`, `agy`, and `hermes` commands must remain
on PATH. Council does not install or reconfigure those tools.

You can also build and install a wheel:

```bash
uv build
uv tool install dist/agent_council-0.5.0-py3-none-any.whl
```

Source development uses the locked project environment (`uv sync --locked`).
`uv tool install` resolves the package's declared dependencies independently of
`uv.lock`. It may need network access unless compatible wheels and index metadata
are already cached.

## Use from any directory

```bash
cd /tmp
council --doctor
council "Compare two approaches to the problem."
```

Relative `--prompt-file`, `--config`, and `--sessions-dir` arguments resolve from the
directory you run the command in. Council does not automatically load an `agents.yaml`
from that directory or use it as the build workspace. Agent working directories and
build outputs live inside each session.

## User configuration and session storage

Run `council --setup` to choose and save coordination mode, leader, and agent roles.
It preserves existing values and backs up the configuration before replacing it.
For a default file without interactive questions:

```bash
council --init-config
council --paths
```

`--init-config` creates the user config only if missing. Re-running it and reinstalling
the package preserve your edits. Defaults apply until you create a user config.
The setup command honors `--config PATH` and `COUNCIL_CONFIG` for a custom destination.

| Purpose | Default | Override precedence |
| --- | --- | --- |
| Agent configuration | `~/.config/agent-council/agents.yaml`, otherwise bundled defaults | `--config`, `COUNCIL_CONFIG`, user config under `XDG_CONFIG_HOME` |
| Sessions | `~/.local/state/agent-council/sessions` | `--sessions-dir`, `COUNCIL_SESSIONS_DIR`, directory under `XDG_STATE_HOME` |

`--paths` prints JSON and does not create directories or contact agents. The packaged
default configuration is read-only application data; customize the user copy instead.
Hermes continues reading its own model and provider settings.

## Migrate an earlier checkout

Old sessions stay where they were; the new installation does not move or delete them.
You can keep using their parent directory with `--sessions-dir /path/to/old-checkout/sessions`.
To reuse an existing agent configuration for one invocation:

```bash
council --config /path/to/old-checkout/agents.yaml --doctor
```

For a permanent migration, run `council --init-config` and transfer the settings you
want into the displayed user file. Keep the new default session-specific Claude
configuration path unless you intentionally want the earlier shared-directory behavior.
Global mode no longer implicitly selects the checkout's former `agents.yaml`.

## Update or remove

After changing or updating the source, reinstall the package, check it, and review
your choices:

```bash
uv tool install --reinstall /path/to/agent-council
council --version
council --doctor
council --setup
```

Reinstalling replaces the program and its bundled defaults but never your user
configuration. Because configuration files are complete rather than merged with the
defaults, improvements to the bundled agent commands only reach you if you have no
user configuration yet, or if you copy them in. For example, a configuration created
before 0.4.0 still sends Claude and Codex prompts as arguments; see
[prompt delivery](configuration.md#prompt-delivery) for the two-line change. Running
`council --setup` on such a file keeps its commands and adds any new settings, such as
`peer_turns`, when you save.

Remove the command and its uv tool environment with:

```bash
uv tool uninstall agent-council
```

User configuration and session history remain after uninstall. Manage those files
separately; sessions can contain private prompts and copied Claude credentials.
