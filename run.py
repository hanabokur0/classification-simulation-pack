#!/usr/bin/env python3
"""Reference runner for Classification Simulation Pack v0.1.0.

Usage:
    python runner/run.py \
      --package packages/business_plan \
      --input examples/this_project.yaml \
      --runs 2000 \
      --seed 42 \
      --output receipts/this_project.json

The runner is intentionally thin. Domain logic belongs in taxonomy.yaml and
evaluator.yaml, not in this file.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import random
import re
import statistics
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import yaml
except ImportError as exc:
    raise SystemExit("Install PyYAML: python -m pip install pyyaml") from exc

try:
    import jsonschema
except ImportError:
    jsonschema = None


RUNNER_VERSION = "0.1.0"
MISSING = object()


class RunnerError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Files and validation
# ---------------------------------------------------------------------------


def load(path: Path) -> Any:
    if not path.exists():
        raise RunnerError(f"File not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh) if path.suffix.lower() == ".json" else yaml.safe_load(fh)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise RunnerError(f"Could not parse {path}: {exc}") from exc


def digest(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def validate(instance: Any, schema_path: Path, label: str) -> None:
    if not schema_path.exists():
        return
    if jsonschema is None:
        raise RunnerError(
            f"{schema_path} exists but jsonschema is missing. "
            "Install it: python -m pip install jsonschema"
        )
    schema = load(schema_path)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    errors = sorted(validator_cls(schema).iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        lines = []
        for error in errors[:20]:
            location = ".".join(map(str, error.absolute_path)) or "<root>"
            lines.append(f"  - {location}: {error.message}")
        raise RunnerError(f"{label} failed schema validation:\n" + "\n".join(lines))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, allow_nan=False)
        fh.write("\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Restricted expression evaluator
# ---------------------------------------------------------------------------


class SafeEval:
    """Small expression language for derived_metrics; never calls eval/exec."""

    BIN = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a**b,
    }
    UNARY = {ast.UAdd: lambda x: +x, ast.USub: lambda x: -x, ast.Not: lambda x: not x}
    CMP = {
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }
    FUNCS = {
        "abs": abs,
        "min": min,
        "max": max,
        "round": round,
        "int": int,
        "float": float,
        "ceil": math.ceil,
        "floor": math.floor,
        "sqrt": math.sqrt,
    }
    CONST = {"null": None, "none": None, "true": True, "false": False}

    def __call__(self, expression: str, context: Mapping[str, Any]) -> Any:
        if not isinstance(expression, str) or not expression.strip():
            raise RunnerError("Derived expression must be a non-empty string")
        normalized = re.sub(r"\bif\s*\(", "if_(", expression.strip())
        try:
            tree = ast.parse(normalized, mode="eval")
        except SyntaxError as exc:
            raise RunnerError(f"Invalid expression {expression!r}: {exc.msg}") from exc
        if sum(1 for _ in ast.walk(tree)) > 200:
            raise RunnerError("Expression is too complex")
        return self._node(tree.body, context)

    def _node(self, node: ast.AST, context: Mapping[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id.lower() in self.CONST:
                return self.CONST[node.id.lower()]
            if node.id in context:
                return context[node.id]
            raise RunnerError(f"Unknown name in expression: {node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in self.BIN:
            left, right = self._node(node.left, context), self._node(node.right, context)
            if isinstance(node.op, ast.Pow) and abs(float(right)) > 12:
                raise RunnerError("Exponent outside allowed range")
            return self.BIN[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in self.UNARY:
            return self.UNARY[type(node.op)](self._node(node.operand, context))
        if isinstance(node, ast.BoolOp):
            values = node.values
            if isinstance(node.op, ast.And):
                result: Any = True
                for value in values:
                    result = self._node(value, context)
                    if not result:
                        return result
                return result
            if isinstance(node.op, ast.Or):
                result = False
                for value in values:
                    result = self._node(value, context)
                    if result:
                        return result
                return result
        if isinstance(node, ast.Compare):
            left = self._node(node.left, context)
            for op, item in zip(node.ops, node.comparators):
                right = self._node(item, context)
                if type(op) not in self.CMP or not self.CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            branch = node.body if self._node(node.test, context) else node.orelse
            return self._node(branch, context)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name == "if_":
                if len(node.args) != 3:
                    raise RunnerError("if(condition, then, else) requires 3 arguments")
                branch = node.args[1] if self._node(node.args[0], context) else node.args[2]
                return self._node(branch, context)
            if name in self.FUNCS:
                return self.FUNCS[name](*(self._node(arg, context) for arg in node.args))
        raise RunnerError(f"Expression element not allowed: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Sampling and rule matching
# ---------------------------------------------------------------------------


def number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunnerError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise RunnerError(f"{label} must be finite")
    return value


def sample(name: str, spec: Any, rng: random.Random) -> Any:
    if not isinstance(spec, Mapping):
        return spec
    if "value" in spec:
        return spec["value"]
    if "values" in spec:
        values = spec["values"]
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
            raise RunnerError(f"variables.{name}.values must be a non-empty list")
        weights = spec.get("weights")
        return rng.choices(list(values), weights=weights, k=1)[0] if weights else rng.choice(list(values))

    kind = str(spec.get("distribution", "constant")).lower()
    if kind in {"constant", "fixed"} and "default" in spec:
        return spec["default"]
    if kind == "uniform":
        return rng.uniform(number(spec.get("min"), f"{name}.min"), number(spec.get("max"), f"{name}.max"))
    if kind in {"integer_uniform", "int_uniform"}:
        return rng.randint(int(number(spec.get("min"), f"{name}.min")), int(number(spec.get("max"), f"{name}.max")))
    if kind == "normal":
        value = rng.gauss(number(spec.get("mean"), f"{name}.mean"), number(spec.get("stddev", spec.get("sigma")), f"{name}.stddev"))
    elif kind == "lognormal":
        sigma = number(spec.get("sigma"), f"{name}.sigma")
        mu = number(spec["mu"], f"{name}.mu") if "mu" in spec else math.log(number(spec.get("median"), f"{name}.median"))
        value = rng.lognormvariate(mu, sigma)
    elif kind in {"boolean", "bernoulli"}:
        probability = number(spec.get("probability", 0.5), f"{name}.probability")
        if not 0 <= probability <= 1:
            raise RunnerError(f"{name}.probability must be between 0 and 1")
        return rng.random() < probability
    else:
        raise RunnerError(f"Unsupported distribution for {name}: {kind}")

    if "min" in spec:
        value = max(value, number(spec["min"], f"{name}.min"))
    if "max" in spec:
        value = min(value, number(spec["max"], f"{name}.max"))
    return value


def get(context: Mapping[str, Any], path: str) -> Any:
    if path in context:
        return context[path]
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return MISSING
        current = current[part]
    return current


def missing(value: Any) -> bool:
    return value is MISSING or value is None


def compare(actual: Any, tests: Any) -> bool:
    if not isinstance(tests, Mapping):
        return actual is not MISSING and actual == tests
    for op, expected in tests.items():
        if op == "exists":
            if (actual is not MISSING) != bool(expected):
                return False
            continue
        if op == "missing":
            if missing(actual) != bool(expected):
                return False
            continue
        if actual is MISSING:
            return False
        try:
            checks = {
                "lt": lambda: actual < expected,
                "lte": lambda: actual <= expected,
                "gt": lambda: actual > expected,
                "gte": lambda: actual >= expected,
                "eq": lambda: actual == expected,
                "ne": lambda: actual != expected,
                "in": lambda: actual in expected,
                "not_in": lambda: actual not in expected,
                "contains": lambda: expected in actual,
                "between": lambda: expected[0] <= actual <= expected[1],
                "regex": lambda: re.search(str(expected), str(actual)) is not None,
            }
            if op not in checks:
                raise RunnerError(f"Unsupported rule operator: {op}")
            if not checks[op]():
                return False
        except (TypeError, IndexError):
            return False
    return True


def blocks(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [{key: item} for key, item in value.items()]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    raise RunnerError("all/any must contain a mapping or list")


def matches(condition: Any, context: Mapping[str, Any]) -> bool:
    if condition is True:
        return True
    if not isinstance(condition, Mapping):
        return False
    results = []
    for key, value in condition.items():
        if key == "default":
            results.append(bool(value))
        elif key == "all":
            results.append(all(matches(item, context) for item in blocks(value)))
        elif key == "any":
            results.append(any(matches(item, context) for item in blocks(value)))
        elif key == "not":
            results.append(not matches(value, context))
        elif key == "any_missing":
            results.append(any(missing(get(context, str(path))) for path in value))
        elif key == "all_missing":
            results.append(all(missing(get(context, str(path))) for path in value))
        elif key == "none_missing":
            results.append(all(not missing(get(context, str(path))) for path in value))
        else:
            results.append(compare(get(context, key), value))
    return all(results)


def derived_values(evaluator: Mapping[str, Any], context: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    result: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    safe_eval = SafeEval()
    for name, definition in (evaluator.get("derived_metrics") or {}).items():
        expression = definition if isinstance(definition, str) else definition.get("expression")
        try:
            value = safe_eval(str(expression), {**context, **result})
            if isinstance(value, float) and not math.isfinite(value):
                raise RunnerError("result is not finite")
            result[str(name)] = value
        except Exception as exc:
            result[str(name)] = None
            errors.append({"code": "DERIVED_METRIC_ERROR", "path": str(name), "message": str(exc)})
    return result, errors


def classify(evaluator: Mapping[str, Any], class_ids: set[str], fallback: str, context: Mapping[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    required = [str(path) for path in evaluator.get("required_inputs", [])]
    absent = [path for path in required if missing(get(context, path))]
    on_error = evaluator.get("on_evaluation_error") or {}
    error_class = str(on_error.get("classify_as", fallback))
    if error_class not in class_ids:
        raise RunnerError(f"Unknown on_evaluation_error class: {error_class}")
    if absent:
        return {"status": "held", "class_id": error_class, "rule_id": None, "holds": [{"code": "MISSING_REQUIRED_INPUT", "paths": absent}]}
    if errors:
        return {"status": "held", "class_id": error_class, "rule_id": None, "holds": errors}

    rules = sorted(evaluator.get("rules") or [], key=lambda rule: int(rule.get("priority", 0)), reverse=True)
    for rule in rules:
        class_id = str(rule.get("classify_as"))
        if class_id not in class_ids:
            raise RunnerError(f"Rule {rule.get('id')} references unknown class: {class_id}")
        if matches(rule.get("when", {}), context):
            holds = []
            if rule.get("hold"):
                hold = rule["hold"] if isinstance(rule["hold"], Mapping) else {"code": str(rule["hold"])}
                holds.append(dict(hold))
            return {
                "status": "held" if holds else "classified",
                "class_id": class_id,
                "rule_id": str(rule.get("id")),
                "holds": holds,
            }
    return {"status": "held", "class_id": fallback, "rule_id": None, "holds": [{"code": "NO_RULE_MATCH"}]}


# ---------------------------------------------------------------------------
# Simulation and Receipt
# ---------------------------------------------------------------------------


def package_contract(taxonomy: Mapping[str, Any], evaluator: Mapping[str, Any]) -> tuple[set[str], str]:
    classes = taxonomy.get("classes") or []
    class_ids = [str(item.get("id")) for item in classes if isinstance(item, Mapping) and item.get("id")]
    if not class_ids or len(class_ids) != len(classes) or len(class_ids) != len(set(class_ids)):
        raise RunnerError("taxonomy.classes must contain unique ids")
    fallback = str(taxonomy.get("fallback_class", "insufficient_context"))
    if fallback not in class_ids:
        raise RunnerError("taxonomy.fallback_class must exist in taxonomy.classes")
    if str(evaluator.get("resolution", {}).get("strategy", "first_match")) != "first_match":
        raise RunnerError("Only resolution.strategy=first_match is supported")
    return set(class_ids), fallback


def run(input_data: Mapping[str, Any], taxonomy: Mapping[str, Any], evaluator: Mapping[str, Any], runs: int, seed: int) -> list[dict[str, Any]]:
    variables = input_data.get("variables") or {}
    constants = input_data.get("constants") or {}
    base_context = input_data.get("context") or {}
    if not all(isinstance(item, Mapping) for item in (variables, constants, base_context)):
        raise RunnerError("variables, constants, and context must be mappings")
    class_ids, fallback = package_contract(taxonomy, evaluator)
    rng = random.Random(seed)
    scenarios = []
    for index in range(runs):
        sampled = {str(name): sample(str(name), spec, rng) for name, spec in variables.items()}
        context = {**base_context, **constants, **sampled}
        derived, errors = derived_values(evaluator, context)
        classification = classify(evaluator, class_ids, fallback, {**context, **derived}, errors)
        scenarios.append({
            "scenario_id": f"scenario-{index:06d}",
            "run_index": index,
            "sampled_values": sampled,
            "derived_values": derived,
            "classification": classification,
        })
    return scenarios


def numeric_summary(values: list[Any]) -> dict[str, Any] | None:
    values = sorted(float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)))
    if not values:
        return None
    return {"count": len(values), "min": values[0], "mean": statistics.fmean(values), "median": statistics.median(values), "max": values[-1]}


def receipt(input_data: Mapping[str, Any], taxonomy: Mapping[str, Any], evaluator: Mapping[str, Any], scenarios: list[dict[str, Any]], runs: int, seed: int) -> dict[str, Any]:
    class_ids, _ = package_contract(taxonomy, evaluator)
    counts = Counter(item["classification"]["class_id"] for item in scenarios)
    metrics = input_data.get("metrics") or list((evaluator.get("derived_metrics") or {}).keys())
    summaries = {}
    for name in metrics:
        summary = numeric_summary([item["derived_values"].get(str(name)) for item in scenarios])
        if summary:
            summaries[str(name)] = summary
    hold_counts = Counter(
        hold.get("code", "UNKNOWN_HOLD")
        for item in scenarios
        for hold in item["classification"].get("holds", [])
    )
    package_id = str(evaluator.get("package_id") or taxonomy.get("package_id") or input_data.get("package", {}).get("id") or "unknown")
    version = str(evaluator.get("version") or taxonomy.get("version") or "0.0.0")
    return {
        "receipt_id": f"sim-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "question": str(input_data.get("question", "")),
        "package": {"id": package_id, "version": version},
        "simulation": {
            "requested_runs": runs,
            "completed_runs": len(scenarios),
            "seed": seed,
            "evidence_level": str(input_data.get("simulation", {}).get("evidence_level", "synthetic_simulation")),
        },
        "distribution": {class_id: {"count": counts[class_id], "ratio": counts[class_id] / len(scenarios)} for class_id in sorted(class_ids)},
        "metric_summaries": summaries,
        "holds": [{"code": code, "count": count} for code, count in hold_counts.most_common()],
        "provenance": {
            "runner_version": RUNNER_VERSION,
            "input_digest": digest(input_data),
            "taxonomy_digest": digest(taxonomy),
            "evaluator_digest": digest(evaluator),
        },
    }


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a classification simulation package")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenarios-output", type=Path)
    parser.add_argument("--schema-dir", type=Path)
    parser.add_argument("--validate-scenarios", action="store_true")
    return parser.parse_args()


def main() -> int:
    options = args()
    try:
        package_dir = options.package.resolve()
        input_data = load(options.input.resolve())
        taxonomy = load(package_dir / "taxonomy.yaml")
        evaluator = load(package_dir / "evaluator.yaml")
        if not all(isinstance(item, Mapping) for item in (input_data, taxonomy, evaluator)):
            raise RunnerError("Input, taxonomy, and evaluator roots must be mappings")

        schema_dir = (options.schema_dir or Path(__file__).resolve().parent.parent / "schemas").resolve()
        validate(input_data, schema_dir / "package.schema.json", "Input package")
        simulation = input_data.get("simulation") or {}
        runs = options.runs if options.runs is not None else int(simulation.get("runs", 1000))
        seed = options.seed if options.seed is not None else int(simulation.get("seed", 42))
        if not 1 <= runs <= 1_000_000:
            raise RunnerError("runs must be between 1 and 1,000,000")

        scenarios = run(input_data, taxonomy, evaluator, runs, seed)
        if options.validate_scenarios:
            for item in scenarios:
                validate(item, schema_dir / "scenario.schema.json", item["scenario_id"])
        output = receipt(input_data, taxonomy, evaluator, scenarios, runs, seed)
        validate(output, schema_dir / "receipt.schema.json", "Receipt")
        write_json(options.output.resolve(), output)

        if options.scenarios_output:
            path = options.scenarios_output.resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                for item in scenarios:
                    fh.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")

        print(json.dumps({"status": "completed", "runs": runs, "seed": seed, "receipt": str(options.output.resolve())}, ensure_ascii=False))
        return 0
    except (RunnerError, jsonschema.SchemaError if jsonschema else RunnerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
