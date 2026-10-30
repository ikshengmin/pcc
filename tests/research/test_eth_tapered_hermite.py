"""Named bounded coverage for the independent tapered-Hermite audit."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.research.eth_tapered_hermite import audit_tapered_hermite


class TaperedHermiteTests(unittest.TestCase):
    def test_exact_identity_precision_and_mutation_controls(self):
        result = audit_tapered_hermite()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["valuation_nodes_exhaustively_checked"], 3132)
        self.assertEqual(result["polynomial_cases"], 24)
        self.assertEqual(result["exact_Hermite_identities"], 24)
        self.assertEqual(result["modular_reconstructions"], 144)
        self.assertEqual(result["local_integrality_coefficients"], 8424)
        self.assertEqual(result["negative_controls"]["underprecision_detected"], 24)
        self.assertEqual(result["negative_controls"]["missing_alpha_scale_detected"], 24)
        self.assertEqual(result["seed"], 20261027)


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=True)
