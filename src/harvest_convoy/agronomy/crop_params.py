"""Crop parameters for paddy variety ADT 45.

Every constant below carries a source URL or an explicit DERIVED/ESTIMATED
comment and derivation. Do not add a bare number here — see
docs/adr/ADR-001-agronomy-core.md for the full sourcing writeup, including
what was searched for and not found.
"""

from __future__ import annotations

# Base temperature for rice GDD accumulation.
#
# Source: Sanwong, P., Sanitchon, J., Dongsansuk, A., & Jothityangkoon, D.
# (2023). "High Temperature Alters Phenology, Seed Development and Yield in
# Three Rice Varieties." Plants 12(3):666.
# https://pmc.ncbi.nlm.nih.gov/articles/PMC9921536/
# Eq. 1 states explicitly: "Tbase is the base temperature (Tbase of
# rice = 10.0 degC)." Fetched and verified directly (open access).
T_BASE_C: float = 10.0

# ADT 45 total crop duration, nursery sowing to physiological maturity.
#
# Source: TNAU AgriTech Portal, "Paddy Varieties of Tamil Nadu".
# http://agritech.tnau.ac.in/expert_system/paddy/TNvarieties.html
# Listed under the 90-120 day short-duration category, explicitly marked
# suitable for the Kuruvai season. Fetched and verified directly.
#
# A conflicting figure (130-135 days) appears on an unofficial mirror site
# that cites neither TNAU nor ICAR, and contradicts the 90-120 day
# short-duration classification the TNAU portal itself assigns to this
# variety. Rejected as non-primary and internally inconsistent.
ADT45_CROP_DURATION_DAYS: int = 110

# DERIVED: midpoint of TNAU's 18-22 day nursery-age guidance for
# short-duration varieties. Not measured for ADT 45 specifically -- a chosen
# point in a cited range, used only to convert the sowing-anchored duration
# above into a transplant-anchored one (see ADR-001 Decision 1 for why this
# system anchors on transplant_date rather than sowing_date).
ADT45_NURSERY_AGE_DAYS_ESTIMATED: int = 20

# DERIVED: field-only duration, transplant_date to physiological maturity.
ADT45_FIELD_DURATION_DAYS_ESTIMATED: int = (
    ADT45_CROP_DURATION_DAYS - ADT45_NURSERY_AGE_DAYS_ESTIMATED
)

# DERIVED, computed from real data: mean daily GDD accrual for Theni,
# computed directly from Open-Meteo Archive API history (fully historical,
# no lag/bridging involved) at (10.0104, 77.4768) across the 90-day window
# May 15 - Aug 12, for the five most recent complete years (2021-2025).
# 450 days total, mean = 19.2133 GDD/day (yearly means: 19.169, 19.111,
# 20.385, 18.361, 19.041 -- the mean of yearly means matches to 4 decimal
# places, so no single year dominates). See ADR-002 Decision 1 for the full
# reconciliation against the Phase 1 gate run's single-year sample
# (20.72 GDD/day for 2026), which is what triggered this re-derivation --
# the original 17.5 was a brief-asserted, never-independently-verified
# figure and undershot the real climatology.
KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED: float = 19.2133

# DERIVED, NOT TNAU/ICAR-sourced: no published thermal-time (GDD) requirement
# for ADT 45 was found despite targeted searches (see ADR-001 Decision 3).
# Estimated as field-duration days times assumed mean daily GDD accrual,
# anchored at transplant_date. This is a single module-level constant,
# deliberately not a literal buried in scheduling logic, so it can be
# corrected in one place if a measured value is found later.
MATURITY_GDD_ESTIMATED: float = (
    ADT45_FIELD_DURATION_DAYS_ESTIMATED * KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED
)

# DERIVED, NOT sourced: no citable day-past-physiological-maturity loss
# curve for paddy was found despite targeted searches, including one search
# result that turned out to cite a specific yield-loss figure not actually
# present in its stated source -- caught by direct verification and
# discarded (see ADR-002 Decision 2 for the full account). This horizon sets
# the *shape* used for urgency ranking in scheduling (a linear ramp from 0
# at maturity to full loss at this many days past it) -- it is a ranking
# proxy, not a validated agronomic yield-loss curve. Chosen as a round
# number past the recurring-but-unquantified "harvest within 10-15 days of
# maturity" guidance found across multiple sources.
DECAY_HORIZON_DAYS_ESTIMATED: int = 20
