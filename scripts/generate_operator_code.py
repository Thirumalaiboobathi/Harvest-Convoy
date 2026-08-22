"""Generates a one-time operator enrollment code for a cluster -- the
provisioner-side half of ADR-012 Part 2. Prints the code to stdout;
handing it to the real operator (phone call, WhatsApp, paper slip) is
out-of-band, outside this system, the same way "who runs this script" is
already out-of-band today.

The operator sends the printed code to the bot as "/operator <code>" to
self-enroll -- see telegram/operator_enrollment.py.

Does NOT invalidate an earlier still-valid code for the same cluster
(disclosed limitation, ADR-012 Part 2 -- acceptable at this project's
scale; two valid codes could coexist briefly if this is run twice before
the first code is used).

Usage:
    uv run python -m scripts.generate_operator_code --cluster-id kamatchipuram
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from harvest_convoy.storage import get_storage
from harvest_convoy.storage.interface import OperatorEnrollmentCode
from harvest_convoy.telegram.operator_enrollment import (
    OPERATOR_ENROLLMENT_CODE_EXPIRY_DAYS,
    generate_code,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cluster-id", required=True)
    args = parser.parse_args(argv)

    storage = get_storage()
    cluster = storage.get_cluster(args.cluster_id)
    if cluster is None:
        print(f"[FAIL] no cluster found with cluster_id={args.cluster_id!r}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    code = generate_code()
    record = OperatorEnrollmentCode(
        code=code,
        cluster_id=args.cluster_id,
        created_at=now.isoformat(),
        expires_at=(now + timedelta(days=OPERATOR_ENROLLMENT_CODE_EXPIRY_DAYS)).isoformat(),
    )
    result = storage.put_operator_enrollment_code(record)
    if not result.success:
        print(f"[FAIL] could not write enrollment code: {result.error}", file=sys.stderr)
        return 1

    print(f"[OK] enrollment code for {cluster.name} ({args.cluster_id}):")
    print(f"    {code}")
    print(
        f"Expires {record.expires_at} ({OPERATOR_ENROLLMENT_CODE_EXPIRY_DAYS} days from now). "
        f"Single-use. Hand this to the real operator out-of-band; they send "
        f'"/operator {code}" to the bot to self-enroll.'
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
