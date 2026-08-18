"""Operator correction: a plot was marked harvested that shouldn't have
been -- returns it to the schedulable pool for scheduling.solver.solve()
on the next trigger day. See ADR-009 Part 1.5, Decision E.

No Telegram/operator command surface for this -- wasn't asked for and
isn't needed for this to be usable; this script is the whole interface,
same shape as this project's other maintenance scripts
(scripts/check_watcher_health.py).

Usage:
    uv run python -m scripts.clear_plot_harvest --cluster-id kamatchipuram \
        --season-id 2026-kuruvai --plot-id p03
"""

from __future__ import annotations

import argparse
import sys

from harvest_convoy.storage import get_storage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster-id", required=True)
    parser.add_argument("--season-id", required=True)
    parser.add_argument("--plot-id", required=True)
    args = parser.parse_args()

    storage = get_storage()
    before = storage.get_harvested_plot_ids(args.cluster_id, args.season_id)
    if args.plot_id not in before:
        print(
            f"[INFO] {args.plot_id} was not marked harvested for "
            f"cluster={args.cluster_id} season={args.season_id} -- nothing to clear."
        )
        return 0

    result = storage.clear_plot_harvest(args.plot_id, args.cluster_id, args.season_id)
    if not result.success:
        print(f"[FAIL] clear_plot_harvest failed: {result.error}")
        return 1

    print(
        f"[OK] {args.plot_id} is no longer marked harvested for "
        f"cluster={args.cluster_id} season={args.season_id} -- "
        f"schedulable again on the next trigger day."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
