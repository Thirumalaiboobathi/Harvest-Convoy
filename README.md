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

## License

MIT — see [LICENSE](LICENSE).
