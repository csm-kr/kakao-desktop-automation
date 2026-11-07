#!/usr/bin/env python3
"""Collect one explicitly configured room and select attributed text quotes locally."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from verify_delivery import session_blocker

KEYS = {"collector", "room_id", "title", "identity_evidence", "tail_rows", "any_keywords", "all_keywords",
        "exclude_keywords", "require_link", "max_items", "output_dir"}


def load_config(path):
    config = json.loads(Path(path).read_text())
    if not isinstance(config, dict) or set(config) - KEYS:
        raise ValueError("unsupported config fields; date ranges are not implemented")
    for key in ("room_id", "title"):
        if not isinstance(config.get(key), str) or not config[key].strip() or "\0" in config[key]:
            raise ValueError(f"{key} must be a nonempty string without NUL")
    for key in ("collector", "identity_evidence", "output_dir"):
        if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute():
            raise ValueError(f"{key} must be an absolute path")
    executable = Path(config["collector"])
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("collector must be an existing executable")
    if not Path(config["identity_evidence"]).read_bytes().strip():
        raise ValueError("identity evidence must be nonempty")
    for key in ("tail_rows", "max_items"):
        if type(config.get(key)) is not int or not 1 <= config[key] <= 200:
            raise ValueError(f"{key} must be an integer 1...200")
    for key in ("any_keywords", "all_keywords", "exclude_keywords"):
        values = config.setdefault(key, [])
        if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values):
            raise ValueError(f"{key} must contain nonempty strings")
    if type(config.get("require_link", False)) is not bool:
        raise ValueError("require_link must be boolean")
    if not config["any_keywords"] and not config["all_keywords"] and not config.get("require_link"):
        raise ValueError("specify usefulness criteria: keywords or require_link")
    return config


def links(text):
    found = []
    for match in re.findall(r"https?://[^\s<>\"']+", text):
        candidate = match.rstrip(".,!?;:)]}")
        try:
            if urlsplit(candidate).hostname and candidate not in found:
                found.append(candidate)
        except ValueError:
            continue
    return found


def select(config, capture):
    if (not isinstance(capture, dict) or type(capture.get("version")) is not int
            or capture["version"] != 1 or capture.get("ready") is not True):
        raise ValueError("capture is not ready")
    if (capture.get("target_title") != config["title"] or type(capture.get("exact_window_count")) is not int
            or capture["exact_window_count"] != 1 or type(capture.get("tail_rows")) is not int
            or type(capture.get("pid")) is not int or capture["pid"] <= 0
            or session_blocker(capture) is not None or capture.get("tail_rows") != config["tail_rows"]):
        raise ValueError("capture target, row scope or session differs from config")
    if not isinstance(capture.get("capture_id"), str) or not capture["capture_id"]:
        raise ValueError("capture lacks identity")
    rows = capture.get("rows")
    if not isinstance(rows, list) or len(rows) > config["tail_rows"]:
        raise ValueError("capture exceeds row scope")
    available = capture.get("rows_available")
    if (type(available) is not int or available < 0 or type(capture.get("rows_examined")) is not int
            or capture["rows_examined"] != len(rows) or len(rows) != min(available, config["tail_rows"])):
        raise ValueError("capture tail count is inconsistent")
    expected_indices = list(range(max(0, available - config["tail_rows"]), available))
    selected = []
    seen_rows = set()
    for expected_index, row in zip(expected_indices, rows):
        if not isinstance(row, dict):
            raise ValueError("source row must be an object")
        index = row.get("row_index")
        if type(index) is not int or index != expected_index or index in seen_rows:
            raise ValueError("invalid or duplicate source row")
        seen_rows.add(index)
        parts = row.get("text_parts")
        if not isinstance(parts, list) or any(not isinstance(part, str) for part in parts):
            raise ValueError("unreadable text parts")
        quote = "\n".join(parts)
        digest = hashlib.sha256(quote.encode()).hexdigest()
        if digest != row.get("body_sha256"):
            raise ValueError("source body hash mismatch")
        folded = quote.casefold()
        if not quote.strip() or any(word.casefold() in folded for word in config["exclude_keywords"]):
            continue
        if config["any_keywords"] and not any(word.casefold() in folded for word in config["any_keywords"]):
            continue
        if not all(word.casefold() in folded for word in config["all_keywords"]):
            continue
        extracted = links(quote)
        if config.get("require_link") and not extracted:
            continue
        selected.append({"quote": quote, "links": extracted,
            "matched_keywords": [word for word in config["any_keywords"] + config["all_keywords"] if word.casefold() in folded],
            "source": {"room_id": config["room_id"], "title": config["title"],
                       "capture_id": capture["capture_id"], "row_index": index, "body_sha256": digest},
            "author": None, "date": None})
    # Preserve native row order. Do not claim it is a verified timeline.
    return {"version": 1, "kind": "rule_selected_quotes", "room_id": config["room_id"],
            "capture_id": capture["capture_id"], "rows_examined": len(rows),
            "criteria": {key: config.get(key, False) for key in
                         ("tail_rows", "max_items", "any_keywords", "all_keywords", "exclude_keywords", "require_link")},
            "matched_rows": len(selected), "items": selected[-config["max_items"]:],
            "ordering": "native AX row order; chronology unverified", "author_date_verified": False,
            "links_fetched": False, "messages_sent": 0}


def write_private(path, payload):
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def run(config, capture_path=None):
    if capture_path is None:
        result = subprocess.run([config["collector"], config["title"], str(config["tail_rows"])],
                                capture_output=True, text=True, timeout=20, check=False)
        if len(result.stdout.encode()) > 2_097_152:
            raise ValueError("collector output exceeds 2MiB")
        capture = json.loads(result.stdout)
        if result.returncode != 0:
            blocker = session_blocker(capture) if isinstance(capture, dict) else None
            raise ValueError(blocker or "collector did not return a ready capture")
    else:
        with Path(capture_path).open("rb") as stream:
            raw = stream.read(2_097_153)
        if len(raw) > 2_097_152:
            raise ValueError("capture exceeds 2MiB")
        capture = json.loads(raw)
    report = select(config, capture)
    output = Path(config["output_dir"])
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = output / uuid.uuid4().hex
    directory.mkdir(mode=0o700)
    if capture_path is None:
        capture_path = directory / "capture.json"
        digest = write_private(capture_path, capture)
    else:
        capture_path = Path(capture_path).resolve()
        digest = hashlib.sha256(raw).hexdigest()
    report["capture_file"] = str(capture_path)
    report["capture_sha256"] = digest
    report_path = directory / "selected.json"
    write_private(report_path, report)
    return {"status": "selected", "rows_examined": report["rows_examined"], "matched_rows": report["matched_rows"],
            "selected_count": len(report["items"]), "capture_file": str(capture_path), "report_file": str(report_path),
            "messages_sent": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--capture", type=Path, help="Reselect a local capture without accessing the app")
    args = parser.parse_args()
    try:
        result = run(load_config(args.config), args.capture)
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc), "messages_sent": 0}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
