"""Orchestration: HealthPlanet -> (new only) -> FIT -> Garmin Connect.

Run once per invocation (driven by a k8s CronJob twice a day):
  1. Determine the fetch window from saved state (or LOOKBACK_DAYS on first run).
  2. Fetch weight measurements from HealthPlanet.
  3. Keep only measurements newer than the last uploaded one.
  4. If none are new, exit immediately without touching Garmin.
  5. Otherwise write a FIT file and upload it, then advance the state.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from typing import Optional

from .config import Config, load_config
from .fit_writer import write_weight_fit
from .garmin import GarminUploader
from .healthplanet import HealthPlanetClient, WeightMeasurement
from . import state

logger = logging.getLogger(__name__)

# HealthPlanet limits the from/to window to ~3 months.
MAX_WINDOW_DAYS = 90


def _effective_floor(config: Config, last_key: str | None) -> Optional[str]:
    """Highest of saved state and the SYNC_SINCE floor.

    Measurements with key <= this are treated as already handled and skipped.
    Keys are fixed-width "YYYYMMDDHHMM" strings, so plain comparison works.
    """
    candidates = [k for k in (last_key, config.sync_since_key) if k]
    return max(candidates) if candidates else None


def _fetch_window(config: Config, floor_key: str | None) -> tuple[datetime, datetime]:
    now = datetime.now(config.hp_tz)
    if floor_key:
        from_dt = datetime.strptime(floor_key, "%Y%m%d%H%M").replace(tzinfo=config.hp_tz)
    else:
        from_dt = now - timedelta(days=config.lookback_days)
    # Never request more than the API allows.
    earliest = now - timedelta(days=MAX_WINDOW_DAYS)
    if from_dt < earliest:
        from_dt = earliest
    return from_dt, now


def _new_measurements(
    measurements: list[WeightMeasurement], last_key: str | None
) -> list[WeightMeasurement]:
    if last_key is None:
        return measurements
    return [m for m in measurements if m.key > last_key]


def run(config: Config, seed: bool = False) -> int:
    last_key = state.load_last_key(config.state_file)
    floor_key = _effective_floor(config, last_key)
    from_dt, to_dt = _fetch_window(config, floor_key)
    logger.info(
        "Fetching HealthPlanet weights from %s to %s (state=%s, since=%s)",
        from_dt.isoformat(),
        to_dt.isoformat(),
        last_key,
        config.sync_since_key,
    )

    hp = HealthPlanetClient(config)
    measurements = hp.get_weight_measurements(from_dt, to_dt)
    logger.info("HealthPlanet returned %d measurement(s)", len(measurements))

    if seed:
        # Mark current measurements as already-synced WITHOUT uploading, so only
        # measurements newer than now flow to Garmin from here on. This protects
        # weights that were already entered in Garmin manually.
        if not measurements:
            logger.info("Seed: no measurements found; state left unchanged.")
            return 0
        newest = measurements[-1].key
        state.save_last_key(config.state_file, newest)
        logger.info(
            "Seed complete: marked %d existing measurement(s) as synced up to %s. "
            "Nothing uploaded.",
            len(measurements),
            newest,
        )
        return 0

    new = _new_measurements(measurements, floor_key)
    if not new:
        logger.info("No new measurements; nothing to upload. Done.")
        return 0
    logger.info("%d new measurement(s) to upload", len(new))

    fit_path = config.data_dir / "upload.fit"
    write_weight_fit(new, fit_path)

    uploader = GarminUploader(config)
    uploader.login()
    uploader.upload_fit(fit_path)

    state.save_last_key(config.state_file, new[-1].key)
    logger.info("Sync complete: uploaded %d measurement(s)", len(new))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="HealthPlanet -> Garmin weight sync")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Mark existing HealthPlanet measurements as already synced "
        "(no upload). Run once before the first real sync so weights already "
        "in Garmin are not re-uploaded.",
    )
    args = parser.parse_args()

    # fit_tool calls logging.basicConfig() at import time, attaching a handler to the root
    # logger (and leaving its level at WARNING). Without force=True our basicConfig would be a
    # no-op and every INFO line below would be silently dropped -> empty container logs. force=True
    # replaces that handler so progress is visible in `kubectl logs` / Loki.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    logger.info("healthplanet-garmin-sync starting (seed=%s)", args.seed)
    try:
        config = load_config()
        return run(config, seed=args.seed)
    except Exception:  # noqa: BLE001 - top-level guard for the CronJob
        logger.exception("Sync failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
