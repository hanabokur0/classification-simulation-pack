# Classification Simulation Pack

> Turn an open-ended plan into repeatable scenarios, classify the outcomes, and return a machine-readable Receipt.

Classification Simulation Pack is a small, AI-readable framework for testing plans, projects, policies, products, and other decisions through repeated scenario generation and deterministic classification.

The core is intentionally compact:

```text
Question
→ Variables
→ Scenario generation
→ Classification
→ Aggregation
→ Receipt
```

The repository does not attempt to predict the future. It helps users observe **which conditions lead to which classes of outcome**, which assumptions matter most, and what must be measured next.

---

## Why this exists

Many plans are discussed as a single story:

> “Will this business work?”

That question is usually too large to answer directly. A more useful approach is to vary the assumptions, run many scenarios, and classify the results.

For example:

```text
viable
viable_if_narrowed
sales_bottleneck
delivery_bottleneck
cashflow_failure
insufficient_context
```

A result is not treated as a prophecy. It is treated as a structured observation produced under declared assumptions.

Classification Simulation Pack makes that process portable by separating:

* the question being tested;
* the variables that may change;
* the scenario-generation rules;
* the allowed classifications;
* the evaluator that assigns a class;
* the Receipt that records what happened.

---

## AI-first interface

This repository is designed to be read by an AI assistant as well as by a human.

A user should not need to learn the YAML format before using the project. The intended workflow is:

```text
User describes a plan in natural language
→ AI reads this repository
→ AI selects or creates a package
→ AI converts the plan into YAML
→ Runner validates and executes it
→ AI explains the resulting Receipt
```

Example request:

> Read this repository. Convert my plan into a simulation input, run 2,000 scenarios, classify the outcomes using the business plan package, and explain the Receipt without presenting the result as a forecast.

The YAML and JSON files are the common language between the AI, the runner, and other tools. They are not intended to become a mandatory user interface.

---

## Repository structure

```text
classification-simulation-pack/
├─ README.md
├─ schemas/
│  ├─ package.schema.json
│  ├─ scenario.schema.json
│  └─ receipt.schema.json
├─ packages/
│  └─ business_plan/
│     ├─ taxonomy.yaml
│     └─ evaluator.yaml
├─ examples/
│  └─ this_project.yaml
├─ runner/
│  └─ run.py
└─ receipts/
   └─ this_project.sample.json
```

### `schemas/`

Defines the machine-readable contracts.

* `package.schema.json` validates a simulation package or input definition.
* `scenario.schema.json` validates one generated scenario.
* `receipt.schema.json` validates the final simulation Receipt.

### `packages/`

Contains reusable classification packages for a domain.

The first package, `business_plan`, defines:

* the allowed outcome classes in `taxonomy.yaml`;
* the ordered classification rules in `evaluator.yaml`.

A package should describe **how to classify**, not claim to know the future.

### `examples/`

Contains complete, runnable examples.

`this_project.yaml` uses Classification Simulation Pack to test the plan for Classification Simulation Pack itself.

### `runner/`

Contains the reference execution engine.

The runner should remain thin. Its responsibilities are:

1. load YAML and JSON;
2. validate inputs;
3. generate reproducible scenarios;
4. calculate derived values;
5. apply evaluator rules;
6. aggregate the classifications;
7. write a Receipt.

### `receipts/`

Contains example outputs and locally generated results.

A Receipt records the assumptions, seed, run count, classification distribution, decisive variables, unresolved holds, provenance, and evidence level.

---

## Core concepts

### Package

A reusable definition of a simulation domain.

A package provides a taxonomy and evaluator. Future packages may cover pricing, project risk, workflow automation, publishing, investment stress, disaster recovery, or policy scenarios.

### Scenario

One sampled or explicitly enumerated combination of conditions.

A scenario must be independently identifiable and reproducible from its input, package version, and random seed.

### Taxonomy

The finite set of allowed outcome classes.

Example:

```yaml
classes:
  - id: viable
    description: The plan is viable under the sampled conditions.

  - id: viable_if_narrowed
    description: The plan may be viable if its scope or target is reduced.

  - id: sales_bottleneck
    description: Customer acquisition is the dominant constraint.

  - id: delivery_bottleneck
    description: Delivery capacity is the dominant constraint.

  - id: cashflow_failure
    description: The plan fails under the declared cash-flow conditions.

  - id: insufficient_context
    description: The available information does not support classification.
```

### Evaluator

An ordered set of deterministic classification rules.

Higher-priority rules are evaluated first. The first matching rule assigns the scenario class unless the package explicitly defines another resolution strategy.

Example:

```yaml
rules:
  - id: insufficient-context
    priority: 100
    when:
      any_missing:
        - monthly_price
        - monthly_fixed_cost
        - delivery_cost_per_customer
    classify_as: insufficient_context

  - id: cashflow-failure
    priority: 90
    when:
      monthly_profit:
        lt: 0
    classify_as: cashflow_failure

  - id: viable
    priority: 50
    when:
      all:
        monthly_profit:
          gte: 300000
        monthly_churn:
          lte: 0.05
    classify_as: viable

  - id: viable-if-narrowed
    priority: 10
    when:
      default: true
    classify_as: viable_if_narrowed
```

### Receipt

A Receipt is the evidence-bearing output of a run.

It should answer:

* What question was tested?
* Which package and version were used?
* Which assumptions and distributions were declared?
* How many scenarios were run?
* Which seed was used?
* How were outcomes distributed?
* Which variables most often changed the classification?
* Which cases remained unresolved?
* Was the result produced by simulation, live observation, or another evidence level?

---

## Quick start

### Requirements

* Python 3.11 or later
* PyYAML
* jsonschema

Install the minimal dependencies:

```bash
python -m pip install pyyaml jsonschema
```

Run the included example:

```bash
python runner/run.py \
  --package packages/business_plan \
  --input examples/this_project.yaml \
  --runs 2000 \
  --seed 42 \
  --output receipts/this_project.json
```

Validate the generated Receipt against `schemas/receipt.schema.json` before treating the run as complete.

The exact command-line interface may evolve during the first implementation, but the package, scenario, and Receipt contracts should remain stable and versioned.

---

## Example input

A simulation input may look like this:

```yaml
id: this-project
question: >
  Can Classification Simulation Pack become a useful open-source project
  and produce a credible path toward paid extensions?

simulation:
  runs: 2000
  seed: 42

variables:
  annual_effective_visitors:
    distribution: lognormal
    median: 900
    sigma: 0.8

  execution_rate:
    distribution: uniform
    min: 0.01
    max: 0.05

  external_receipt_rate:
    distribution: uniform
    min: 0.00
    max: 0.30

  paid_conversion_rate:
    distribution: uniform
    min: 0.00
    max: 0.08

  average_paid_value:
    values:
      - 0
      - 50000
      - 150000
      - 300000

package:
  id: business_plan
  taxonomy: packages/business_plan/taxonomy.yaml
  evaluator: packages/business_plan/evaluator.yaml
```

The example should contain assumptions that are visible and editable. Hidden assumptions reduce the value of the Receipt.

---

## Example Receipt

```json
{
  "receipt_id": "sim-20260731-this-project-001",
  "question": "Can Classification Simulation Pack become a useful open-source project and produce a credible path toward paid extensions?",
  "package": {
    "id": "business_plan",
    "version": "0.1.0"
  },
  "simulation": {
    "runs": 2000,
    "seed": 42,
    "evidence_level": "synthetic_simulation"
  },
  "distribution": {
    "viable": 0.182,
    "viable_if_narrowed": 0.361,
    "sales_bottleneck": 0.214,
    "delivery_bottleneck": 0.096,
    "cashflow_failure": 0.081,
    "insufficient_context": 0.066
  },
  "decisive_variables": [
    "external_receipt_rate",
    "execution_rate",
    "paid_conversion_rate"
  ],
  "holds": [
    {
      "code": "NO_EXTERNAL_USAGE_DATA",
      "message": "The model has not yet been calibrated with external user behavior."
    }
  ],
  "provenance": {
    "input_digest": "sha256:...",
    "taxonomy_digest": "sha256:...",
    "evaluator_digest": "sha256:...",
    "runner_version": "0.1.0"
  }
}
```

Numbers in a sample Receipt are illustrative unless the corresponding input and runner execution are included.

---

## Design principles

### 1. Classification before prediction

The project classifies outcomes under declared conditions. It does not claim that repeated model output becomes a real-world probability.

### 2. Assumptions must remain visible

Every distribution, threshold, formula, and fallback must be inspectable and versioned.

### 3. Deterministic evaluation first

The reference evaluator should prefer explicit rules. LLMs may help generate scenarios, propose classifications, or explain Receipts, but they should not silently replace declared evaluation logic.

### 4. Missing information is a valid result

`insufficient_context` and explicit holds are first-class outcomes. The runner must not invent missing facts merely to complete a classification.

### 5. Reproducibility matters

A run should record its seed, package version, schema version, input digest, evaluator digest, and runner version.

### 6. Simulation and observation are different evidence levels

A synthetic simulation Receipt must not be presented as a live measurement. Future implementations may support evidence levels such as:

```text
synthetic_simulation
historical_replay
controlled_experiment
live_observation
live_drill
```

### 7. The core stays small

New domains should normally be added as packages, examples, or adapters rather than by expanding the runner into a large application.

---

## Creating a new package

To add a package:

1. create a directory under `packages/`;
2. define a finite taxonomy;
3. define ordered evaluator rules;
4. add at least one valid example input;
5. add at least one expected Receipt;
6. test missing data, overlapping rules, boundary values, and reproducibility;
7. document what the package can and cannot infer.

Suggested future package layout:

```text
packages/
├─ business_plan/
├─ pricing/
├─ project_risk/
├─ workflow_automation/
├─ content_release/
├─ investment_stress/
└─ workload_evacuation/
```

A package should be useful on its own and understandable without modifying the core runner.

---

## What this project is not

Classification Simulation Pack is not:

* a guarantee of business success;
* a substitute for market research or live experiments;
* a financial adviser;
* an automatic source of statistically independent observations;
* a reason to run the same LLM prompt thousands of times and call the output a probability;
* a replacement for domain expertise, legal review, safety review, or operational testing.

Its purpose is narrower and more practical:

> Convert a vague plan into explicit assumptions, repeatable scenarios, finite outcome classes, and an auditable Receipt.

---

## Initial milestone

The first usable release is complete when another person or AI can:

1. read this repository;
2. convert a natural-language plan into a valid input file;
3. run 2,000 reproducible scenarios;
4. classify every scenario or produce an explicit hold;
5. generate a Receipt that passes schema validation;
6. explain the result without presenting it as a forecast.

The first external success signal is not a star count. It is an independently generated Receipt using a taxonomy or input created outside this repository.
