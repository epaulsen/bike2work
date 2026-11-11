"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ConfigError, default_config_path, load_config
from .findmy_client import FindMyClient, FindMyClientError
from .registration import check_and_register, get_status
from .storage import StorageError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bike-tracker")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"configuration file (default: {default_config_path()})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="check Find My location and register today if eligible")
    subparsers.add_parser("status", help="show recorded days and distance")
    return parser


def main() -> int:
    args = _parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = load_config(args.config)
        if args.command == "check":
            print(check_and_register(config, FindMyClient(config)).message)
        else:
            status = get_status(config)
            print(f"Sykkeldager: {status.days} / 150")
            print(f"Distanse:    {status.distance_km:.1f} / 1500 km")
            if status.reached_days and status.reached_distance:
                bonus = "Oppnådd via dager og distanse"
            elif status.reached_days:
                bonus = "Oppnådd via dager"
            elif status.reached_distance:
                bonus = "Oppnådd via distanse"
            else:
                bonus = "Ikke oppnådd"
            print(f"Bonuskrav:   {bonus}")
    except (ConfigError, FindMyClientError, StorageError, OSError, ValueError) as exc:
        logging.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
