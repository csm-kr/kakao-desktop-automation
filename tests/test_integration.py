"""Real subprocess contract across scheduler/adapter; all app backends are fake."""
import importlib.util
import json
import sys
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SenderIntegrationTest(unittest.TestCase):
    def test_due_job_runs_adapter_once_and_stores_independent_receipt(self):
        fixtures = load("sender_fixtures", ROOT / "tests/test_sender.py")
        scheduler = load("integration_scheduler", ROOT / "scripts/kakao_schedule.py")
        self.assertEqual(scheduler.DEFAULT_DB, Path.cwd() / "var/kakao-schedule.sqlite3")
        fixture = fixtures.SenderTest()
        fixture.setUp()
        try:
            sender_config = fixture.root / "scheduler-sender.json"
            evidence = fixture.root / "fixture-evidence.txt"
            evidence.write_text("Isolated fake backend verification only, not a live KakaoTalk send.")
            sender_config.write_text(json.dumps({"verified": True, "verification_evidence": str(evidence),
                "command": [sys.executable, str(ROOT / "scripts/kakao_sender.py"),
                            "--config", str(fixture.config)]}))
            with closing(scheduler.connect(fixture.root / "schedule.db")) as db:
                job = scheduler.add_job(db, fixture.request["recipient"], fixture.request["message"],
                    at="2030-01-01T09:01+09:00", clock=datetime(2030, 1, 1, tzinfo=timezone.utc))
                db.execute("UPDATE jobs SET approved=1 WHERE id=?", (job["job_id"],)); db.commit()
                clock = datetime(2030, 1, 1, 0, 2, tzinfo=timezone.utc)
                result = scheduler.run_due(db, True, sender_config, clock)[0]
                self.assertEqual(result["status"], "sent")
                self.assertEqual(result["verification"]["level"], "confirmed_in_ui")
                self.assertIsNone(result["message_id"])
                self.assertEqual(scheduler.run_due(db, True, sender_config, clock), [])
                self.assertEqual(fixture.invocation_count(), 1)
                self.assertEqual(db.execute("SELECT state FROM jobs").fetchone()[0], "completed")
                self.assertTrue(Path(result["verification"]["path"]).is_file())
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
