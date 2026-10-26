"""Local fixture with externally controlled failures and persistent call counts."""
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time

name, prompt = sys.argv[1:3]
if prompt == "-":
    prompt = sys.stdin.read()
root = Path(os.environ["FIXTURE_STATE"])
control = json.loads((root / "control.json").read_text())
if "\nFINAL SYNTHESIS." in prompt:
    phase = "final"
elif match := re.search(r"\nLEADER (PLAN|REVIEW) (\d+)\.", prompt):
    phase = match[1].lower() + match[2]
else:
    phase = "round" + re.search(r"\nROUND (\d+) —", prompt)[1]
key = phase + "." + name
counts = root / (key + ".count")
count = int(counts.read_text()) + 1 if counts.exists() else 1
counts.write_text(str(count))
(root / (key + ".pid")).write_text(str(os.getpid()))
failure = control.get("fail", {}).get(key)
if failure == "capacity":
    print("You’ve hit your usage limit. Try again later.")
    sys.exit(0)  # Deliberately mimic CLIs which report a limit with exit zero.
if failure == "transient_once" and count == 1:
    print("503 Service unavailable", file=sys.stderr)
    sys.exit(1)
if key in control.get("hang", []):
    print("started", flush=True)
    time.sleep(60)
if phase == "round0":
    print("TASK_TYPE: question\n- [ ] Verify evidence\n- [ ] Preserve work")
elif phase == "final":
    if "BUILD OUTPUT:" in prompt:
        script = "import os; from pathlib import Path; p=Path('runs'); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); assert os.path.exists('fixed'), 'needs repair'"
        print(f"FILE: test.py\n```python\n{script}\n```\nRUN: {shlex.quote(sys.executable)} test.py")
    else:
        print("Completed with retained evidence and checklist status.")
elif phase.startswith("plan"):
    leader = re.search(r"Leader: ([^.]+)\.", prompt)[1]
    assert name == leader
    print(f"{name} assigns remaining specialists their role-specific verification work.")
else:
    if phase.startswith("review"):
        leader = re.search(r"Leader: ([^.]+)\.", prompt)[1]
        assert name == leader
    print(f"EVIDENCE_{name}: {phase} verified.")
    if phase.endswith("3"):
        print("CONSENSUS: yes")
