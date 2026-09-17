"""Check Git-index content and paths against the private student-name mapping."""

from __future__ import annotations

import re
import subprocess

from khan_kids.student_identity import load_aliases, require_aliases


def audit() -> list[str]:
    aliases = load_aliases()
    require_aliases(aliases)
    pattern = re.compile("|".join(re.escape(name) for name in aliases), re.IGNORECASE)
    entries = subprocess.check_output(["git", "ls-files", "--stage", "-z"]).split(b"\0")
    findings = []
    for entry in filter(None, entries):
        metadata, path = entry.split(b"\t", 1)
        blob = metadata.split()[1].decode("ascii")
        content = subprocess.check_output(["git", "cat-file", "blob", blob])
        if pattern.search(path.decode(errors="replace")) or pattern.search(
            content.decode(errors="replace")
        ):
            findings.append(blob)
    return findings


def main() -> int:
    findings = audit()
    print(f"Git-index blobs containing private student names: {len(findings)}")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
