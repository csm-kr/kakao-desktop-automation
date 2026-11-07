#!/usr/bin/env python3
"""Compare read-only AX observations; never send or invent a server message ID."""
import argparse
import json
import re
import sys
from pathlib import Path


def session_blocker(observation):
    flags = observation.get("session_flags", {})
    if not isinstance(flags, dict):
        return "session_flags_invalid"
    keys = ("CGSSessionScreenIsLocked", "kCGSessionLoginDoneKey", "kCGSSessionOnConsoleKey")
    if any(key in flags and type(flags[key]) is not bool for key in keys):
        return "session_flags_invalid"
    if flags.get("CGSSessionScreenIsLocked") is True:
        return "session_screen_locked"
    if flags.get("kCGSessionLoginDoneKey") is False or flags.get("kCGSSessionOnConsoleKey") is False:
        return "session_not_interactive"
    return None


def verify(before, after):
    reasons = []
    for label, observation in [("before", before), ("after", after)]:
        if blocker := session_blocker(observation):
            reasons.append(f"{label}_{blocker}")
        if observation.get("ready") is not True or type(observation.get("exact_window_count")) is not int or observation["exact_window_count"] != 1:
            reasons.append(f"{label}_window_or_probe_not_ready")
        if type(observation.get("pid")) is not int or observation["pid"] <= 0:
            reasons.append(f"{label}_invalid_pid")
        if not isinstance(observation.get("target_title"), str) or not observation["target_title"]:
            reasons.append(f"{label}_invalid_title")
        if not isinstance(observation.get("message_sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", observation["message_sha256"]) is None:
            reasons.append(f"{label}_invalid_message_hash")
        if type(observation.get("scan_limit")) is not int or observation["scan_limit"] <= 0:
            reasons.append(f"{label}_invalid_scan_limit")
    for key in ["pid", "target_title", "message_sha256", "scan_limit"]:
        if before.get(key) is None or before.get(key) != after.get(key):
            reasons.append(f"{key}_changed_or_missing")
    if before.get("composer_empty") is not True:
        reasons.append("baseline_draft_not_empty")
    if after.get("composer_empty") is not True:
        reasons.append("composer_not_empty_after")
    old, new = before.get("matching_outgoing_count"), after.get("matching_outgoing_count")
    if type(old) is not int or type(new) is not int or old < 0 or new != old + 1:
        reasons.append("one_new_outgoing_message_not_observed")
    return {"status": "unconfirmed" if reasons else "confirmed_in_ui",
            "reasons": reasons, "native_message_id": None,
            "recipient_read_verified": False, "automatic_retry_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    try:
        before, after = json.loads(args.before.read_text()), json.loads(args.after.read_text())
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError("observations must be JSON objects")
        result = verify(before, after)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "confirmed_in_ui" else 2
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "unconfirmed", "error": str(exc), "automatic_retry_allowed": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
