import Std

/- Pure model of integer_fold_contract.fold_llvm_integer_compare.
   This is NOT a proof of the Python-to-Lean translation or PCC execution.
   The generic theorem permits arbitrary total unsigned/signed projections,
   isolating the actual rewrite: do not demand signed values on unsigned paths.
-/
namespace PCCFoldCompare

inductive Pred where
  | eq | ne | ult | ule | ugt | uge | slt | sle | sgt | sge
  deriving DecidableEq

def reference (unsigned signed : Int → Nat → Int)
    (pred : Pred) (width : Nat) (lhs rhs : Int) : Option Bool :=
  if width = 0 then none else
  let lhsU := unsigned lhs width
  let rhsU := unsigned rhs width
  let lhsS := signed lhsU width
  let rhsS := signed rhsU width
  some (match pred with
    | .eq => decide (lhsU = rhsU)
    | .ne => decide (lhsU ≠ rhsU)
    | .ult => decide (lhsU < rhsU)
    | .ule => decide (lhsU ≤ rhsU)
    | .ugt => decide (lhsU > rhsU)
    | .uge => decide (lhsU ≥ rhsU)
    | .slt => decide (lhsS < rhsS)
    | .sle => decide (lhsS ≤ rhsS)
    | .sgt => decide (lhsS > rhsS)
    | .sge => decide (lhsS ≥ rhsS))

def candidate (unsigned signed : Int → Nat → Int)
    (pred : Pred) (width : Nat) (lhs rhs : Int) : Option Bool :=
  if width = 0 then none else
  let lhsU := unsigned lhs width
  let rhsU := unsigned rhs width
  some (match pred with
    | .eq => decide (lhsU = rhsU)
    | .ne => decide (lhsU ≠ rhsU)
    | .ult => decide (lhsU < rhsU)
    | .ule => decide (lhsU ≤ rhsU)
    | .ugt => decide (lhsU > rhsU)
    | .uge => decide (lhsU ≥ rhsU)
    | .slt => decide (signed lhsU width < signed rhsU width)
    | .sle => decide (signed lhsU width ≤ signed rhsU width)
    | .sgt => decide (signed lhsU width > signed rhsU width)
    | .sge => decide (signed lhsU width ≥ signed rhsU width))

theorem projection_demand_equivalence
    (unsigned signed : Int → Nat → Int)
    (pred : Pred) (width : Nat) (lhs rhs : Int) :
    reference unsigned signed pred width lhs rhs =
    candidate unsigned signed pred width lhs rhs := by
  cases pred <;> rfl

def unsignedValue (value : Int) (width : Nat) : Int :=
  value % (2 ^ width : Int)

def signedValue (value : Int) (width : Nat) : Int :=
  let raw := unsignedValue value width
  if raw ≥ (2 ^ (width - 1) : Int) then raw - (2 ^ width : Int) else raw

theorem integer_model_equivalence
    (pred : Pred) (width : Nat) (lhs rhs : Int) :
    reference unsignedValue signedValue pred width lhs rhs =
    candidate unsignedValue signedValue pred width lhs rhs :=
  projection_demand_equivalence unsignedValue signedValue pred width lhs rhs

-- This is an explicit operational-demand model, not Lean evaluation timing.
def referenceSignedCalls : Nat → Nat
  | 0 => 0
  | _ + 1 => 2

def candidateSignedCalls (pred : Pred) : Nat → Nat
  | 0 => 0
  | _ + 1 => match pred with
    | .slt | .sle | .sgt | .sge => 2
    | _ => 0

theorem signed_calls_never_increase (pred : Pred) (width : Nat) :
    candidateSignedCalls pred width ≤ referenceSignedCalls width := by
  cases width with
  | zero => cases pred <;> exact Nat.le_refl 0
  | succ n => cases pred <;> first | exact Nat.zero_le 2 | exact Nat.le_refl 2

theorem equality_path_eliminates_two_calls (widthMinusOne : Nat) :
    referenceSignedCalls (widthMinusOne + 1) -
      candidateSignedCalls .eq (widthMinusOne + 1) = 2 := rfl

-- Deliberately changing equality to inequality is observably wrong.
theorem reject_wrong_equality_rewrite :
    reference unsignedValue signedValue .eq 8 1 1 ≠ some false := by
  decide

#print axioms projection_demand_equivalence
#print axioms integer_model_equivalence
#print axioms signed_calls_never_increase
#print axioms equality_path_eliminates_two_calls
#print axioms reject_wrong_equality_rewrite

end PCCFoldCompare
