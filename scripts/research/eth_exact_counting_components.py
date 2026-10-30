"""Bounded exact checks of the ETH paper's equations, not a practical SAT solver.

Source: October 25 paper, sections 2.4-2.5, 3, 4.2-4.5 and 5.1-5.2.
The r=Q=R=2 auxiliary construction deliberately lies outside R<Q and the
asymptotic feasibility regime. Inputs are capped at four Boolean variables.
No PCC production imports, process launching, or dependency requirements.
"""

from fractions import Fraction
from itertools import product
from math import comb, lcm


def poly_add(left, right, limit):
    return tuple((left[i] if i < len(left) else 0)
                 + (right[i] if i < len(right) else 0)
                 for i in range(min(limit, max(len(left), len(right)))))


def poly_scale(poly, scale):
    return tuple(value * scale for value in poly)


def poly_mul(left, right, limit):
    out = [Fraction(0)] * min(limit, len(left) + len(right) - 1)
    for i, value in enumerate(left):
        for j, other in enumerate(right[:max(0, limit - i)]):
            out[i + j] += value * other
    return tuple(out)


def poly_shift(poly, point, limit):
    return tuple(sum(poly[d] * comb(d, j) * point ** (d - j)
                     for d in range(j, len(poly)))
                 for j in range(min(limit, len(poly))))


def poly_eval(poly, value):
    result = Fraction(0)
    for coefficient in reversed(poly):
        result = result * value + coefficient
    return result


def auxiliary_row(label):
    """Eq. (4.10), I={0,1}: M0=Pi1+Pi2-Pi1Pi2, on all 16 labels.

    label=(a0,a1,d0,d1). Each Pi swaps a_t,d_t. The antisymmetric
    tensor eigenvector is the sole measurement row (Lemma 4.1).
    """
    if len(label) != 4 or any(bit not in (0, 1) for bit in label):
        raise ValueError("expected one of the sixteen binary labels")
    weights = {}
    for positions, weight in (((0,), 1), ((1,), 1), ((0, 1), -1)):
        column = list(label)
        for t in positions:
            column[t], column[t + 2] = column[t + 2], column[t]
        key = tuple(column)
        weights[key] = weights.get(key, 0) + weight
    row = {label: (Fraction(1),)}
    for column, weight in weights.items():
        if not weight:
            continue
        phi = (Fraction(1),)
        for t in range(2):
            if column[t] != label[t + 2]:
                phi = poly_mul(phi, (1, -Fraction(1, t + 1)), 3)
        correction = poly_scale(poly_add(phi, (-1,), 3), weight)
        row[column] = poly_add(row.get(column, (0,)), correction, 3)
    return row


def measurement_entry(label):
    value = 1
    for t in range(2):
        if label[t] == label[t + 2]:
            return 0
        value *= 1 if label[t] == 0 else -1
    return value


def clause_polynomial(clause):
    """Integer multilinear satisfaction polynomial 1-product(false literals)."""
    terms = {0: Fraction(1)}
    for literal in clause:
        bit = 1 << (abs(literal) - 1)
        false_literal = {0: 1, bit: -1} if literal > 0 else {bit: 1}
        next_terms = {}
        for mask, value in terms.items():
            for other, coefficient in false_literal.items():
                target = mask | other
                next_terms[target] = next_terms.get(target, 0) + value * coefficient
        terms = next_terms
    result = {mask: -value for mask, value in terms.items()}
    result[0] = result.get(0, 0) + 1
    return {mask: value for mask, value in result.items() if value}


def build_small_design(n, clauses):
    """Eq. (4.14)-(4.15), one auxiliary copy per factor, zero unused slots.

    Unique two-bit a_v labels suffice for n<=4. This is an explicitly small
    structural design, NOT the balanced placement/parameter asymptotic proof.
    """
    if type(n) is not int or not 1 <= n <= 4 or not 1 <= len(clauses) <= 3:
        raise ValueError("audit domain is 1..4 variables and 1..3 clauses")
    assignments = tuple((v & 1, (v >> 1) & 1) for v in range(n))
    factors, replacements, measurements, destinations = [], [], [], []
    for index, clause in enumerate(clauses):
        if not 1 <= len(clause) <= 3:
            raise ValueError("audit clauses have width 1..3")
        if any(type(x) is not int or x == 0 or abs(x) > n for x in clause):
            raise ValueError("invalid literal")
        if len({abs(x) for x in clause}) != len(clause):
            raise ValueError("normalize repeated variables before this audit")
        destination = (index & 1, (index >> 1) & 1)
        scope = sorted(abs(x) - 1 for x in clause)
        occupied = {assignments[v] + destination: v for v in scope}
        rows = {}
        for v in scope:
            raw = auxiliary_row(assignments[v] + destination)
            rows[v] = {other: raw[label] for label, other in occupied.items()
                       if label in raw}
        measurements.append(tuple(sum(measurement_entry(label)
                                      for label, value in occupied.items() if value == v)
                                  for v in range(n)))
        factors.append(clause_polynomial(clause))
        replacements.append(rows)
        destinations.append(destination)
    return {"n": n, "clauses": tuple(tuple(c) for c in clauses),
            "groups": assignments, "factors": tuple(factors),
            "rows": tuple(replacements), "measurements": tuple(measurements),
            "destinations": tuple(destinations),
            "degree_bound": sum(len(c) for c in clauses)}


def evaluate_factor(factor, values, limit):
    result = (Fraction(0),)
    for mask, coefficient in factor.items():
        term = (coefficient,)
        for v, value in values.items():
            if mask & (1 << v):
                term = poly_mul(term, value, limit)
        result = poly_add(result, term, limit)
    return result


def direct_count(n, clauses):
    """Independent Boolean oracle: no polynomial or replacement code."""
    return sum(all(any(bool(bits[abs(lit) - 1]) == (lit > 0) for lit in clause)
                   for clause in clauses) for bits in product((0, 1), repeat=n))


def direct_deformation(design):
    """Eq. (3.3) by direct Boolean summation; not the grouped routine."""
    n = design["n"]
    limit = 2 * design["degree_bound"] + 1
    result = (Fraction(0),)
    for bits in product((0, 1), repeat=n):
        term = (Fraction(1),)
        for factor, rows in zip(design["factors"], design["rows"]):
            values = {v: tuple(sum((row.get(w, ())[j] if j < len(row.get(w, ())) else 0)
                                  * bits[w] for w in range(n))
                              for j in range(3)) for v, row in rows.items()}
            term = poly_mul(term, evaluate_factor(factor, values, limit), limit)
        result = poly_add(result, term, limit)
    return result


def group_buckets(design, point):
    """List attainable measurements using each group's Boolean assignments.

    Skipping a tuple with an empty group fiber is exactly the zero branch of
    Lemma 3.2; this bounded audit does not enumerate the large empty box.
    """
    groups = [tuple(v for v in range(design["n"])
                    if design["groups"][v][point - 1] == g) for g in range(2)]
    buckets = []
    for group in groups:
        current = {}
        for local in product((0, 1), repeat=len(group)):
            bits = tuple(local[group.index(v)] if v in group else 0
                         for v in range(design["n"]))
            key = tuple(sum(row[v] * bits[v] for v in group)
                        for row in design["measurements"])
            current.setdefault(key, []).append(bits)
        buckets.append(current)
    return groups, buckets


def grouped_restricted_sum(design, point, forced_mask, correction_mask, order):
    """Z_{t,E,U}(z), eqs (2.8), (3.11), via independent group fibers."""
    groups, buckets = group_buckets(design, point)
    total = (Fraction(0),)
    for keys in product(*(tuple(bucket) for bucket in buckets)):
        representatives = [buckets[g][keys[g]][0] for g in range(2)]
        contribution = (Fraction(1),)
        for g in range(2):
            subtotal = (Fraction(0),)
            for local in buckets[g][keys[g]]:
                if any(forced_mask & (1 << v) and not local[v] for v in groups[g]):
                    continue
                assembled = tuple(local[v] if v in groups[g] else representatives[1-g][v]
                                  for v in range(design["n"]))
                factor_product = (Fraction(1),)
                for factor, rows, destination in zip(
                        design["factors"], design["rows"], design["destinations"]):
                    if destination[point - 1] != g:
                        continue
                    values = {}
                    for v, row in rows.items():
                        value = (sum(poly_eval(poly, point) * assembled[w]
                                     for w, poly in row.items()),)
                        for w, poly in row.items():
                            if correction_mask & (1 << w):
                                delta = poly_add(poly_shift(poly, point, order),
                                                 (-poly_eval(poly, point),), order)
                                value = poly_add(value, delta, order)
                        values[v] = value
                    factor_product = poly_mul(factor_product,
                                              evaluate_factor(factor, values, order), order)
                subtotal = poly_add(subtotal, factor_product, order)
            contribution = poly_mul(contribution, subtotal, order)
        total = poly_add(total, contribution, order)
    return total


def grouped_taylor(design, point):
    """Lemma 3.3 inclusion-exclusion, eq (2.9); q=floor(dR/Q)=d."""
    q = design["degree_bound"]
    result = (Fraction(0),)
    for forced in range(1 << design["n"]):
        if forced.bit_count() > q:
            continue
        correction = forced
        while True:
            sign = -1 if (forced.bit_count() - correction.bit_count()) % 2 else 1
            value = grouped_restricted_sum(design, point, forced, correction, q + 1)
            result = poly_add(result, poly_scale(value, sign), q + 1)
            if correction == 0:
                break
            correction = (correction - 1) & forced
    return result + (Fraction(0),) * (q + 1 - len(result))


def hermite_constant(taylor_by_point):
    """Exact rational linear solve from section 2.5 / Appendix B.3.

    This small checker uses Fraction Gaussian elimination, not the paper's
    fraction-free implementation or its large-input bit-cost guarantee.
    """
    size = sum(len(coefficients) for coefficients in taylor_by_point.values())
    matrix = []
    for point, coefficients in taylor_by_point.items():
        for j, value in enumerate(coefficients):
            matrix.append([Fraction(comb(u, j) * point ** (u - j)) if u >= j else Fraction(0)
                           for u in range(size)] + [Fraction(value)])
    for column in range(size):
        pivot = next((r for r in range(column, size) if matrix[r][column]), None)
        if pivot is None:
            raise ValueError("singular Hermite system")
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        scale = matrix[column][column]
        matrix[column] = [v / scale for v in matrix[column]]
        for r in range(size):
            if r == column:
                continue
            multiple = matrix[r][column]
            matrix[r] = [a - multiple * b for a, b in zip(matrix[r], matrix[column])]
    return matrix[0][-1]


def fraction_free_determinant(matrix):
    """Appendix B.3's fraction-free elimination, with exact-division checks."""
    size = len(matrix)
    if any(len(row) != size or any(type(x) is not int for x in row) for row in matrix):
        raise ValueError("expected a square integer matrix")
    if not size:
        return 1
    work = [list(row) for row in matrix]
    previous = 1
    sign = 1
    for column in range(size - 1):
        pivot_row = next((row for row in range(column, size) if work[row][column]), None)
        if pivot_row is None:
            return 0
        if pivot_row != column:
            work[column], work[pivot_row] = work[pivot_row], work[column]
            sign = -sign
        pivot = work[column][column]
        for row in range(column + 1, size):
            for other in range(column + 1, size):
                numerator = work[row][other] * pivot - work[row][column] * work[column][other]
                value, remainder = divmod(numerator, previous)
                if remainder:
                    raise AssertionError("fraction-free update was not an exact division")
                work[row][other] = value
            work[row][column] = 0
        previous = pivot
    return sign * work[-1][-1]


def hermite_constant_fraction_free(taylor_by_point):
    """Appendix B.3 eq (B.1), common denominator + two integer determinants."""
    size = sum(len(coefficients) for coefficients in taylor_by_point.values())
    denominator = 1
    for coefficients in taylor_by_point.values():
        for value in coefficients:
            denominator = lcm(denominator, Fraction(value).denominator)
    matrix, rhs = [], []
    for point, coefficients in taylor_by_point.items():
        for j, value in enumerate(coefficients):
            matrix.append([comb(u, j) * point ** (u - j) if u >= j else 0
                           for u in range(size)])
            rhs.append(int(Fraction(value) * denominator))
    replaced = [list(row) for row in matrix]
    for row, value in zip(replaced, rhs):
        row[0] = value
    determinant = fraction_free_determinant(matrix)
    if not determinant:
        raise ValueError("singular Hermite system")
    return Fraction(fraction_free_determinant(replaced), denominator * determinant)


def count_by_components(design):
    return hermite_constant({t: grouped_taylor(design, t) for t in (1, 2)})


def parity_lift(ell):
    """Lemma 5.1: construct p_ell and divide repeatedly by h=Y^2-Y."""
    if type(ell) is not int or not 1 <= ell <= 5:
        raise ValueError("audit precision is 1..5")
    base = tuple(Fraction((-1) ** i * comb(ell, i)) for i in range(ell + 1))
    series = tuple(Fraction(comb(ell + j - 1, j)) for j in range(ell))
    polynomial = poly_mul(base, series, 2 * ell)
    remaining = list(polynomial)
    remainders = []
    for _ in range(ell):
        quotient = [Fraction(0)] * max(1, len(remaining) - 2)
        for degree in range(len(remaining) - 1, 1, -1):
            coefficient = remaining[degree]
            quotient[degree - 2] += coefficient
            remaining[degree] -= coefficient
            remaining[degree - 1] += coefficient
        remainders.append(tuple((remaining + [Fraction(0)] * 2)[:2]))
        remaining = quotient
    if any(remaining) or any(c.denominator != 1 for row in remainders for c in row):
        raise AssertionError("monic divisions must end with integral linear remainders")
    return polynomial, tuple(remainders)


def truncated_parity_product(values, ell, *, retained=None):
    """Lemma 5.2: truncate TOTAL h-degree, then sum V coefficients."""
    if retained is None:
        retained = ell
    if not 1 <= retained <= ell:
        raise ValueError("invalid retained precision")
    _, remainders = parity_lift(ell)
    result = (Fraction(1),)
    for value in values:
        h = value * (value - 1)
        lifted = tuple(poly_eval(remainders[j], value) * h ** j for j in range(ell))
        result = poly_mul(result, lifted, retained)
    answer = sum(result)
    if answer.denominator != 1:
        raise AssertionError("integer lift unexpectedly became rational")
    return int(answer) % (1 << ell)
