# CLAUDE.md — classification-simulation-pack

Loaded automatically by Claude Code at session start. v0.5.0, actually runnable (Python 3.11+, real unittest suite) — this is the most mature repo in the set. Treat its "Not implemented yet" list as a hard boundary, not a TODO you can silently fill in.

## One-line summary

Packages ambiguous real-world work into an evidence-calibrated simulation workload, runs many declared operating conditions, and returns an auditable Receipt. Explicitly does **not** predict the future — it classifies outcomes under stated assumptions.

## Core pipeline

```
Natural language / logs / measurements → Evidence Pack → Simulation Package
→ Workload Manifest → Reference Runner → Simulation Receipt → Next Package / PoC / HOLD
```

## Directory map

| Path | Role | Read when |
|---|---|---|
| `schemas/*.schema.json` | The four transport contracts: evidence-pack, simulation-package, workload-manifest, simulation-receipt (+ `package`/`receipt` compatibility aliases, `scenario`) | Before creating/validating any document at that stage |
| `packages/automation_workflow/`, `packages/business_plan/` | Domain-specific Package definitions | Reference before writing a new Package — check if one already covers the domain |
| `examples/*.yaml` | Worked inputs, including a sparse-evidence variant | To see calibration in action — compare `data_entry_automation.yaml` vs `_sparse.yaml` outputs |
| `runner/run.py` | The reference runner (`--package`, `--input`, `--runs`, `--seed`, `--output`, `--validate-scenarios`) | Actually executing a simulation |
| `receipts/` | Generated Receipts | Reading past results |
| `docs/contracts.md` | Full field-level contract spec | Before extending any schema |
| `tests/test_runner.py` | Schema/regression tests | Before claiming a change is safe |

## Hard rules

- **Classification before prediction.** A Receipt classifies outcomes under declared conditions. Never present a synthetic frequency as a real-world probability or forecast.
- **Evidence changes distributions, not scores.** More evidence narrows/reshapes the condition space (`calibration.method`: `observed_range`/`mean_std`/`empirical`/`none`). It never adds points toward a favorable conclusion.
- **Missing information is a valid result.** Use `HOLD`, widen the range (`missing_policy: widen_range`), or emit a warning — never invent a value to fill a gap.
- **Deterministic evaluation, not model judgment.** AI may prepare Packages and explain Receipts; the declared evaluator rules (versioned) control classification, not free-form LLM output.
- **`data_quality` fields are routing proxies, not accuracy claims.** `evidence_coverage`, `model_confidence`, etc. describe how well-calibrated the simulation is — never state them as "this will happen with X% real-world probability."
- **This project is explicitly not:** a guarantee of business/automation success; a replacement for live observation or controlled experiments; financial/legal/medical/safety advice; proof that simulated cases are independent real-world observations; permission to automate high-impact decisions without human authority.

## Quick task recipes

**"Turn my workflow description into a Package"** → separate observed facts / reported info / inferences / assumptions into an Evidence Pack first. Do not skip straight to a Simulation Package from prose.

**"Run this and explain the Receipt"** → run with a fixed `--seed`, use `--validate-scenarios`, then read in this order: `plain_summary` → `outcomes` → `data_quality` → `automation_result` → `holds` → `safety_net` → `provenance`. State the work-allocation share (average per record) and the operating-mode distribution (per full sampled world) as two separate numbers — never collapse them into one "X% automated" headline.

**"Is this Package ready to promote?"** → check `readiness_status` (`ready`/`exploratory`/`held`) plus `unresolved_variables`. `exploratory` or `held` means do not present the result as decision-ready.

## Relationship to other repos in this ecosystem

Part of a wider set alongside `information-compost`, `lopas-protocol-foundry`, `LoPAS-Open-Translator-Core`, `Verifiable-Capability-Exchange`, `LoPAS-LCA`. This repo's `SCI`-adjacent indicators are not wired to any of the others; if a task seems to need LoPAS-SEED indicators (DoQ-S, SCI-S, etc.) inside a Package's evaluator rules, say so explicitly rather than assuming the connection exists.
