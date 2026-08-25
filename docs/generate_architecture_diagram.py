"""Generates docs/architecture.png. Not part of the shipped package --
a build-time diagram generator, kept so the diagram is editable/reproducible
rather than a hand-made image nobody can update.

Usage: python docs/generate_architecture_diagram.py
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

W, H = 1760, 1300
BG = (255, 255, 255)
INK = (30, 34, 40)
MUTED = (110, 118, 128)
DETERMINISTIC_FILL = (222, 241, 230)
DETERMINISTIC_EDGE = (46, 125, 87)
LLM_FILL = (250, 231, 208)
LLM_EDGE = (196, 111, 26)
INFRA_FILL = (232, 236, 245)
INFRA_EDGE = (90, 100, 130)
EXTERNAL_FILL = (238, 238, 240)
EXTERNAL_EDGE = (120, 120, 128)
# Not part of the original legend -- added alongside ADR-016 specifically
# to make "code exists" visually distinct from "code is deployed", so
# this diagram can't independently drift from that ADR's correction the
# way it already had (see ADR-016's note on the generator encoding the
# same wrong belief the prose did).
NOT_DEPLOYED_FILL = (250, 228, 228)
NOT_DEPLOYED_EDGE = (176, 62, 62)
ARROW = (60, 66, 76)

FONT_DIR = "C:/Windows/Fonts/"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_DIR + name, size)

f_title = font("segoeuib.ttf", 40)
f_section = font("segoeuib.ttf", 24)
f_box_title = font("segoeuib.ttf", 20)
f_box_body = font("segoeui.ttf", 15)
f_small = font("segoeui.ttf", 14)
f_mono = font("consola.ttf", 14)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def rounded_box(xy, fill, edge, width=2, radius=14):
    d.rounded_rectangle(xy, radius=radius, fill=fill, outline=edge, width=width)


def centered_text(cx, y, text, fnt, color=INK):
    bbox = d.textbbox((0, 0), text, font=fnt)
    w = bbox[2] - bbox[0]
    d.text((cx - w / 2, y), text, font=fnt, fill=color)


def wrapped_lines(cx, y, lines, fnt, color=INK, line_h=19):
    for i, line in enumerate(lines):
        centered_text(cx, y + i * line_h, line, fnt, color)


def arrowhead(p2, ang, color=ARROW, width=3):
    import math
    x2, y2 = p2
    for side in (0.5, -0.5):
        ax = x2 - 14 * math.cos(ang - side * 0.6)
        ay = y2 - 14 * math.sin(ang - side * 0.6)
        d.line([p2, (ax, ay)], fill=color, width=width)


def arrow(p1, p2, color=ARROW, width=3, label=None, label_side="above", label_dx=0):
    import math
    d.line([p1, p2], fill=color, width=width)
    x2, y2 = p2
    x1, y1 = p1
    ang = math.atan2(y2 - y1, x2 - x1)
    arrowhead(p2, ang, color, width)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        dy = -20 if label_side == "above" else 6
        centered_text(mx + label_dx, my + dy, label, f_small, MUTED)


def elbow_arrow(p1, p2, color=ARROW, width=3, label=None, label_dx=0, label_dy=0):
    """Vertical-then-horizontal connector -- avoids diagonal lines crossing text."""
    x1, y1 = p1
    x2, y2 = p2
    mid_y = y2
    d.line([(x1, y1), (x1, mid_y)], fill=color, width=width)
    d.line([(x1, mid_y), (x2, mid_y)], fill=color, width=width)
    import math
    arrowhead((x2, y2), 0.0 if x2 >= x1 else math.pi, color, width)
    if label:
        centered_text(x1 + label_dx, y1 + 8 + label_dy, label, f_small, MUTED)


def elbow_arrow_hv(p1, p2, bend_y, color=ARROW, width=3):
    """Horizontal-then-vertical connector: goes sideways at bend_y (while still
    clear of any header text), then straight down at the target x. Used to
    route external-input arrows past the runtime box's header line."""
    x1, y1 = p1
    x2, y2 = p2
    d.line([(x1, y1), (x1, bend_y)], fill=color, width=width)
    d.line([(x1, bend_y), (x2, bend_y)], fill=color, width=width)
    d.line([(x2, bend_y), (x2, y2)], fill=color, width=width)
    arrowhead((x2, y2), 1.5708, color, width)


# ---- Title ----
centered_text(W / 2, 22, "Harvest Convoy — Architecture", f_title)
centered_text(W / 2, 70, "One shared harvester, eight plots, one rule: math decides, judgment only argues", f_small, MUTED)

# ---- External services row ----
# Telegram Bot API used to sit here too, with an arrow INTO the runtime --
# wrong: the deployed runtime never receives anything from Telegram, only
# sends to it. It's drawn below instead, downstream of notify.py, as the
# outbound-only destination it actually is. See ADR-016.
om = (60, 120, 300, 190)
rounded_box(om, EXTERNAL_FILL, EXTERNAL_EDGE)
centered_text((om[0]+om[2])/2, 133, "Open-Meteo API", f_box_title)
wrapped_lines((om[0]+om[2])/2, 158, ["Archive + Forecast,", "gap-bridged, cached"], f_small, MUTED)

# ---- AWS infra chain (top right) ----
sched = (760, 120, 990, 190)
lam = (1030, 120, 1260, 190)
rounded_box(sched, INFRA_FILL, INFRA_EDGE)
centered_text((sched[0]+sched[2])/2, 135, "EventBridge Schedule", f_box_title)
wrapped_lines((sched[0]+sched[2])/2, 160, ["cron(0 6 * * ? *)", "Asia/Kolkata, daily"], f_small, MUTED)

rounded_box(lam, INFRA_FILL, INFRA_EDGE)
centered_text((lam[0]+lam[2])/2, 135, "Lambda shim", f_box_title)
wrapped_lines((lam[0]+lam[2])/2, 160, ["watcher-invoker: boto3", "InvokeAgentRuntime call"], f_small, MUTED)

arrow((sched[2], 155), (lam[0], 155))

# ---- AgentCore Runtime outer container ----
runtime_box = (60, 245, 1260, 940)
rounded_box(runtime_box, (250, 250, 252), (150, 150, 158), width=2, radius=18)
d.text((260, 257), "AgentCore Runtime  (bedrock_agentcore.BedrockAgentCoreApp, arm64 codeConfiguration)", font=f_box_title, fill=INK)
d.text((260, 285), "watcher.run_daily_watch()  —  reads weather + plot state, runs the deterministic core, hands CONTESTED plots to LLM judgment", font=f_small, fill=MUTED)

lam_cx = lam[0] + (lam[2] - lam[0]) / 2
d.line([(lam_cx, lam[3]), (lam_cx, 320), (1210, 320), (1210, 335)], fill=ARROW, width=3)
arrowhead((1210, 335), 1.5708)
centered_text(lam_cx + 90, lam[3] + 6, "payload: cluster_id, season_id", f_small, MUTED)

elbow_arrow_hv((om[0]+120, om[3]+8), (160, 335), bend_y=210, color=EXTERNAL_EDGE)

# ---- Deterministic core box ----
det_box = (110, 335, 645, 800)
rounded_box(det_box, DETERMINISTIC_FILL, DETERMINISTIC_EDGE, width=3)
d.text((130, 347), "DETERMINISTIC CORE", font=f_section, fill=DETERMINISTIC_EDGE)
d.text((130, 377), "pure Python — no LLM call anywhere in this box", font=f_small, fill=MUTED)

sub_boxes_det = [
    ("agronomy/gdd.py + crop_params.py + calibration.py", ["Growing Degree Days accumulation,", "per-cluster derived maturity threshold", "→ TOO_GREEN vs ready"]),
    ("scheduling/capacity.py + solver.py", ["usable_harvest_days() rain budget,", "greedy acreage allocation", "→ FITS vs CONTESTED"]),
    ("scheduling/route.py", ["straight-line route ordering", "for FITS plots"]),
    ("storage/decay.py + fairness.py", ["geometric season-decay,", "fairness ledger score"]),
]
by = 405
for name, lines in sub_boxes_det:
    bh = 24 + len(lines) * 18 + 10
    b = (135, by, 620, by + bh)
    rounded_box(b, (255, 255, 255), DETERMINISTIC_EDGE, width=1, radius=8)
    d.text((148, by + 8), name, font=f_mono, fill=INK)
    yy = by + 28
    for line in lines:
        d.text((148, yy), line, font=f_small, fill=MUTED)
        yy += 18
    by += bh + 8

# ---- LLM judgment box ----
llm_box = (705, 335, 1235, 800)
rounded_box(llm_box, LLM_FILL, LLM_EDGE, width=3)
d.text((725, 347), "LLM JUDGMENT", font=f_section, fill=LLM_EDGE)
d.text((725, 377), "Strands Agents + Bedrock Nova Pro (ap-south-1) — only for CONTESTED plots", font=f_small, fill=MUTED)

sub_boxes_llm = [
    ("agents/coordinator.py", ["negotiate_pair(): up to 3 rounds,", "each round = both advocates' claims"]),
    ("agents/advocate.py", ["one Strands agent per plot,", "fairness_lookup tool + AdvocateClaim", "structured output, prompt-cached", "system prompt; Tamil argument templated"]),
    ("resolution (deterministic)", ["clear win → route updated,", "no resolution after 3 rounds", "→ escalate to human operator"]),
]
by = 405
for name, lines in sub_boxes_llm:
    bh = 24 + len(lines) * 18 + 10
    b = (730, by, 1210, by + bh)
    rounded_box(b, (255, 255, 255), LLM_EDGE, width=1, radius=8)
    d.text((743, by + 8), name, font=f_mono, fill=INK)
    yy = by + 28
    for line in lines:
        d.text((743, yy), line, font=f_small, fill=MUTED)
        yy += 18
    by += bh + 8

mid_y = (det_box[1] + det_box[3]) / 2
arrow((det_box[2], mid_y), (llm_box[0], mid_y), width=4)
centered_text((det_box[2] + llm_box[0]) / 2, mid_y - 34, "CONTESTED", f_small, MUTED)
centered_text((det_box[2] + llm_box[0]) / 2, mid_y - 18, "plots only", f_small, MUTED)

# ---- Storage + notify row inside runtime ----
row_y = 830
dynamo = (110, row_y, 400, row_y + 80)
notify = (450, row_y, 890, row_y + 80)
rounded_box(dynamo, INFRA_FILL, INFRA_EDGE)
centered_text((dynamo[0]+dynamo[2])/2, row_y+12, "DynamoDB (harvest_convoy)", f_box_title)
wrapped_lines((dynamo[0]+dynamo[2])/2, row_y+38, ["single-table, PK/SK + GSI1", "plots, farmers, ledger, watcher marker"], f_small, MUTED)

rounded_box(notify, EXTERNAL_FILL, EXTERNAL_EDGE)
centered_text((notify[0]+notify[2])/2, row_y+12, "Telegram notify.py — outbound only", f_box_title)
wrapped_lines((notify[0]+notify[2])/2, row_y+38, ["harvest_scheduled, not_ready, route_summary, escalation", "Tamil default / English opt-in — messages_ta.py / _en.py"], f_small, MUTED)

elbow_arrow((det_box[0]+150, det_box[3]+2), (dynamo[0]+145, row_y))
elbow_arrow((llm_box[0]+230, llm_box[3]+2), (notify[0]+220, row_y), color=LLM_EDGE)

# ---- OTel/CloudWatch box ----
otel = (940, row_y, 1230, row_y + 80)
rounded_box(otel, INFRA_FILL, INFRA_EDGE)
centered_text((otel[0]+otel[2])/2, row_y+12, "ADOT → CloudWatch / X-Ray", f_box_title)
wrapped_lines((otel[0]+otel[2])/2, row_y+38, ["coordinator.negotiate →", "negotiation.round spans, aws/spans"], f_small, MUTED)
elbow_arrow((llm_box[2]-100, llm_box[3]+2), (otel[0]+145, row_y), color=LLM_EDGE)

# ---- Outbound destination + the never-deployed inbound half (ADR-016) ----
telegram = (110, 950, 480, 1050)
rounded_box(telegram, EXTERNAL_FILL, EXTERNAL_EDGE)
centered_text((telegram[0]+telegram[2])/2, 966, "Telegram Bot API", f_box_title)
wrapped_lines((telegram[0]+telegram[2])/2, 996, ["outbound only: harvest_scheduled,", "not_ready, route_summary, escalation"], f_small, MUTED, line_h=22)

webhook = (530, 950, 1000, 1050)
rounded_box(webhook, NOT_DEPLOYED_FILL, NOT_DEPLOYED_EDGE, width=2)
centered_text((webhook[0]+webhook[2])/2, 960, "webhook.py, registration.py", f_box_title)
centered_text((webhook[0]+webhook[2])/2, 982, "NOT DEPLOYED", f_box_title, NOT_DEPLOYED_EDGE)
wrapped_lines((webhook[0]+webhook[2])/2, 1006, ["registration, /help, taps, replies — tested via", "run_polling.py / suite only. No live entry point. ADR-016."], f_small, MUTED, line_h=20)

notify_cx = notify[0] + (notify[2] - notify[0]) / 2
elbow_arrow((notify_cx, notify[3]), ((telegram[0]+telegram[2])/2, telegram[1]), color=EXTERNAL_EDGE)

# ---- Bottom note bar ----
note_box = (60, 1070, 1260, 1140)
rounded_box(note_box, (255, 255, 255), (150, 150, 158), width=1, radius=10)
d.text((80, 1082), "Escalation is the product, not a fallback:", font=f_box_title, fill=INK)
d.text((80, 1112), "the LLM never computes a number — GDD, capacity, fairness score, and route are all deterministic Python. It only argues, and only when the math is genuinely tied.", font=f_small, fill=MUTED)

# ---- Legend (right side) ----
lx = 1310
ly = 240
d.text((lx, ly), "Legend", font=f_section, fill=INK)
ly += 40
legend = [
    (DETERMINISTIC_FILL, DETERMINISTIC_EDGE, "Deterministic Python"),
    (LLM_FILL, LLM_EDGE, "LLM (Strands + Nova Pro)"),
    (INFRA_FILL, INFRA_EDGE, "AWS infrastructure"),
    (EXTERNAL_FILL, EXTERNAL_EDGE, "External service"),
    (NOT_DEPLOYED_FILL, NOT_DEPLOYED_EDGE, "Code exists, not deployed"),
]
for fill, edge, label in legend:
    rounded_box((lx, ly, lx + 34, ly + 24), fill, edge, width=2, radius=6)
    d.text((lx + 44, ly + 3), label, font=f_small, fill=INK)
    ly += 40

ly += 20
d.text((lx, ly), "Deployment", font=f_section, fill=INK)
ly += 36
deploy_lines = [
    "Region: ap-south-1",
    "Runtime: codeConfiguration,",
    "  arm64, PYTHON_3_12",
    "Trigger: EventBridge → Lambda",
    "  shim → InvokeAgentRuntime",
    "  (universal target can't",
    "  marshal the blob body)",
    "Tracing: OTEL_EXPORTER_OTLP_",
    "  TRACES_ENDPOINT set explicitly",
    "  (no local collector in",
    "  code-deploy mode)",
    "Clusters: watcher.py iterates",
    "  a list in code (any TN cluster,",
    "  GDD is per-plot) -- deployed",
    "  schedule still targets one",
    "  cluster_id per Schedule rule",
]
for line in deploy_lines:
    d.text((lx, ly), line, font=f_small, fill=MUTED)
    ly += 20

ly += 16
d.text((lx, ly), "Cost (measured)", font=f_section, fill=INK)
ly += 36
cost_lines = [
    "~$0.011 / full 8-plot run",
    "  (Nova Pro, prompt-cached,",
    "  measured range across 3 runs)",
    "~$2/month standing infra",
    "  worst case",
]
for line in cost_lines:
    d.text((lx, ly), line, font=f_small, fill=MUTED)
    ly += 20

img.save("docs/architecture.png")
print("wrote docs/architecture.png", img.size)
