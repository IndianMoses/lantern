# Execution evidence (sandbox run)

Generated 2026-10-18T15:56:34.997471+00:00 with provider `deterministic` against a fresh seeded database.

- `journey.json` — 27 steps of a complete customer and insurer journey (requests, statuses, assistant excerpts, tools called with permission tiers, approvals). Steps 9, 11 and 24 are expected denials.
- `operation-logs/` — one JSON Lines file per agent run: run_started, intent_classified, tool_call (tool, tier, status, masked input, output summary, duration), grounding_verified, run_finished. `index.jsonl` lists all runs. Personal data is masked before writing.
- `audit-chain.json` — verification of the hash chained audit log after the run, plus the last 40 events.
- `eval-results.json` — evaluation suite results (`backend/evals/cases.json`), if the suite was run for this provider.

Regenerate with `make evidence` (offline) or `LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... make evidence`.
