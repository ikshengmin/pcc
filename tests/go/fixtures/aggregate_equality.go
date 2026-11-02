package main

// Authored value-comparison obligations, not an upstream-project substitute.
type Pair [2]uint64
type Alias = Pair
type OtherPair [2]uint64
type Flags [3]bool
type Padded struct { Small uint8; Wide uint64; End int8 }
type Inner struct { Name string; Values Pair }
type Record struct { First uint8; Body Inner; Last bool }
type Empty struct {}

var trace uint64
var shared = Pair{4, 5}

func Arrays() uint64 {
    a := [3]int8{-128, 0, 127}
    b := a
    result := uint64(0)
    if a == b { result += 1 }
    b[2] = -1
    if a != b { result += 2 }
    if (Flags{true, false, true}) == (Flags{true, false, true}) { result += 4 }
    if [0]uint64{} == ([0]uint64{}) { result += 8 }
    return result
}

func NamedValues() uint64 {
    a := Pair{0xfedcba9876543210, 7}
    var b [2]uint64 = a
    var c Alias = a
    if a == b && b == c && a == Pair(OtherPair(a)) {
        return a[0] + 7
    }
    return 0
}

func PaddingAndCopies() uint64 {
    a := Padded{1, 0x8877665544332211, -7}
    b := Padded{Small: 1, Wide: a.Wide, End: -7}
    if a != b { return 0 }
    b.Small = 2
    if a == b { return 0 }
    b = a
    b.End = -8
    if a == b { return 0 }
    return a.Wide
}

func NestedStrings() uint64 {
    a := Record{3, Inner{"a\x00\xff", Pair{7, 9}}, true}
    b := a
    result := uint64(0)
    if a == b { result += 1 }
    b.Body.Name = "a\x00"
    if a != b { result += 2 }
    b = a
    b.Body.Values[1] = 10
    if a != b { result += 4 }
    b = a
    b.Last = false
    if a != b { result += 8 }
    b = a
    b.First = 4
    if a != b { result += 16 }
    b = a
    b.Body.Name = "a\x00\xfe"
    if a != b { result += 32 }
    return result
}

func EmptyStrings() uint64 {
    var a Inner
    b := Inner{"", Pair{}}
    if a != b { return 0 }
    b.Name = "\x00"
    if a == b { return 0 }
    return 17
}

func leftValue() Pair { trace = trace*10+1; return shared }
func rightValue() Pair { trace = trace*10+2; shared[0] = 9; return shared }
func CapturedOperands() uint64 {
    shared = Pair{4, 5}
    trace = 0
    // Calls have a defined left-to-right order; do not assert an unspecified
    // plain-variable-load ordering relative to a side-effecting call.
    equal := leftValue() == rightValue()
    if equal { return 0 }
    return trace*100 + shared[0]
}

func emptyLeft() Empty { trace = trace*10+1; return Empty{} }
func emptyRight() Empty { trace = trace*10+2; return Empty{} }
func EmptyOperandCalls() uint64 {
    trace = 0
    if emptyLeft() != emptyRight() { return 0 }
    return trace
}

func caseValue(digit uint64, first uint64) Pair {
    trace = trace*10+digit
    shared[0] = 99
    return Pair{first, 5}
}
func SwitchCapture() uint64 {
    trace = 0
    shared = Pair{4, 5}
    result := uint64(0)
    switch leftValue() {
    default: result = 999
    case caseValue(2, 8), caseValue(3, 4): result = 7
    case caseValue(4, 4): result = 998
    }
    return trace*100 + result
}

func StructSwitch() uint64 {
    value := Inner{"a\x00b", Pair{4, 5}}
    switch value {
    case Inner{"a\x00", Pair{4, 5}}: return 0
    case Inner{"a\x00b", Pair{4, 5}}: return 23
    default: return 0
    }
}

func FallthroughSkipsLabels() uint64 {
    trace = 0
    result := uint64(0)
    switch (Pair{4, 5}) {
    case caseValue(1, 4): result = 2; fallthrough
    case caseValue(9, 0): result = result*10+3
    }
    return trace*100 + result
}

func RepeatedCases() uint64 {
    a := Pair{3, 4}
    switch a {
    case a: return 29
    case a: return 0
    }
    return 0
}

func EmptySwitch() uint64 {
    trace = 0
    switch emptyLeft() {}
    switch (Empty{}) {
    case Empty{}: return trace+30
    case Empty{}: return 0
    }
    return 0
}

func guardedEqual(a, b *Pair) bool {
    return a != nil && b != nil && *a == *b
}
func GuardedPointers() uint64 {
    a := Pair{1, 2}
    b := a
    if guardedEqual(nil, &b) { return 0 }
    if !guardedEqual(&a, &b) { return 0 }
    b[0] = 3
    if guardedEqual(&a, &b) { return 0 }
    return 37
}

func LoopSwitch() uint64 {
    total := uint64(0)
    for i := uint64(0); i < 4; i++ {
        switch [1]uint64{i} {
        case [1]uint64{1}: continue
        case [1]uint64{3}: total += 10; break
        default: total += i
        }
        total += 1
    }
    return total
}

func ShortCircuit() uint64 {
    trace = 0
    if false && emptyLeft() == emptyRight() { return 0 }
    if true || emptyLeft() != emptyRight() { return trace+41 }
    return 0
}

func main() {
    if Arrays() != 15 { for {} }
    if NamedValues() != 0xfedcba9876543217 { for {} }
    if PaddingAndCopies() != 0x8877665544332211 { for {} }
    if NestedStrings() != 63 { for {} }
    if EmptyStrings() != 17 { for {} }
    if CapturedOperands() != 1209 { for {} }
    if EmptyOperandCalls() != 12 { for {} }
    if SwitchCapture() != 12307 { for {} }
    if StructSwitch() != 23 { for {} }
    if FallthroughSkipsLabels() != 123 { for {} }
    if RepeatedCases() != 29 { for {} }
    if EmptySwitch() != 31 { for {} }
    if GuardedPointers() != 37 { for {} }
    if LoopSwitch() != 15 { for {} }
    if ShortCircuit() != 41 { for {} }
}
