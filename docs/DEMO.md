# Demo runbook — 5-minute video

A step-by-step script, in order, with exact commands. Everything under
"Beat" is something to say or show; everything in a code block is
something to type or a URL to open. Total target: under 5:00.

Run through Setup once, before you hit record — don't discover a missing
`.env` value on camera.

Updated after the ADR-008 round (Tamil-language interface, per-cluster
GDD, second cluster, redeploy) — see "What changed since the last
version of this runbook" at the bottom for exactly what's different and
why, including one open item (fresh trace visibility) this version does
not depend on.

## Setup (before recording, not on camera)

```bash
cd "d:/AWS user grp mad/Devpost Hackthon"
uv sync --all-groups
cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN from @BotFather if not already set
```

Have these open in browser tabs, ready to switch to:
1. `docs/architecture.png` (or `ARCHITECTURE.md` rendered on GitHub)
2. Your phone, Telegram app open, chat with your bot
3. AWS Console → CloudWatch, already logged in, `ap-south-1` selected
4. A terminal in the repo root

Know your Telegram chat ID (message your bot once, or check
`scripts/trigger_scenario.py`'s docstring for how it's used) — call it
`<CHAT_ID>` below. Two separate things use it:
- **Local demo** (Beats 3–4): `trigger_scenario.py <CHAT_ID>` overrides
  every farmer's chat ID in memory for that one run — nothing to
  pre-configure.
- **Deployed runtime** (Beat 5): the live Kamatchipuram cluster's
  `operator_chat_id` and four of its eight farmers' `telegram_chat_id`
  are already wired to a real chat in DynamoDB (done once, ahead of
  filming — see "What changed" at the bottom if you need to redo this
  for a different chat ID). The other four farmers are deliberately left
  unset, to prove the missing-chat-ID path degrades cleanly instead of
  crashing.

## Beat 1 — The problem (0:00–0:30)

Say it in one breath, don't read a slide: *"A village cluster in Tamil
Nadu shares one combine harvester across its paddy plots. Someone has
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
rush past it. If there's time, one more line: *"This works anywhere in
Tamil Nadu, not just one village — the maturity threshold is derived per
cluster from that cluster's own weather history, and every farmer-facing
message is Tamil by default."* (see the legend's "Clusters" note on the
diagram itself if you want to point at it instead of saying it).

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
label so it's clear this take isn't claiming to be live judgment. These
messages are Tamil by default (the seeded farmers don't set a language
override) — a natural, honest place to mention that without a separate
beat for it.

## Beat 4 — Live negotiation, real Bedrock (2:00–3:15)

This is the centerpiece — and, as of this version of the runbook, the
**only** reliable way to show an actual escalation. The deployed
8-plot Kamatchipuram cluster doesn't naturally produce a tie under real
capacity and real weather (verified live — everything either fits the
route or is still too green most days); `trigger_scenario.py` engineers
a genuine two-plot capacity deadlock on purpose, specifically so this
beat doesn't depend on today's weather cooperating. Terminal:

```bash
uv run python -m scripts.trigger_scenario <CHAT_ID>
```

(no `--offline` — real Bedrock calls, ~10–15 seconds). While it runs,
switch to your phone: Telegram messages should start arriving — harvest
scheduled, not-ready, and (per the engineered p03/p04 deadlock scenario)
an escalation with both farmers' real arguments side by side, in Tamil
(the default; both engineered farmers use it here).

**If it resolves cleanly instead of escalating**: that's a real, honest
result — one side's case was genuinely weaker and the model conceded
live. Say so on camera (*"this take resolved instead of escalating — that's
the model being honest, not a failure"*) and either keep it, or re-run
once more (~$0.02/run) for a take that escalates. Do not re-run more than
twice chasing a specific outcome — if it keeps resolving, that's the
honest result and the video should say that plainly.

## Beat 5 — The deployed runtime, live (3:15–4:15)

Different centerpiece from Beat 4: this shows the *actual deployed*
AgentCore Runtime — real weather, real DynamoDB, real Telegram — running
on the same path EventBridge fires every morning at 06:00 IST. Terminal:

```bash
aws lambda invoke --function-name harvest-convoy-watcher-invoker \
  --payload '{"cluster_id":"kamatchipuram","season_id":"2026-kuruvai","force":true}' \
  --cli-binary-format raw-in-base64-out --region ap-south-1 /tmp/out.json && cat /tmp/out.json
```

This calls the exact Lambda the EventBridge Schedule calls — `force:
true` just skips the "wait for rain in the forecast" gate so it runs
right now instead of waiting for a real trigger condition; every
scheduled run still uses the real gate. Say: *"This is the same shim,
same runtime, same DynamoDB the 6am cron job hits — I'm just triggering
it on demand instead of waiting for tomorrow."*

While it runs, switch to your phone: real Telegram messages should
land — Tamil `harvest_scheduled` for the fitting plots, Tamil
`not_ready` for the too-green one, and (mixed-language, on purpose) one
plot's message in **English**, since one of the wired-up farmers is
registered in English — a real demonstration of the per-farmer language
field, not a staged screenshot.

**If nothing arrives**: check the response body's `"status"` field.
`"already_ran"` means today's watcher marker is already set — see
"Re-running Beat 5" below. `"error"` with `"reason":
"weather_unavailable"` means Open-Meteo's forecast didn't return usable
data at all (rare — the far-horizon-day gap this project found and
fixed live is now handled automatically, see "What changed" below); wait
a minute and retry.

**On escalations**: today's real weather/capacity split may not produce
a tie (see Beat 4's note — this is expected and disclosed, not a bug).
The response body's `"escalations"` count tells you; if it's `0`, that's
the honest live result and the video can say so in one line, the same
way Beat 4 handles a clean resolve. Don't try to force one here — Beat 4
is the dedicated, reliable place for a negotiation, and forcing an
escalation on the live cluster means temporarily throttling its real
capacity, which risks leaving production data in a bad state if the
revert step is rushed. Not worth the risk for one beat.

**CloudWatch, if you want to show it's real, not a mock**: the runtime's
own log group has real, readable evidence of live computation happening —
more useful on camera than an empty trace view.

```
CloudWatch → Logs → Log groups
→ /aws/bedrock-agentcore/runtimes/harvest_convoy_watcher-7DW91DHIBA-DEFAULT
```

Filter for `FORECAST HORIZON TRUNCATED` or `MATURITY THRESHOLD FALLBACK`
or `no chat_id for farmer` — these are real log lines from real
decisions this project made live (a forecast day not yet computed by
Open-Meteo, a cluster with no calibrated threshold yet, farmers
deliberately left unwired). Say: *"These aren't canned log lines — this
is the actual deterministic core making real calls against real data,
today."*

*A note on distributed tracing, honestly stated rather than glossed
over*: this project's OpenTelemetry spans are documented (ADR-006) to
export to CloudWatch/X-Ray with correct, deep nesting, verified during
the original deployment session. As of this runbook's last check, a
fresh individual trace was not independently reproducible via the AWS
CLI in the few minutes available — CloudWatch Application Signals
*does* show the `app.daily_watch` operation as live and healthy (proof
the tracing pipeline itself is running), but the raw per-span trace view
wasn't confirmed. If you have time before filming, check
`CloudWatch → Application Signals → Services → harvest-convoy-watcher`
for a live trace list; if one's there, great, show it. If not, skip this
sub-beat rather than promise something that might not render live on
camera — the log-line evidence above already makes the "this is real"
point on its own.

## Beat 6 — Cost and honesty (4:15–4:45)

One screen, either the README's Cost section or just say it: *"A full
scheduling run costs about a cent and a half with prompt caching —
measured across multiple real runs, not estimated. Standing
infrastructure is about two dollars a month."* Then, briefly: *"This is
a simulated cluster on real coordinates and real weather data — no live
farmers are onboarded yet. The maturity threshold is a derived estimate
where a cluster hasn't been calibrated against its own climatology, and
the README shows exactly how it's derived either way. We also tested
whether the model could write Tamil directly — it couldn't, reliably —
so that one field is templated from facts instead of generated. The
README has the actual garbled samples, not just the claim."* Ten to
fifteen seconds, not a caveat-dump — it's there to show the project is
honest about its own limits, not to undersell it.

## Beat 7 — Close (4:45–5:00)

*"Harvest Convoy — deterministic scheduling, LLM judgment only where it's
actually needed, in the language the farmer actually uses. Repo link and
license in the description."* Cut.

---

## Re-running Beat 5

Each cluster only runs once per calendar day (the watcher's idempotency
marker) — `force: true` skips the *rain-trigger* gate, not this one. If
you need a second take, reset the marker first:

```bash
uv run python -c "
from harvest_convoy.storage import get_storage
import os
os.environ['HARVEST_CONVOY_STORAGE'] = 'dynamo'
get_storage().set_watcher_last_run('kamatchipuram', '2026-08-17')
"
```

(Any date before today works — this rolls the marker back by one day
rather than deleting the item outright, using the project's own storage
API instead of a raw DynamoDB call.) Then re-run the `aws lambda invoke`
command from Beat 5.

## What changed since the last version of this runbook

This runbook was rewritten after redeploying the AgentCore Runtime with
current code (Tamil support, per-cluster GDD derivation, multi-cluster-
capable watcher) and re-verifying the deployed path end to end, not just
assuming the redeploy made everything work:

- **Two real bugs found and fixed live, both required for Beat 5 to
  work at all**, neither specific to Tamil: (1) `DynamoStorage` decoded
  every DynamoDB Number as a Python `float`, so a farmer's real
  `telegram_chat_id` (an int) round-tripped as e.g. `1276258406.0` — a
  JSON float where Telegram's Bot API needs an integer, silently
  breaking every deployed send. (2) Requesting the full 16-day forecast
  horizon hit a real Open-Meteo gap — the far day hadn't been computed
  yet — which hard-failed the whole watcher run before it could do
  anything; a trailing not-yet-computed day is now trimmed instead of
  treated as a fatal gap. Both are covered in
  [ADR-006](adr/ADR-006-deploy.md) (this project's ongoing deploy-finding
  log) with the exact live evidence.
- **A third, unrelated pre-existing gap found the same session**: the
  deployed runtime's `TELEGRAM_BOT_TOKEN` had never been set (visible in
  its logs as every Telegram call failing against
  `.../botNone/sendMessage`) — meaning no scheduled run had ever
  actually delivered a message since this was first deployed. Fixed by
  adding it to the runtime's environment variables.
- **Beat 4 and Beat 5 now have clearly different jobs.** Beat 4
  (`trigger_scenario.py`, local) is the only beat that reliably produces
  an escalation — confirmed live that the real 8-plot cluster's actual
  capacity doesn't naturally tie today, matching what ADR-006 already
  found for this same cluster. Beat 5 (deployed runtime) demonstrates
  the real infrastructure and real message delivery instead of trying to
  force the same outcome twice.
- **The previous version of this runbook pointed at a specific captured
  trace ID for Beat 5.** That trace was not reproducible via the AWS CLI
  when this version was written (see Beat 5's tracing note) — rather
  than leave a runbook step that might silently fail on camera, that
  step was replaced with CloudWatch log-line evidence that was directly
  verified this session, plus an optional, clearly-hedged trace check.
- **A second round, after live Telegram verification**: once messages
  were actually landing on a real phone, two more real bugs surfaced
  that no amount of reading rendered strings in isolation had caught —
  the operator route summary's distance/direction hint was half-Tamil,
  half-English ("...NNW of village center" tail), and the route-position
  ordinal used "1வது," not a real Tamil word. Both fixed (see ADR-008
  Decision 17), along with two more half-translation instances found by
  auditing every farmer/operator-facing string the same way — both were
  in Telegram callback-query "toast" answers, a message shape the string
  dump never covered because they'd never been extracted into
  `messages_ta.py`/`messages_en.py` in the first place. Redeployed again
  and re-verified live the same way as the first round.
- **Runtime version**: the deployed AgentCore Runtime went from version
  6 (pre-ADR-008 code, the state this runbook previously assumed) to
  version 11 across five updates across both rounds — one for the
  current source tree, one each for the two Decision-9 bug fixes, one
  for the missing token, one for the Decision-17 translation fixes.
  `list-agent-runtime-endpoints` confirms the `DEFAULT` endpoint's
  `liveVersion` tracks the latest automatically; the EventBridge Schedule
  and Lambda shim were never touched (`LastModificationDate`/
  `LastModified` unchanged throughout) and don't need to be for any of
  this.
