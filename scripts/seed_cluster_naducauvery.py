"""The second seeded demo cluster: Naducauvery, Thanjavur district --
proves the multi-district claim in ADR-008 rather than asserting it.

Same shape as seed_cluster.py (CLUSTER/FARMERS/PLOTS/seed_into_storage/
main), deliberately a separate module rather than a change to
seed_cluster.py -- ADR-005 Decision 3 promises that module's fixtures
stay exactly as-is for every test that imports them directly, and this
file makes the same promise for itself going forward.

Village: Naducauvery, Thiruvaiyaru taluk, Thanjavur district, Tamil
Nadu. Center coordinates 10.861N 79.046E, verified directly against
https://en.wikipedia.org/wiki/Naducauvery (fetched, not just
search-surfaced). Thiruvaiyaru taluk sits in the Cauvery delta, Tamil
Nadu's principal rice-growing belt -- a genuinely different growing
environment from Theni's semi-arid interior climate: re-running ADR-002's
climatology methodology at these coordinates measured 21.2509 GDD/day
against Theni's 19.2133 (~10.6% hotter), the real number behind ADR-008
Decision 2's per-cluster maturity calibration. Per-plot coordinates below
are small illustrative offsets around that center (a few hundred meters
to ~1.5km), like Kamatchipuram's -- they do not correspond to real
property boundaries. Farmer names are synthetic, not real people --
same honesty disclosure as seed_cluster.py's Kamatchipuram fixture.

farmer_id/plot_id are prefixed "nc-" specifically so seeding both this
cluster and Kamatchipuram into the same Storage backend never collides
on FARMER#{id}/PLOT#{id} keys -- Kamatchipuram already owns the bare
f01..f08/p01..p08 ids.

machine_capacity_acres_per_day is kept identical to Kamatchipuram's
(3.5) on purpose: the whole point of this second cluster is to isolate
the climate/GDD effect (ADR-008 Decision 2), not confound it with also
varying machine capacity.

Transplant dates are fixed ISO calendar dates, same caveat as
Kamatchipuram's fixture: re-running this against live weather on a later
date will show a different too-green/fits/contested mix as real GDD
accumulates -- that's the model working correctly, not a bug.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage import Storage, get_storage
from harvest_convoy.weather.openmeteo import WeatherError

VILLAGE_LAT = 10.861
VILLAGE_LON = 79.046

CLUSTER = Cluster(
    cluster_id="naducauvery",
    name="Naducauvery",
    machine_capacity_acres_per_day=3.5,
    machine_start_lat=VILLAGE_LAT,
    machine_start_lon=VILLAGE_LON,
    operator_chat_id=None,
)

FARMERS: list[Farmer] = [
    Farmer(farmer_id="nc-f01", name="Marimuthu Iyer", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="nc-f02", name="Kamala Pillai", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="nc-f03", name="Rajendran Mudaliar", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="nc-f04", name="Meenakshi Iyengar", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="nc-f05", name="Sundaram Chettiar", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="nc-f06", name="Pappathi Naidu", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="nc-f07", name="Ganesan Pillai", cluster_id=CLUSTER.cluster_id),
    Farmer(farmer_id="nc-f08", name="Valliammai Naidu", cluster_id=CLUSTER.cluster_id),
]

# (dlat, dlon) offsets from the village center -- small illustrative
# spread, not real field boundaries. A distinct set from Kamatchipuram's,
# same purpose.
_OFFSETS = [
    (0.0055, -0.0035),
    (0.0030, 0.0075),
    (-0.0045, 0.0050),
    (0.0085, 0.0010),
    (-0.0070, -0.0055),
    (0.0018, -0.0100),
    (0.0100, 0.0065),
    (-0.0090, 0.0020),
]

PLOTS: list[Plot] = [
    Plot(
        plot_id="nc-p01",
        farmer_id="nc-f01",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[0][0],
        lon=VILLAGE_LON + _OFFSETS[0][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 10),  # well past maturity
        area_acres=2.0,
    ),
    Plot(
        plot_id="nc-p02",
        farmer_id="nc-f02",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[1][0],
        lon=VILLAGE_LON + _OFFSETS[1][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 16),
        area_acres=1.0,  # smallholder
    ),
    Plot(
        plot_id="nc-p03",
        farmer_id="nc-f03",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[2][0],
        lon=VILLAGE_LON + _OFFSETS[2][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 22),
        area_acres=2.5,
    ),
    Plot(
        plot_id="nc-p04",
        farmer_id="nc-f04",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[3][0],
        lon=VILLAGE_LON + _OFFSETS[3][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 30),
        area_acres=1.5,
    ),
    Plot(
        plot_id="nc-p05",
        farmer_id="nc-f05",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[4][0],
        lon=VILLAGE_LON + _OFFSETS[4][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 6, 8),
        area_acres=3.0,
    ),
    Plot(
        plot_id="nc-p06",
        farmer_id="nc-f06",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[5][0],
        lon=VILLAGE_LON + _OFFSETS[5][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 6, 20),
        area_acres=0.75,  # smallholder
    ),
    Plot(
        plot_id="nc-p07",
        farmer_id="nc-f07",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[6][0],
        lon=VILLAGE_LON + _OFFSETS[6][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 7, 25),  # too green as of mid-Aug 2026
        area_acres=1.25,
    ),
    Plot(
        plot_id="nc-p08",
        farmer_id="nc-f08",
        cluster_id=CLUSTER.cluster_id,
        lat=VILLAGE_LAT + _OFFSETS[7][0],
        lon=VILLAGE_LON + _OFFSETS[7][1],
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 8, 2),  # too green as of mid-Aug 2026
        area_acres=1.0,
    ),
]


def seed_into_storage(storage: Storage, *, calibrate: bool = False) -> None:
    """Write the fixture data above into a real Storage backend. See
    seed_cluster.py:seed_into_storage's docstring -- same contract,
    same --calibrate opt-in (network-gated, off by default, never called
    by the hermetic test suite)."""
    cluster = CLUSTER
    if calibrate:
        from harvest_convoy.agronomy.calibration import derive_cluster_maturity_gdd

        try:
            value = derive_cluster_maturity_gdd(
                CLUSTER.machine_start_lat, CLUSTER.machine_start_lon
            )
            cluster = replace(CLUSTER, maturity_gdd_override=value)
            print(f"  calibrated maturity threshold for {CLUSTER.cluster_id}: {value:.1f} GDD")
        except WeatherError as exc:
            print(
                f"  calibration failed ({exc}) -- seeding with "
                f"maturity_gdd_override=None; the global fallback "
                f"constant applies until this is retried"
            )

    result = storage.put_cluster(cluster)
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
    parser.add_argument(
        "--calibrate", action="store_true",
        help="Also derive this cluster's own maturity GDD threshold from "
        "real Open-Meteo climatology (live network calls, ~5 requests) "
        "and store it as Cluster.maturity_gdd_override. Requires --write. "
        "See ADR-008 Decision 2.",
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
        seed_into_storage(get_storage(), calibrate=args.calibrate)
        print("Done.")
    elif args.calibrate:
        print("\n--calibrate requires --write; nothing calibrated.")


if __name__ == "__main__":
    main()
