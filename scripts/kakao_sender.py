#!/usr/bin/env python3
"""One JSON request to guarded background kmsg plus independent UI confirmation."""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from ui_receipt import binding, load_verified_receipt
from verify_delivery import session_blocker, verify


class ObservationUnavailable(ValueError):
    pass


def save(path, value, exclusive=False):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    destination = path if exclusive else path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with destination.open("xb") as stream:
            os.chmod(destination, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if not exclusive:
            os.replace(destination, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if not exclusive:
            destination.unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


def config_for(path, recipient):
    config = json.loads(Path(path).read_text())
    if config.get("mode", "background-safe") != "background-safe":
        raise ValueError("this adapter only implements background-safe; no foreground fallback")
    for name in ("kmsg", "probe"):
        executable = Path(config[name])
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f"{name} must be an existing absolute executable path")
    state = Path(config["state_dir"])
    if not state.is_absolute():
        raise ValueError("state_dir must be absolute")
    target = config["recipients"][recipient]
    title = target["title"]
    if not isinstance(title, str) or not title.strip() or "\0" in title:
        raise ValueError("recipient needs an exact verified title")
    evidence = Path(target["identity_evidence"])
    if not evidence.is_absolute() or not evidence.is_file() or not evidence.read_bytes().strip():
        raise ValueError("recipient needs nonempty local identity evidence at an absolute path")
    return config, state.resolve(), title


def observe(config, title, message):
    result = subprocess.run([config["probe"], title, message], text=True, capture_output=True,
                            timeout=3, check=False)
    observation = json.loads(result.stdout)
    if result.returncode != 0 or not isinstance(observation, dict) or observation.get("ready") is not True:
        if isinstance(observation, dict):
            blocker = session_blocker(observation)
            if blocker:
                raise ObservationUnavailable(blocker)
        raise ValueError("read-only probe did not return a ready observation")
    return observation


def baseline_ready(observation, title, digest):
    return (session_blocker(observation) is None
            and observation.get("ready") is True and type(observation.get("exact_window_count")) is int
            and observation["exact_window_count"] == 1 and observation.get("composer_empty") is True
            and type(observation.get("pid")) is int and observation["pid"] > 0
            and observation.get("target_title") == title and observation.get("message_sha256") == digest
            and type(observation.get("scan_limit")) is int and observation["scan_limit"] > 0
            and type(observation.get("matching_outgoing_count")) is int
            and observation["matching_outgoing_count"] >= 0)


def deliver(config_path, request):
    expected = binding(request)
    config, state, title = config_for(config_path, request["recipient"])
    # Anchor the ledger outside the configurable state directory. Editing that
    # setting must not silently discard completed/in-flight request history.
    registry = Path(config_path).resolve().parent
    lock = (registry / ".kakao-sender.lock").open("a+")
    os.chmod(registry / ".kakao-sender.lock", 0o600)
    state_lock = None
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"request_id": request["request_id"], "status": "not_sent", "reason": "sender_busy"}
        anchor = registry / ".kakao-sender-state.json"
        if anchor.exists():
            if json.loads(anchor.read_text()).get("state_dir") != str(state):
                raise ValueError("state_dir differs from the pinned ledger; migrate existing history explicitly")
        else:
            save(anchor, {"version": 1, "state_dir": str(state)}, exclusive=True)
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Different configuration directories can intentionally share one ledger.
        # They must also share its UI owner lock.
        state_lock = (state / ".sender.lock").open("a+")
        os.chmod(state / ".sender.lock", 0o600)
        try:
            fcntl.flock(state_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"request_id": request["request_id"], "status": "not_sent", "reason": "sender_busy"}
        identifier = hashlib.sha256(request["request_id"].encode()).hexdigest()
        intent_path = state / f"{identifier}.intent.json"
        if intent_path.exists():
            intent = json.loads(intent_path.read_text())
            if any(intent.get(key) != value for key, value in expected.items()) or intent.get("target_title") != title:
                return {"request_id": request["request_id"], "status": "not_sent", "reason": "request_id_conflict"}
            response = intent.get("response")
            if isinstance(response, dict):
                if response.get("status") == "sent":
                    load_verified_receipt(response.get("verification"), request)
                return response
            return {"request_id": request["request_id"], "status": "unknown", "reason": "existing_unfinished_intent_no_retry"}
        # A killed parent may have left its native child alive. Do not let a
        # different request operate that app while the earlier call is unresolved.
        for previous in state.glob("*.intent.json"):
            pending = json.loads(previous.read_text())
            if pending.get("sender_invocations") == 1 and not isinstance(pending.get("response"), dict):
                return {"request_id": request["request_id"], "status": "not_sent",
                        "reason": "prior_sender_interrupted_requires_reconciliation"}
        intent = {"version": 1, **expected, "target_title": title, "phase": "preflight",
                  "sender_invocations": 0, "automatic_retry_allowed": False}
        save(intent_path, intent, exclusive=True)

        def finish(status, reason, proof=None):
            response = {"request_id": request["request_id"], "status": status, "reason": reason,
                        "message_id": None, "automatic_retry_allowed": False}
            if proof is not None:
                response["verification"] = proof
            intent.update(phase="finished", response=response)
            save(intent_path, intent)
            return response

        try:
            before = observe(config, title, request["message"])
        except ObservationUnavailable as exc:
            return finish("not_sent", str(exc))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return finish("not_sent", "baseline_probe_unavailable")
        if not baseline_ready(before, title, expected["message_sha256"]):
            return finish("not_sent", "baseline_target_draft_or_observation_not_ready")
        intent.update(phase="sender_in_flight", sender_invocations=1, before=before)
        save(intent_path, intent)
        report = None
        try:
            result = subprocess.run([config["kmsg"], "send", "--background-safe", "--", title, request["message"]],
                                    text=True, capture_output=True, timeout=8, check=False)
            candidate = json.loads(result.stdout)
            if isinstance(candidate, dict):
                report = candidate
        except OSError:
            return finish("not_sent", "sender_process_could_not_start")
        except (ValueError, subprocess.TimeoutExpired):
            pass
        if (isinstance(report, dict) and type(report.get("commitAttempts")) is int
                and report["commitAttempts"] == 0 and report.get("status") == "blocked"):
            intent["draft_may_have_changed"] = report.get("draftMayHaveChanged")
            return finish("not_sent", "background_sender_stopped_before_commit")
        if not isinstance(report, dict) or type(report.get("commitAttempts")) is not int or report["commitAttempts"] != 1:
            return finish("unknown", "sender_commit_evidence_unavailable_no_retry")
        intent.update(phase="commit_attempted", commit_attempts=1)
        save(intent_path, intent)
        deadline = time.monotonic() + 2
        while True:
            try:
                after = observe(config, title, request["message"])
                if verify(before, after)["status"] == "confirmed_in_ui":
                    receipt = {"version": 1, **expected, "status": "confirmed_in_ui", "target_title": title,
                               "sender_invocations": 1, "commit_attempts": 1, "native_message_id": None,
                               "before": before, "after": after, "automatic_retry_allowed": False}
                    receipt_path = state / f"{identifier}.receipt.json"
                    digest = save(receipt_path, receipt)
                    proof = {"type": "ui_receipt", "path": str(receipt_path), "sha256": digest}
                    load_verified_receipt(proof, request)
                    return finish("sent", "confirmed_in_ui", proof)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return finish("unknown", "commit_attempted_ui_confirmation_unavailable_no_retry")
            time.sleep(min(0.1, remaining))
    finally:
        if state_lock is not None:
            state_lock.close()
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    request = None
    try:
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            raise ValueError("request exceeds 65536 characters")
        request = json.loads(raw)
        response = deliver(args.config, request)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # A durable in-flight intent survives even if storing the final response
        # fails. Never reinterpret an exception as evidence of no delivery.
        response = {"request_id": request.get("request_id") if isinstance(request, dict) else None,
                    "status": "unknown", "reason": type(exc).__name__, "automatic_retry_allowed": False}
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
