"""
yc-ip-cycler — cycle Yandex Cloud static IPs until one lands in a desired subnet.

Usage:
    python main.py [--config CONFIG] [--dry-run] [--verbose]

Environment variables (set ONE):
    YC_TOKEN    — short-lived IAM token  (yc iam create-token)
    YC_API_KEY  — long-lived API key     (Yandex Cloud console → Service accounts)
"""

import argparse
import logging
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows so checkmarks/crosses render correctly
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from cycler import load_config, run_cycle


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s  %(levelname)-7s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # File handler — always DEBUG so the log file is complete
    file_handler = logging.FileHandler("cycle.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt))

    # Console handler — respects --verbose flag
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(fmt, datefmt))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yc-ip-cycler",
        description="Cycle Yandex Cloud static IPs until one falls in a desired subnet.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        metavar="FILE",
        help="Path to config YAML (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without making any API calls",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show DEBUG-level log messages on stdout (always written to cycle.log)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    _setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    config_path = Path(args.config)
    if not config_path.exists() and not args.dry_run:
        print(f"Config file not found: {config_path}")
        print("Copy config.yaml.example to config.yaml and fill in your values.")
        sys.exit(1)

    try:
        if config_path.exists():
            config = load_config(str(config_path))
        else:
            # dry-run with missing config: show placeholder output
            from cycler import Config
            config = Config(
                folder_id="<folder_id>",
                zone="ru-central1-a",
                desired_subnets=["5.45.192.0/18"],
            )
            logger.warning("Config not found; using placeholder values for dry run.")
    except (ValueError, OSError) as exc:
        print(f"Config error: {exc}")
        sys.exit(1)

    logger.debug("Config loaded: %s", config)
    exit_code = run_cycle(config, dry_run=args.dry_run)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
