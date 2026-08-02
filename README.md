# Classification Simulation Pack v0.5.0

> **More verified input does not add points. It calibrates the worlds being simulated.**

Classification Simulation Pack turns a plan or workflow into a YAML workload, samples its variable conditions, classifies many possible worlds, and returns an auditable Receipt.

## v0.5.0: four fixed transport contracts

```text
Natural language, logs, documents, measurements
        ↓
Evidence Pack
        ↓
Simulation Package
        ↓
Workload Manifest
        ↓
Runner / spare compute
        ↓
Simulation Receipt
```

The four primary schemas are:

- `schemas/evidence-pack.schema.json`
- `schemas/simulation-package.schema.json`
- `schemas/workload-manifest.schema.json`
- `schemas/simulation-receipt.schema.json`

`package.schema.json` and `receipt.schema.json` remain compatibility aliases used by the reference CLI.

## What “more information improves precision” means

A Package begins with declared ranges. Evidence may calibrate those ranges or replace them with empirical samples.

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

With enough numeric evidence, the runner derives an effective range and records both the declared and effective ranges in the Receipt. Without enough evidence, it keeps the broad declared range and emits a warning. It never silently invents a precise value.

Supported calibration methods:

- `observed_range`: observed minimum/maximum plus declared padding;
- `mean_std`: a bounded normal distribution from evidence mean and standard deviation;
- `empirical`: resampling from observed values; and
- `none`: no automatic calibration.

## Compare sparse and calibrated Packages

Calibrated example:

```bash
python runner/run.py \
  --package packages/automation_workflow \
  --input examples/data_entry_automation.yaml \
  --output receipts/data_entry_automation.sample.json \
  --validate-scenarios
```

Sparse-evidence comparison:

```bash
python runner/run.py \
  --package packages/automation_workflow \
  --input examples/data_entry_automation_sparse.yaml \
  --output receipts/data_entry_automation_sparse.sample.json \
  --validate-scenarios
```

The calibrated Receipt should show narrower effective ranges, higher evidence coverage/reliability, and higher model-confidence proxy. The result does not automatically become more favorable; it becomes better constrained.

## Data-quality Receipt

Every Receipt now separates:

```yaml
data_quality:
  readiness_status: ready | exploratory | held
  evidence_coverage: 0.0
  evidence_reliability: 0.0
  variable_calibration: 0.0
  model_confidence: 0.0
  mean_range_reduction: 0.0
  unresolved_variables: []
  calibration_report: {}
```

These are transparent routing proxies, not claims of real-world predictive accuracy.

## Workload Manifest

```yaml
workload:
  id: automation-workflow-monte-carlo
  version: 0.1.0
  estimated_runs:
    min: 200
    target: 2000
    max: 20000
  resource:
    cpu: preferred
    gpu: optional
    memory_gb: 4
  execution:
    interruptible: true
    checkpointable: true
    resumable: true
    divisible: true
  expansion:
    allowed: true
    max_depth: 4
    stop_when_range_reduction_below: 0.01
```

The CLI enforces the declared run range. A future Capacity Router can select any run count inside it according to available compute.

## Existing v0.4.0 operation layer

The automation workflow Package remains intact and returns:

- AUTO / REVIEW / ESCALATE / HOLD / MANUAL work allocation;
- whole-scenario operating-mode distribution;
- bottleneck and guarded PoC recommendation;
- reusable failure structures;
- transfer-domain candidates; and
- next-Package questions.

## Repository structure

```text
classification-simulation-pack-v0.5.0/
├─ README.md
├─ CHANGELOG.md
├─ schemas/
│  ├─ evidence-pack.schema.json
│  ├─ simulation-package.schema.json
│  ├─ workload-manifest.schema.json
│  ├─ simulation-receipt.schema.json
│  ├─ package.schema.json
│  ├─ receipt.schema.json
│  └─ scenario.schema.json
├─ packages/
│  ├─ business_plan/
│  └─ automation_workflow/
├─ examples/
│  ├─ this_project.yaml
│  ├─ data_entry_automation.yaml
│  └─ data_entry_automation_sparse.yaml
├─ runner/run.py
├─ receipts/
├─ docs/contracts.md
└─ tests/test_runner.py
```

## Quick start

Requires Python 3.11 or later.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## Safety boundary

v0.5.0 does not prove production accuracy, savings, safety, or civilizational value. It does not connect to live business systems or spare data-center capacity. It provides the packaging, calibration, workload, and Receipt contracts needed to test those connections without hiding assumptions.
