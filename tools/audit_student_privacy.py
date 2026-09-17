"""Check Git-index content and paths against the private student-name mapping."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from khan_kids.student_identity import load_aliases, require_aliases

CI_CONFIGURATION = (
    Path(__file__).resolve().parents[1] / "private/ci-privacy-fingerprints.local.json"
)
KEY_CHECK = b"khan-kids-privacy-key-check-v1"


def fingerprints(aliases: dict[str, str], key: str) -> list[dict[str, str | int]]:
    require_aliases(aliases)
    return [
        {"length": 0, "digest": hmac.new(key.encode(), KEY_CHECK, hashlib.sha256).hexdigest()}
    ] + [
        {
            "length": len(name.casefold()),
            "digest": hmac.new(key.encode(), name.casefold().encode(), hashlib.sha256).hexdigest(),
        }
        for name in aliases
    ]


def verify_configuration() -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    configuration = json.loads(CI_CONFIGURATION.read_text())
    if not configuration["key"] or configuration["entries"] != fingerprints(
        load_aliases(), configuration["key"]
    ):
        raise ValueError("CI fingerprints are stale; run tools/configure_student_privacy.py")


IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
}


def is_image(path: str, content: bytes) -> bool:
    return (
        Path(path).suffix.casefold() in IMAGE_SUFFIXES
        or content.startswith(
            (
                b"\x89PNG\r\n\x1a\n",
                b"\xff\xd8\xff",
                b"GIF87a",
                b"GIF89a",
                b"BM",
                b"II*\x00",
                b"MM\x00*",
            )
        )
        or (content.startswith(b"RIFF") and content[8:12] == b"WEBP")
        or (
            content[4:8] == b"ftyp"
            and any(brand in content[8:40] for brand in (b"heic", b"heif", b"mif1", b"avif"))
        )
    )


def name_matcher() -> Callable[[str], bool]:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        key = os.environ.get("KHAN_PRIVACY_KEY", "").encode()
        entries = json.loads(os.environ.get("KHAN_PRIVACY_FINGERPRINTS", "[]"))
        if not key or not entries:
            raise ValueError("CI privacy key and fingerprints are required")
        checks = [entry["digest"] for entry in entries if entry["length"] == 0]
        if checks != [hmac.new(key, KEY_CHECK, hashlib.sha256).hexdigest()]:
            raise ValueError("CI privacy key does not match fingerprint configuration")
        lengths = {entry["length"] for entry in entries if entry["length"] != 0}
        if not lengths:
            raise ValueError("CI privacy fingerprints contain no names")
        if any(not isinstance(length, int) or length < 1 for length in lengths):
            raise ValueError("Invalid privacy fingerprint length")
        digests = {entry["digest"] for entry in entries}

        @lru_cache(maxsize=65536)
        def forbidden(value: str) -> bool:
            return hmac.new(key, value.encode(), hashlib.sha256).hexdigest() in digests

        def matches(text: str) -> bool:
            text = text.casefold()
            for length in lengths:
                for offset in range(len(text) - length + 1):
                    if forbidden(text[offset : offset + length]):
                        return True
            return False

        return matches
    aliases = load_aliases()
    require_aliases(aliases)
    pattern = re.compile("|".join(re.escape(name) for name in aliases), re.IGNORECASE)
    return lambda text: bool(pattern.search(text))


def violates(matches: Callable[[str], bool], path: bytes, content: bytes) -> bool:
    filename = path.decode(errors="replace")
    return (
        is_image(filename, content)
        or matches(filename)
        or matches(content.decode(errors="replace"))
    )


def audit() -> list[str]:
    matches = name_matcher()
    entries = subprocess.check_output(["git", "ls-files", "--stage", "-z"]).split(b"\0")
    findings = []
    for entry in filter(None, entries):
        metadata, path = entry.split(b"\t", 1)
        blob = metadata.split()[1].decode("ascii")
        content = subprocess.check_output(["git", "cat-file", "blob", blob])
        if violates(matches, path, content):
            findings.append(blob)
    return findings


def audit_history(revisions: list[str]) -> list[str]:
    matches = name_matcher()
    findings = []
    seen = set()
    for commit in subprocess.check_output(["git", "rev-list", *revisions]).decode().splitlines():
        metadata = subprocess.check_output(
            ["git", "show", "-s", "--format=%an %ae %cn %ce %B", commit]
        ).decode(errors="replace")
        if matches(metadata):
            findings.append(commit)
        entries = subprocess.check_output(["git", "ls-tree", "-rz", commit]).split(b"\0")
        for entry in filter(None, entries):
            metadata, path = entry.split(b"\t", 1)
            _, kind, blob = metadata.split()
            if kind != b"blob" or (blob, path) in seen:
                continue
            seen.add((blob, path))
            content = subprocess.check_output(["git", "cat-file", "blob", blob.decode()])
            if violates(matches, path, content):
                findings.append(blob.decode())
    return findings


def main() -> int:
    verify_configuration()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--revision", action="append")
    parser.add_argument("--outgoing", action="store_true")
    args = parser.parse_args()
    revisions = args.revision or ["HEAD"]
    if args.outgoing:
        import sys

        revisions = [line.split()[1] for line in sys.stdin if line.split()[1].strip("0")]
    findings = (
        audit_history(revisions) if (args.history or args.outgoing) and revisions else audit()
    )
    print(f"Privacy violations (names or image captures): {len(findings)}")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
