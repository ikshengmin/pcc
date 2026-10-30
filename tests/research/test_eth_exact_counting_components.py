"""Exact small-case equation audits; unittest, standard library only."""

import copy
from fractions import Fraction
from itertools import combinations_with_replacement, permutations, product
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.research import eth_exact_counting_components as alg


class AuxiliaryConstructionTests(unittest.TestCase):
    def test_all_sixteen_rows_restore_inputs_and_measurements_separate(self):
        labels = tuple(product((0, 1), repeat=4))
        for label in labels:
            row = alg.auxiliary_row(label)
            for column in labels:
                self.assertEqual(alg.poly_eval(row.get(column, (0,)), 0),
                                 int(column == label), (label, column))
            for point in (1, 2):
                other_group = 1 - label[point + 1]
                outside = [col for col in labels if col[point - 1] == other_group]
                witness = next(col for col in outside if alg.measurement_entry(col))
                scalar = (alg.poly_eval(row.get(witness, (0,)), point)
                          / alg.measurement_entry(witness))
                for column in outside:
                    self.assertEqual(alg.poly_eval(row.get(column, (0,)), point),
                                     scalar * alg.measurement_entry(column),
                                     (label, point, column))

    def test_remark_4_4_exact_golden_row(self):
        row = alg.auxiliary_row((0, 0, 1, 1))
        self.assertEqual(row[(0, 0, 1, 1)], (Fraction(1),))
        self.assertEqual(row[(1, 0, 0, 1)], (0, -Fraction(1, 2)))
        self.assertEqual(row[(0, 1, 1, 0)], (0, -1))
        self.assertEqual(alg.poly_eval(row[(1, 1, 0, 0)], 1), 0)

    def test_copy_back_separation_for_all_boolean_assignments(self):
        design = alg.build_small_design(4, ((1, -2, 3), (-1, 2, -4), (3, 4)))
        assignments = tuple(product((0, 1), repeat=4))
        for point in (1, 2):
            for rows, destination in zip(design["rows"], design["destinations"]):
                group = destination[point - 1]
                fibers = {}
                for bits in assignments:
                    key = (tuple(bits[v] for v in range(4)
                                 if design["groups"][v][point - 1] == group),
                           tuple(sum(c * bit for c, bit in zip(row, bits))
                                 for row in design["measurements"]))
                    values = tuple(sum(alg.poly_eval(poly, point) * bits[w]
                                       for w, poly in row.items()) for row in rows.values())
                    if key in fibers:
                        self.assertEqual(values, fibers[key])
                    fibers[key] = values


class CommonCountingTests(unittest.TestCase):
    def check_formula(self, n, clauses):
        design = alg.build_small_design(n, clauses)
        direct = alg.direct_deformation(design)
        data = {}
        for point in (1, 2):
            actual = alg.grouped_taylor(design, point)
            expected = alg.poly_shift(direct, point, len(actual))
            expected += (Fraction(0),) * (len(actual) - len(expected))
            self.assertEqual(actual, expected, (n, clauses, point))
            data[point] = actual
        self.assertEqual(alg.hermite_constant(data), alg.direct_count(n, clauses),
                         (n, clauses))
        self.assertEqual(alg.hermite_constant_fraction_free(data), alg.direct_count(n, clauses),
                         ("fraction-free", n, clauses))

    def test_exhaustive_normalized_two_variable_formulas_up_to_two_clauses(self):
        clauses = ((1,), (-1,), (2,), (-2,), (1, 2), (1, -2), (-1, 2), (-1, -2))
        formula_count = 0
        for length in (1, 2):
            for formula in combinations_with_replacement(clauses, length):
                self.check_formula(2, formula)
                formula_count += 1
        self.assertEqual(formula_count, 44)

    def test_all_eight_three_literal_clause_polarities(self):
        for signs in product((-1, 1), repeat=3):
            self.check_formula(3, (tuple(sign * (v + 1) for v, sign in enumerate(signs)),))

    def test_directed_cross_group_formulae_and_unused_variables(self):
        cases = (
            (1, ((1,),)),
            (1, ((1,), (-1,))),
            (3, ((1, -2), (2, 3))),
            (3, ((1, 2, 3), (-1, -2, -3))),
            (4, ((1,),)),
            (4, ((1, -2, 3), (-1, 2, -4))),
            (4, ((1, 2), (-3, 4))),
            (4, ((1, 2, 3), (-2, -3, 4))),
        )
        for n, clauses in cases:
            self.check_formula(n, clauses)

    def test_independent_hermite_recovery_of_rational_polynomials(self):
        count = 0
        for raw in product((-1, 0, 1), repeat=4):
            polynomial = tuple(Fraction(c, i + 1) for i, c in enumerate(raw))
            data = {t: alg.poly_shift(polynomial, t, 2) for t in (1, 2)}
            self.assertEqual(alg.hermite_constant(data), polynomial[0])
            self.assertEqual(alg.hermite_constant_fraction_free(data), polynomial[0])
            count += 1
        self.assertEqual(count, 81)

    def test_negative_projection_control_is_rejected_by_boolean_oracle(self):
        design = copy.deepcopy(alg.build_small_design(1, ((1,),)))
        # Intentionally break P(0)=I: original x becomes 2*x.
        design["rows"][0][0][0] = (Fraction(2),)
        self.assertNotEqual(alg.count_by_components(design), alg.direct_count(1, ((1,),)))

    def test_negative_taylor_control_changes_recovered_count(self):
        data = {1: (Fraction(2), Fraction(1)), 2: (Fraction(3), Fraction(1))}
        self.assertEqual(alg.hermite_constant(data), 1)
        broken = {1: (Fraction(3), Fraction(1)), 2: data[2]}
        self.assertNotEqual(alg.hermite_constant(broken), 1)

    def test_fraction_free_determinants_against_independent_permutation_sum(self):
        checked = 0
        for size, alphabet in ((2, (-1, 0, 1)), (3, (0, 1))):
            for entries in product(alphabet, repeat=size * size):
                matrix = [entries[i * size:(i + 1) * size] for i in range(size)]
                expected = 0
                for permutation in permutations(range(size)):
                    inversions = sum(permutation[i] > permutation[j]
                                     for i in range(size) for j in range(i + 1, size))
                    term = (-1) ** inversions
                    for row in range(size):
                        term *= matrix[row][permutation[row]]
                    expected += term
                self.assertEqual(alg.fraction_free_determinant(matrix), expected)
                checked += 1
        self.assertEqual(checked, 593)


class ParityLiftTests(unittest.TestCase):
    def test_lift_coefficients_and_monic_reconstruction(self):
        for ell in range(1, 6):
            polynomial, remainders = alg.parity_lift(ell)
            reconstructed = (Fraction(0),)
            power = (Fraction(1),)
            for remainder in remainders:
                reconstructed = alg.poly_add(reconstructed,
                                             alg.poly_mul(remainder, power, 2 * ell), 2 * ell)
                power = alg.poly_mul(power, (0, -1, 1), 2 * ell)
            self.assertEqual(reconstructed, polynomial)
            for value in range(-8, 9):
                self.assertEqual(alg.poly_eval(polynomial, value) % (1 << ell),
                                 int(value % 2 == 0), (ell, value))

    def test_total_degree_truncation_exhaustive_small_integer_tuples(self):
        checked = 0
        for ell in range(1, 6):
            for length in (1, 2, 3):
                for values in product(range(-2, 4), repeat=length):
                    self.assertEqual(alg.truncated_parity_product(values, ell),
                                     int(all(value % 2 == 0 for value in values)),
                                     (ell, values))
                    checked += 1
        self.assertEqual(checked, 1290)

    def test_negative_parity_and_early_truncation_controls(self):
        polynomial, _ = alg.parity_lift(2)
        broken = alg.poly_add(polynomial, (1,), 4)
        self.assertNotEqual(alg.poly_eval(broken, 2) % 4, 1)
        self.assertEqual(alg.truncated_parity_product((2,), 2), 1)
        self.assertNotEqual(alg.truncated_parity_product((2,), 2, retained=1), 1)

    def test_explicit_input_caps(self):
        for n in (0, 5, True):
            with self.assertRaises(ValueError):
                alg.build_small_design(n, ((1,),))
        for ell in (0, 6, True):
            with self.assertRaises(ValueError):
                alg.parity_lift(ell)


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=True)
