"""Market context for the post-harvest drying-window alert (ADR-009
Part 4).

Unlike every constant in crop_params.py, neither figure below is
derived or estimated by me. Both are real regulatory/procurement
numbers -- grade- and notification-dependent, changing season to
season -- and must be set and verified by a human (source URL,
announcement date) before this feature ships anything beyond the bare
rain warning. Never scraped, never model-generated, never guessed from
a conversational aside. See ADR-009 Part 4.

If either constant is None, watcher.py's drying-window check omits that
specific figure from the alert entirely rather than inventing one --
the two are independent claims, tested independently.
"""

from __future__ import annotations

# MSP (Minimum Support Price) for paddy, Common grade, per quintal (Rs).
#
# Source: <TNCSC / CACP / Ministry of Agriculture & Farmers Welfare
# notification URL -- set this before shipping>
# Announced: <date of the notification -- set this>
#
# None until a real, source-cited value is set here. Wording rule this
# feeds (ADR-009 Part 4): "MSP for this grade is Rs X" -- never "you
# will receive Rs X". Actual payment depends on grade, moisture, and
# the DPC's own assessment; MSP is a published reference figure, not a
# personal payment promise.
MSP_PADDY_COMMON_PER_QUINTAL: int | None = None

# Moisture content ceiling for full-price DPC (Direct Procurement
# Centre) acceptance, percent.
#
# Source: <FCI / TNCSC procurement specification URL -- set this>
# Announced: <date, if this is a per-season notified figure -- set this>
#
# None until a real, source-cited value is set here. The "roughly 14%"
# figure that came up in conversation is a discussion point, not a
# citable number -- do not use it to fill this in without a real source.
DPC_MOISTURE_THRESHOLD_PERCENT: int | None = None
