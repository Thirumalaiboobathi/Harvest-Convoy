# Demo runbook — 5-minute video

A step-by-step script, in order, with exact commands. Everything under
"Beat" is something to say or show; everything in a code block is
something to type or a URL to open. Total target: under 5:00.

Run through Setup once, before you hit record — don't discover a missing
`.env` value on camera.

## Setup (before recording, not on camera)

```bash
cd "d:/AWS user grp mad/Devpost Hackthon"
uv sync --all-groups
cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN from @BotFather if not already set
```

Have these open in browser tabs, ready to switch to:
1. `docs/architecture.png` (or `ARCHITECTURE.md` rendered on GitHub)
2. Your phone, Telegram app open, chat with your bot
3. AWS Console → CloudWatch → already navigated to the trace (steps below,
   do this once now so you know the click path cold)
4. A terminal in the repo root

Know your Telegram chat ID (message your bot once, or check
`scripts/trigger_scenario.py`'s docstring for how it's used) — call it
`<CHAT_ID>` below.

---

## Beat 1 — The problem (0:00–0:30)

Say it in one breath, don't read a slide: *"A village cluster in Tamil
Nadu shares one combine harvester across eight paddy plots. Someone has
to decide, every day, whose plot gets harvested first — normally that's
a phone-tag argument. Harvest Convoy automates the 95% of days where the
answer is obvious, and only asks a human when two plots genuinely tie."*

## Beat 2 — Architecture, the one rule (0:30–1:15)

Show `docs/architecture.png` full-screen. Point at the green box, then
the orange box:

*"Everything green is plain Python — crop maturity, rain capacity, route
order, fairness — deterministic, unit-tested, no LLM involved. The LLM,
Strands Agents on Bedrock Nova Pro, only touches the orange box: it
argues one plot's case against another, and only when the math has
already produced a genuine tie. It never computes a number itself."*

That sentence is the whole Technical Implementation argument — don't
rush past it.

## Beat 3 — Zero-AWS quickstart (1:15–2:00)

Terminal, run:

```bash
uv run python -m scripts.seed_cluster --write
uv run python -m scripts.trigger_scenario <CHAT_ID> --offline
```

While it runs, narrate: *"No AWS account needed for this — offline mode
uses a scripted claim provider instead of live Bedrock, so a judge can
clone this and run it in thirty seconds."* Let the four message types
scroll by in the terminal output; point out the `[scripted offline mode]`
label so it's clear this take isn't claiming to be live judgment.

## Beat 4 — Live negotiation, real Bedrock (2:00–3:15)

This is the centerpiece. Terminal:

```bash
uv run python -m scripts.trigger_scenario <CHAT_ID>
```

(no `--offline` — real Bedrock calls, ~10–15 seconds). While it runs,
switch to your phone: Telegram messages should start arriving — harvest
scheduled, not-ready, and (per the engineered p03/p04 deadlock scenario)
an escalation with both farmers' real arguments side by side.

**If it resolves cleanly instead of escalating**: that's a real, honest
result — one side's case was genuinely weaker and the model conceded
live. Say so on camera (*"this take resolved instead of escalating — that's
the model being honest, not a failure"*) and either keep it, or re-run
once more (~$0.02/run) for a take that escalates. Do not re-run more than
twice chasing a specific outcome — if it keeps resolving, that's the
honest result and the video should say that plainly.

## Beat 5 — The deployed trace (3:15–4:15)

Switch to AWS Console, already logged in, `ap-south-1` region selected.

```
CloudWatch → Logs → Log groups
→ /aws/bedrock-agentcore/runtimes/harvest_convoy_watcher-7DW91DHIBA-DEFAULT
→ Log streams → "spans"
```

Filter the stream to `2026-08-17` around `10:32` UTC (`16:02` IST) — or
use **Logs Insights** instead for a cleaner on-screen query, pointed at
the same log group:

```
fields @timestamp, name, traceId, spanId, parentSpanId, attributes.round, attributes.outcome
| filter traceId = "360de14942b4643e8d9f23e3ed2adca9"
| sort @timestamp asc
```

This is a real, already-captured trace from a genuine live negotiation
(escalated, p02 vs p03, 3 rounds) — you'll see `coordinator.negotiate`
(with `outcome: escalated`) as the parent of three `negotiation.round`
spans (`round: 1`, `2`, `3`), each holding its own `advocate.get_claim` →
Strands `invoke_agent`/`execute_event_loop_cycle` subtree. Say: *"This
is the actual negotiation, traced end to end, running on AgentCore
Runtime — not a mock."* Retention on this log group is indefinite, so
it'll still be there whenever you film.

If you'd rather show a **fresh** trace instead of this captured one, see
"Regenerating a live trace" below — it requires temporarily throttling
the cluster's machine capacity in DynamoDB, which needs care to revert.

## Beat 6 — Cost and honesty (4:15–4:45)

One screen, either the README's Cost section or just say it: *"A full
scheduling run costs $0.008 with prompt caching — measured, not
estimated. Standing infrastructure is about two dollars a month."* Then,
briefly: *"This is a simulated cluster on real Theni coordinates and real
weather data — no live farmers are onboarded yet. The maturity threshold
is a derived estimate, not an agronomist-sourced number, and the README
shows exactly how it's derived."* Ten seconds, not a caveat-dump — it's
there to show the project is honest about its own limits, not to
undersell it.

## Beat 7 — Close (4:45–5:00)

*"Harvest Convoy — deterministic scheduling, LLM judgment only where it's
actually needed. Repo link and license in the description."* Cut.

---

## Regenerating a live trace (optional, if you want a fresh one on camera)

Only do this if you specifically want to show the trace-capture *process*
live rather than an already-proven one. It temporarily mutates live
DynamoDB data and must be reverted — don't do this without watching the
revert step complete.

```bash
# 1. Throttle capacity so there's a genuine capacity-constrained tie today
aws dynamodb update-item --table-name harvest_convoy \
  --key '{"PK":{"S":"CLUSTER#kamatchipuram"},"SK":{"S":"METADATA"}}' \
  --update-expression "SET machine_capacity_acres_per_day = :c" \
  --expression-attribute-values '{":c":{"N":"0.2"}}' --region ap-south-1

# 2. Clear today's watcher marker so it actually re-checks
aws dynamodb delete-item --table-name harvest_convoy \
  --key '{"PK":{"S":"CLUSTER#kamatchipuram"},"SK":{"S":"WATCHER#RUN"}}' --region ap-south-1

# 3. Invoke the deployed runtime through the real Scheduler path (Lambda shim)
aws lambda invoke --function-name harvest-convoy-watcher-invoker \
  --payload '{"cluster_id":"kamatchipuram","season_id":"2026-kuruvai","force":true}' \
  --cli-binary-format raw-in-base64-out --region ap-south-1 /tmp/out.json && cat /tmp/out.json

# 4. REVERT — do this immediately, before anything else
aws dynamodb update-item --table-name harvest_convoy \
  --key '{"PK":{"S":"CLUSTER#kamatchipuram"},"SK":{"S":"METADATA"}}' \
  --update-expression "SET machine_capacity_acres_per_day = :c" \
  --expression-attribute-values '{":c":{"N":"3.5"}}' --region ap-south-1
```

Step 3's response body includes `"escalations": <N>`. If `N >= 1`, a
fresh `coordinator.negotiate` span exists in the same log
group/stream — find it by timestamp (the invoke just ran, so "now" in
CloudWatch's time range picker). If `N == 0`, the real weather forecast
that day didn't produce a tie even under throttled capacity; re-run step
3 once, and if it still doesn't escalate, fall back to Beat 5's captured
trace instead of spending more time chasing it live on camera.
