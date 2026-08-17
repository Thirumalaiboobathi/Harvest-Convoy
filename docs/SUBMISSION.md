# Submission checklist — AWS "Agents for Humans" hackathon

Track: **Good Neighbor Agents**. Work through this top to bottom before
submitting on Devpost. Items marked **(you)** need a decision or action
only you can take — I can't push to GitHub or submit the Devpost form on
your behalf.

## Repository

- [ ] **(you)** Repo is pushed to a **public** GitHub repository. As of
      this session, `git remote -v` shows no remote configured — this
      repo has never been pushed anywhere. You'll need to create the
      GitHub repo and push before anything below is checkable by a judge.
      ```bash
      gh repo create harvest-convoy --public --source=. --remote=origin
      git push -u origin main
      ```
- [ ] **(you)** MIT license is visible in the repo's **GitHub "About"
      section** (the sidebar badge), not just the `LICENSE` file — GitHub
      usually detects this automatically from `LICENSE` on push, but
      confirm it after pushing (repo page → About → should show "MIT
      License" as a clickable badge). If it doesn't appear, GitHub's
      license detector can be finicky about non-standard formatting;
      the current `LICENSE` file uses the standard MIT template text so
      it should be fine.
- [ ] `README.md` is present and complete (done this session) — covers
      what it is, zero-AWS quickstart, full AWS setup, architecture
      summary, honesty section, deployment findings, measured cost.
- [ ] `ARCHITECTURE.md` + `docs/architecture.png` are present (done this
      session) — the diagram is a separate, required deliverable per
      your instructions, not just embedded in the README.
- [ ] `docs/adr/` — all 7 ADRs present, showing the design reasoning
      trail (already existed from Phases 0–6).

## Video (under 5 minutes)

- [ ] **(you)** Record following `docs/DEMO.md`.
- [ ] **(you)** Upload (YouTube unlisted/public, or wherever Devpost's
      submission form wants it) and confirm the runtime is under 5:00.
- [ ] **(you)** Paste the video URL into the Devpost submission form.

## Devpost form content

These are standard Devpost project-submission fields — fill using the
material already in this repo, don't write new copy from scratch:

- [ ] **Project name**: Harvest Convoy
- [ ] **Elevator pitch / tagline**: the two-sentence lead from the top of
      `README.md`.
- [ ] **"What it does" / full description**: the README's Architecture
      summary + Honesty section cover this — paste or paraphrase.
- [ ] **"How we built it"**: pull from `ARCHITECTURE.md`'s "Request flow"
      and "Deployment topology" sections.
- [ ] **"Challenges we ran into"**: the README's "What we learned
      deploying" section is written exactly for this field — two real,
      root-caused bugs, not generic hackathon-fatigue complaints.
- [ ] **Built With / tags**: at minimum `python`, `aws-bedrock`,
      `aws-agentcore`, `strands-agents`, `dynamodb`, `aws-lambda`,
      `eventbridge`, `telegram-bot-api`, `opentelemetry`.
- [ ] **Strands Agents usage is explicitly described** somewhere in the
      submission text — not just visible in code. Say plainly: *"Strands
      Agents (Bedrock Nova Pro) powers exactly two things: pairwise plot
      negotiation (`agents/coordinator.py`, `agents/advocate.py`) and
      farmer-facing message text — every factual field is overwritten
      with deterministic ground truth before use, so the model never
      originates a number."*
- [ ] **AgentCore deployment is explicitly described**: *"Deployed to
      Bedrock AgentCore Runtime via direct code deployment (arm64,
      `codeConfiguration`, no container), triggered daily by EventBridge
      Schedule through a small Lambda shim (EventBridge's universal
      target can't call `InvokeAgentRuntime` directly — see
      ADR-006 Decision 7), with OpenTelemetry traces exported to
      CloudWatch/X-Ray via AWS's ADOT distro."*
- [ ] **Repo link**: the public GitHub URL from the step above.
- [ ] **Category**: Good Neighbor Agents.

## Testing instructions field

Devpost usually asks how judges can try it themselves. Point them at the
README's Quickstart section verbatim — it's already written for someone
who has never seen this repo:

```
uv sync --all-groups
uv run python -m scripts.seed_cluster --write
uv run python -m scripts.trigger_scenario <any_number> --offline
```

No AWS account needed for this path. If judges want to see the deployed
AWS side without provisioning anything themselves, point them at the
CloudWatch trace location in `docs/DEMO.md` Beat 5 (this only works while
the AWS resources are still live — see Teardown timing below).

## Teardown

- [ ] **(you)** After judging closes (not before — the deployed
      infrastructure is what judges may check), tear down the AWS
      resources to stop the standing ~$2/month cost:
      ```bash
      bash scripts/teardown.sh
      ```
      Review the confirmation prompt — it lists every resource by name
      before deleting anything. Safe to re-run if interrupted partway.
- [ ] **(you)** Decide whether to keep the GitHub repo public
      indefinitely (recommended — it's the portfolio artifact) even
      after AWS resources are torn down; the README/ADRs/tests all still
      work with the zero-AWS quickstart regardless.

## What I did **not** do, on purpose

- Did not push to GitHub or create a remote — that's a visible, public
  action you should trigger yourself.
- Did not submit the Devpost form — same reason, and I don't have
  access to your Devpost account.
- Did not touch the LICENSE file's copyright line (already correct:
  `Copyright (c) 2026 thiru260402`).
