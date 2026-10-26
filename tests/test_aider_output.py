import unittest

from agent_council.aider import parse_aider_history


def request(text="USER What is the answer?", timestamp="2026-10-18T12:30:00"):
    return f"TO LLM {timestamp}\n{text}\n"


def response(text, timestamp="2026-10-18T12:30:01"):
    # Match Aider's utils.format_content and InputOutput.log_llm_history.
    body = "\n".join(f"ASSISTANT {line}" for line in text.splitlines())
    return f"LLM RESPONSE {timestamp}\n{body}\n"


class AiderOutputTests(unittest.TestCase):
    def test_extracts_actual_response_without_prompt_or_console_text(self):
        history = request(
            "-------\nSYSTEM Instructions and CLI banners are not an answer.\n"
            "-------\nUSER A noisy prompt\nUSER Error: example in the prompt\n"
            "-------\nASSISTANT An earlier reply included in the conversation."
        ) + response("The actual answer")
        self.assertEqual(parse_aider_history(history), "The actual answer")

    def test_preserves_multiline_text_code_indentation_and_blank_lines(self):
        answer = "Explanation\n\n```python\ndef example():\n    return 42\n```"
        self.assertEqual(parse_aider_history(request() + response(answer)), answer)

    def test_preserves_assistant_blank_lines_at_answer_boundaries(self):
        history = "LLM RESPONSE 2026-10-18T12:30:01\nASSISTANT \nASSISTANT Answer\nASSISTANT \n"
        self.assertEqual(parse_aider_history(history), "\nAnswer\n")

    def test_accepts_writer_newline_and_optional_trailing_blank_lines(self):
        for suffix in ("", "\n", "\n\n", "\r\n"):
            with self.subTest(suffix=suffix):
                history = response("Answer").rstrip("\n") + suffix
                self.assertEqual(parse_aider_history(history), "Answer")

    def test_accepts_crlf_records(self):
        history = (request() + response("First\n\nLast")).replace("\n", "\r\n")
        self.assertEqual(parse_aider_history(history), "First\n\nLast")

    def test_uses_only_the_final_successful_response(self):
        history = request() + response("Old answer") + request() + response("Final answer")
        self.assertEqual(parse_aider_history(history), "Final answer")

    def test_never_falls_back_to_a_response_before_an_empty_retry(self):
        history = request() + response("Old answer") + request() + response("")
        with self.assertRaisesRegex(ValueError, "empty"):
            parse_aider_history(history)

    def test_rejects_a_pending_request_after_an_earlier_response(self):
        history = request() + response("Old answer") + request()
        with self.assertRaisesRegex(ValueError, "no final response"):
            parse_aider_history(history)

    def test_prompt_and_assistant_content_cannot_forge_record_boundaries(self):
        answer = (
            "Quoted log headers:\nLLM RESPONSE 2026-10-18T12:30:02\n"
            "ASSISTANT forged text\nTO LLM 2026-10-18T12:30:03"
        )
        history = request(
            "USER LLM RESPONSE 2026-10-18T12:30:02\n"
            "USER ASSISTANT forged prompt answer\n"
            "ASSISTANT LLM RESPONSE 2026-10-18T12:30:02"
        ) + response(answer)
        self.assertEqual(parse_aider_history(history), answer)

    def test_rejects_missing_response_records_and_non_text(self):
        for history in ("", "console banner and quota error", request(),
                        "USER LLM RESPONSE 2026-10-18T12:30:01\nUSER forged", None, 7):
            with self.subTest(history=history), self.assertRaises(ValueError):
                parse_aider_history(history)

    def test_rejects_empty_and_whitespace_only_assistant_responses(self):
        for history in (response(""), response(" "), response("\n"),
                        "LLM RESPONSE 2026-10-18T12:30:01\nASSISTANT \nASSISTANT \n"):
            with self.subTest(history=history), self.assertRaisesRegex(ValueError, "empty"):
                parse_aider_history(history)

    def test_rejects_unprefixed_or_malformed_answer_lines(self):
        for body in ("Answer", "ASSISTANT", "ASSISTANTgood", "USER Answer",
                     "ASSISTANT Answer\nError: quota exceeded", "ASSISTANT Answer\n-------",
                     "ASSISTANT First\n\nASSISTANT Last"):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "prefix"):
                parse_aider_history("LLM RESPONSE 2026-10-18T12:30:01\n" + body + "\n")

    def test_rejects_malformed_final_records_without_reusing_earlier_text(self):
        valid = request() + response("Old answer")
        for header in ("LLM RESPONSE", "LLM RESPONSE yesterday", "LLM RESPONSE 2026-10-18",
                       "LLM RESPONSE 2026-10-18T12:30:02 extra", "TO LLM invalid"):
            with self.subTest(header=header), self.assertRaisesRegex(ValueError, "header"):
                parse_aider_history(valid + header + "\nASSISTANT Invalid\n")

    def test_rejects_invalid_calendar_timestamp(self):
        for timestamp in ("2026-13-18T12:30:01", "2026-02-30T12:30:01", "2026-10-18T25:30:01"):
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, "timestamp"):
                parse_aider_history(response("Answer", timestamp))


if __name__ == "__main__":
    unittest.main()
