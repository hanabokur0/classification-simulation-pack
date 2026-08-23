import importlib.util
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT
RUNNER_PATH = REPO_ROOT / "runner" / "run.py"


def load_runner():
    if not RUNNER_PATH.exists():
        raise unittest.SkipTest(
            "Copy this addition into the classification-simulation-pack repository before running this test."
        )
    spec = importlib.util.spec_from_file_location("csp_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_yaml(path):
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def fixed_input(**overrides):
    values = {
        "workload_power_mw": 4.0,
        "workload_gpu_units": 16,
        "workload_latency_limit_ms": 40,
        "workload_interruptible": True,
        "workload_checkpointable": True,
        "workload_divisible": True,
        "data_residency_remote_allowed": True,
        "routing_authority_confirmed": True,
        "workload_profile_complete": True,
        "telemetry_fresh": True,
        "sendai_power_headroom_mw": 8.0,
        "sendai_gpu_headroom_units": 32,
        "sendai_rtt_ms": 18.0,
        "sendai_energy_cost_jpy_kwh": 26.0,
        "sendai_renewable_share": 0.60,
        "sendai_thermal_margin": 0.80,
        "akita_power_headroom_mw": 3.0,
        "akita_gpu_headroom_units": 12,
        "akita_rtt_ms": 30.0,
        "akita_energy_cost_jpy_kwh": 27.0,
        "akita_renewable_share": 0.70,
        "akita_thermal_margin": 0.85,
        "niigata_power_headroom_mw": 3.0,
        "niigata_gpu_headroom_units": 12,
        "niigata_rtt_ms": 25.0,
        "niigata_energy_cost_jpy_kwh": 28.0,
        "niigata_renewable_share": 0.50,
        "niigata_thermal_margin": 0.75,
        "local_power_headroom_mw": 3.0,
        "local_gpu_headroom_units": 12,
        "local_energy_cost_jpy_kwh": 36.0,
        "local_renewable_share": 0.20,
        "local_thermal_margin": 0.50,
    }
    values.update(overrides)
    return {
        "variables": {name: {"value": value} for name, value in values.items()},
        "constants": {},
        "context": {"domain": "compute_routing_test"},
    }


class ComputeRoutingPackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()
        cls.taxonomy = load_yaml(REPO_ROOT / "packages" / "compute_routing" / "taxonomy.yaml")
        cls.evaluator = load_yaml(REPO_ROOT / "packages" / "compute_routing" / "evaluator.yaml")

    def classify(self, data):
        result = self.runner.run(data, self.taxonomy, self.evaluator, runs=1, seed=42)
        return result[0]["classification"]["class_id"]

    def test_direct_route(self):
        self.assertEqual(self.classify(fixed_input()), "route")

    def test_stale_telemetry_holds(self):
        self.assertEqual(self.classify(fixed_input(telemetry_fresh=False)), "hold")

    def test_remote_disallowed_keeps_local_when_local_capacity_exists(self):
        data = fixed_input(
            data_residency_remote_allowed=False,
            local_power_headroom_mw=8.0,
            local_gpu_headroom_units=32,
        )
        self.assertEqual(self.classify(data), "local")

    def test_flexible_partial_capacity_shifts(self):
        data = fixed_input(
            sendai_power_headroom_mw=3.0,
            sendai_gpu_headroom_units=12,
            local_power_headroom_mw=2.0,
            local_gpu_headroom_units=8,
            workload_power_mw=4.0,
            workload_gpu_units=16,
        )
        self.assertEqual(self.classify(data), "shift")

    def test_no_capacity_holds(self):
        data = fixed_input(
            sendai_power_headroom_mw=0.2,
            sendai_gpu_headroom_units=1,
            akita_power_headroom_mw=0.2,
            akita_gpu_headroom_units=1,
            niigata_power_headroom_mw=0.2,
            niigata_gpu_headroom_units=1,
            local_power_headroom_mw=0.2,
            local_gpu_headroom_units=1,
        )
        self.assertEqual(self.classify(data), "hold")


if __name__ == "__main__":
    unittest.main()
