"""
Core cycling logic: allocate IPs one at a time, check against desired
subnets, release misses, keep the hit.
"""

import json
import logging
import signal
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from subnet_match import ip_in_any_subnet
from yc_client import APIError, AuthError, YandexCloudClient

logger = logging.getLogger(__name__)

RESULT_FILE = Path("result.json")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Config:
    folder_id: str
    zone: str
    desired_subnets: list[str]
    max_attempts: int = 50
    release_on_exit: bool = True


def load_config(path: str) -> Config:
    with open(path) as fh:
        raw = yaml.safe_load(fh)

    missing = [k for k in ("folder_id", "zone", "desired_subnets") if k not in raw]
    if missing:
        raise ValueError(f"Config is missing required fields: {missing}")

    subnets = raw["desired_subnets"]
    if not isinstance(subnets, list) or not subnets:
        raise ValueError("desired_subnets must be a non-empty list of CIDR strings")

    return Config(
        folder_id=str(raw["folder_id"]),
        zone=str(raw["zone"]),
        desired_subnets=[str(s) for s in subnets],
        max_attempts=int(raw.get("max_attempts", 50)),
        release_on_exit=bool(raw.get("release_on_exit", True)),
    )


# ---------------------------------------------------------------------------
# SIGINT / cleanup state
# ---------------------------------------------------------------------------

class _CleanupState:
    """Mutable holder so the signal handler can see the latest address ID."""
    def __init__(self) -> None:
        self.address_id: Optional[str] = None
        self.client: Optional[YandexCloudClient] = None

    def release_current(self) -> None:
        if self.address_id and self.client:
            logger.warning("Releasing %s before exit…", self.address_id)
            try:
                self.client.release_ip(self.address_id)
                logger.info("Released %s.", self.address_id)
            except Exception as exc:
                logger.error("Failed to release %s: %s", self.address_id, exc)
            self.address_id = None


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle(config: Config, dry_run: bool = False) -> int:
    """
    Execute the IP-cycling loop.
    Returns 0 on success (matching IP found), 1 on failure / exhausted.
    """
    # Idempotency guard
    if RESULT_FILE.exists():
        try:
            with open(RESULT_FILE) as fh:
                existing = json.load(fh)
            print(
                f"Already have a matching IP: {existing['ip_address']} "
                f"(subnet: {existing['matched_subnet']}, "
                f"resource: {existing['resource_id']})"
            )
            print(f"Delete {RESULT_FILE} to re-run.")
        except (json.JSONDecodeError, KeyError):
            print(f"{RESULT_FILE} exists but is malformed — delete it and re-run.")
            return 1
        return 0

    if dry_run:
        _print_dry_run(config)
        return 0

    # Real run
    try:
        client = YandexCloudClient()
    except AuthError as exc:
        print(f"Auth error: {exc}")
        return 1

    state = _CleanupState()
    state.client = client

    def _sigint_handler(signum, frame):  # noqa: ANN001
        print("\n[Interrupted]")
        state.release_current()
        raise SystemExit(130)

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        return _loop(config, client, state)
    finally:
        client.close()
        state.client = None


def _loop(config: Config, client: YandexCloudClient, state: _CleanupState) -> int:
    for attempt in range(1, config.max_attempts + 1):
        # Unique name: timestamp + short UUID so parallel runs don't collide
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        uid = uuid.uuid4().hex[:6]
        name = f"ipcycler-{ts}-{uid}"

        logger.info(
            "[%d/%d] Allocating IP (name=%s, zone=%s)…",
            attempt, config.max_attempts, name, config.zone,
        )

        try:
            address_id, ip_address = client.allocate_ip(config.folder_id, config.zone, name)
        except APIError as exc:
            print(f"API error while allocating IP: {exc.body}")
            logger.error("Allocation failed: %s", exc)
            return 1

        state.address_id = address_id
        matched = ip_in_any_subnet(ip_address, config.desired_subnets)

        if matched:
            _on_found(ip_address, address_id, matched, attempt, config.zone)
            state.address_id = None  # keep this IP — don't release on exit
            return 0

        # Not a match — release and try again
        print(f"✗ Attempt {attempt}: {ip_address} — not in desired range, releasing…")
        logger.info("Miss: %s (%s) — releasing.", ip_address, address_id)

        try:
            client.release_ip(address_id)
        except APIError as exc:
            logger.warning("Could not release %s: %s", address_id, exc)

        state.address_id = None

        if attempt < config.max_attempts:
            time.sleep(1.5)

    print(f"No matching IP found after {config.max_attempts} attempts.")
    logger.warning("Exhausted %d attempts without a match.", config.max_attempts)
    return 1


def _on_found(
    ip_address: str,
    address_id: str,
    matched_subnet: str,
    attempts: int,
    zone: str,
) -> None:
    print(
        f"✓ Found matching IP: {ip_address} "
        f"(subnet: {matched_subnet}) "
        f"after {attempts} attempt(s)"
    )
    print(f"  Resource ID: {address_id}")

    result = {
        "ip_address": ip_address,
        "resource_id": address_id,
        "matched_subnet": matched_subnet,
        "attempts": attempts,
        "zone": zone,
    }
    with open(RESULT_FILE, "w") as fh:
        json.dump(result, fh, indent=2)
        fh.write("\n")

    logger.info("Result written to %s", RESULT_FILE)
    print(f"  Result saved to {RESULT_FILE}")


def _print_dry_run(config: Config) -> None:
    print("[DRY RUN] No API calls will be made.")
    print()
    print(f"  folder_id:       {config.folder_id}")
    print(f"  zone:            {config.zone}")
    print(f"  max_attempts:    {config.max_attempts}")
    print(f"  release_on_exit: {config.release_on_exit}")
    print(f"  desired_subnets:")
    for cidr in config.desired_subnets:
        print(f"    - {cidr}")
    print()
    print("[DRY RUN] Would loop up to", config.max_attempts, "times:")
    print("  POST https://vpc.api.cloud.yandex.net/vpc/v1/addresses")
    print("  -> check returned IP against subnets above")
    print("  -> on miss: DELETE https://vpc.api.cloud.yandex.net/vpc/v1/addresses/<id>")
    print("  -> on hit:  write result.json and exit 0")
