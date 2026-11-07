#!/usr/bin/env python3
"""Local message schedules. Delivery requires a separately verified JSON sender."""
import argparse
import json
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ui_receipt import load_verified_receipt

SEOUL = ZoneInfo("Asia/Seoul")
DEFAULT_DB = Path.cwd() / "var/kakao-schedule.sqlite3"


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    return int(value.timestamp())


def display(value):
    return datetime.fromtimestamp(value, SEOUL).isoformat()


def daily_time(value):
    try:
        result = datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("daily time must be HH:MM in Asia/Seoul") from exc
    if result.strftime("%H:%M") != value:
        raise ValueError("daily time must be HH:MM in Asia/Seoul")
    return result


def next_daily(value, after):
    clock = daily_time(value)
    local = after.astimezone(SEOUL)
    candidate = datetime.combine(local.date(), clock, SEOUL)
    if candidate <= local:
        candidate += timedelta(days=1)
    return stamp(candidate)


def once_time(value, after):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("at must be an ISO date/time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SEOUL)
    result = stamp(parsed)
    if result <= stamp(after):
        raise ValueError("at must be in the future")
    return result


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, recipient TEXT NOT NULL, message TEXT NOT NULL,
            kind TEXT NOT NULL, daily TEXT, due_at INTEGER NOT NULL,
            approved INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS attempts (
            request_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
            due_at INTEGER NOT NULL, state TEXT NOT NULL, detail TEXT,
            message_id TEXT, verification TEXT, UNIQUE(job_id, due_at)
        );
    """)
    # Upgrade existing local databases without replacing pending jobs or attempts.
    if "verification" not in {row[1] for row in db.execute("PRAGMA table_info(attempts)")}:
        try:
            db.execute("ALTER TABLE attempts ADD COLUMN verification TEXT")
        except sqlite3.OperationalError:
            if "verification" not in {row[1] for row in db.execute("PRAGMA table_info(attempts)")}:
                raise
    return db


def add_job(db, recipient, message, at=None, daily=None, clock=None):
    clock = clock or now()
    if not recipient.strip() or not message.strip():
        raise ValueError("recipient and message must be nonempty")
    if (at is None) == (daily is None):
        raise ValueError("choose exactly one of at or daily")
    due = once_time(at, clock) if at else next_daily(daily, clock)
    identifier = str(uuid.uuid4())
    with db:
        db.execute("INSERT INTO jobs(id,recipient,message,kind,daily,due_at) VALUES(?,?,?,?,?,?)",
                   (identifier, recipient, message, "once" if at else "daily", daily, due))
    return {"job_id": identifier, "due_at": display(due), "approved": False}


def load_sender(path):
    if path is None:
        return None, "sender_not_configured"
    config_path = Path(path).resolve()
    try:
        config = json.loads(config_path.read_text())
        command = config["command"]
        if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
            raise ValueError("command must be a nonempty argv array")
        executable = Path(command[0])
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("command[0] must be an existing absolute executable path")
        if config.get("verified") is not True:
            raise ValueError("sender must be explicitly marked verified")
        evidence = Path(config["verification_evidence"])
        if not evidence.is_absolute():
            evidence = config_path.parent / evidence
        if not evidence.is_file() or not evidence.read_bytes().strip():
            raise ValueError("verification_evidence must point to a nonempty local file")
        return command, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"sender_invalid: {exc}"


def claim(db, job, request_id):
    db.execute("BEGIN IMMEDIATE")
    try:
        current = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if not current or current["state"] != "active" or not current["approved"] or current["due_at"] != job["due_at"]:
            db.rollback()
            return False
        db.execute("INSERT INTO attempts(request_id,job_id,due_at,state,detail,message_id) VALUES(?,?,?,?,?,?)",
                   (request_id, job["id"], job["due_at"], "in_flight", None, None,))
        db.execute("UPDATE jobs SET state='held' WHERE id=?", (job["id"],))
        db.commit()
        return True
    except sqlite3.IntegrityError:
        db.rollback()
        return False
    except Exception:
        db.rollback()
        raise


def run_due(db, execute=False, sender_config=None, clock=None, timeout=30):
    clock = clock or now()
    command, blocker = load_sender(sender_config) if execute else (None, None)
    results = []
    jobs = db.execute("SELECT * FROM jobs WHERE state='active' AND due_at<=? ORDER BY due_at,id",
                      (stamp(clock),)).fetchall()
    for job in jobs:
        result = {"job_id": job["id"], "recipient": job["recipient"], "message": job["message"],
                  "due_at": display(job["due_at"])}
        if not execute:
            results.append({**result, "status": "dry_run", "approved": bool(job["approved"])})
            continue
        if not job["approved"] or blocker:
            results.append({**result, "status": "blocked", "reason": "job_not_approved" if not job["approved"] else blocker})
            continue
        request_id = f"{job['id']}:{job['due_at']}"
        if not claim(db, job, request_id):
            results.append({**result, "status": "not_claimed"})
            continue
        request = {"version": 1, "request_id": request_id, "job_id": job["id"],
                   "recipient": job["recipient"], "message": job["message"],
                   "scheduled_at": display(job["due_at"]), "timezone": "Asia/Seoul"}
        status, detail, message_id = "unknown", "sender response was not verified", None
        verification = None
        try:
            response = subprocess.run(command, input=json.dumps(request, ensure_ascii=False),
                                      text=True, capture_output=True, timeout=timeout, check=False)
            payload = json.loads(response.stdout)
            if response.returncode == 0 and payload.get("request_id") == request_id:
                if payload.get("status") == "sent" and payload.get("verification") is not None:
                    verification = load_verified_receipt(payload["verification"], request)
                    status, detail = "sent", "confirmed_in_ui"
                elif (payload.get("status") == "sent" and isinstance(payload.get("message_id"), str)
                      and payload["message_id"]):
                    status, detail, message_id = "sent", "sender_confirmed_native_id", payload["message_id"]
                elif payload.get("status") == "not_sent":
                    status, detail = "not_sent", str(payload.get("reason", "sender_declared_not_sent"))[:1000]
            if status == "unknown":
                detail = f"unverified_sender_response_exit_{response.returncode}"
        except (subprocess.TimeoutExpired, OSError, ValueError, AttributeError, TypeError, KeyError) as exc:
            detail = type(exc).__name__
        with db:
            db.execute("UPDATE attempts SET state=?,detail=?,message_id=?,verification=? WHERE request_id=?",
                       (status, detail, message_id, json.dumps(verification) if verification else None, request_id))
            if status == "sent":
                if job["kind"] == "daily":
                    # Coalesce missed days; never send a backlog burst.
                    due = next_daily(job["daily"], max(clock, now()))
                    db.execute("UPDATE jobs SET state='active',due_at=? WHERE id=? AND state='held'", (due, job["id"]))
                else:
                    db.execute("UPDATE jobs SET state='completed' WHERE id=? AND state='held'", (job["id"],))
        results.append({**result, "status": status, "reason": detail, "request_id": request_id,
                        "message_id": message_id, "verification": verification})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser("add")
    add.add_argument("--recipient", required=True)
    add.add_argument("--message", required=True)
    when = add.add_mutually_exclusive_group(required=True)
    when.add_argument("--at")
    when.add_argument("--daily")
    commands.add_parser("list")
    commands.add_parser("attempts")
    for name in ["approve", "cancel"]:
        command = commands.add_parser(name)
        command.add_argument("job_id")
    validate = commands.add_parser("validate-sender")
    validate.add_argument("sender_config", type=Path)
    run = commands.add_parser("run-due")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--sender-config", type=Path)
    run.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    try:
        if args.action == "validate-sender":
            _, error = load_sender(args.sender_config)
            print(json.dumps({"status": "blocked" if error else "valid_config", "reason": error}, ensure_ascii=False))
            return 2 if error else 0
        with closing(connect(args.db)) as db, db:
            if args.action == "add":
                result = add_job(db, args.recipient, args.message, args.at, args.daily)
            elif args.action in ["approve", "cancel"]:
                if args.action == "approve":
                    cursor = db.execute("UPDATE jobs SET approved=1 WHERE id=? AND state='active'", (args.job_id,))
                else:
                    cursor = db.execute("UPDATE jobs SET state='cancelled' WHERE id=? AND state!='completed'", (args.job_id,))
                if cursor.rowcount != 1:
                    raise ValueError("job not found or not eligible for this action")
                result = {"job_id": args.job_id, "action": args.action}
            elif args.action == "run-due":
                if args.timeout <= 0:
                    raise ValueError("timeout must be positive")
                result = run_due(db, args.execute, args.sender_config, timeout=args.timeout)
            else:
                table = "jobs" if args.action == "list" else "attempts"
                result = [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if isinstance(result, list) and any(row.get("status") in ["blocked", "unknown", "not_sent"] for row in result) else 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
