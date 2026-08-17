# Harvest Convoy

A village cluster in Tamil Nadu shares one combine harvester. Harvest Convoy
is an agent that tracks each plot's crop maturity and the incoming weather,
and reschedules the shared machine's route across all farmers automatically —
surfacing to a human only when two plots genuinely conflict for the same slot.

Built for the AWS "Agents for Humans" hackathon, track **Good Neighbor
Agents**.

> **Status: early build.** This README will be replaced with full setup
> instructions, an architecture diagram, and a demo scenario runbook in the
> final submission phase. See `docs/adr/` for the design decisions made so
> far.

## Development

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency
management and `pytest` for tests.

```bash
uv sync --all-groups
uv run pytest
```

## Storage

No AWS account is needed to run this project. By default it uses a plain
JSON file on disk (`.data/harvest_convoy.json`, created automatically) —
nothing to install, nothing to configure.

To use real DynamoDB instead (either actual AWS, or DynamoDB Local if you
have it running), set:

```bash
export HARVEST_CONVOY_STORAGE=dynamo
# against real AWS: normal AWS credentials (env vars, ~/.aws/credentials, etc.)
# against DynamoDB Local instead: also set
export DYNAMODB_ENDPOINT_URL=http://localhost:8000
```

Both backends implement the same interface (`storage/interface.py`)
against the same single-table key scheme — see `docs/adr/ADR-005-persistence-fairness.md`
for the design. Seed the demo cluster into whichever backend is active:

```bash
uv run python -m scripts.seed_cluster --write
```

## License

MIT — see [LICENSE](LICENSE).
