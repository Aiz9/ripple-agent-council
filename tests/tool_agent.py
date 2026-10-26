"""Request one approved command, then use the normal local debate fixture."""
import json
import os
from pathlib import Path
import runpy
import sys

name, mode, prompt = sys.argv[1:4]
if prompt == "-":
    prompt = sys.stdin.read()
    sys.argv[3] = prompt
if "\nROUND 0 —" in prompt and "\nCOUNCIL TOOL RESULT" not in prompt:
    if os.environ.get("NATIVE_TOOL_DENIAL") and "NATIVE TOOL ACCESS FAILED:" not in prompt:
        print('headless tool command permission denied', file=sys.stderr)
        raise SystemExit(1)
    root = Path(os.environ["TOOL_FIXTURE_ROOT"])
    request = {"tool": "shell", "argv": [sys.executable, "-c",
               "from pathlib import Path; p=Path('approved-count'); "
               "p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); print('REAL_TOOL_RESULT')"],
               "cwd": str(root), "reason": "Run the local approval fixture"}
    answer = "```council-tool\n" + json.dumps(request) + "\n```"
    if mode == "claude_json":
        print(json.dumps({"result": answer, "is_error": False, "total_cost_usd": 0.01}))
    else:
        print(answer)
else:
    if "\nROUND 0 —" in prompt:
        assert "REAL_TOOL_RESULT" in prompt or "denied" in prompt
    runpy.run_path(str(Path(__file__).with_name("fake_agent.py")), run_name="__main__")
