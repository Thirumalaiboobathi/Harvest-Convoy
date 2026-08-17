"""The 8-plot Kamatchipuram demo cluster.

Plain fixture data only -- no DynamoDB (that's Phase 5). Real village,
real coordinates; the plots, farmers, and transplant dates are synthetic,
designed to exercise the three-way TOO_GREEN / FITS / CONTESTED split the
Phase 2 gate requires. This must be disclosed as simulated in the README
(per the project's honesty rules) -- we did not onboard 8 live farmers.

Village: Kamatchipuram, Chinnamanur block, Uthamapalayam taluk, Theni
district, Tamil Nadu. Center coordinates 9.86500N 77.45389E, verified
directly against https://en.wikipedia.org/wiki/Kamatchipuram (fetched, not
just search-surfaced). Per-plot coordinates are small illustrative offsets
around that center (a few hundred meters to ~1.5km) -- they do not
correspond to real property boundaries.

Transplant dates are fixed ISO calendar dates rather than relative to
"today", so re-running this script on a later date will show the same
plots progressively further past maturity (or, eventually, all of them
badly overripe) -- that's real GDD accumulation behaving correctly, not a
bug. The dates below were chosen against 2026-08-16 to produce a mix of
too-green, comfortably-ready, and contested plots as of that date.
"""

from __future__ import annotations

import argparse
from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage import Storage, get_storage

VILLAGE_LAT = 9.86500
VILLAGE_LON = 77.45389

CLUSTER = Cluster(
    cluster_id="kamatchipuram",
    name="Kamatchipuram",
    machine_capacity_acres_per_day=3.5,
    machine_start_lat=VILLAGE_LAT,
    machine_start_lon=VILLAGE_LON,
    operator_chat_id=None,  # wired in Phase 4
)

FARMERS: list[Farmer] = [
    Farmer(farmer_id="f01", name="Muthu Pandian", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="f02", name="Selvi Karuppiah", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="f03", name="Kannan Raja", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="f04", name="Meena Subramani", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="f05", name="Raja Gounder", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="f06", name="Lakshmi Nadar", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="f07", name="Karthik Murugan", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="f08", name="Valli Chinnasamy", cluster_id=CLUSTER.cluster_id),
]

# (dlat, dlon) offsets from the village center -- small illustrative spread,
# not real field boundaries.
_OFFSETS = [
    (0.0060, -0.0040),
    (0.0035, 0.0080),
    (-0.0050, 0.0055),
    (0.0090, 0.0015),
    (-0.0075, -0.0060),
    (0.0020, -0.0110),
    (0.0110, 0.0070),
    (-0.0095, 0.0025),
]

PLOTS: list[Plot] = [
    Plot(
        plot_id="p01",
        farmer_id="f01",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[0][0],
        lon=VILLAGE_LON + _OFFSETS[0][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 1),  # well past maturity by 2026-08-16
        area_acres=2.5,
    ),
    Plot(
        plot_id="p02",
        farmer_id="f02",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[1][0],
        lon=VILLAGE_LON + _OFFSETS[1][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 5),
        area_acres=0.75,  # smallholder
    ),
    Plot(
        plot_id="p03",
        farmer_id="f03",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[2][0],
        lon=VILLAGE_LON + _OFFSETS[2][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 12),
        area_acres=3.0,
    ),
    Plot(
        plot_id="p04",
        farmer_id="f04",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[3][0],
        lon=VILLAGE_LON + _OFFSETS[3][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 18),
        area_acres=1.25,
    ),
    Plot(
        plot_id="p05",
        farmer_id="f05",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[4][0],
        lon=VILLAGE_LON + _OFFSETS[4][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 24),
        area_acres=2.0,
    ),
    Plot(
        plot_id="p06",
        farmer_id="f06",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[5][0],
        lon=VILLAGE_LON + _OFFSETS[5][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 30),
        area_acres=0.5,  # smallholder
    ),
    Plot(
        plot_id="p07",
        farmer_id="f07",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[6][0],
        lon=VILLAGE_LON + _OFFSETS[6][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 7, 20),  # too green as of 2026-08-16
        area_acres=1.5,
    ),
    Plot(
        plot_id="p08",
        farmer_id="f08",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[7][0],
        lon=VILLAGE_LON + _OFFSETS[7][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 7, 28),  # too green as of 2026-08-16
        area_acres=1.0,
    ),
]


def seed_into_storage(storage: Storage) -> None:
    """Write the fixture data above into a real Storage backend. The
    PLOTS/FARMERS/CLUSTER module-level fixtures stay exactly as they are
    for every test that imports them directly -- this just persists a
    copy, it doesn't replace them. See ADR-005 Decision 3."""
    result = storage.put_cluster(CLUSTER)
    if not result.success:
        print(f"  failed to write cluster: {result.error}")
    for farmer in FARMERS:
        result = storage.put_farmer(farmer)
        if not result.success:
            print(f"  failed to write farmer {farmer.farmer_id}: {result.error}")
    for plot in PLOTS:
        result = storage.put_plot(plot)
        if not result.success:
            print(f"  failed to write plot {plot.plot_id}: {result.error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write", action="store_true",
        help="Write this fixture data into the configured Storage backend "
        "(HARVEST_CONVOY_STORAGE env var; defaults to FileStorage).",
    )
    args = parser.parse_args()

    print(f"Cluster: {CLUSTER.name} ({CLUSTER.cluster_id})")
    print(f"Machine capacity: {CLUSTER.machine_capacity_acres_per_day} acres/day")
    print(f"{len(FARMERS)} farmers, {len(PLOTS)} plots")
    for plot in PLOTS:
        farmer = next(f for f in FARMERS if f.farmer_id == plot.farmer_id)
        print(
            f"  {plot.plot_id} ({farmer.name}): {plot.area_acres} acres, "
            f"transplanted {plot.transplant_date}, ({plot.lat:.4f}, {plot.lon:.4f})"
        )

    if args.write:
        print("\nWriting into storage backend...")
        seed_into_storage(get_storage())
        print("Done.")


if __name__ == "__main__":
    main()
