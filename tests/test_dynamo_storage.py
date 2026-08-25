"""dynamo.py tests. Pure encode/decode helpers are tested directly (no
AWS needed). The "DynamoDB unavailable" failure path is tested against a
genuinely unreachable endpoint -- this is a real TCP-level connect
timeout, not an instant failure, so this one test takes several seconds;
that's inherent to proving the timeout/degrade behavior actually works,
not a flaky test. See ADR-005 Decision 5.

No real AWS table is created or used by anything in this file -- an
actual DynamoStorage <-> real-or-local-DynamoDB round trip was not
verified (see the phase report); FileStorage is what every other test in
this suite actually exercises.
"""

from __future__ import annotations

import typing
from decimal import Decimal

import pytest

from harvest_convoy.storage import dynamo
from harvest_convoy.storage.dynamo import (
    DynamoStorage,
    _decode,
    _encode,
    _from_decimal,
    _item_to_cluster,
    _item_to_decision_record,
    _item_to_plot,
    _strip_keys,
    _to_decimal,
)


def test_to_decimal_converts_float() -> None:
    assert _to_decimal(2.5) == Decimal("2.5")


def test_to_decimal_leaves_non_float_alone() -> None:
    assert _to_decimal("x") == "x"
    assert _to_decimal(5) == 5
    assert _to_decimal(True) is True


def test_from_decimal_converts_back_to_float() -> None:
    assert _from_decimal(Decimal("2.5")) == 2.5
    assert isinstance(_from_decimal(Decimal("2.5")), float)


def test_from_decimal_preserves_int_for_integral_values() -> None:
    """DynamoDB's Number type doesn't distinguish int from float -- any
    plain int a real table stores (e.g. Farmer.telegram_chat_id) comes
    back from boto3 as a Decimal regardless. Unconditionally casting to
    float turned a real chat_id like 1276258406 into 1276258406.0 --
    caught live wiring up deployment verification, a JSON float in that
    position is not a valid Telegram chat_id. A genuinely fractional
    Decimal (acreage, GDD values) must still come back as float."""
    result = _from_decimal(Decimal("1276258406"))
    assert result == 1276258406
    assert isinstance(result, int)


def test_from_decimal_round_trip_preserves_int_chat_id() -> None:
    """The realistic path: an int chat_id survives _to_decimal (encode)
    then _from_decimal (decode) as the same int, not a float."""
    encoded = _to_decimal(1276258406)
    assert encoded == 1276258406  # _to_decimal only Decimal-izes floats
    # A real DynamoDB table returns this as Decimal on read regardless of
    # how it was written -- simulate that boundary directly.
    decoded = _from_decimal(Decimal(1276258406))
    assert decoded == 1276258406
    assert isinstance(decoded, int)


def test_encode_decode_round_trip() -> None:
    original = {"acres": 2.5, "name": "x", "count": 3}
    encoded = _encode(original)
    assert encoded["acres"] == Decimal("2.5")
    decoded = _decode(encoded)
    assert decoded == original


def test_to_decimal_recurses_into_nested_dicts() -> None:
    """ADR-010's DecisionRecord carries nested dicts (own_claim/
    opponent_claim, an AdvocateClaim.model_dump()) with float fields --
    every entity before it was flat, so this recursion is new behavior,
    not just an existing guarantee re-asserted."""
    nested = {"urgency_score": 0.3, "acres": 2.5, "concedes": False, "plot_id": "p1"}
    encoded = _to_decimal({"own_claim": nested, "plain": 1.5})
    assert encoded["own_claim"]["urgency_score"] == Decimal("0.3")
    assert encoded["own_claim"]["acres"] == Decimal("2.5")
    assert encoded["own_claim"]["concedes"] is False
    assert encoded["plain"] == Decimal("1.5")


def test_from_decimal_recurses_into_nested_dicts_and_round_trips() -> None:
    nested = {"urgency_score": 0.3, "acres": 2.5, "days_past_maturity": 6}
    round_tripped = _from_decimal(_to_decimal({"own_claim": nested}))
    assert round_tripped == {"own_claim": nested}
    assert isinstance(round_tripped["own_claim"]["days_past_maturity"], int)
    assert isinstance(round_tripped["own_claim"]["urgency_score"], float)


def test_strip_keys_removes_pk_sk_and_extras() -> None:
    item = {"PK": "a", "SK": "b", "GSI1PK": "c", "GSI1SK": "d", "name": "keep"}
    assert _strip_keys(item) == {"GSI1PK": "c", "GSI1SK": "d", "name": "keep"}
    assert _strip_keys(item, extra=("GSI1PK", "GSI1SK")) == {"name": "keep"}


def test_legacy_farmer_item_without_language_attribute_defaults_to_tamil() -> None:
    """No real table needed -- this exercises the exact
    Farmer(**_decode(_strip_keys(item, extra=(...)))) construction
    get_farmer() uses, against a hand-built item shaped like a
    pre-ADR-008 DynamoDB record (no "language" attribute at all)."""
    from harvest_convoy.models import Farmer

    item = {
        "PK": "FARMER#legacy1", "SK": "METADATA",
        "GSI1PK": "CLUSTER#c1", "GSI1SK": "FARMER#legacy1",
        "farmer_id": "legacy1", "name": "Old Record", "cluster_id": "c1",
        "telegram_chat_id": 555,
        # no "language" attribute -- simulates a pre-existing DynamoDB item
    }

    farmer = Farmer(**_decode(_strip_keys(item, extra=("GSI1PK", "GSI1SK"))))

    assert farmer.language == "ta"


def test_legacy_cluster_item_without_maturity_gdd_override_defaults_to_none() -> None:
    from harvest_convoy.models import Cluster

    item = {
        "PK": "CLUSTER#legacyc1", "SK": "METADATA",
        "cluster_id": "legacyc1", "name": "Old Cluster",
        "machine_capacity_acres_per_day": Decimal("3.5"),
        "machine_start_lat": Decimal("9.865"), "machine_start_lon": Decimal("77.454"),
        "operator_chat_id": None,
        # no "maturity_gdd_override" attribute
    }

    cluster = Cluster(**_decode(_strip_keys(item)))

    assert cluster.maturity_gdd_override is None


def test_legacy_cluster_item_without_operator_language_defaults_to_tamil() -> None:
    from harvest_convoy.models import Cluster

    item = {
        "PK": "CLUSTER#legacyc2", "SK": "METADATA",
        "cluster_id": "legacyc2", "name": "Old Cluster 2",
        "machine_capacity_acres_per_day": Decimal("3.5"),
        "machine_start_lat": Decimal("9.865"), "machine_start_lon": Decimal("77.454"),
        "operator_chat_id": None, "maturity_gdd_override": None,
        # no "operator_language" attribute
    }

    cluster = Cluster(**_decode(_strip_keys(item)))

    assert cluster.operator_language == "ta"


# --- ADR-015: float/int type fidelity ---
#
# _from_decimal's int-when-integral heuristic (needed for
# Farmer.telegram_chat_id, ADR-006 Decision 9) is exactly wrong for a
# float field that happens to hold a whole number -- DynamoDB's Number
# type can't distinguish Decimal("3") meant as an int from Decimal("3")
# meant as a float, so no heuristic over the value can resolve it. The
# fix (dynamo.py's _item_to_cluster/_item_to_plot/_item_to_decision_record
# plus their *_FLOAT_FIELDS constants) casts by type information instead.
# Every assertion below uses `type(x) is float` / `is int`, not `==` --
# int(0) == float(0.0) is True in Python, which is exactly why the
# original bug passed a naive round-trip check unnoticed.


def test_telegram_chat_id_style_int_field_stays_int_not_regressed_by_float_fix() -> None:
    """ADR-006 Decision 9's fix must survive ADR-015's float-fidelity
    work untouched -- Farmer has no float fields and goes through no new
    _item_to_* helper, but OperatorEnrollmentCode/OperatorAuditEvent's
    chat_id-shaped ints sit right next to fields that now DO get cast,
    so this proves the int path wasn't accidentally widened too."""
    from harvest_convoy.models import Farmer
    from harvest_convoy.storage.interface import OperatorAuditEvent, OperatorEnrollmentCode

    farmer_item = {
        "PK": "FARMER#f1", "SK": "METADATA", "GSI1PK": "CLUSTER#c1", "GSI1SK": "FARMER#f1",
        "farmer_id": "f1", "name": "N", "cluster_id": "c1",
        "telegram_chat_id": Decimal("1276258406"), "language": "ta",
    }
    farmer = Farmer(**_decode(_strip_keys(farmer_item, extra=("GSI1PK", "GSI1SK"))))
    assert farmer.telegram_chat_id == 1276258406
    assert type(farmer.telegram_chat_id) is int

    oec_item = {
        "PK": "OPCODE#X", "SK": "OPCODE", "code": "X", "cluster_id": "c1",
        "created_at": "t", "expires_at": "t2",
        "used_at": "t3", "used_by_chat_id": Decimal("987654321"),
    }
    oec = OperatorEnrollmentCode(**_decode(_strip_keys(oec_item)))
    assert type(oec.used_by_chat_id) is int

    oae_item = {
        "PK": "CLUSTER#c1", "SK": "OPAUDIT#t#enrolled", "cluster_id": "c1",
        "event_type": "enrolled", "occurred_at": "t", "code_used": "X",
        "new_operator_chat_id": Decimal("111222333"), "previous_operator_chat_id": None,
    }
    oae = OperatorAuditEvent(**_decode(_strip_keys(oae_item)))
    assert type(oae.new_operator_chat_id) is int
    assert oae.previous_operator_chat_id is None


def test_decision_record_float_fields_stay_float() -> None:
    item = {
        "PK": "PLOT#p1", "SK": "DECISION#s1#2099-01-01",
        "GSI1PK": "CLUSTER#c1", "GSI1SK": "DECISION#s1#2099-01-01#p1",
        "plot_id": "p1", "farmer_id": "f1", "cluster_id": "c1", "season_id": "s1",
        "decision_date": "2099-01-01",
        # every whole-number float, on purpose -- 0.0 is DecisionRecord's
        # own rain_urgency_boost default and the case that broke before.
        "accumulated_gdd": Decimal("2200"),
        "maturity_gdd_used": Decimal("2100"),
        "threshold_source": "fallback",
        "outcome": "fits",
        "days_past_maturity": None,
        "urgency": Decimal("0"),
        "route_position": Decimal("3"),
        "rain_threshold_mm": Decimal("5"),
        "forecast_horizon_days": Decimal("16"),
        "usable_harvest_days": Decimal("14"),
        "machine_capacity_acres_per_day": Decimal("3.5"),  # non-whole control case
        "capacity_budget_acres": Decimal("42"),
        "opponent_plot_id": "p2",
        "own_claim": {
            "plot_id": "p1", "urgency_score": Decimal("1"), "days_past_maturity": Decimal("3"),
            "rain_vulnerability": "high", "acres": Decimal("3"), "bumped_last_season": False,
            "weighted_bump_days": Decimal("0"), "argument": "a", "concedes": False,
            "degraded": False,
        },
        "opponent_claim": None,
        "rounds_run": None, "resolution": None, "fairness_decisive": None,
        "resolved_at": None, "trigger_reason": "scheduled",
        "rain_event_classification": "none", "rain_urgency_boost": Decimal("0"),
    }

    record = _item_to_decision_record(item)

    for field_name in dynamo.DECISION_RECORD_FLOAT_FIELDS:
        value = getattr(record, field_name)
        assert type(value) is float, f"{field_name}: expected float, got {type(value).__name__}"
    assert record.rain_urgency_boost == 0.0
    assert record.route_position == 3
    assert type(record.route_position) is int
    for key in dynamo.DECISION_CLAIM_FLOAT_FIELDS:
        assert type(record.own_claim[key]) is float, (
            f"own_claim[{key!r}]: expected float, got {type(record.own_claim[key]).__name__}"
        )
    assert record.opponent_claim is None


def test_plot_and_cluster_float_fields_stay_float() -> None:
    plot_item = {
        "PK": "PLOT#p1", "SK": "METADATA", "GSI1PK": "CLUSTER#c1", "GSI1SK": "PLOT#p1",
        "plot_id": "p1", "farmer_id": "f1", "cluster_id": "c1",
        "lat": Decimal("10"), "lon": Decimal("77"),  # whole-number, the risky case
        "crop": "paddy", "variety": "ADT45", "transplant_date": "2099-01-01",
        "area_acres": Decimal("3"), "area_unit": "acre", "village": None,
        "retired_reason": None,
    }
    plot = _item_to_plot(plot_item)
    for field_name in dynamo.PLOT_FLOAT_FIELDS:
        value = getattr(plot, field_name)
        assert type(value) is float, f"{field_name}: expected float, got {type(value).__name__}"
    assert plot.area_acres == 3.0

    cluster_item = {
        "PK": "CLUSTER#c1", "SK": "METADATA", "cluster_id": "c1", "name": "N",
        "machine_capacity_acres_per_day": Decimal("4"),  # whole-number
        "machine_start_lat": Decimal("10"), "machine_start_lon": Decimal("77"),
        "operator_chat_id": None, "maturity_gdd_override": Decimal("2200"),
        "operator_language": "ta",
    }
    cluster = _item_to_cluster(cluster_item)
    for field_name in dynamo.CLUSTER_FLOAT_FIELDS:
        value = getattr(cluster, field_name)
        assert type(value) is float, f"{field_name}: expected float, got {type(value).__name__}"

    # None must stay None, not become 0.0 -- the "not yet calibrated" case
    cluster_item2 = dict(cluster_item, maturity_gdd_override=None)
    cluster2 = _item_to_cluster(cluster_item2)
    assert cluster2.maturity_gdd_override is None


def _float_field_names(cls: type) -> set[str]:
    """Every field on cls whose resolved annotation is float or
    float | None -- the same shape dynamo.py's *_FLOAT_FIELDS constants
    must cover exactly, for a stdlib dataclass. Deliberately doesn't try
    to see inside dict-typed fields (own_claim/opponent_claim) -- that
    gap is real and is why DECISION_CLAIM_FLOAT_FIELDS is checked
    separately against AdvocateClaim.model_fields, not by this function."""
    hints = typing.get_type_hints(cls)
    names = set()
    for field_name, hint in hints.items():
        if hint is float or float in typing.get_args(hint):
            names.add(field_name)
    return names


def test_every_float_field_across_all_dataclasses_is_covered_by_a_dynamo_cast_list() -> None:
    """Completeness guard for ADR-015's per-type deserializers: a future
    float field added to any dataclass here and forgotten in dynamo.py's
    corresponding *_FLOAT_FIELDS constant fails THIS test, rather than
    silently reading back as the wrong Python type from a live table."""
    from harvest_convoy.models import Cluster, Farmer, Plot
    from harvest_convoy.storage.interface import (
        AdvanceNoticeRecord,
        BreakdownDisplacement,
        DecisionRecord,
        HarvestConfirmation,
        LedgerEntry,
        MachineStatus,
        OperatorAuditEvent,
        OperatorEnrollmentCode,
        RouteOverride,
        SeasonRolloverPrompt,
        StorageResult,
    )

    covered = {
        Cluster: dynamo.CLUSTER_FLOAT_FIELDS,
        Plot: dynamo.PLOT_FLOAT_FIELDS,
        DecisionRecord: dynamo.DECISION_RECORD_FLOAT_FIELDS,
    }
    no_float_fields_expected = [
        StorageResult, LedgerEntry, Farmer, HarvestConfirmation, SeasonRolloverPrompt,
        BreakdownDisplacement, MachineStatus, AdvanceNoticeRecord, OperatorEnrollmentCode,
        OperatorAuditEvent, RouteOverride,
    ]

    for cls, declared in covered.items():
        actual = _float_field_names(cls)
        assert actual == set(declared), (
            f"{cls.__name__}: dynamo.py's float-field list {set(declared)} doesn't "
            f"match the dataclass's actual float fields {actual} -- "
            f"a field was added or removed without updating dynamo.py"
        )

    for cls in no_float_fields_expected:
        actual = _float_field_names(cls)
        assert actual == set(), (
            f"{cls.__name__} now has float field(s) {actual} with no *_FLOAT_FIELDS "
            f"constant in dynamo.py and no cast in its get_* method -- "
            f"add one before this ships, see ADR-015"
        )


def test_advocate_claim_nested_float_fields_are_covered() -> None:
    """own_claim/opponent_claim are plain dict | None in DecisionRecord's
    own type annotation, so _float_field_names can't see their nested
    shape -- checked here instead, against AdvocateClaim's own Pydantic
    field types directly."""
    from harvest_convoy.agents.contracts import AdvocateClaim

    pydantic_float_fields = {
        name for name, f in AdvocateClaim.model_fields.items() if f.annotation is float
    }
    assert pydantic_float_fields == set(dynamo.DECISION_CLAIM_FLOAT_FIELDS)


@pytest.mark.slow
def test_unreachable_endpoint_degrades_reads_to_none_and_writes_to_failure() -> None:
    """One representative read (get_cluster) and one write (put_cluster)
    against a genuinely unreachable endpoint -- both go through the same
    try/except-and-degrade pattern every other method in this class uses,
    so this is representative of all of them, not just these two. Each
    call incurs a real ~8s TCP connect timeout (see _CLIENT_CONFIG); this
    test intentionally isn't multiplied across every method to keep the
    suite's runtime proportionate to what it's actually proving.
    """
    from harvest_convoy.models import Cluster

    storage = DynamoStorage(
        table_name="does-not-matter",
        region_name="ap-south-1",
        endpoint_url="http://localhost:59999",  # nothing listening here
    )

    assert storage.get_cluster("x") is None

    result = storage.put_cluster(
        Cluster(
            cluster_id="x", name="n", machine_capacity_acres_per_day=3.5,
            machine_start_lat=1.0, machine_start_lon=1.0,
        )
    )
    assert result.success is False
    assert result.error is not None
