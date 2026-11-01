package main

import "testing"

// Diagnostic observer only: original pointers.go stays byte-identical.
func TestOriginalPointerObserver(t *testing.T) {
    t.Logf("PCC_POINTER_OBSERVATION\tWideAlias\t%016x\t%016x", uint64(WideAlias()), uint64(18364758544493064727))
    t.Logf("PCC_POINTER_OBSERVATION\tRebind\t%016x\t%016x", uint64(Rebind()), uint64(509))
    t.Logf("PCC_POINTER_OBSERVATION\tParallel\t%016x\t%016x", uint64(Parallel()), uint64(1317))
    t.Logf("PCC_POINTER_OBSERVATION\tEqual\t%016x\t%016x", uint64(Equal()), uint64(63))
    t.Logf("PCC_POINTER_OBSERVATION\tRecordSnapshot\t%016x\t%016x", uint64(RecordSnapshot()), uint64(3501))
    t.Logf("PCC_POINTER_OBSERVATION\tRecordStore\t%016x\t%016x", uint64(RecordStore()), uint64(810))
    t.Logf("PCC_POINTER_OBSERVATION\tArrayStore\t%016x\t%016x", uint64(ArrayStore()), uint64(2533))
    t.Logf("PCC_POINTER_OBSERVATION\tIndexOnce\t%016x\t%016x", uint64(IndexOnce()), uint64(108))
    t.Logf("PCC_POINTER_OBSERVATION\tCompoundOnce\t%016x\t%016x", uint64(CompoundOnce()), uint64(1213))
    t.Logf("PCC_POINTER_OBSERVATION\tShortRebind\t%016x\t%016x", uint64(ShortRebind()), uint64(907))
    t.Logf("PCC_POINTER_OBSERVATION\tNestedScope\t%016x\t%016x", uint64(NestedScope()), uint64(15))
    t.Logf("PCC_POINTER_OBSERVATION\tIterationLocal\t%016x\t%016x", uint64(IterationLocal()), uint64(14))
    t.Logf("PCC_POINTER_OBSERVATION\tOuterLoopBinding\t%016x\t%016x", uint64(OuterLoopBinding()), uint64(4))
    t.Logf("PCC_POINTER_OBSERVATION\tNamedPointer\t%016x\t%016x", uint64(NamedPointer()), uint64(7))
    t.Logf("PCC_POINTER_OBSERVATION\tNamedValue\t%016x\t%016x", uint64(NamedValue()), uint64(18364758544493064729))
    t.Logf("PCC_POINTER_OBSERVATION\tValueMethod\t%016x\t%016x", uint64(ValueMethod()), uint64(1164))
    t.Logf("PCC_POINTER_OBSERVATION\tStaticString\t%016x\t%016x", uint64(StaticString()), uint64(7))
    t.Logf("PCC_POINTER_OBSERVATION\tBoolAlias\t%016x\t%016x", uint64(BoolAlias()), uint64(19))
    t.Logf("PCC_POINTER_OBSERVATION\tGlobalAddress\t%016x\t%016x", uint64(GlobalAddress()), uint64(8))
    t.Logf("PCC_POINTER_OBSERVATION\tParameterAddress\t%016x\t%016x", uint64(ParameterAddress(4)), uint64(9))
    t.Logf("PCC_POINTER_OBSERVATION\tResultAddress\t%016x\t%016x", uint64(ResultAddress()), uint64(23))
    t.Logf("PCC_POINTER_OBSERVATION\tDirectNilTypes\t%016x\t%016x", uint64(DirectNilTypes()), uint64(7))
    t.Logf("PCC_POINTER_OBSERVATION\tExplicitReceiverOrder\t%016x\t%016x", uint64(ExplicitReceiverOrder()), uint64(9))
    t.Logf("PCC_POINTER_OBSERVATION\tImplicitReceiverOrder\t%016x\t%016x", uint64(ImplicitReceiverOrder()), uint64(1))
}
