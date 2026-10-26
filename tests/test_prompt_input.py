"""Prompt sources, validation, and selection before session startup."""
import io
import os
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from agent_council import cli
from agent_council.prompt_input import read_prompt
from test_council import TemporaryTest


class PromptInputTests(TemporaryTest):
    def test_utf8_text_and_markdown_preserve_content(self):
        text = "  # Request\n\nExplain café and नमस्ते.\n\n```txt\n  keep indentation\n```\n"
        for name, encoding in (("request.txt", "utf-8"), ("my request.md", "utf-8-sig")):
            with self.subTest(name=name):
                path = self.root / name
                path.write_text(text, encoding=encoding)
                self.assertEqual(read_prompt(None, path), text)

    def test_expand_home_and_resolve_relative_paths(self):
        (self.root / "request.md").write_text("Explain this.", encoding="utf-8")
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            self.assertEqual(read_prompt(None, Path("~/request.md")), "Explain this.")
        relative = Path(os.path.relpath(self.root / "request.md"))
        self.assertEqual(read_prompt(None, relative), "Explain this.")

    def test_literal_and_stdin_prompts(self):
        text = "  Explain this.\n\n"
        self.assertEqual(read_prompt(text), text)
        with patch("sys.stdin", io.StringIO(text)):
            self.assertEqual(read_prompt("-"), text)
        # File contents that happen to be '-' are literal text.
        path = self.root / "dash.txt"
        path.write_text("-", encoding="utf-8")
        with patch("sys.stdin") as stdin:
            self.assertEqual(read_prompt(None, path), "-")
            stdin.read.assert_not_called()

    def test_invalid_file_contents(self):
        path = self.root / "request.md"
        for contents, message in ((b"", "empty"), (b" \t\n", "whitespace"),
                                  (b"\xef\xbb\xbf", "empty"), (b"\xff", "UTF-8"),
                                  (b"binary\x00text", "NUL")):
            with self.subTest(contents=contents):
                path.write_bytes(contents)
                with self.assertRaisesRegex(ValueError, message) as caught:
                    read_prompt(None, path)
                self.assertIn(str(path), str(caught.exception))

    def test_invalid_file_paths(self):
        for path, message in ((self.root / "missing.md", "cannot read"),
                              (self.root, "regular text file")):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, message):
                read_prompt(None, path)
        path = self.root / "denied.txt"
        path.touch()
        with patch.object(Path, "read_text", side_effect=PermissionError(13, "Permission denied")), \
                self.assertRaisesRegex(ValueError, "cannot read.*denied.txt.*Permission denied"):
            read_prompt(None, path)

    def test_conflicting_sources_fail_before_reading(self):
        for prompt in ("literal", "", "-"):
            with self.subTest(prompt=prompt), patch("sys.stdin") as stdin, \
                    self.assertRaisesRegex(ValueError, "either a positional prompt or --prompt-file"):
                read_prompt(prompt, self.root / "missing.md")
            stdin.read.assert_not_called()

    def test_empty_and_invalid_literal_or_stdin(self):
        for prompt in (None, "", " \t\n", "binary\x00text"):
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                read_prompt(prompt)
        with patch("sys.stdin", io.StringIO(" \n")), self.assertRaisesRegex(ValueError, "stdin prompt.*empty"):
            read_prompt("-")

    def invoke(self, *args, stdin=None):
        config = self.write_config(self.config())
        with patch("sys.stdin", io.StringIO(stdin or "")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = cli.main(["--config", str(config), "--sessions-dir", str(self.root / "sessions"),
                             "--non-interactive", "--quiet", *args])
        return code, err.getvalue()

    def test_cli_alias_and_original_option(self):
        path = self.root / "my request.md"
        path.write_text("# Request\n\nExplain this.\n", encoding="utf-8-sig")
        for flag in ("-f", "--prompt-file"):
            with self.subTest(flag=flag), patch.object(cli, "run_council", return_value=0) as run:
                code, err = self.invoke(flag, str(path))
                self.assertEqual(code, 0, err)
                self.assertEqual(run.call_args.args[0].prompt, "# Request\n\nExplain this.\n")

    def test_invalid_input_never_selects_agents_or_starts_session(self):
        path = self.root / "empty.md"
        path.touch()
        for args in (("-f", str(path)), ("-f", str(path), "literal")):
            with self.subTest(args=args), patch.object(cli, "choose_roster") as choose, \
                    patch.object(cli, "run_council") as run:
                code, err = self.invoke("--agents", "auto", *args)
                self.assertEqual(code, 2, err)
                choose.assert_not_called()
                run.assert_not_called()
                self.assertFalse((self.root / "sessions").exists())

    def test_auto_roster_uses_actual_prompt_from_every_source(self):
        path = self.root / "request.md"
        for text, is_build in (("Build a Python tool", True), ("Explain this tradeoff", False)):
            path.write_text(text, encoding="utf-8")
            for args, stdin in (((text,), None), (("-f", str(path)), None), (("-",), text)):
                with self.subTest(text=text, args=args), \
                        patch.object(cli, "choose_roster", return_value=(["alpha", "beta"], [])) as choose, \
                        patch.object(cli, "run_council", return_value=0):
                    code, err = self.invoke("--agents", "auto:2", *args, stdin=stdin)
                    self.assertEqual(code, 0, err)
                    self.assertEqual(choose.call_args.kwargs["is_build"], is_build)

    def test_doctor_does_not_read_prompt_file(self):
        with patch.object(cli, "doctor", return_value=0), patch.object(cli, "read_prompt") as read:
            code, err = self.invoke("--doctor", "-f", str(self.root / "missing.md"))
            self.assertEqual(code, 0, err)
            read.assert_not_called()

    def test_resume_rejects_new_prompt_file(self):
        code, err = self.invoke("--resume", "latest", "-f", str(self.root / "missing.md"))
        self.assertEqual(code, 2)
        self.assertIn("--resume restores the saved goal", err)
