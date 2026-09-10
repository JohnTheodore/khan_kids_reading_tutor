#!/usr/bin/env python3
"""Wake and unlock an Android device, then safely open Khan Academy Kids."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from khan_kids.adb import AndroidDevice, AutomationError
from khan_kids.launcher import ensure_khan_kids_open, pin_provider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="ADB serial, usually IP:port")
    parser.add_argument(
        "--secrets-file",
        type=Path,
        help="owner-private JSON file (default: .secrets.json when present)",
    )
    args = parser.parse_args()

    device = AndroidDevice(args.serial)
    device.assert_connected()
    with device.awake_session():
        result = ensure_khan_kids_open(device, pin_provider=pin_provider(args.secrets_file))
    print(json.dumps({"status": "open", **asdict(result)}, separators=(",", ":")))


def cli() -> None:
    try:
        main()
    except (AutomationError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    cli()
