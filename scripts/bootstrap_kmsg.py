#!/usr/bin/env python3
"""Build a pinned, project-local kmsg adapter without opening KakaoTalk."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM_URL = "https://github.com/channprj/kmsg.git"
UPSTREAM_COMMIT = "54cbff790b409214735ef365a621d791034fe500"
PACKAGE = Path(__file__).resolve().parents[1]
VARIANTS = {
    "foreground": "kmsg-foreground.patch",
    "background-experimental": "kmsg-background-experimental.patch",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False,
                                timeout=180 if argv[0] == "git" else 900)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command deadline exceeded: {argv[0]}") from exc
    if result.returncode:
        raise RuntimeError(f"command failed: {argv[0]}: {result.stderr.strip()}")
    return result.stdout.strip()


def snapshot(checkout):
    paths = command(["git", "-C", str(checkout), "ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    state = hashlib.sha256()
    for name in sorted(set(paths.split("\0")) - {""}):
        path = checkout / name
        if path.is_symlink():
            link = os.readlink(path)
            if Path(link).is_absolute() or not path.resolve().is_relative_to(checkout.resolve()) or not path.is_file():
                raise RuntimeError("source symlink must resolve to a file inside its checkout")
            state.update(name.encode() + b"\0link\0" + link.encode() + b"\0" + bytes.fromhex(digest(path)))
        elif path.is_file():
            state.update(name.encode() + b"\0file\0" + bytes.fromhex(digest(path)))
        else:
            raise RuntimeError("source checkout contains a missing file")
    return state.hexdigest()


def build(checkout, probe_source):
    command(["swift", "build", "-c", "release", "--package-path", str(checkout)])
    command(["swiftc", "-O", str(probe_source), "-o", str(checkout / ".build/release/kakao-probe")])


def bootstrap(project, variant="foreground"):
    if sys.platform != "darwin":
        raise RuntimeError("This adapter builds only on macOS; Windows needs a separate backend")
    if variant not in VARIANTS:
        raise ValueError("unknown adapter variant")
    for executable in ["git", "swift", "swiftc"]:
        if shutil.which(executable) is None:
            raise RuntimeError(f"required existing tool is missing: {executable}")
    project = Path(project).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    tools = project / ".tools"
    if tools.is_symlink():
        raise RuntimeError("project .tools is a symlink; choose a local managed directory")
    tools.mkdir(exist_ok=True)
    destination = tools / f"kmsg-{variant}"
    receipt = tools / f"kmsg-{variant}.install.json"
    patch = PACKAGE / "patches" / VARIANTS[variant]
    probe_source = PACKAGE / "scripts/probe.swift"
    expected = {"upstream": UPSTREAM_URL, "commit": UPSTREAM_COMMIT, "variant": variant,
                "patch_sha256": digest(patch), "probe_source_sha256": digest(probe_source)}
    # Local build locking is separate from send idempotency; bootstrap never sends.
    import fcntl
    with (tools / f"kmsg-{variant}.install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            if destination.is_symlink():
                raise RuntimeError("existing checkout is a symlink; leave it untouched")
            try:
                previous = json.loads(receipt.read_text())
            except (OSError, ValueError) as exc:
                raise RuntimeError("existing checkout has no matching install receipt; leave it untouched") from exc
            if any(previous.get(key) != value for key, value in expected.items()):
                raise RuntimeError("existing checkout uses different source/patch; leave it untouched")
            head = command(["git", "-C", str(destination), "rev-parse", "HEAD"])
            if head != UPSTREAM_COMMIT or snapshot(destination) != previous.get("source_tree_sha256"):
                raise RuntimeError("existing source was changed; leave it untouched")
            binary = destination / ".build/release/kmsg"
            probe = destination / ".build/release/kakao-probe"
            if not binary.is_file() or not probe.is_file() or digest(binary) != previous.get("binary_sha256") or digest(probe) != previous.get("probe_sha256"):
                raise RuntimeError("cached executable is missing or changed; use a fresh project directory")
            return {"status": "reused", "variant": variant, "kmsg": str(binary), "probe": str(probe)}
        if receipt.exists():
            raise RuntimeError("orphaned install receipt exists; inspect it before retrying")
        temporary = Path(tempfile.mkdtemp(prefix=".kmsg-build-", dir=tools))
        moved = False
        try:
            command(["git", "init", "--quiet", str(temporary)])
            command(["git", "-C", str(temporary), "fetch", "--depth", "1", UPSTREAM_URL, UPSTREAM_COMMIT])
            command(["git", "-C", str(temporary), "checkout", "--quiet", "--detach", "FETCH_HEAD"])
            if command(["git", "-C", str(temporary), "rev-parse", "HEAD"]) != UPSTREAM_COMMIT:
                raise RuntimeError("fetched source did not match pinned commit")
            command(["git", "-C", str(temporary), "apply", "--check", str(patch)])
            command(["git", "-C", str(temporary), "apply", str(patch)])
            build(temporary, probe_source)
            binary = temporary / ".build/release/kmsg"
            probe = temporary / ".build/release/kakao-probe"
            record = {**expected, "source_tree_sha256": snapshot(temporary),
                      "binary_sha256": digest(binary), "probe_sha256": digest(probe)}
            # Reserve the receipt before exposing the final directory; failures leave no half install.
            with receipt.open("x") as file:
                json.dump(record, file, indent=2)
                file.write("\n")
            try:
                temporary.rename(destination)
                moved = True
            except BaseException:
                receipt.unlink()
                raise
            return {"status": "built", "variant": variant,
                    "kmsg": str(destination / ".build/release/kmsg"),
                    "probe": str(destination / ".build/release/kakao-probe")}
        finally:
            if not moved:
                shutil.rmtree(temporary, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--variant", choices=VARIANTS, default="foreground")
    args = parser.parse_args()
    try:
        print(json.dumps(bootstrap(args.project, args.variant), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
