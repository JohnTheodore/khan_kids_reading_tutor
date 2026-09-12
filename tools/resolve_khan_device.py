#!/usr/bin/env python3
"""Print a private tablet default or resolve its current verified ADB endpoint."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from khan_kids.device_discovery import DeviceConfig, DeviceDiscoveryError, resolve_device


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("private/tablet-device.local.json"))
    parser.add_argument("--field", choices=("serial", "student"), default="serial")
    args = parser.parse_args()
    try:
        config = DeviceConfig.load(args.config)
        value = config.student if args.field == "student" else resolve_device(config)
    except DeviceDiscoveryError as error:
        parser.exit(2, f"tablet discovery failed: {error}\n")
    print(value)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
