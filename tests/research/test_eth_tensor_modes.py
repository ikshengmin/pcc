"""Finite D.12 mode expansion and D.16 child factorization checks."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.research.eth_tensor_modes import run_audit


class TensorModeTests(unittest.TestCase):
    def test_crossing_modes_and_one_packet_child_identity(self):
        result = run_audit()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["seed"], 20261027)
        mode = result["mode_expansion"]
        self.assertEqual(mode["crossing_pair_cases"], 2500)
        self.assertEqual(mode["modular_tail_checks"], 20000)
        self.assertEqual(mode["mode_coefficient_valuation_checks"], 17500)
        self.assertEqual(mode["zero_zeta_pair_cases"], 500)
        child = result["child_identity"]
        self.assertEqual(child["child_identity_cases"], 16)
        self.assertEqual(child["direct_boolean_assignments"], 4096)
        self.assertEqual(child["weighted_tuples_processed"], 592)
        self.assertEqual(child["negative_controls"]["zero_mode_only_detected"], 7)
        self.assertEqual(child["negative_controls"]["wrong_zero_power_detected"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=True)
