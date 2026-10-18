"""Fixture that fails when coordination delivers the wrong conversation context."""
import os
import re
import sys

name, prompt = sys.argv[1:3]
if prompt == "-":  # prompt_via: stdin
    prompt = sys.stdin.read()
leader = os.environ.get("LEADER", "alpha")
scenario = os.environ.get("SCENARIO", "normal")
names = ["alpha", "beta", "gamma"]
hierarchical = "COORDINATION: hierarchical" in prompt
parallel_turns = "PEER TURNS: parallel" in prompt
vote = "CONSENSUS: no" if scenario == "dissent" and name == "beta" else "CONSENSUS: yes"

if "\nFINAL SYNTHESIS." in prompt:
    if hierarchical:
        assert name == leader
        assert "LEADER_REVIEW_3" in prompt
    if scenario == "final_fail":
        sys.exit(7)
    if "BUILD OUTPUT:" in prompt:
        import shlex
        print(f"FILE: test.py\n```python\nassert 2 + 2 == 4\n```\nRUN: {shlex.quote(sys.executable)} test.py")
    else:
        print("Final answer with disagreements and checklist status.")
elif match := re.search(r"\nLEADER PLAN (\d+)\.", prompt):
    assert name == leader
    rnd = int(match[1])
    if scenario == "plan_fail":
        sys.exit(7)
    if rnd > 1:
        for worker in names:
            if worker != leader:
                assert f"SPECIALIST_EVIDENCE_{worker}" in prompt
    print("\n".join(f"{worker}: ASSIGNMENT_FOR_{worker}: investigate your specialty." for worker in names if worker != leader))
elif match := re.search(r"\nLEADER REVIEW (\d+)\.", prompt):
    assert name == leader
    rnd = int(match[1])
    if scenario == "review_fail":
        sys.exit(7)
    for worker in names:
        if worker != leader:
            assert f"SPECIALIST_EVIDENCE_{worker}" in prompt
    print(f"LEADER_REVIEW_{rnd}: inspected specialist evidence.")
    if rnd >= 3:
        print(vote)
elif "\nROUND 0 —" in prompt:
    assert not hierarchical or name == leader
    print("TASK_TYPE: question\n- [ ] Review evidence\n- [ ] Resolve disagreements")
else:
    rnd = int(re.search(r"\nROUND (\d+) —", prompt)[1])
    if scenario == "worker_drop" and name == "gamma" and rnd == 2:
        sys.exit(7)
    if hierarchical:
        assert name != leader
        assert f"ASSIGNMENT_FOR_{name}" in prompt
        for peer in names:
            if peer != name:
                assert f"SPECIALIST_EVIDENCE_{peer}" not in prompt, "raw peer reports leaked to worker"
        print(f"SPECIALIST_EVIDENCE_{name}: report directly to {leader}.")
    elif rnd == 1:
        for peer in names:
            assert f"PEER_EVIDENCE_{peer}" not in prompt
        print(f"PEER_EVIDENCE_{name}")
    elif parallel_turns and rnd == 2:
        for peer in names:
            assert f"PEER_EVIDENCE_{peer}" in prompt, "completed round not delivered"
            assert f"PARALLEL_POINT_{peer}" not in prompt, "parallel turn saw a same-round reply"
        print(f"PARALLEL_POINT_{name}: reviewed every round-1 answer.")
    elif parallel_turns:
        for peer in names:
            assert f"PARALLEL_POINT_{peer}" in prompt, "completed parallel round not delivered"
            assert f"PARALLEL_RESOLVED_{peer}" not in prompt, "parallel turn saw a same-round reply"
        print(f"PARALLEL_RESOLVED_{name}: resolved after the parallel round.")
    elif rnd == 2:
        if name == "alpha":
            print("beta: ALPHA_QUESTION — verify the evidence for me.")
        elif name == "beta":
            assert "ALPHA_QUESTION" in prompt, "same-round direct message not delivered"
            print("alpha: BETA_REPLY — I inspected your evidence and found the missing assumption.")
        else:
            assert "BETA_REPLY" in prompt
            print("alpha and beta: I reviewed your exchange.")
    else:
        assert "BETA_REPLY" in prompt
        print(f"{name}: resolved the direct exchange.")
    if rnd >= 3:
        print(vote)
