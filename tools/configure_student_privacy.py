"""Refresh private CI fingerprints after updating the local student alias map."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile

from audit_student_privacy import CI_CONFIGURATION, fingerprints
from khan_kids.student_identity import load_aliases


def main() -> None:
    configuration = json.loads(CI_CONFIGURATION.read_text()) if CI_CONFIGURATION.exists() else {}
    key = configuration.get("key") or secrets.token_hex(32)
    entries = fingerprints(load_aliases(), key)
    for secret, value in (
        ("KHAN_PRIVACY_KEY", key),
        ("KHAN_PRIVACY_FINGERPRINTS", json.dumps(entries)),
    ):
        subprocess.run(
            ["gh", "secret", "set", secret, "--repo", "JohnTheodore/khan_kids_reading_tutor"],
            input=value.encode(),
            check=True,
            capture_output=True,
        )
    CI_CONFIGURATION.parent.mkdir(mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=CI_CONFIGURATION.parent, delete=False
    ) as temporary:
        temporary_path = temporary.name
        json.dump({"key": key, "entries": entries}, temporary)
    try:
        os.replace(temporary_path, CI_CONFIGURATION)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    print("Updated GitHub privacy secrets and owner-private local configuration")


if __name__ == "__main__":
    main()
