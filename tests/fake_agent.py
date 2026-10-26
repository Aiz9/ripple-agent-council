"""Deterministic CLI fixture. Never calls a model or uses real credentials."""
import json
import os
import re
from pathlib import Path
import sys
import time

name, mode, prompt = sys.argv[1:4]
if prompt == "-":  # prompt_via: stdin
    prompt = sys.stdin.read()
scenario = os.environ.get("SCENARIO", "question")
if "\nEVALUATION JUDGE." in prompt:
    phase = "judge"
elif "\nBASELINE REQUEST." in prompt:
    phase = "baseline"
elif "\nFINAL SYNTHESIS." in prompt:
    phase = "final"
elif "\nPEER RANKING." in prompt:
    phase = "ranking"
elif "\nROUND 3 —" in prompt:
    phase = "round3"
elif "\nROUND 2 —" in prompt:
    phase = "round2"
elif "\nROUND 1 —" in prompt:
    phase = "round1"
else:
    phase = "round0"

print(f"fixture {name} {phase}", file=sys.stderr)
if scenario == "sleep":
    print("started", flush=True)
    time.sleep(60)
if (scenario == "fail_all" and phase == "round1"
        or scenario == "drop" and name == "gamma" and phase == "round2"
        or scenario == "lose_quorum" and name != "alpha" and phase == "round2"
        or scenario == "fail_frame" and phase == "round0"
        or scenario == "fail_final" and phase == "final"
        or scenario == "fallback" and name == "alpha" and phase == "final"):
    sys.exit(7)

if phase == "round0":
    answer = "TASK_TYPE: question\n- [ ] Include evidence\n- [ ] Address disagreement"
elif phase == "round1":
    # Fail if another agent wrote into this agent's current working directory.
    marker = Path("owner.txt")
    assert not marker.exists() or marker.read_text() == name
    marker.write_text(name)
    answer = f"[VERIFIED: fixture] ORIGINAL_EVIDENCE_{name}. CHALLENGE: retain this claim."
    if "\nGUARDRAIL FEEDBACK:" in prompt and not (scenario == "stubborn" and name == "gamma"):
        answer = f"[VERIFIED: fixture] ORIGINAL_EVIDENCE_{name}. Revised to comply."
elif phase == "round2":
    assert "ORIGINAL_EVIDENCE_alpha" in prompt
    answer = "The earlier challenge remains open."
elif phase == "round3":
    assert "ORIGINAL_EVIDENCE_alpha" in prompt
    signature = f"I am {name.upper()}, and Alpha agrees. " if scenario == "named" else ""
    answer = f"{signature}Resolved using the original evidence.\nCONSENSUS: yes"
elif phase == "ranking":
    labels = re.findall(r"(?m)^--- Response ([A-Z]) ---$", prompt)
    if scenario == "bad_ballot" and name == "gamma":
        answer = "RANKING: " + " > ".join([labels[0]] * len(labels))
    else:
        answer = "Each response cites the original evidence.\nRANKING: " + " > ".join(sorted(labels))
elif phase == "judge":
    graded = prompt.split("ANSWER TO GRADE:", 1)[1]
    answer = "Grounded." + "\nSCORE: " + ("8" if "original evidence" in graded else "3")
elif "BUILD OUTPUT:" in prompt:
    code = "raise SystemExit(3)" if scenario == "bad_build" else "assert 2 + 2 == 4\nprint('passed')"
    import shlex
    answer = f"FILE: __init__.py\n```python\n```\nFILE: test_generated.py\n```python\n{code}\n```\nRUN: {shlex.quote(sys.executable)} test_generated.py"
elif phase == "baseline":
    answer = f"Single-agent answer from {name}."
else:
    assert "ORIGINAL_EVIDENCE_alpha" in prompt
    answer = "Final answer includes original evidence and checklist status."

if mode == "claude_json":
    print(json.dumps({"result": answer, "is_error": False, "total_cost_usd": 0.25,
                      "usage": {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 30}}))
elif mode == "claude_stream_json":  # the event shapes Claude Code 2.1 emits
    emit = lambda event: print(json.dumps(event), flush=True)
    emit({"type": "system", "subtype": "init", "session_id": "s"})
    emit({"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_start"}})
    emit({"type": "stream_event", "parent_tool_use_id": "tool-1", "event": {
        "type": "content_block_delta", "delta": {"type": "text_delta", "text": "SUBAGENT TEXT\n"}}})
    for piece in re.findall(r"\S+\s*", answer):
        emit({"type": "stream_event", "parent_tool_use_id": None, "event": {
            "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}}})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": answer, "total_cost_usd": 0.25,
          "usage": {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 30}})
elif mode == "file":
    Path(sys.argv[4]).write_text(answer)
    print("diagnostic stdout, not the final answer")
    print("tokens used\n1,000", file=sys.stderr)  # as Codex reports usage
elif mode == "gemini_json":
    print(json.dumps({"response": answer, "stats": {"models": {}}}))
elif mode == "qwen_json":
    print(json.dumps([{"type": "system", "subtype": "init"},
                      {"type": "assistant", "message": "draft, not the answer"},
                      {"type": "result", "subtype": "success", "is_error": False, "result": answer}]))
elif mode == "opencode_json":
    for event in ({"type": "step_start", "part": {"messageID": "m1"}},
                  {"type": "text", "part": {"messageID": "m1", "text": answer}},
                  {"type": "step_finish", "part": {"messageID": "m1", "reason": "stop"}}):
        print(json.dumps(event))
elif mode == "cursor_json":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer}))
elif mode == "aider_history":
    response = "\n".join(f"ASSISTANT {line}" for line in answer.splitlines())
    Path(sys.argv[4]).write_text("TO LLM 2026-10-18T00:00:00\nUSER the request\n"
                                 f"LLM RESPONSE 2026-10-18T00:00:01\n{response}\n")
    print("Aider console output, not the answer")
else:
    print(answer)
