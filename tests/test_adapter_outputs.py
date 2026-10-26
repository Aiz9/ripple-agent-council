import json
import unittest

from agent_council.outputs import OUTPUT_MODES, parse_structured


def opencode(*events):
    return "\n".join(json.dumps(event) for event in events)


def event(kind, message="answer", **fields):
    return {"type": kind, "part": {"messageID": message, **fields}}


def qwen_result(**changes):
    return {"type": "result", "subtype": "success", "is_error": False,
            "result": "Final answer", **changes}


class AdapterOutputTests(unittest.TestCase):
    def test_registered_modes_and_unknown_mode(self):
        self.assertEqual(OUTPUT_MODES, {"gemini_json", "qwen_json", "opencode_json", "cursor_json"})
        with self.assertRaises(ValueError):
            parse_structured("unknown", "{}")

    def test_cursor_extracts_successful_result(self):
        stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 9,
                             "result": " Final answer\n", "session_id": "s"})
        self.assertEqual(parse_structured("cursor_json", stdout), "Final answer")

    def test_cursor_rejects_errors_and_incomplete_results(self):
        ok = {"type": "result", "subtype": "success", "is_error": False, "result": "answer"}
        for obj in ([], None, {}, ok | {"is_error": True}, ok | {"is_error": "false"},
                    ok | {"subtype": "error"}, ok | {"type": "assistant"}, ok | {"result": ""},
                    ok | {"result": 3}):
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                parse_structured("cursor_json", json.dumps(obj))

    def test_gemini_extracts_only_response(self):
        stdout = json.dumps({"response": " Final answer\n", "stats": {"diagnostic": "ignore"}})
        self.assertEqual(parse_structured("gemini_json", stdout), "Final answer")

    def test_gemini_rejects_errors_and_invalid_responses(self):
        for obj in ([], None, "answer", {}, {"response": 4}, {"response": " "},
                    {"response": "partial", "error": {"message": "quota"}},
                    {"response": "partial", "error": None}):
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                parse_structured("gemini_json", json.dumps(obj))

    def test_qwen_extracts_terminal_result_not_assistant_or_tool_text(self):
        stdout = json.dumps([
            {"type": "system", "subtype": "session_start"},
            {"type": "assistant", "message": {"content": [{"text": "planning"}]}},
            {"type": "user", "message": {"content": [{"tool_result": "diagnostic"}]}},
            qwen_result(),
        ])
        self.assertEqual(parse_structured("qwen_json", stdout), "Final answer")

    def test_qwen_requires_genuine_terminal_success(self):
        cases = [
            {}, [], [None], [{"type": "assistant", "result": "partial"}],
            [qwen_result(), {"type": "assistant"}],
            [qwen_result(is_error=True)], [qwen_result(is_error="false")],
            [qwen_result(is_error=0)], [qwen_result(subtype="error_during_execution")],
            [qwen_result(result=None)], [qwen_result(result="")],
            [{"type": "result", "result": "missing success flag"}],
            [{"type": "error", "message": "quota"}, qwen_result()],
            [qwen_result(error={"message": "failed"})],
        ]
        for obj in cases:
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                parse_structured("qwen_json", json.dumps(obj))

    def test_opencode_filters_tool_steps_reasoning_and_diagnostics(self):
        stdout = opencode(
            event("step_start", "tool-step"),
            event("text", "tool-step", text="I will inspect this."),
            event("tool_use", "tool-step", state={"output": "tool output"}),
            event("step_finish", "tool-step", reason="tool-calls"),
            event("step_start"),
            event("reasoning", text="private reasoning"),
            event("text", text="Final "),
            event("text", text="answer"),
            event("step_finish", reason="stop"),
            {"type": "diagnostic", "message": "ignored"},
        )
        self.assertEqual(parse_structured("opencode_json", stdout), "Final answer")

    def test_opencode_allows_reconciled_text_for_completed_message(self):
        stdout = opencode(event("text", text="Final "),
                          event("step_finish", reason="stop"),
                          event("text", text="answer"))
        self.assertEqual(parse_structured("opencode_json", stdout), "Final answer")

    def test_opencode_cannot_reuse_earlier_message_as_final_answer(self):
        stdout = opencode(event("text", "tool-step", text="Earlier text"),
                          event("step_finish", "tool-step", reason="tool-calls"),
                          event("step_finish", reason="stop"))
        with self.assertRaises(ValueError):
            parse_structured("opencode_json", stdout)

    def test_opencode_discards_tool_step_text_when_message_id_is_reused(self):
        stdout = opencode(event("text", text="Earlier tool planning"),
                          event("step_finish", reason="tool-calls"),
                          event("step_start"), event("text", text="Final answer"),
                          event("step_finish", reason="stop"))
        self.assertEqual(parse_structured("opencode_json", stdout), "Final answer")

    def test_opencode_rejects_partial_error_or_malformed_events(self):
        complete = [event("text", text="Final answer"), event("step_finish", reason="stop")]
        cases = [
            "", opencode(event("text", text="partial")),
            opencode(event("text", text="partial"), event("step_finish", reason="length")),
            opencode(*complete, {"type": "error", "error": {"message": "quota"}}),
            opencode(*complete, event("step_start", "unfinished")),
            opencode(*complete, event("text", "unfinished", text="partial")),
            opencode(*complete, event("tool_use")),
            opencode(*complete, event("step_start"), event("step_finish", reason="stop")),
            opencode(event("text", text=7), event("step_finish", reason="stop")),
            opencode(event("text", message=7, text="invalid")),
            opencode(event("step_finish", reason=None)),
            opencode({"type": "text", "part": []}),
            opencode({"type": 5}), opencode([]), opencode(None),
            opencode(*complete) + '\n{"type":',
        ]
        for stdout in cases:
            with self.subTest(stdout=stdout), self.assertRaises(ValueError):
                parse_structured("opencode_json", stdout)

    def test_malformed_json_and_diagnostic_prefixes_are_not_answers(self):
        for mode in OUTPUT_MODES:
            for stdout in ("not JSON", "warning\n{}", '{"response": "truncated'):
                with self.subTest(mode=mode, stdout=stdout), self.assertRaises(ValueError):
                    parse_structured(mode, stdout)


if __name__ == "__main__":
    unittest.main()
