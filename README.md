# Classification Simulation Pack

> **Package real-world work into an evidence-calibrated simulation workload, run many possible operating conditions, and return an auditable Receipt.**

**日本語概要:** 自然文・業務ログ・実績値を、検証可能なYAMLシミュレーションPackageへ変換するための小さな規格と実行系です。条件の可変域から多数のシナリオを生成し、結果を有限の状態へ分類して、「どの条件なら回るか」「どこで詰まるか」「次に何を測るか」をReceiptとして返します。

Classification Simulation Pack does **not** claim to predict the future. It makes assumptions visible, tests many declared conditions reproducibly, and records what the model could and could not determine.

## What goes in, and what comes back?

### Input

A plan or workflow, plus whatever evidence is available:

- natural-language descriptions;
- operating logs and historical measurements;
- public information and reported values;
- assumptions that still need verification; and
- the compute requirements of the simulation workload.

### Output

A machine-readable and human-readable Receipt containing:

- the distribution of classified outcomes;
- effective variable ranges after evidence calibration;
- bottlenecks and boundary conditions;
- unresolved variables and explicit HOLDs;
- reusable failure patterns and transfer candidates;
- suggested next Packages or PoC steps; and
- seed, versions, digests, provenance, and workload usage.

```text
Natural language / logs / measurements
                ↓
          Evidence Pack
                ↓
        Simulation Package
                ↓
         Workload Manifest
                ↓
          Reference Runner
                ↓
       Simulation Receipt
                ↓
       Next Package / PoC / HOLD

Future operation layer:
Workload Manifest → Capacity Router → available / spare compute
```

## A concrete example: AI workflow automation

The bundled example asks:

> For a monthly data-entry workflow, which records can be automated, which need human review, which require specialist escalation, and which must be held?

The Package varies conditions such as:

- incoming record volume;
- source-system compatibility;
- exception rate;
- AI extraction accuracy;
- rule clarity;
- human review capacity;
- labor and tool costs; and
- the cost of an incorrect record.

Each simulated world is classified as one of:

```text
AUTO       safe to complete automatically under the declared rules
REVIEW     AI-assisted, with human confirmation
ESCALATE   specialist handling required
HOLD       required evidence, authority, or safety condition is missing
MANUAL     automation does not provide sufficient operational value
```

The Receipt separates two different questions:

1. **Work allocation:** On average, what share of records follows each route?
2. **Operating mode:** Under each complete set of sampled conditions, what mode describes the workflow as a whole?

This prevents an average `AUTO` share from being mistaken for an end-to-end autonomous workflow.

## Why evidence matters

More information does not add points to the result.

It calibrates the worlds being simulated.

```text
broad assumption
    ↓ + verified evidence
narrower or empirical condition distribution
    ↓
fewer unsupported simulated worlds
    ↓
more decision-ready Receipt
```

Example:

```yaml
variables:
  exception_rate:
    distribution: uniform
    min: 0.02
    max: 0.25
    evidence_refs: [ev-exceptions]
    confidence: high
    missing_policy: widen_range
    calibration:
      method: observed_range
      min_samples: 3
      padding_ratio: 0.15
```

If sufficient numeric evidence is available, the runner records both the **declared range** and the **effective calibrated range**. If evidence is insufficient, the broad declared range remains visible and the Receipt emits a warning or HOLD according to `missing_policy`.

Supported calibration methods:

- `observed_range` — observed minimum and maximum, with declared padding;
- `mean_std` — a bounded normal distribution derived from evidence;
- `empirical` — resampling from observed values; and
- `none` — keep the declared distribution unchanged.

This is calibration, not guaranteed real-world predictive accuracy.

## The four transport contracts

v0.5.0 fixes four primary contracts so a simulation workload can be packaged, validated, executed, and returned consistently.

### 1. Evidence Pack

Separates observations, reports, public information, inferences, and assumptions. Each item can carry freshness, confidence, provenance, value, unit, and timestamps.

```text
schemas/evidence-pack.schema.json
```

### 2. Simulation Package

Defines the question, variable condition space, formulas, Package selector, evidence references, and optional domain-specific layer. Natural language is not executed directly.

```text
schemas/simulation-package.schema.json
```

### 3. Workload Manifest

Describes how the workload may be transported and executed: minimum, target, and maximum runs; CPU/GPU preference; memory; interruptibility; checkpointing; resumption; divisibility; and expansion limits.

```text
schemas/workload-manifest.schema.json
```

### 4. Simulation Receipt

Returns outcomes, calibration, data quality, unresolved variables, workload usage, reusable structures, and provenance.

```text
schemas/simulation-receipt.schema.json
```

For the complete field-level contract, see [`docs/contracts.md`](docs/contracts.md).

## Quick start

### Requirements

- Python 3.11 or later

Install dependencies and run the tests:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Run the calibrated AI-automation example:

```bash
python runner/run.py \
  --package packages/automation_workflow \
  --input examples/data_entry_automation.yaml \
  --runs 2000 \
  --seed 42 \
  --output receipts/data_entry_automation.json \
  --validate-scenarios
```

Run the same workflow with sparse evidence:

```bash
python runner/run.py \
  --package packages/automation_workflow \
  --input examples/data_entry_automation_sparse.yaml \
  --runs 2000 \
  --seed 42 \
  --output receipts/data_entry_automation_sparse.json \
  --validate-scenarios
```

Compare the two Receipts. The richer Evidence Pack should calibrate more variables and narrow more ranges. It should **not** automatically produce a more favorable business result.

## Reading a Receipt

Start with these sections:

```text
plain_summary       human-readable conclusion
outcomes            classification counts and ratios
data_quality        evidence coverage, reliability, calibration, readiness
automation_result   work allocation, operating modes, bottlenecks, PoC proposal
holds               missing information or blocked decisions
safety_net          reusable patterns, transfer candidates, next Packages
provenance          input, rules, schema, seed, and runner lineage
```

The data-quality fields are routing proxies, not accuracy claims:

```yaml
data_quality:
  readiness_status: ready | exploratory | held
  evidence_coverage: 0.0
  evidence_reliability: 0.0
  variable_calibration: 0.0
  model_confidence: 0.0
  mean_range_reduction: 0.0
  unresolved_variables: []
```

## Why this is different from running the same prompt 2,000 times

Classification Simulation Pack separates generation, evaluation, and evidence:

- scenarios are sampled from declared, inspectable distributions;
- evidence calibrates variables without silently rewriting the question;
- classification uses ordered, versioned evaluator rules;
- missing information remains a first-class result;
- the seed and relevant file digests are recorded; and
- simulation output is never relabeled as live observation.

The reference runner stays intentionally thin. Domain knowledge belongs in Packages, taxonomies, evaluator rules, examples, and evidence—not as hidden logic inside the engine.

## Repository structure

```text
classification-simulation-pack/
├─ README.md
├─ CHANGELOG.md
├─ schemas/
│  ├─ evidence-pack.schema.json
│  ├─ simulation-package.schema.json
│  ├─ workload-manifest.schema.json
│  ├─ simulation-receipt.schema.json
│  ├─ package.schema.json          # compatibility alias
│  ├─ receipt.schema.json          # compatibility alias
│  └─ scenario.schema.json
├─ packages/
│  ├─ automation_workflow/
│  └─ business_plan/
├─ examples/
│  ├─ data_entry_automation.yaml
│  ├─ data_entry_automation_sparse.yaml
│  └─ this_project.yaml
├─ runner/
│  └─ run.py
├─ receipts/
├─ docs/
│  └─ contracts.md
├─ tests/
│  └─ test_runner.py
└─ requirements.txt
```

## Use it with an AI assistant

The repository is designed to be readable by both humans and AI assistants. A useful starting request is:

> Read this repository and my workflow description. Separate observed facts, reported information, inferences, and assumptions into an Evidence Pack. Create a valid Simulation Package and Workload Manifest. Do not invent missing values: use visible ranges or HOLDs. Run the automation workflow Package with a fixed seed, validate the scenarios, and explain the Receipt without presenting it as a forecast.

The YAML and JSON contracts are the common language between the user, the AI assistant, the runner, and future capacity-routing systems. They are not intended to become a mandatory end-user interface.

## Current scope

### Implemented in v0.5.0

- four versioned transport contracts;
- deterministic scenario classification;
- reproducible seeded runs;
- evidence-based variable calibration;
- sparse-evidence warnings and HOLD behavior;
- AI workflow allocation across AUTO / REVIEW / ESCALATE / HOLD / MANUAL;
- bottleneck and guarded PoC recommendations;
- reusable failure structures and next-Package candidates;
- workload run-range enforcement; and
- schema and regression tests.

### Not implemented yet

- a production natural-language-to-Package compiler;
- live business-system connectors;
- a Capacity Router connected to spare data-center resources;
- distributed checkpoint and resume across compute nodes;
- automatic execution of child Packages; or
- proof of production savings, safety, or predictive accuracy.

These boundaries are deliberate. The current repository defines and tests the **cargo format** before connecting it to a larger compute logistics network.

## Design principles

1. **Classification before prediction**  
   Classify outcomes under declared conditions. Do not turn synthetic frequency into prophecy.

2. **Evidence changes distributions, not scores**  
   Better evidence narrows or reshapes the condition space.

3. **Missing information is a valid result**  
   Use warnings, broad ranges, or HOLDs instead of invented precision.

4. **Deterministic evaluation first**  
   AI may help prepare Packages and explain Receipts, but declared rules control classification.

5. **Receipts carry lineage**  
   Record versions, seed, digests, evidence level, unresolved conditions, and provenance.

6. **The workload must be transportable**  
   Execution requirements, interruptibility, checkpointing, and run limits belong in the Package contract.

7. **Failure should not be a total loss**  
   Reusable structures, boundary conditions, and next questions can be transferred to other Packages.

8. **Keep the core small**  
   Add new domains as Packages rather than embedding them in the runner.

## Safety boundary

Classification Simulation Pack is not:

- a guarantee that a business or automation will succeed;
- a replacement for live observation, controlled experiments, or operational testing;
- financial, legal, medical, or safety advice;
- proof that simulated cases are statistically independent real-world observations; or
- permission to automate high-impact decisions without domain review and human authority.

Its purpose is narrower:

> **Convert an ambiguous workflow into visible assumptions, evidence-calibrated condition ranges, repeatable scenarios, finite operating states, and an auditable Receipt.**

# Addition: `compute_routing` Package

Copy these paths into the repository root:

- `packages/compute_routing/taxonomy.yaml`
- `packages/compute_routing/evaluator.yaml`
- `examples/tohoku_compute_routing.yaml`
- `docs/compute-routing.md`
- `tests/test_compute_routing.py`

Then run:

```bash
python runner/run.py \
  --package packages/compute_routing \
  --input examples/tohoku_compute_routing.yaml \
  --runs 2000 \
  --seed 42 \
  --output receipts/tohoku_compute_routing.json \
  --validate-scenarios
```

And regression test:

```bash
python -m unittest tests/test_compute_routing.py -v
```

## README insertion suggestion

Add `compute_routing` under `packages/` and `tohoku_compute_routing.yaml` under `examples/` in the repository tree. In "Current scope", move the Capacity Router line only after a real capacity connector exists; this addition is intentionally a simulation Package, not the live Router itself.

Suggested paragraph:

> `packages/compute_routing` explores the boundary immediately before a live Capacity Router. It classifies declared compute and infrastructure conditions into ROUTE / SHIFT / LOCAL / HOLD while keeping synthetic node ranges distinct from live telemetry. The bundled Tohoku example is a scenario-only PoC; replace its capacity, price, latency, renewable, and thermal ranges with Evidence Pack observations before operational use.
