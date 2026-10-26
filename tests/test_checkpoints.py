"""Crash-safe persistence, exclusive resume ownership, and child recovery."""
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_council.checkpoints import (
    CallStore, SessionLock, atomic_json, process_identity, recover_processes,
    resolve_session, track_process,
)

PROJECT = Path(__file__).resolve().parents[1]


class CheckpointTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def session(self, name="run", *, status=None, timestamp=None):
        session = self.root / name
        session.mkdir()
        atomic_json(session / "run.json", {"schema_version": 1, "prompt": "a question"})
        if status is not None:
            atomic_json(session / "session.json", {"status": status})
        if timestamp is not None:
            os.utime(session / "run.json", ns=(timestamp, timestamp))
        return session


class CheckpointTests(CheckpointTestCase):
    def test_atomic_json_replaces_document_with_private_permissions(self):
        path = self.root / "nested" / "state.json"
        atomic_json(path, {"answer": "नमस्ते"})
        path.chmod(0o644)
        atomic_json(path, {"answer": "updated"})
        self.assertEqual(json.loads(path.read_text()), {"answer": "updated"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_failed_replace_keeps_previous_complete_document_and_cleans_temporary(self):
        path = self.root / "state.json"
        atomic_json(path, {"old": True})
        with patch("agent_council.checkpoints.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                atomic_json(path, {"new": True})
        self.assertEqual(json.loads(path.read_text()), {"old": True})
        self.assertEqual(list(self.root.iterdir()), [path])

    @unittest.skipUnless(os.name == "posix", "directory fsync is POSIX-specific")
    def test_atomic_json_fsyncs_file_and_directory(self):
        with patch("agent_council.checkpoints.os.fsync", wraps=os.fsync) as fsync:
            atomic_json(self.root / "state.json", {"done": True})
        self.assertEqual(fsync.call_count, 2)

    def test_invalid_json_payload_does_not_replace_valid_document(self):
        path = self.root / "state.json"
        atomic_json(path, {"old": True})
        with self.assertRaises(ValueError):
            atomic_json(path, {"value": float("nan")})
        self.assertEqual(json.loads(path.read_text()), {"old": True})

    def test_resolve_existing_path_and_bare_directory_id(self):
        session = self.session()
        self.assertEqual(resolve_session(str(session), self.root), session)
        self.assertEqual(resolve_session(session.name, self.root), session)

    def test_latest_chooses_newest_incomplete_checkpoint_enabled_run(self):
        self.session("old", timestamp=100)
        newest_incomplete = self.session("new", status="interrupted", timestamp=200)
        self.session("done", status="completed", timestamp=400)
        uncheckpointed = self.root / "legacy"
        uncheckpointed.mkdir()
        atomic_json(uncheckpointed / "session.json", {"status": "running"})
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "run.json").write_text("partial JSON")
        self.assertEqual(resolve_session("latest", self.root), newest_incomplete)

    def test_latest_can_resolve_run_without_optional_session_metadata(self):
        session = self.session()
        self.assertEqual(resolve_session("latest", self.root), session)

    def test_explicit_completed_session_can_be_inspected(self):
        session = self.session(status="completed")
        self.assertEqual(resolve_session(str(session), self.root), session)
        with self.assertRaisesRegex(ValueError, "No incomplete"):
            resolve_session("latest", self.root)

    def test_resolve_rejects_missing_or_invalid_runs_without_creating_anything(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        for spec in ("missing", str(self.root / "missing-path"), str(legacy), "", "../missing"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                resolve_session(spec, self.root)
        self.assertEqual(list(self.root.iterdir()), [legacy])

    def test_successful_call_cache_is_bound_to_tag_name_and_full_unicode_prompt(self):
        store = CallStore(self.root)
        answer = {"name": "hermes", "ok": True, "answer": "answer", "nested": {"value": 1}}
        store.save("r1", "hermes", "प्रश्न\nexact", answer)
        self.assertEqual(store.load("r1", "hermes", "प्रश्न\nexact"), answer)
        self.assertIsNone(store.load("r1", "hermes", "प्रश्न\nexact "))
        self.assertIsNone(store.load("r2", "hermes", "प्रश्न\nexact"))
        self.assertIsNone(store.load("r1", "qwen", "प्रश्न\nexact"))

    def test_failed_call_replays_only_when_explicitly_requested(self):
        store = CallStore(self.root)
        result = {"ok": False, "error": "rate limit", "answer": ""}
        store.save("r1", "qwen", "prompt", result)
        self.assertIsNone(store.load("r1", "qwen", "prompt"))
        self.assertEqual(store.load("r1", "qwen", "prompt", include_failure=True), result)
        self.assertIsNone(store.load("r1", "qwen", "other prompt", include_failure=True))

    def test_retry_keeps_independent_attempts_and_replaces_current_call(self):
        store = CallStore(self.root)
        failed = {"ok": False, "error": "rate limit"}
        passed = {"ok": True, "answer": "finished"}
        store.save("r1", "qwen", "prompt", failed)
        store.save("r1", "qwen", "prompt", passed)
        attempts = [json.loads(path.read_text()) for path in (self.root / "attempts").glob("*.json")]
        self.assertEqual(len(attempts), 2)
        self.assertCountEqual([entry["result"] for entry in attempts], [failed, passed])
        self.assertEqual(store.load("r1", "qwen", "prompt"), passed)

    def test_cache_survives_attempt_history_write_failure(self):
        store = CallStore(self.root)
        real_atomic_json = atomic_json

        def save_unless_attempt(path, data):
            if path.parent.name == "attempts":
                raise OSError("attempt log write failed")
            real_atomic_json(path, data)

        with patch("agent_council.checkpoints.atomic_json", side_effect=save_unless_attempt):
            with self.assertRaises(OSError):
                store.save("r1", "qwen", "prompt", {"ok": True, "answer": "saved"})
        self.assertEqual(store.load("r1", "qwen", "prompt")["answer"], "saved")

    def test_composite_call_names_are_unambiguous_and_traversal_is_rejected(self):
        store = CallStore(self.root)
        store.save("a.b", "c", "prompt", {"ok": True, "answer": "one"})
        store.save("a", "b.c", "prompt", {"ok": True, "answer": "two"})
        self.assertEqual(store.load("a.b", "c", "prompt")["answer"], "one")
        self.assertEqual(store.load("a", "b.c", "prompt")["answer"], "two")
        for tag, name in (("../outside", "qwen"), ("r1", "/outside"), ("", "qwen")):
            with self.subTest(tag=tag, name=name), self.assertRaises(ValueError):
                store.save(tag, name, "prompt", {"ok": True})

    def test_cache_rejects_truncated_corrupt_or_tampered_records(self):
        store = CallStore(self.root)
        result = {"ok": True, "answer": "saved"}
        store.save("r1", "qwen", "prompt", result)
        path = next((self.root / "calls").glob("*.json"))
        original = json.loads(path.read_text())
        for replacement in ("{", "[]", json.dumps(original | {"name": "different"}),
                            json.dumps(original | {"result": {"ok": 1}})):
            with self.subTest(replacement=replacement):
                path.write_text(replacement)
                self.assertIsNone(store.load("r1", "qwen", "prompt", include_failure=True))


class ProcessTestCase(CheckpointTestCase):
    def child(self, script, *args):
        process = subprocess.Popen([sys.executable, "-c", script, *map(str, args)],
                                   cwd=PROJECT, env=os.environ | {"PYTHONPATH": str(PROJECT)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)

        def cleanup():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)

        self.addCleanup(cleanup)
        return process

    def wait_ready(self, process):
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, "child did not become ready")
        self.assertEqual(process.stdout.readline().strip(), "ready")


@unittest.skipUnless(os.name == "posix", "process locks require POSIX")
class SessionLockTests(ProcessTestCase):
    def test_competing_process_cannot_lock_session(self):
        script = ("import sys; from pathlib import Path; from agent_council.checkpoints import SessionLock\n"
                  "try:\n with SessionLock(Path(sys.argv[1])): pass\n"
                  "except ValueError:\n sys.exit(7)\n")
        with SessionLock(self.root):
            child = self.child(script, self.root)
            stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 7, stdout + stderr)
        with SessionLock(self.root):
            pass

    def test_sigkill_releases_lock_and_same_session_can_resume(self):
        script = ("import sys,time; from pathlib import Path; "
                  "from agent_council.checkpoints import SessionLock\n"
                  "with SessionLock(Path(sys.argv[1])):\n print('ready',flush=True); time.sleep(60)\n")
        child = self.child(script, self.root)
        self.wait_ready(child)
        with self.assertRaisesRegex(ValueError, "already in use"):
            with SessionLock(self.root):
                self.fail("concurrent lock unexpectedly acquired")
        child.kill()
        child.communicate(timeout=5)
        with SessionLock(self.root):
            pass
        self.assertTrue((self.root / "session.lock").is_file())

    def test_successful_call_remains_reusable_after_owner_is_sigkilled(self):
        script = ("import sys,time; from pathlib import Path; "
                  "from agent_council.checkpoints import CallStore,SessionLock\n"
                  "with SessionLock(Path(sys.argv[1])):\n"
                  " CallStore(Path(sys.argv[1])).save('r1','qwen','prompt',"
                  "{'ok':True,'answer':'durably finished'})\n"
                  " print('ready',flush=True); time.sleep(60)\n")
        child = self.child(script, self.root)
        self.wait_ready(child)
        child.kill()
        child.communicate(timeout=5)
        with SessionLock(self.root):
            self.assertEqual(CallStore(self.root).load("r1", "qwen", "prompt"),
                             {"ok": True, "answer": "durably finished"})
        self.assertEqual(len(list((self.root / "attempts").glob("*.json"))), 1)


@unittest.skipUnless(sys.platform.startswith("linux"), "PID identity requires Linux /proc")
class ProcessRecoveryTests(ProcessTestCase):
    def test_identity_handles_parentheses_and_spaces_in_process_name(self):
        process_stat = "123 (command ) with spaces)) " + " ".join(["S"] + ["0"] * 18 + ["98765"])
        with patch("agent_council.checkpoints.Path.read_text", side_effect=[process_stat, "boot-id\n"]):
            self.assertEqual(process_identity(123), "boot-id:98765")
        self.assertIsNotNone(process_identity(os.getpid()))
        for pid in (-1, 0, 1, True, 1_000_000_000):
            self.assertIsNone(process_identity(pid))

    def test_matching_live_process_group_is_killed_once(self):
        child = self.child("import time; print('ready',flush=True); time.sleep(60)")
        self.wait_ready(child)
        marker = self.root / "processes" / "call.json"
        tracked = track_process(marker, child)
        self.assertEqual(tracked["pgid"], child.pid)
        self.assertEqual(recover_processes(self.root), [child.pid])
        child.communicate(timeout=5)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assertEqual(json.loads(marker.read_text())["status"], "recovered")
        self.assertEqual(recover_processes(self.root), [])

    def test_record_with_reused_pid_token_never_kills_current_process(self):
        child = self.child("import time; print('ready',flush=True); time.sleep(60)")
        self.wait_ready(child)
        path = self.root / "processes" / "call.json"
        tracked = track_process(path, child)
        atomic_json(path, tracked | {"start_token": "a-different-boot:1"})
        with patch("agent_council.checkpoints.os.killpg") as kill:
            self.assertEqual(recover_processes(self.root), [])
            kill.assert_not_called()
        self.assertIsNone(child.poll())
        self.assertEqual(json.loads(path.read_text())["status"], "stale")

    def test_recovery_rejects_wrong_group_own_group_and_unverifiable_identity(self):
        own = {"pid": os.getpid(), "pgid": os.getpgrp(), "start_token": process_identity(os.getpid()),
               "status": "running"}
        atomic_json(self.root / "processes" / "own.json", own)
        atomic_json(self.root / "processes" / "wrong.json", own | {"pid": 999_999_999, "pgid": 999_999_998})
        atomic_json(self.root / "processes" / "gone.json", own | {"pid": 999_999_999, "pgid": 999_999_999})
        with patch("agent_council.checkpoints.os.killpg") as kill:
            self.assertEqual(recover_processes(self.root), [])
            kill.assert_not_called()

    def test_track_refuses_process_without_dedicated_process_group(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            with self.assertRaisesRegex(ValueError, "own process group"):
                track_process(self.root / "processes" / "bad.json", child)
        finally:
            child.kill()
            child.wait(timeout=5)

    def test_recovered_group_cannot_complete_delayed_grandchild_side_effect(self):
        sentinel = self.root / "grandchild-finished"
        grandchild = "import pathlib,sys,time; time.sleep(0.7); pathlib.Path(sys.argv[1]).touch()"
        parent = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
                  "print('ready',flush=True); time.sleep(60)")
        child = self.child(parent, grandchild, sentinel)
        self.wait_ready(child)
        track_process(self.root / "processes" / "tree.json", child)
        self.assertEqual(recover_processes(self.root), [child.pid])
        child.communicate(timeout=5)
        time.sleep(0.9)
        self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
