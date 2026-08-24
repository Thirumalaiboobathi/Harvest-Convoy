"""Manual verification harness: drives the seeded Kamatchipuram cluster
through one full weather-triggered run and sends all four Telegram message
types to a single chat ID (yours) so they can be checked on a phone. Also
the demo video driver -- see the docstring notes on determinism below.

Usage:
    uv run python -m scripts.trigger_scenario <your_chat_id> [--offline]

Determinism, read before assuming this is fully reproducible:
- The plot classification (too-green / fits / contested) comes from
  Phase 2's solver against a FIXED synthetic forecast and a FIXED
  synthetic weather series (same construction as the Phase 2/3 gate
  tests) -- this part is 100% deterministic and always reproduces the
  proven split: p01/p02 fit, p03/p04 contested, p05-p08 too green.
- The escalation now runs the REAL negotiate_pair() loop -- it is not
  hand-constructed with a forced concedes=False the way an earlier
  version of this script did. That approach let a live run resolve
  cleanly instead of escalating (a real, correct model decision -- p04
  conceding when its case was genuinely weak), which is exactly right
  for the product but unusable for a demo take that must show the
  escalation reliably. Fixed by changing the SCENARIO, not the code path:
  the escalation pair (p03/p04's real farmer identities) gets transplant
  dates engineered so both are well past maturity with only a 1-day
  (0.05) urgency gap -- comfortably under CLEAR_MARGIN, and both sides
  have a genuinely strong, comparable case, so a real advocate conceding
  is implausible rather than impossible. See build_deadlock_facts().
- Default mode makes real Bedrock calls and reports whatever
  negotiate_pair() actually decides -- if a take surprises you and
  resolves anyway, the script says so plainly and exits rather than
  faking an escalation.
- --offline is the documented, explicit fixed-response fallback for when
  a live take isn't an option (bad connection, out of patience at 1am):
  no Bedrock calls, a canned claim provider that always returns
  concedes=False, and its argument text says "[scripted offline mode]"
  on screen -- reproducible every time, honestly labeled as scripted
  rather than pretending to be live judgment.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

from harvest_convoy.agents.contracts import AdvocateClaim, EscalationPayload, PlotFacts
from harvest_convoy.agents.coordinator import build_plot_facts, negotiate_pair
from harvest_convoy.agronomy import crop_params
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.models import Farmer
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.scheduling.solver import PlotOutcome, assess_plot, solve
from harvest_convoy.storage import get_storage
from harvest_convoy.telegram import notify, webhook
from harvest_convoy.telegram.client import TelegramClient
from scripts import seed_cluster

REFERENCE_TODAY = date(2026, 8, 16)
CURRENT_SEASON_ID = "2026-kuruvai"
# Fixed synthetic forecast: 1 usable day then rain -- forces contestation
# among the ready plots. Same fixture the Phase 2/3 gate tests use.
FIXED_FORECAST = [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)]
RAIN_THRESHOLD_MM = 5.0
CALLBACK_POLL_TIMEOUT_SECONDS = 20
CALLBACK_WAIT_MAX_SECONDS = 300

# Escalation demo pair: p03/p04's real farmer identities, transplant dates
# adjusted so both are well past maturity (90 days under the reference
# synthetic rate -- 19.2133 * 90 == 1729.197 == MATURITY_GDD_ESTIMATED
# exactly) with only a 1-day, 0.05-urgency gap between them. Computed, not
# guessed -- see the module docstring for why this replaced forcing
# concedes=False on the real seeded p03/p04 dates.
DEADLOCK_TRANSPLANT_A = date(2026, 5, 3)  # -> 15 days past maturity, urgency 0.75
DEADLOCK_TRANSPLANT_B = date(2026, 5, 2)  # -> 16 days past maturity, urgency 0.80


def _synthetic_days(transplant_date: date, today: date) -> list[DailyTemperature]:
    rate = crop_params.KURUVAI_MEAN_GDD_PER_DAY_REFERENCE_ESTIMATED
    mean_temp = crop_params.T_BASE_C + rate
    n = (today - transplant_date).days + 1
    return [
        DailyTemperature(
            date=(transplant_date + timedelta(days=i)).isoformat(),
            t_max_c=mean_temp,
            t_min_c=mean_temp,
        )
        for i in range(n)
    ]


def _offline_claim(facts: PlotFacts, round_num: int, opponent_argument: str | None) -> AdvocateClaim:
    """Deterministic, no-AWS fallback claim provider -- see module
    docstring. Always concedes=False; the argument text says plainly that
    it's scripted rather than pretending to be live judgment.
    """
    argument = (
        f"[scripted offline mode -- not live LLM judgment] "
        f"{facts.days_past_maturity} days past maturity, {facts.acres} acres."
    )
    return AdvocateClaim.from_facts(facts, argument=argument, concedes=False)


def build_deadlock_facts(plots_by_id, storage) -> tuple[PlotFacts, PlotFacts]:
    plot_a = replace(plots_by_id["p03"], transplant_date=DEADLOCK_TRANSPLANT_A)
    plot_b = replace(plots_by_id["p04"], transplant_date=DEADLOCK_TRANSPLANT_B)

    decision_a = assess_plot(
        plot_a, _synthetic_days(plot_a.transplant_date, REFERENCE_TODAY), REFERENCE_TODAY
    )
    decision_b = assess_plot(
        plot_b, _synthetic_days(plot_b.transplant_date, REFERENCE_TODAY), REFERENCE_TODAY
    )

    facts_a = build_plot_facts(plot_a, decision_a, storage)
    facts_b = build_plot_facts(plot_b, decision_b, storage)
    return facts_a, facts_b


def build_escalation(plots_by_id, storage, *, offline: bool) -> EscalationPayload | None:
    """Runs the REAL negotiate_pair() loop against the engineered deadlock
    facts -- not a hand-constructed EscalationPayload with a forced
    outcome. Returns None if this take resolved naturally instead of
    escalating (a real, unforced model decision, reported plainly to the
    caller rather than papered over).
    """
    facts_a, facts_b = build_deadlock_facts(plots_by_id, storage)
    print(
        f"    deadlock pair urgency: {facts_a.plot_id}={facts_a.urgency:.2f}, "
        f"{facts_b.plot_id}={facts_b.urgency:.2f} (gap={abs(facts_a.urgency - facts_b.urgency):.2f})"
    )

    if offline:
        get_claim = _offline_claim
    else:
        from harvest_convoy.agents.advocate import get_advocate_claim

        def get_claim(facts, round_num, opponent_argument):
            print(f"    Calling live Bedrock: {facts.plot_id} round {round_num}...")
            return get_advocate_claim(
                facts, storage=storage, round_num=round_num, opponent_argument=opponent_argument
            )

    result = negotiate_pair(facts_a, facts_b, get_claim)

    if not result.escalated:
        print(
            f"\n    NOTE: this take resolved naturally instead of escalating "
            f"(winner={result.winner_plot_id}) -- a real, unforced model "
            f"decision, not a bug. Re-run for another live attempt, or add "
            f"--offline for a guaranteed deterministic escalation."
        )
        return None

    return EscalationPayload(
        cluster_id=seed_cluster.CLUSTER.cluster_id,
        plot_a_id=facts_a.plot_id,
        plot_b_id=facts_b.plot_id,
        claim_a=result.claim_a,
        claim_b=result.claim_b,
        rounds_run=result.rounds_used,
        reason="No clear resolution after max negotiation rounds.",
    )


def wait_for_resolution(client: TelegramClient, storage, lookup) -> bool:
    """Long-poll getUpdates until the escalation callback arrives, or
    CALLBACK_WAIT_MAX_SECONDS elapses. Returns True if resolved."""
    print(
        f"\nWaiting up to {CALLBACK_WAIT_MAX_SECONDS}s for you to tap a button "
        f"on the escalation message... (Ctrl+C to stop waiting)"
    )
    deadline = time.time() + CALLBACK_WAIT_MAX_SECONDS
    offset = None
    while time.time() < deadline:
        params = {"timeout": CALLBACK_POLL_TIMEOUT_SECONDS}
        if offset is not None:
            params["offset"] = offset
        try:
            response = httpx.get(
                f"https://api.telegram.org/bot{client.token}/getUpdates",
                params=params,
                timeout=CALLBACK_POLL_TIMEOUT_SECONDS + 5.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            print(f"getUpdates failed: {exc}; retrying")
            time.sleep(2)
            continue

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            if "callback_query" in update:
                webhook.handle_update(
                    client, update, storage, CURRENT_SEASON_ID,
                    lookup_farmer_for_plot=lookup,
                )
                return True
            # Not a callback -- still consume it so it doesn't get replayed.
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("chat_id", type=int, help="Your Telegram chat ID")
    parser.add_argument(
        "--offline", action="store_true",
        help="Skip live Bedrock calls; use canned deterministic argument text.",
    )
    args = parser.parse_args()

    load_dotenv()
    client = TelegramClient()
    if not client.token:
        print("TELEGRAM_BOT_TOKEN not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)

    chat_id = args.chat_id
    storage = get_storage()

    # Everyone's chat_id is overridden to yours -- this is a solo
    # verification run, not a multi-farmer broadcast.
    farmers_by_id = {
        f.farmer_id: replace(f, telegram_chat_id=chat_id) for f in seed_cluster.FARMERS
    }
    plots_by_id = {p.plot_id: p for p in seed_cluster.PLOTS}
    cluster = replace(seed_cluster.CLUSTER, operator_chat_id=chat_id)

    print("Computing plot classification from the fixed synthetic scenario...")
    plot_days = {
        p.plot_id: _synthetic_days(p.transplant_date, REFERENCE_TODAY)
        for p in seed_cluster.PLOTS
    }
    decisions = solve(
        seed_cluster.PLOTS, plot_days, cluster, FIXED_FORECAST,
        rain_threshold_mm=RAIN_THRESHOLD_MM, today=REFERENCE_TODAY,
    )
    decisions_by_id = {d.plot_id: d for d in decisions}
    for d in decisions:
        print(f"  {d.plot_id}: {d.outcome.value}")

    def farmer_of(plot_id: str) -> Farmer:
        return farmers_by_id[plots_by_id[plot_id].farmer_id]

    print("\n1/4 Sending 'harvest scheduled' (p01)...")
    result = notify.send_harvest_scheduled(
        client, farmer_of("p01"), plots_by_id["p01"],
        route_position=decisions_by_id["p01"].route_position,
    )
    print(f"    success={result.success} error={result.error}")

    print("\n2/4 Sending 'no action needed' -- too-green (p07)...")
    result = notify.send_not_ready(
        client, farmer_of("p07"), plots_by_id["p07"],
        season_id=CURRENT_SEASON_ID, decision_date=REFERENCE_TODAY.isoformat(),
    )
    print(f"    success={result.success} error={result.error}")

    print("\n3/4 Sending operator route summary...")
    fits = sorted(
        (d for d in decisions if d.outcome == PlotOutcome.FITS),
        key=lambda d: d.route_position,
    )
    route = [(farmer_of(d.plot_id), plots_by_id[d.plot_id]) for d in fits]
    result = notify.send_operator_route_summary(
        client, cluster.operator_chat_id, cluster, route
    )
    route_labels = [notify.short_label(f, p) for f, p in route]
    print(f"    route={route_labels} success={result.success} error={result.error}")

    print("\n4/4 Building and sending the escalation (p03 vs p04 deadlock pair)...")
    escalation = build_escalation(plots_by_id, storage, offline=args.offline)
    if escalation is None:
        print("    Skipping the escalation send -- see the note above.")
        sys.exit(1)
    webhook.register_escalation(escalation)
    result = notify.send_escalation(
        client, cluster.operator_chat_id,
        escalation.cluster_id,
        escalation.plot_a_id, farmer_of("p03"), plots_by_id["p03"], escalation.claim_a,
        escalation.plot_b_id, farmer_of("p04"), plots_by_id["p04"], escalation.claim_b,
        operator_language=cluster.operator_language,
    )
    print(f"    success={result.success} error={result.error}")

    def lookup(plot_id: str):
        if plot_id not in plots_by_id:
            return None
        return farmer_of(plot_id), plots_by_id[plot_id]

    resolved = wait_for_resolution(client, storage, lookup)
    if resolved:
        print("\nEscalation resolved -- check your phone for the two resolution messages.")
    else:
        print(
            "\nNo tap received within the wait window. The escalation message "
            "is still live on your phone; run this script again or use "
            "scripts/run_polling.py if you want to tap it later (note: "
            "run_polling.py's default lookup won't resolve it -- see ADR-004)."
        )


if __name__ == "__main__":
    main()
