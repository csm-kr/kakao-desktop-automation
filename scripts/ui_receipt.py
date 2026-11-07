"""Local UI confirmation evidence; never a Kakao server message identifier."""
import hashlib
import json
import re
from pathlib import Path

from verify_delivery import verify


def binding(request):
    if not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 1:
        raise ValueError("request version must be 1")
    for field in ("request_id", "recipient", "message"):
        value = request.get(field)
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise ValueError(f"{field} must be a nonempty string without NUL")
    if len(request["request_id"]) > 512:
        raise ValueError("request_id is too long")
    return {"request_id": request["request_id"], "recipient": request["recipient"],
            "message_sha256": hashlib.sha256(request["message"].encode()).hexdigest()}


def load_verified_receipt(proof, request):
    expected = binding(request)
    if not isinstance(proof, dict) or proof.get("type") != "ui_receipt":
        raise ValueError("verification must identify a ui_receipt")
    path = Path(proof.get("path", ""))
    digest = proof.get("sha256")
    if not path.is_absolute() or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("receipt requires an absolute path and SHA-256")
    with path.open("rb") as stream:
        raw = stream.read(1_048_577)
    if len(raw) > 1_048_576 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("receipt size or SHA-256 mismatch")
    receipt = json.loads(raw)
    if (not isinstance(receipt, dict) or type(receipt.get("version")) is not int
            or receipt["version"] != 1 or receipt.get("status") != "confirmed_in_ui"):
        raise ValueError("receipt is not a confirmed version 1 UI observation")
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("receipt does not match the request")
    before, after = receipt.get("before"), receipt.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("receipt lacks before/after observations")
    if not isinstance(receipt.get("target_title"), str) or not receipt["target_title"]:
        raise ValueError("receipt lacks an exact target title")
    if any(item.get("message_sha256") != expected["message_sha256"]
           or item.get("target_title") != receipt["target_title"] for item in (before, after)):
        raise ValueError("observation body or target differs from receipt")
    if any(type(receipt.get(key)) is not int or receipt[key] != 1
           for key in ("commit_attempts", "sender_invocations")):
        raise ValueError("receipt must describe one sender invocation and commit attempt")
    if verify(before, after)["status"] != "confirmed_in_ui":
        raise ValueError("observations do not confirm one new outgoing message")
    return {"type": "ui_receipt", "path": str(path), "sha256": digest,
            "level": "confirmed_in_ui", "native_message_id": None}
