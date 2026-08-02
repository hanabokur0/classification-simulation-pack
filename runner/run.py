#!/usr/bin/env python3
"""Reference runner for Classification Simulation Pack v0.5.0.

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
import copy
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


RUNNER_VERSION = "0.5.0"
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


CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4, "insufficient": 0.1}
FRESHNESS_WEIGHT = {"current": 1.0, "aging": 0.8, "stale": 0.4, "unknown": 0.6}
EVIDENCE_TYPE_WEIGHT = {
    "observed": 1.0,
    "reported": 0.8,
    "public": 0.8,
    "inferred": 0.5,
    "assumed": 0.25,
}


def numeric_evidence_values(value: Any) -> list[float]:
    """Flatten finite numeric evidence values without treating booleans as numbers."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        candidate = float(value)
        return [candidate] if math.isfinite(candidate) else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result: list[float] = []
        for item in value:
            result.extend(numeric_evidence_values(item))
        return result
    if isinstance(value, Mapping):
        for key in ("values", "samples", "observations"):
            if key in value:
                return numeric_evidence_values(value[key])
    return []


def evidence_index(input_data: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    pack = input_data.get("evidence_pack") or {}
    items = pack.get("items") if isinstance(pack, Mapping) else []
    result: dict[str, Mapping[str, Any]] = {}
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
        for item in items:
            if isinstance(item, Mapping) and item.get("id"):
                result[str(item["id"])] = item
    return result


def _bounds(spec: Mapping[str, Any]) -> dict[str, float] | None:
    if "min" not in spec or "max" not in spec:
        return None
    try:
        low = number(spec["min"], "variable.min")
        high = number(spec["max"], "variable.max")
    except RunnerError:
        return None
    return {"min": low, "max": high, "width": max(0.0, high - low)}


def _metadata(spec: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "label",
        "description",
        "evidence_refs",
        "confidence",
        "missing_policy",
        "calibration",
    )
    return {key: copy.deepcopy(spec[key]) for key in keys if key in spec}


def calibrate_variable(
    name: str,
    spec: Any,
    index: Mapping[str, Mapping[str, Any]],
) -> tuple[Any, dict[str, Any]]:
    """Return an effective variable spec and an auditable calibration report."""
    if not isinstance(spec, Mapping):
        return spec, {
            "status": "not_requested",
            "method": "none",
            "evidence_refs": [],
            "evidence_count": 0,
            "confidence": "insufficient",
            "missing_policy": "continue_with_warning",
        }

    calibration = spec.get("calibration")
    refs = []
    if isinstance(calibration, Mapping):
        refs = calibration.get("evidence_refs") or spec.get("evidence_refs") or []
    else:
        refs = spec.get("evidence_refs") or []
    refs = [str(ref) for ref in refs]
    missing_policy = str(spec.get("missing_policy", "continue_with_warning"))
    confidence = str(spec.get("confidence", "insufficient"))
    declared_bounds = _bounds(spec)

    if not isinstance(calibration, Mapping) or str(calibration.get("method", "none")) == "none":
        return copy.deepcopy(spec), {
            "status": "not_requested",
            "method": "none",
            "evidence_refs": refs,
            "evidence_count": sum(1 for ref in refs if ref in index),
            "confidence": confidence,
            "missing_policy": missing_policy,
            "declared_bounds": declared_bounds,
            "effective_bounds": declared_bounds,
            "range_reduction_ratio": 0.0,
        }

    method = str(calibration.get("method"))
    min_samples = int(calibration.get("min_samples", 2))
    values: list[float] = []
    used_refs: list[str] = []
    for ref in refs:
        item = index.get(ref)
        if not item:
            continue
        extracted = numeric_evidence_values(item.get("value"))
        if extracted:
            values.extend(extracted)
            used_refs.append(ref)

    report: dict[str, Any] = {
        "status": "insufficient",
        "method": method,
        "evidence_refs": refs,
        "used_evidence_refs": used_refs,
        "evidence_count": len(values),
        "confidence": confidence,
        "missing_policy": missing_policy,
        "declared_bounds": declared_bounds,
        "effective_bounds": declared_bounds,
        "range_reduction_ratio": 0.0,
    }
    if len(values) < min_samples:
        report["warning"] = (
            f"{name} needs {min_samples} numeric evidence samples; {len(values)} were available."
        )
        return copy.deepcopy(spec), report

    effective = _metadata(spec)
    original_kind = str(spec.get("distribution", "constant")).lower()
    padding_ratio = float(calibration.get("padding_ratio", 0.10))

    if method == "empirical":
        if original_kind in {"integer_uniform", "int_uniform"}:
            empirical_values: list[Any] = [int(round(value)) for value in values]
        else:
            empirical_values = values
        effective["values"] = empirical_values
    elif method == "observed_range":
        low, high = min(values), max(values)
        span = high - low
        padding = span * padding_ratio
        if span == 0:
            padding = max(abs(low) * padding_ratio, 1.0 if original_kind in {"integer_uniform", "int_uniform"} else 1e-6)
        low -= padding
        high += padding
        if declared_bounds:
            low = max(low, declared_bounds["min"])
            high = min(high, declared_bounds["max"])
        if low > high:
            report["warning"] = f"{name} evidence range does not intersect the declared range."
            return copy.deepcopy(spec), report
        if original_kind in {"integer_uniform", "int_uniform"}:
            effective["distribution"] = "integer_uniform"
            effective["min"] = math.floor(low)
            effective["max"] = math.ceil(high)
        else:
            effective["distribution"] = "uniform"
            effective["min"] = low
            effective["max"] = high
    elif method == "mean_std":
        mean = statistics.fmean(values)
        stddev = statistics.stdev(values) if len(values) > 1 else 0.0
        if stddev <= 0:
            stddev = max(abs(mean) * padding_ratio, 1e-6)
        multiplier = float(calibration.get("stddev_multiplier", 2.0))
        low, high = mean - multiplier * stddev, mean + multiplier * stddev
        if declared_bounds:
            low = max(low, declared_bounds["min"])
            high = min(high, declared_bounds["max"])
        effective["distribution"] = "normal"
        effective["mean"] = mean
        effective["stddev"] = stddev
        effective["min"] = low
        effective["max"] = high
    else:
        report["status"] = "unsupported"
        report["warning"] = f"Unsupported calibration method for {name}: {method}"
        return copy.deepcopy(spec), report

    effective_bounds = _bounds(effective)
    reduction = 0.0
    if declared_bounds and effective_bounds and declared_bounds["width"] > 0:
        reduction = max(
            0.0,
            min(1.0, 1.0 - effective_bounds["width"] / declared_bounds["width"]),
        )
    report.update(
        {
            "status": "applied",
            "effective_bounds": effective_bounds,
            "range_reduction_ratio": round(reduction, 6),
            "effective_distribution": "values" if "values" in effective else effective.get("distribution"),
        }
    )
    return effective, report


def prepare_variables(input_data: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    variables = input_data.get("variables") or {}
    if not isinstance(variables, Mapping):
        raise RunnerError("variables must be a mapping")
    index = evidence_index(input_data)
    effective: dict[str, Any] = {}
    reports: dict[str, Any] = {}
    unresolved: list[str] = []
    warnings: list[str] = []
    for name, spec in variables.items():
        calibrated, report = calibrate_variable(str(name), spec, index)
        effective[str(name)] = calibrated
        reports[str(name)] = report
        if report.get("status") in {"insufficient", "unsupported"}:
            if report.get("missing_policy") == "hold":
                unresolved.append(str(name))
            if report.get("warning"):
                warnings.append(str(report["warning"]))
    return effective, {
        "variables": reports,
        "unresolved_variables": unresolved,
        "warnings": warnings,
    }


def evidence_quality(
    input_data: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    counts: Counter[str],
) -> dict[str, Any]:
    variables = input_data.get("variables") or {}
    index = evidence_index(input_data)
    _, calibration = prepare_variables(input_data)
    variable_reports = calibration["variables"]

    total_variables = len(variables) if isinstance(variables, Mapping) else 0
    referenced_variables = 0
    used_refs: set[str] = set()
    requested = 0
    applied = 0
    reductions: list[float] = []
    for name, spec in (variables.items() if isinstance(variables, Mapping) else []):
        if isinstance(spec, Mapping):
            refs = [str(ref) for ref in spec.get("evidence_refs") or []]
            valid = [ref for ref in refs if ref in index]
            if valid:
                referenced_variables += 1
                used_refs.update(valid)
            if isinstance(spec.get("calibration"), Mapping) and str(spec["calibration"].get("method", "none")) != "none":
                requested += 1
                report = variable_reports.get(str(name), {})
                if report.get("status") == "applied":
                    applied += 1
                    reductions.append(float(report.get("range_reduction_ratio", 0.0)))

    coverage = referenced_variables / total_variables if total_variables else 0.0
    qualities: list[float] = []
    for ref in used_refs:
        item = index[ref]
        qualities.append(
            CONFIDENCE_WEIGHT.get(str(item.get("confidence", "insufficient")), 0.1)
            * FRESHNESS_WEIGHT.get(str(item.get("freshness", "unknown")), 0.6)
            * EVIDENCE_TYPE_WEIGHT.get(str(item.get("type", "assumed")), 0.25)
        )
    reliability = statistics.fmean(qualities) if qualities else 0.0
    calibration_score = applied / requested if requested else 0.0
    outcome_stability = outcome_uncertainty_reduction(taxonomy, counts)
    model_confidence = (
        0.35 * coverage
        + 0.25 * reliability
        + 0.25 * calibration_score
        + 0.15 * outcome_stability
    )
    unresolved = calibration["unresolved_variables"]
    if unresolved:
        status = "held"
    elif model_confidence >= 0.65 and coverage >= 0.50 and calibration_score >= 0.50:
        status = "ready"
    else:
        status = "exploratory"

    return {
        "readiness_status": status,
        "evidence_count": len(index),
        "referenced_evidence_count": len(used_refs),
        "evidence_coverage": round(coverage, 6),
        "evidence_reliability": round(reliability, 6),
        "variable_calibration": round(calibration_score, 6),
        "model_confidence": round(max(0.0, min(1.0, model_confidence)), 6),
        "outcome_stability_proxy": outcome_stability,
        "mean_range_reduction": round(statistics.fmean(reductions), 6) if reductions else 0.0,
        "unresolved_variables": unresolved,
        "calibration_report": variable_reports,
        "warnings": calibration["warnings"],
        "interpretation": (
            "More evidence improves this Receipt only by calibrating variable ranges and distributions; it does not add points directly."
        ),
    }


def workload_result(input_data: Mapping[str, Any], selected_runs: int) -> dict[str, Any]:
    workload = input_data.get("workload") or {}
    estimates = workload.get("estimated_runs") if isinstance(workload, Mapping) else {}
    target = int((estimates or {}).get("target", input_data.get("simulation", {}).get("runs", selected_runs)))
    return {
        "manifest_id": str(workload.get("id", "legacy-workload")) if isinstance(workload, Mapping) else "legacy-workload",
        "manifest_version": str(workload.get("version", "0.0.0")) if isinstance(workload, Mapping) else "0.0.0",
        "target_runs": target,
        "selected_runs": selected_runs,
        "within_declared_range": bool(
            not estimates
            or int(estimates.get("min", 1)) <= selected_runs <= int(estimates.get("max", 1_000_000))
        ),
        "resource": copy.deepcopy(workload.get("resource", {})) if isinstance(workload, Mapping) else {},
        "execution": copy.deepcopy(workload.get("execution", {})) if isinstance(workload, Mapping) else {},
        "expansion": copy.deepcopy(workload.get("expansion", {})) if isinstance(workload, Mapping) else {},
    }


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
    variables, _ = prepare_variables(input_data)
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
    values = sorted(
        float(value)
        for value in values
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
    if not values:
        return None
    return {
        "count": len(values),
        "min": values[0],
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "max": values[-1],
    }


def localized(value: Any, language: str) -> str:
    if isinstance(value, Mapping):
        if language in value:
            return str(value[language])
        if "en" in value:
            return str(value["en"])
        if value:
            return str(next(iter(value.values())))
        return ""
    return str(value or "")


def taxonomy_index(taxonomy: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item["id"]): item
        for item in taxonomy.get("classes", [])
        if isinstance(item, Mapping) and item.get("id")
    }


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denominator = math.sqrt(sum(value * value for value in dx) * sum(value * value for value in dy))
    if denominator == 0:
        return None
    result = sum(a * b for a, b in zip(dx, dy)) / denominator
    return max(-1.0, min(1.0, result))


def decisive_variables(
    input_data: Mapping[str, Any],
    scenarios: list[dict[str, Any]],
    definitions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    scores = [
        float(definitions[item["classification"]["class_id"]].get("score", 0))
        for item in scenarios
    ]
    result = []
    for name in (input_data.get("variables") or {}).keys():
        values: list[float] = []
        aligned_scores: list[float] = []
        for item, score in zip(scenarios, scores):
            value = item["sampled_values"].get(str(name), MISSING)
            if isinstance(value, bool):
                values.append(float(int(value)))
                aligned_scores.append(score)
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                values.append(float(value))
                aligned_scores.append(score)
        corr = pearson(values, aligned_scores)
        if corr is not None:
            result.append(
                {
                    "variable": str(name),
                    "correlation": round(corr, 6),
                    "direction": "positive" if corr >= 0 else "negative",
                }
            )
    result.sort(key=lambda item: abs(item["correlation"]), reverse=True)
    return result[:5]


def build_funnel(
    input_data: Mapping[str, Any], counts: Counter[str], total: int
) -> list[dict[str, Any]]:
    result = []
    previous = 1.0
    for stage in input_data.get("funnel") or []:
        class_ids = [str(item) for item in stage.get("classes", [])]
        count = sum(counts[class_id] for class_id in class_ids)
        ratio = count / total
        result.append(
            {
                "id": str(stage["id"]),
                "label": str(stage["label"]),
                "count": count,
                "ratio": ratio,
                "drop_from_previous": max(0.0, previous - ratio),
            }
        )
        previous = ratio
    return result


def select_verdict(
    evaluator: Mapping[str, Any], aggregate_context: Mapping[str, Any], language: str
) -> dict[str, str]:
    interpretation = evaluator.get("interpretation") or {}
    verdicts = sorted(
        interpretation.get("verdicts") or [],
        key=lambda item: int(item.get("priority", 0)),
        reverse=True,
    )
    for item in verdicts:
        if matches(item.get("when", {}), aggregate_context):
            return {
                "code": str(item.get("id", "unclassified")),
                "label": localized(item.get("label"), language),
                "message": localized(item.get("message"), language),
            }
    return {
        "code": "unclassified",
        "label": "Unclassified" if language == "en" else "未分類",
        "message": "No interpretation rule matched." if language == "en" else "解釈ルールに一致しませんでした。",
    }


def outcome_uncertainty_reduction(
    taxonomy: Mapping[str, Any], counts: Counter[str]
) -> float:
    """Return a transparent 0..1 concentration proxy, not real-world certainty."""
    class_ids = [
        str(item["id"])
        for item in taxonomy.get("classes") or []
        if item.get("kind") != "hold"
    ]
    active_total = sum(counts[class_id] for class_id in class_ids)
    if active_total <= 0 or len(class_ids) <= 1:
        return 0.0
    probabilities = [
        counts[class_id] / active_total
        for class_id in class_ids
        if counts[class_id] > 0
    ]
    entropy = -sum(value * math.log(value) for value in probabilities)
    normalized = entropy / math.log(len(class_ids))
    return round(max(0.0, min(1.0, 1.0 - normalized)), 6)


def build_safety_net(
    input_data: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    counts: Counter[str],
    total: int,
    language: str,
) -> dict[str, Any]:
    """Convert outcome classes into reusable structures and next-package candidates.

    This intentionally does not generate or execute child packages. It only emits
    auditable candidates. True marginal civilizational yield requires comparing a
    parent Receipt with one or more child Receipts and is therefore not computed here.
    """
    policy = input_data.get("safety_net") or {}
    enabled = bool(policy.get("enabled", True))
    min_ratio = float(policy.get("min_outcome_ratio", 0.05))
    max_next = int(policy.get("max_next_packages", 3))

    pattern_sources: dict[str, dict[str, Any]] = {}
    domain_sources: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []

    if enabled:
        for definition in taxonomy.get("classes") or []:
            class_id = str(definition.get("id", ""))
            ratio = counts[class_id] / total if total else 0.0
            if not class_id or ratio < min_ratio:
                continue

            for pattern_id in definition.get("reusable_patterns") or []:
                key = str(pattern_id)
                entry = pattern_sources.setdefault(
                    key, {"pattern_id": key, "source_classes": [], "observed_ratio": 0.0}
                )
                entry["source_classes"].append(class_id)
                entry["observed_ratio"] = min(1.0, entry["observed_ratio"] + ratio)

            for domain in definition.get("transfer_domains") or []:
                key = str(domain)
                entry = domain_sources.setdefault(
                    key, {"domain": key, "source_classes": [], "observed_ratio": 0.0}
                )
                entry["source_classes"].append(class_id)
                entry["observed_ratio"] = min(1.0, entry["observed_ratio"] + ratio)

            next_package = definition.get("next_package")
            if isinstance(next_package, Mapping) and next_package.get("id"):
                candidates.append(
                    {
                        "id": str(next_package["id"]),
                        "source_class": class_id,
                        "source_ratio": ratio,
                        "question": localized(next_package.get("question", ""), language),
                        "reason": localized(next_package.get("reason", ""), language),
                    }
                )

    reusable_patterns = sorted(
        pattern_sources.values(), key=lambda item: (-item["observed_ratio"], item["pattern_id"])
    )
    transfer_candidates = sorted(
        domain_sources.values(), key=lambda item: (-item["observed_ratio"], item["domain"])
    )
    for item in reusable_patterns:
        item["observed_ratio"] = round(item["observed_ratio"], 6)
    for item in transfer_candidates:
        item["observed_ratio"] = round(item["observed_ratio"], 6)

    candidates.sort(key=lambda item: (-item["source_ratio"], item["id"]))
    next_candidates = candidates[:max(0, max_next)]
    for item in next_candidates:
        item["source_ratio"] = round(item["source_ratio"], 6)

    if not enabled:
        decision = "stop"
        reason = (
            "Safety-net extraction is disabled."
            if language == "en"
            else "セーフティネット抽出が無効です。"
        )
        status = "disabled"
    elif next_candidates:
        decision = "continue"
        reason = (
            f"{len(next_candidates)} next-package candidates remain after this Receipt."
            if language == "en"
            else f"このReceiptから、次Package候補が{len(next_candidates)}件残っています。"
        )
        status = "proxy_only"
    elif reusable_patterns or transfer_candidates:
        decision = "hold"
        reason = (
            "Reusable structure exists, but no next-package template is declared."
            if language == "en"
            else "再利用可能な構造はありますが、次Packageのテンプレートが未定義です。"
        )
        status = "proxy_only"
    else:
        decision = "stop"
        reason = (
            "No reusable structure above the declared outcome-ratio threshold was found."
            if language == "en"
            else "指定した出現率の閾値を超える再利用構造が見つかりませんでした。"
        )
        status = "proxy_only"

    warning = (
        "This is a routing proxy, not a direct measurement of civilizational value. "
        "Marginal yield requires parent-child Receipt comparison."
        if language == "en"
        else "これは配分用Proxyであり、文明的価値の直接測定ではありません。"
        "限界利回りには親Receiptと子Receiptの比較が必要です。"
    )

    return {
        "enabled": enabled,
        "total_loss_avoided": bool(
            enabled and (reusable_patterns or transfer_candidates or next_candidates)
        ),
        "reusable_patterns": reusable_patterns,
        "transfer_candidates": transfer_candidates,
        "next_package_candidates": next_candidates,
        "civilizational_yield_proxy": {
            "status": status,
            "marginal_yield_status": "not_computed",
            "uncertainty_reduction": outcome_uncertainty_reduction(taxonomy, counts),
            "reusable_pattern_count": len(reusable_patterns),
            "transfer_domain_count": len(transfer_candidates),
            "next_option_count": len(next_candidates),
            "warning": warning,
        },
        "expansion": {"decision": decision, "reason": reason},
    }


def metric_context(summaries: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Flatten summary statistics for aggregate interpretation rules."""
    context: dict[str, Any] = {}
    for metric, summary in summaries.items():
        if not isinstance(summary, Mapping):
            continue
        for statistic in ("min", "mean", "median", "max", "count"):
            if statistic in summary:
                context[f"{metric}_{statistic}"] = summary[statistic]
    return context


def select_configured_rule(
    rules: Any,
    context: Mapping[str, Any],
    language: str,
) -> Mapping[str, Any] | None:
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        return None
    ordered = sorted(
        (item for item in rules if isinstance(item, Mapping)),
        key=lambda item: int(item.get("priority", 0)),
        reverse=True,
    )
    for item in ordered:
        if matches(item.get("when", {}), context):
            return item
    return None


def metric_snapshot(
    summaries: Mapping[str, Mapping[str, Any]], metric: str
) -> dict[str, Any] | None:
    summary = summaries.get(metric)
    if not isinstance(summary, Mapping):
        return None
    return {
        key: summary[key]
        for key in ("min", "mean", "median", "max")
        if key in summary
    }


def build_automation_result(
    input_data: Mapping[str, Any],
    evaluator: Mapping[str, Any],
    counts: Counter[str],
    total: int,
    summaries: Mapping[str, Mapping[str, Any]],
    language: str,
) -> dict[str, Any] | None:
    """Build an automation-specific layer without changing the generic runner.

    Work allocation is computed from mean per-scenario route-share metrics. Operating
    mode distribution is separately computed from taxonomy classifications. Keeping
    the two percentages separate prevents a common interpretation error.
    """
    automation = input_data.get("automation")
    config = evaluator.get("automation_receipt")
    if not isinstance(automation, Mapping) or not isinstance(config, Mapping):
        return None

    allocation: dict[str, Any] = {}
    allocation_metrics = config.get("allocation_metrics") or {}
    if not isinstance(allocation_metrics, Mapping):
        raise RunnerError("automation_receipt.allocation_metrics must be a mapping")
    for route, metric_name in allocation_metrics.items():
        metric = str(metric_name)
        snapshot = metric_snapshot(summaries, metric)
        if snapshot is None:
            continue
        allocation[str(route)] = {"metric": metric, **snapshot}

    operating_modes: dict[str, Any] = {}
    mode_classes = config.get("operating_mode_classes") or {}
    if not isinstance(mode_classes, Mapping):
        raise RunnerError("automation_receipt.operating_mode_classes must be a mapping")
    for mode, class_ids in mode_classes.items():
        if not isinstance(class_ids, Sequence) or isinstance(class_ids, (str, bytes)):
            raise RunnerError(f"operating mode {mode} must list class ids")
        count = sum(counts[str(class_id)] for class_id in class_ids)
        operating_modes[str(mode)] = {
            "classes": [str(class_id) for class_id in class_ids],
            "count": count,
            "ratio": count / total if total else 0.0,
        }

    aggregate = metric_context(summaries)
    for route, item in allocation.items():
        if "mean" in item:
            aggregate[f"{route}_allocation_rate"] = item["mean"]
    for mode, item in operating_modes.items():
        aggregate[f"{mode}_mode_rate"] = item["ratio"]

    bottleneck_rule = select_configured_rule(
        config.get("bottlenecks") or [], aggregate, language
    )
    if bottleneck_rule:
        bottleneck = {
            "code": str(bottleneck_rule.get("id", "unclassified")),
            "label": localized(bottleneck_rule.get("label", ""), language),
            "reason": localized(bottleneck_rule.get("reason", ""), language),
            "action": localized(bottleneck_rule.get("action", ""), language),
        }
    else:
        bottleneck = {
            "code": "unclassified",
            "label": "未分類" if language == "ja" else "Unclassified",
            "reason": "ボトルネック規則に一致しませんでした。" if language == "ja" else "No bottleneck rule matched.",
            "action": "実測値を追加する。" if language == "ja" else "Add measured operating data.",
        }

    poc_rule = select_configured_rule(
        config.get("poc_recommendations") or [], aggregate, language
    )
    if poc_rule:
        guardrails = [localized(item, language) for item in poc_rule.get("guardrails") or []]
        poc = {
            "id": str(poc_rule.get("id", "unclassified")),
            "scope": localized(poc_rule.get("scope", ""), language),
            "reason": localized(poc_rule.get("reason", ""), language),
            "guardrails": guardrails,
        }
    else:
        poc = {
            "id": "measure-first",
            "scope": "現行業務を計測する。" if language == "ja" else "Measure the current workflow first.",
            "reason": "安全なPoC条件を選ぶ情報が不足しています。" if language == "ja" else "There is not enough information to choose a safe PoC.",
            "guardrails": [],
        }

    headline_metrics: dict[str, Any] = {}
    configured_headlines = config.get("headline_metrics") or {}
    if isinstance(configured_headlines, Mapping):
        for label, metric_name in configured_headlines.items():
            snapshot = metric_snapshot(summaries, str(metric_name))
            if snapshot is not None:
                headline_metrics[str(label)] = {
                    "metric": str(metric_name),
                    **snapshot,
                }

    allocation_total = sum(
        float(item.get("mean", 0.0)) for item in allocation.values()
    )
    warning = (
        "作業配分は各条件世界で算出した比率の平均です。運用モード分布は、"
        "条件世界全体をAUTO等へ分類した割合であり、同じ％ではありません。"
        if language == "ja"
        else "Work allocation is the mean route share across scenarios. Operating-mode "
        "distribution is the share of whole scenarios classified into each mode; the two "
        "percentages are not interchangeable."
    )

    return {
        "workflow": {
            "name": str(automation.get("workflow_name", "")),
            "description": str(automation.get("workflow_description", "")),
            "unit_of_work": str(automation.get("unit_of_work", "")),
            "risk_level": str(automation.get("risk_level", "medium")),
            "current_process": str(automation.get("current_process", "")),
        },
        "work_allocation": allocation,
        "allocation_mean_total": round(allocation_total, 6),
        "operating_mode_distribution": operating_modes,
        "headline_metrics": headline_metrics,
        "bottleneck": bottleneck,
        "recommended_poc": poc,
        "warning": warning,
    }


def receipt(
    input_data: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    evaluator: Mapping[str, Any],
    scenarios: list[dict[str, Any]],
    runs: int,
    seed: int,
) -> dict[str, Any]:
    definitions = taxonomy_index(taxonomy)
    counts = Counter(item["classification"]["class_id"] for item in scenarios)
    total = len(scenarios)
    data_quality = evidence_quality(input_data, taxonomy, counts)
    workload = workload_result(input_data, runs)
    project = dict(input_data.get("project") or {})
    language = str(project.get("language", "en"))

    metrics = input_data.get("metrics") or list((evaluator.get("derived_metrics") or {}).keys())
    summaries = {}
    for name in metrics:
        summary = numeric_summary(
            [item["derived_values"].get(str(name)) for item in scenarios]
        )
        if summary:
            summaries[str(name)] = summary

    automation_result = build_automation_result(
        input_data, evaluator, Counter(item["classification"]["class_id"] for item in scenarios),
        len(scenarios), summaries, language
    )

    hold_counts = Counter(
        hold.get("code", "UNKNOWN_HOLD")
        for item in scenarios
        for hold in item["classification"].get("holds", [])
    )

    outcomes: dict[str, Any] = {}
    weighted_score = 0.0
    for item in taxonomy.get("classes") or []:
        class_id = str(item["id"])
        score = float(item.get("score", 0))
        count = counts[class_id]
        weighted_score += score * count
        outcomes[class_id] = {
            "plain_label": localized(item.get("plain_label", class_id), language),
            "kind": str(item.get("kind", "intermediate")),
            "score": score,
            "count": count,
            "ratio": count / total,
        }
    success_score = weighted_score / total

    likely_class_id, likely_count = counts.most_common(1)[0]
    likely_definition = definitions[likely_class_id]

    funnel = build_funnel(input_data, counts, total)
    stage_rates = {f"{item['id']}_rate": item["ratio"] for item in funnel}
    aggregate_context = {**stage_rates, "success_score": success_score}
    verdict = select_verdict(evaluator, aggregate_context, language)

    failure_classes = [
        item for item in taxonomy.get("classes", []) if item.get("kind") == "failure"
    ]
    if failure_classes:
        risk_definition = max(
            failure_classes, key=lambda item: counts[str(item["id"])]
        )
    else:
        risk_definition = likely_definition
    risk_id = str(risk_definition["id"])

    bottleneck = max(funnel, key=lambda item: item["drop_from_previous"])
    funnel_source = {
        str(item["id"]): item for item in input_data.get("funnel") or []
    }
    current_stage = str(project.get("current_stage", ""))
    stage_ids = [item["id"] for item in funnel]
    if current_stage in stage_ids and stage_ids.index(current_stage) + 1 < len(funnel):
        target_stage = funnel[stage_ids.index(current_stage) + 1]
        action_basis = "current_stage"
    else:
        target_stage = bottleneck
        action_basis = "largest_drop"
    action = str(funnel_source[target_stage["id"]]["next_action"])
    if language == "ja":
        if action_basis == "current_stage":
            action_reason = (
                f"現在地は「{funnel[stage_ids.index(current_stage)]['label']}」です。"
                f"次の未確認段階は「{target_stage['label']}」で、"
                f"シミュレーションでは{target_stage['ratio']:.0%}の条件が到達しました。"
            )
        else:
            action_reason = (
                f"「{target_stage['label']}」までの落ち幅が最も大きく、"
                f"{target_stage['drop_from_previous']:.0%}の条件がここで失われたためです。"
            )
        evidence_warning = (
            "この成功度は現実の市場確率ではありません。入力された仮定と分類ルールの中で、"
            f"{runs:,}通りを試した結果です。"
        )
        plain_summary = [
            str(project.get("plain_description", "")),
            f"総合成功度は100点中{success_score:.0f}点です。判定は「{verdict['label']}」です。",
            f"{stage_rates.get('useful_rate', 0):.0%}の条件で誰かの役に立ち、{stage_rates.get('paid_rate', 0):.0%}で売上が発生しました。",
            f"継続できる事業まで進んだのは{stage_rates.get('business_rate', 0):.0%}です。",
            f"最大のリスクは「{localized(risk_definition.get('plain_label'), language)}」です。",
            f"次にすることは「{action}」です。",
        ]
    else:
        if action_basis == "current_stage":
            current_label = funnel[stage_ids.index(current_stage)]["label"]
            action_reason = (
                f'The declared current stage is "{current_label}". The next unproven '
                f'milestone is "{target_stage["label"]}", reached in '
                f'{target_stage["ratio"]:.0%} of scenarios.'
            )
        else:
            action_reason = (
                f'The largest drop occurs before "{target_stage["label"]}", where '
                f'{target_stage["drop_from_previous"]:.0%} of scenarios are lost.'
            )
        evidence_warning = (
            "This is not a measured market probability. It is the result of "
            f"{runs:,} runs under the declared assumptions and classification rules."
        )
        plain_summary = [
            str(project.get("plain_description", "")),
            f"Overall success score: {success_score:.0f}/100. Verdict: {verdict['label']}.",
            f"{stage_rates.get('useful_rate', 0):.0%} of scenarios became useful to someone, and {stage_rates.get('paid_rate', 0):.0%} produced paid demand.",
            f"{stage_rates.get('business_rate', 0):.0%} became a repeatable business.",
            f"Biggest risk: {localized(risk_definition.get('plain_label'), language)}.",
            f"Next action: {action}",
        ]

    if automation_result is not None:
        allocation = automation_result["work_allocation"]
        mode_distribution = automation_result["operating_mode_distribution"]
        auto_rate = float(allocation.get("auto", {}).get("mean", 0.0))
        review_rate = float(allocation.get("review", {}).get("mean", 0.0))
        escalate_rate = float(allocation.get("escalate", {}).get("mean", 0.0))
        hold_rate = float(allocation.get("hold", {}).get("mean", 0.0))
        manual_rate = float(allocation.get("manual", {}).get("mean", 0.0))
        likely_mode = (
            max(mode_distribution.items(), key=lambda pair: pair[1].get("ratio", 0.0))[0]
            if mode_distribution
            else "unclassified"
        )
        if language == "ja":
            plain_summary = [
                str(project.get("plain_description", "")),
                f"平均作業配分はAUTO {auto_rate:.0%}、REVIEW {review_rate:.0%}、ESCALATE {escalate_rate:.0%}、HOLD {hold_rate:.0%}、MANUAL {manual_rate:.0%}です。",
                f"条件世界全体で最も多い運用モードは「{likely_mode.upper()}」です。",
                f"主なボトルネックは「{automation_result['bottleneck']['label']}」です。",
                f"推奨PoCは「{automation_result['recommended_poc']['scope']}」です。",
                f"次にすることは「{action}」です。",
            ]
        else:
            plain_summary = [
                str(project.get("plain_description", "")),
                f"Mean work allocation: AUTO {auto_rate:.0%}, REVIEW {review_rate:.0%}, ESCALATE {escalate_rate:.0%}, HOLD {hold_rate:.0%}, MANUAL {manual_rate:.0%}.",
                f"The most common whole-scenario operating mode is {likely_mode.upper()}.",
                f"Primary bottleneck: {automation_result['bottleneck']['label']}.",
                f"Recommended PoC: {automation_result['recommended_poc']['scope']}",
                f"Next action: {action}",
            ]

    if language == "ja":
        plain_summary.append(
            f"初期情報の状態は{data_quality['readiness_status'].upper()}、"
            f"モデル信頼Proxyは{data_quality['model_confidence']:.0%}、"
            f"平均可変域縮小率は{data_quality['mean_range_reduction']:.0%}です。"
        )
    else:
        plain_summary.append(
            f"Input readiness is {data_quality['readiness_status'].upper()}; "
            f"model-confidence proxy is {data_quality['model_confidence']:.0%}, and "
            f"mean range reduction is {data_quality['mean_range_reduction']:.0%}."
        )

    interpretation = evaluator.get("interpretation") or {}
    package_id = str(
        evaluator.get("package_id")
        or taxonomy.get("package_id")
        or input_data.get("package", {}).get("id")
        or "unknown"
    )
    version = str(evaluator.get("version") or taxonomy.get("version") or "0.0.0")

    result = {
        "receipt_id": f"sim-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "project": project,
        "question": str(input_data.get("question", "")),
        "answer": {
            "verdict_code": verdict["code"],
            "verdict": verdict["message"],
            "success_score": success_score,
            "success_label": verdict["label"],
            "likely_outcome": {
                "class_id": likely_class_id,
                "plain_label": localized(likely_definition.get("plain_label"), language),
                "ratio": likely_count / total,
            },
            "confidence": str(interpretation.get("confidence", "low")),
            "confidence_reason": localized(
                interpretation.get("confidence_reason", ""), language
            ),
        },
        "plain_summary": plain_summary,
        "outcomes": outcomes,
        "funnel": funnel,
        "main_risk": {
            "class_id": risk_id,
            "plain_label": localized(risk_definition.get("plain_label"), language),
            "ratio": counts[risk_id] / total,
            "response": localized(risk_definition.get("response", ""), language),
        },
        "next_action": {
            "stage_id": target_stage["id"],
            "stage_label": target_stage["label"],
            "action": action,
            "reason": action_reason,
        },
        "decisive_variables": decisive_variables(input_data, scenarios, definitions),
        "safety_net": build_safety_net(input_data, taxonomy, counts, total, language),
        "evidence": {
            "level": str(
                input_data.get("simulation", {}).get(
                    "evidence_level", "synthetic_simulation"
                )
            ),
            "pack_id": str((input_data.get("evidence_pack") or {}).get("id", "legacy-evidence")),
            "pack_version": str((input_data.get("evidence_pack") or {}).get("version", "0.0.0")),
            "runs": runs,
            "seed": seed,
            "warning": evidence_warning,
        },
        "data_quality": data_quality,
        "workload_result": workload,
        "metric_summaries": summaries,
        "holds": [
            {"code": code, "count": count}
            for code, count in hold_counts.most_common()
        ],
        "provenance": {
            "runner_version": RUNNER_VERSION,
            "input_digest": digest(input_data),
            "taxonomy_digest": digest(taxonomy),
            "evaluator_digest": digest(evaluator),
            "effective_variables_digest": digest(prepare_variables(input_data)[0]),
        },
    }
    if automation_result is not None:
        result["automation_result"] = automation_result
    return result

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
        workload = input_data.get("workload") or {}
        estimates = workload.get("estimated_runs") if isinstance(workload, Mapping) else {}
        default_runs = simulation.get("runs", (estimates or {}).get("target", 1000))
        runs = options.runs if options.runs is not None else int(default_runs)
        seed = options.seed if options.seed is not None else int(simulation.get("seed", 42))
        if not 1 <= runs <= 1_000_000:
            raise RunnerError("runs must be between 1 and 1,000,000")
        if estimates:
            minimum = int(estimates.get("min", 1))
            maximum = int(estimates.get("max", 1_000_000))
            if not minimum <= runs <= maximum:
                raise RunnerError(f"runs must stay within workload.estimated_runs [{minimum}, {maximum}]")

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
