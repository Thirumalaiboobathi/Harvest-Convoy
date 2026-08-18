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

from decimal import Decimal

import pytest

from harvest_convoy.storage.dynamo import (
    DynamoStorage,
    _decode,
    _encode,
    _from_decimal,
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
