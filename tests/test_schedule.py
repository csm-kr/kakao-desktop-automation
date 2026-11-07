import importlib.util
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/kakao_schedule.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("kakao_schedule", SCRIPT)
scheduler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler)
BASE = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)  # Seoul 09:00.
DUE = datetime(2030, 1, 1, 0, 2, tzinfo=timezone.utc)


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "schedule.db"
        self.db = scheduler.connect(self.db_path)
        self.calls = self.root / "calls"
        self.config = self.root / "sender.json"

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def job(self, approved=True, daily=None):
        result = scheduler.add_job(self.db, "self:test-id", "가짜 전송 테스트", daily=daily,
                                   at=None if daily else "2030-01-01T09:01", clock=BASE)
        if approved:
            with self.db:
                self.db.execute("UPDATE jobs SET approved=1 WHERE id=?", (result["job_id"],))
        return result["job_id"]

    def sender(self, response="sent", sleep=0):
        fake = self.root / "fake.py"
        fake.write_text(f'''import sys,json,time
request=json.load(sys.stdin)
with open({str(self.calls)!r}, "a") as file: file.write(request["request_id"]+"\\n")
time.sleep({sleep!r})
if {response!r} == "invalid": print("not json")
elif {response!r} == "wrong_id": print(json.dumps({{"request_id":"wrong", "status":"sent", "message_id":"fake-1"}}))
else: print(json.dumps({{"request_id":request["request_id"], "status":{response!r}, "message_id":"fake-1", "reason":"fake rejection"}}))
''')
        evidence = self.root / "verification.txt"
        evidence.write_text("Fake sender verification for isolated tests only; not KakaoTalk delivery.")
        self.config.write_text(json.dumps({"command": [sys.executable, str(fake)], "verified": True,
                                          "verification_evidence": str(evidence)}))

    def test_future_validation_and_seoul_time(self):
        with self.assertRaises(ValueError):
            scheduler.once_time("2030-01-01T08:59", BASE)
        self.assertEqual(scheduler.display(scheduler.once_time("2030-01-01T09:01", BASE)),
                         "2030-01-01T09:01:00+09:00")
        self.assertEqual(scheduler.display(scheduler.next_daily("09:00", BASE)),
                         "2030-01-02T09:00:00+09:00")
        with self.assertRaises(ValueError):
            scheduler.daily_time("9:00")

    def test_dry_run_never_calls_sender_or_claims(self):
        self.job()
        self.sender()
        self.assertEqual(scheduler.run_due(self.db, sender_config=self.config, clock=DUE)[0]["status"], "dry_run")
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)

    def test_missing_sender_keeps_approved_job_due(self):
        identifier = self.job()
        result = scheduler.run_due(self.db, execute=True, clock=DUE)
        self.assertEqual(result[0]["reason"], "sender_not_configured")
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        self.assertEqual(row["state"], "active")
        self.assertEqual(self.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)

    def test_unapproved_job_does_not_send(self):
        self.job(approved=False)
        self.sender()
        result = scheduler.run_due(self.db, True, self.config, DUE)
        self.assertEqual(result[0]["reason"], "job_not_approved")
        self.assertFalse(self.calls.exists())

    def test_once_delivery_does_not_repeat(self):
        self.job()
        self.sender()
        self.assertEqual(scheduler.run_due(self.db, True, self.config, DUE)[0]["status"], "sent")
        self.assertEqual(scheduler.run_due(self.db, True, self.config, DUE), [])
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_unknown_or_not_sent_result_is_held(self):
        for response in ["invalid", "wrong_id", "not_sent"]:
            with self.subTest(response=response):
                self.db.execute("DELETE FROM attempts")
                self.db.execute("DELETE FROM jobs")
                self.db.commit()
                self.job()
                self.sender(response)
                result = scheduler.run_due(self.db, True, self.config, DUE)
                self.assertEqual(result[0]["status"], "not_sent" if response == "not_sent" else "unknown")
                self.assertEqual(self.db.execute("SELECT state FROM jobs").fetchone()[0], "held")
                self.assertEqual(scheduler.run_due(self.db, True, self.config, DUE), [])

    def test_timeout_never_retries(self):
        self.job()
        self.sender(sleep=2)
        self.assertEqual(scheduler.run_due(self.db, True, self.config, DUE, timeout=0.5)[0]["status"], "unknown")
        self.assertEqual(scheduler.run_due(self.db, True, self.config, DUE), [])
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_daily_advance_skips_backlog(self):
        self.job(daily="09:01")
        self.sender()
        late = datetime(2030, 1, 5, 0, 2, tzinfo=timezone.utc)
        self.assertEqual(scheduler.run_due(self.db, True, self.config, late)[0]["status"], "sent")
        row = self.db.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["state"], "active")
        self.assertEqual(scheduler.display(row["due_at"]), "2030-01-06T09:01:00+09:00")
        self.assertEqual(scheduler.run_due(self.db, True, self.config, late), [])

    def test_process_concurrency_claims_only_once(self):
        self.job()
        self.sender(sleep=0.1)
        # Put only this isolated test job in the past for two real CLI processes.
        with self.db:
            self.db.execute("UPDATE jobs SET due_at=1")
        command = [sys.executable, str(SCRIPT), "--db", str(self.db_path), "run-due", "--execute",
                   "--sender-config", str(self.config)]
        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for process in [first, second]:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            json.loads(stdout)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)

    def test_crash_after_claim_cannot_reclaim(self):
        identifier = self.job()
        job = self.db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        self.assertTrue(scheduler.claim(self.db, job, "crashed-request"))
        other = scheduler.connect(self.db_path)
        try:
            self.assertFalse(scheduler.claim(other, job, "second-request"))
        finally:
            other.close()
        self.assertEqual(self.db.execute("SELECT state FROM attempts").fetchone()[0], "in_flight")

    def ui_result(self, request, alteration=None, bad_hash=False):
        digest = hashlib.sha256(request["message"].encode()).hexdigest()
        before = {"ready": True, "exact_window_count": 1, "pid": 123, "target_title": "fixture chat",
                  "message_sha256": digest, "scan_limit": 80, "composer_empty": True,
                  "matching_outgoing_count": 0}
        receipt = {"version": 1, "request_id": request["request_id"], "recipient": request["recipient"],
                   "message_sha256": digest, "target_title": "fixture chat", "status": "confirmed_in_ui",
                   "before": before, "after": {**before, "matching_outgoing_count": 1},
                   "sender_invocations": 1, "commit_attempts": 1, "native_message_id": None}
        receipt.update(alteration or {})
        raw = json.dumps(receipt).encode()
        path = self.root / "receipt.json"
        path.write_bytes(raw)
        return subprocess.CompletedProcess([], 0, json.dumps({"request_id": request["request_id"],
            "status": "sent", "message_id": None, "verification": {"type": "ui_receipt", "path": str(path),
            "sha256": "0" * 64 if bad_hash else hashlib.sha256(raw).hexdigest()}}), "")

    def test_ui_confirmation_completes_job_without_inventing_message_id(self):
        self.job()
        self.sender()
        def respond(command, **kwargs):
            return self.ui_result(json.loads(kwargs["input"]))
        with patch.object(scheduler.subprocess, "run", side_effect=respond):
            result = scheduler.run_due(self.db, True, self.config, DUE)[0]
        self.assertEqual(result["status"], "sent")
        self.assertIsNone(result["message_id"])
        self.assertEqual(result["verification"]["level"], "confirmed_in_ui")
        row = self.db.execute("SELECT * FROM attempts").fetchone()
        self.assertIsNone(row["message_id"])
        self.assertEqual(json.loads(row["verification"])["type"], "ui_receipt")
        self.assertEqual(self.db.execute("SELECT state FROM jobs").fetchone()[0], "completed")

    def test_forged_or_mismatched_ui_receipt_is_held_without_retry(self):
        self.sender()
        for alteration, bad_hash in [({"recipient": "other"}, False), ({"request_id": "other"}, False),
                                     ({"message_sha256": "0" * 64}, False), ({}, True),
                                     ({"after": {}}, False)]:
            with self.subTest(alteration=alteration, bad_hash=bad_hash):
                with self.db:
                    self.db.execute("DELETE FROM attempts"); self.db.execute("DELETE FROM jobs")
                self.job()
                def respond(command, **kwargs):
                    return self.ui_result(json.loads(kwargs["input"]), alteration, bad_hash)
                with patch.object(scheduler.subprocess, "run", side_effect=respond) as execute:
                    result = scheduler.run_due(self.db, True, self.config, DUE)[0]
                    self.assertEqual(result["status"], "unknown")
                    self.assertEqual(scheduler.run_due(self.db, True, self.config, DUE), [])
                    self.assertEqual(execute.call_count, 1)
                self.assertEqual(self.db.execute("SELECT state FROM jobs").fetchone()[0], "held")

    def test_existing_database_migration_preserves_attempts(self):
        old_path = self.root / "old.db"
        old = sqlite3.connect(old_path)
        old.executescript('''CREATE TABLE jobs (id TEXT PRIMARY KEY, recipient TEXT NOT NULL,message TEXT NOT NULL,
          kind TEXT NOT NULL,daily TEXT,due_at INTEGER NOT NULL,approved INTEGER NOT NULL DEFAULT 0,
          state TEXT NOT NULL DEFAULT 'active');
          CREATE TABLE attempts(request_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id),
          due_at INTEGER NOT NULL,state TEXT NOT NULL,detail TEXT,message_id TEXT,UNIQUE(job_id,due_at));
          INSERT INTO jobs VALUES('old','fixture','fixture','once',NULL,1,1,'held');
          INSERT INTO attempts VALUES('old:1','old',1,'unknown','uncertain',NULL);''')
        old.commit(); old.close()
        upgraded = scheduler.connect(old_path)
        self.assertIsNone(upgraded.execute("SELECT verification FROM attempts").fetchone()[0])
        self.assertEqual(upgraded.execute("SELECT state FROM jobs").fetchone()[0], "held")
        upgraded.close()


if __name__ == "__main__":
    unittest.main()
