"""Named, inspectable policy parameters for the reporting scripts (ADR-010
Part 2) -- as opposed to crop_params.py/market_params.py, where every
constant is sourced or derived from real data, the constant below is a
configurable *definition*, not a measurement. It drives only how
equity_report.py groups its output; nothing upstream (scheduling,
fairness scoring) reads it.
"""

from __future__ import annotations

# Policy parameter, not a derived or sourced value: the holding-size
# threshold equity_report.py uses to split "smallholder" from "larger"
# coverage. No citable Government of India or Tamil Nadu numeric
# definition of "small and marginal holding" specific to paddy/Tamil Nadu
# was found in the time available for ADR-010 -- the closest citable
# figures (Agriculture Census operational-holding-size classes: marginal
# <1 ha / ~2.47 acres, small 1-2 ha / ~2.47-4.94 acres) describe a
# farmer's TOTAL operational holding across all their land, not a single
# plot's area, which is what this project actually has on record
# (Plot.area_acres). Using 2.5 acres here is a deliberate, disclosed
# approximation to that lower marginal-holding boundary, not a citation of
# it. Change freely -- this drives only this report's grouping.
SMALLHOLDER_THRESHOLD_ACRES: float = 2.5
