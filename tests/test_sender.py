import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import kakao_sender as sender
from ui_receipt import binding, load_verified_receipt


class SenderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kakao fake sender ")
        self.root = Path(self.temp.name)
        self.scenario = self.root / "scenario.json"
        self.scenario.write_text(json.dumps({"case": "delivered_error"}))
        self.calls = self.root / "calls.jsonl"
        self.committed = self.root / "committed"
        self.title = "Local 'fixture' chat"
        self.request = {"version": 1, "request_id": "fixture-request-1", "recipient": "fixture-id",
                        "message": "가짜 $(touch should-not-exist) `literal`\n다음 줄"}
        evidence = self.root / "identity.txt"
        evidence.write_text("Fixture identity, no actual KakaoTalk account or delivery.")
        self.config = self.root / "config.json"
        for name in ("probe", "kmsg"):
            executable = self.root / name
            executable.write_text(f'''#!{sys.executable}
import hashlib,json,pathlib,sys,time
root=pathlib.Path({str(self.root)!r})
case=json.loads((root/'scenario.json').read_text())['case']
name=pathlib.Path(sys.argv[0]).name
if name=='probe':
    title,message=sys.argv[1:]
    delivered=(root/'committed').exists()
    print(json.dumps({{'ready':case not in ['probe_unavailable','locked'],'exact_window_count':1,
      'pid':456 if case=='pid_changed' and delivered else 123,'target_title':title,
      'message_sha256':hashlib.sha256(message.encode()).hexdigest(),'scan_limit':80,
      'composer_empty':case!='draft_present','matching_outgoing_count':int(delivered),
      'session_flags':{{'CGSSessionScreenIsLocked':True}} if case=='locked' else {{}}}}))
else:
    with (root/'calls.jsonl').open('a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')
    if case=='slow': time.sleep(.35)
    if case in ['blocked','precommit_draft']:
        print(json.dumps({{'status':'blocked','commitAttempts':0,'draftMayHaveChanged':case=='precommit_draft'}}))
    else:
        if case!='unconfirmed': (root/'committed').write_text('fixture commit')
        if case=='bad_report': print('not JSON')
        else: print(json.dumps({{'status':'commit_attempted_unconfirmed','commitAttempts':1}}))
    sys.exit(1)
''')
            executable.chmod(0o700)
        self.config.write_text(json.dumps({"kmsg": str(self.root / "kmsg"), "probe": str(self.root / "probe"),
            "state_dir": str(self.root / "state"), "mode": "background-safe",
            "recipients": {"fixture-id": {"title": self.title, "identity_evidence": str(evidence)}}}))

    def tearDown(self):
        self.temp.cleanup()

    def scenario_case(self, case):
        self.scenario.write_text(json.dumps({"case": case}))

    def deliver(self, request=None):
        # A fake monotonic clock avoids waiting on negative receipt fixtures.
        with patch.object(sender.time, "monotonic", side_effect=[0, 3]):
            return sender.deliver(self.config, request or self.request)

    def invocation_count(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def test_ax_error_with_independent_receipt_confirms_without_native_id(self):
        result = self.deliver()
        self.assertEqual(result["status"], "sent")
        self.assertIsNone(result["message_id"])
        verified = load_verified_receipt(result["verification"], self.request)
        self.assertEqual(verified["level"], "confirmed_in_ui")
        self.assertEqual(json.loads(self.calls.read_text()),
                         ["send", "--background-safe", "--", self.title, self.request["message"]])
        self.assertEqual(self.invocation_count(), 1)
        self.assertFalse((self.root / "should-not-exist").exists())

    def test_completed_request_replays_receipt_without_sending(self):
        original = self.deliver()
        self.assertEqual(self.deliver(), original)
        self.assertEqual(self.invocation_count(), 1)

    def test_same_id_different_body_cannot_send(self):
        self.deliver()
        changed = {**self.request, "message": "different fixture body"}
        self.assertEqual(self.deliver(changed)["reason"], "request_id_conflict")
        self.assertEqual(self.invocation_count(), 1)

    def test_preflight_failure_never_invokes_sender(self):
        for case in ("probe_unavailable", "draft_present"):
            with self.subTest(case=case):
                self.scenario_case(case)
                request = {**self.request, "request_id": case}
                self.assertEqual(self.deliver(request)["status"], "not_sent")
                self.assertEqual(self.invocation_count(), 0)

    def test_locked_session_stops_before_kmsg_with_a_specific_reason(self):
        self.scenario_case("locked")
        result = self.deliver()
        self.assertEqual(result["status"], "not_sent")
        self.assertEqual(result["reason"], "session_screen_locked")
        self.assertEqual(self.invocation_count(), 0)

    def test_precommit_stop_and_owned_draft_do_not_trigger_retry(self):
        self.scenario_case("precommit_draft")
        first = self.deliver()
        self.assertEqual(first["status"], "not_sent")
        self.assertEqual(self.deliver(), first)
        self.assertEqual(self.invocation_count(), 1)
        self.assertFalse(self.committed.exists())

    def test_unconfirmed_or_missing_commit_evidence_is_never_retried(self):
        for case in ("unconfirmed", "pid_changed", "bad_report"):
            with self.subTest(case=case):
                self.committed.unlink(missing_ok=True)
                self.scenario_case(case)
                request = {**self.request, "request_id": case}
                first = self.deliver(request)
                self.assertEqual(first["status"], "unknown")
                calls = self.invocation_count()
                self.assertEqual(self.deliver(request), first)
                self.assertEqual(self.invocation_count(), calls)

    def test_unfinished_intent_is_not_reclaimed(self):
        config, state, title = sender.config_for(self.config, self.request["recipient"])
        state.mkdir()
        key = hashlib.sha256(self.request["request_id"].encode()).hexdigest()
        sender.save(state / f"{key}.intent.json", {**binding(self.request), "target_title": title,
                    "phase": "sender_in_flight", "sender_invocations": 1}, exclusive=True)
        self.assertEqual(self.deliver()["reason"], "existing_unfinished_intent_no_retry")
        self.assertEqual(self.invocation_count(), 0)

    def test_state_dir_edit_cannot_reset_request_history(self):
        self.deliver()
        config = json.loads(self.config.read_text())
        config["state_dir"] = str(self.root / "replacement-state")
        self.config.write_text(json.dumps(config))
        with self.assertRaises(ValueError):
            self.deliver()
        self.assertEqual(self.invocation_count(), 1)
        self.assertFalse((self.root / "replacement-state").exists())

    def test_different_request_does_not_bypass_interrupted_sender(self):
        config, state, title = sender.config_for(self.config, self.request["recipient"])
        state.mkdir()
        sender.save(state / "interrupted.intent.json", {**binding(self.request), "target_title": title,
                    "phase": "sender_in_flight", "sender_invocations": 1}, exclusive=True)
        result = self.deliver({**self.request, "request_id": "another-request"})
        self.assertEqual(result["reason"], "prior_sender_interrupted_requires_reconciliation")
        self.assertEqual(self.invocation_count(), 0)

    def test_tampered_receipt_cannot_be_replayed(self):
        result = self.deliver()
        receipt = Path(result["verification"]["path"])
        receipt.write_text(receipt.read_text() + " ")
        with self.assertRaises(ValueError):
            self.deliver()
        self.assertEqual(self.invocation_count(), 1)

    def test_receipt_must_bind_body_request_and_observed_target(self):
        result = self.deliver()
        proof = result["verification"]
        original = json.loads(Path(proof["path"]).read_text())
        for change in ({"request_id": "wrong"}, {"message_sha256": "0" * 64},
                       {"target_title": "wrong"}, {"commit_attempts": True}):
            with self.subTest(change=change):
                digest = sender.save(Path(proof["path"]), {**original, **change})
                with self.assertRaises(ValueError):
                    load_verified_receipt({**proof, "sha256": digest}, self.request)

    def assert_serialized(self, other_config):
        self.scenario_case("slow")
        command = [sys.executable, str(SCRIPTS / "kakao_sender.py"), "--config", str(self.config)]
        first = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        first.stdin.write(json.dumps(self.request)); first.stdin.close(); first.stdin = None
        deadline = time.monotonic() + 5
        while not self.calls.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        other_command = [sys.executable, str(SCRIPTS / "kakao_sender.py"), "--config", str(other_config)]
        other = subprocess.run(other_command, input=json.dumps({**self.request, "request_id": "second"}),
                               capture_output=True, text=True, timeout=5)
        stdout, stderr = first.communicate(timeout=5)
        self.assertEqual(first.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "sent")
        self.assertEqual(json.loads(other.stdout)["reason"], "sender_busy")
        self.assertEqual(self.invocation_count(), 1)

    def test_two_processes_cannot_operate_the_ui_concurrently(self):
        self.assert_serialized(self.config)

    def test_distinct_config_directories_share_the_ledger_lock(self):
        directory = self.root / "another-config-directory"
        directory.mkdir()
        other = directory / "config.json"
        other.write_text(self.config.read_text())
        self.assert_serialized(other)


if __name__ == "__main__":
    unittest.main()
