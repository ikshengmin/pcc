// Complete authored Go program. The owned and official compilers receive these
// same bytes; self-check failures loop rather than substitute abort for panic.
package main

type Count uint16

var calls int
var trace int

func bound() int { calls++; return 3 }
func zeroBound() int { calls++; return 0 }
func target() int { calls++; return 0 }
func orderedBound() int { trace = trace*10+1; return 2 }
func orderedTarget() int { trace = trace*10+2; return 0 }
func countValue(n Count) Count { return n }

func DefaultBound() uint64 {
    total := 0
    for i := range 5 { total += i }
    return uint64(total)
}

func UnsignedMaximum() uint64 {
    total := uint64(0)
    for i := range uint8(255) { total += uint64(i) }
    return total
}

func SignedMaximum() uint64 {
    total := uint64(0)
    for i := range int8(127) { total += uint64(i) }
    return total
}

func Nonpositive() uint64 {
    count := uint64(0)
    for range 0 { count++ }
    for range -3 { count++ }
    for range int8(-128) { count++ }
    return count
}

func ExistingUntyped() uint64 {
    var i uint8
    count := uint64(0)
    for i = range 255 { count++ }
    return uint64(i)*1000+count
}

func NamedInteger() uint64 {
    total := Count(0)
    for i := range Count(4) { total += countValue(i) }
    return uint64(total)
}

func BoundSnapshot() uint64 {
    n, total := 5, 0
    for i := range n { total += i; n = 1 }
    return uint64(total*10+n)
}

func ShadowBound() uint64 {
    n, total := 4, 0
    for n := range n { total += n }
    return uint64(n*100+total)
}

func HiddenInduction() uint64 {
    total := 0
    for i := range 4 { total += i; i = 100 }
    return uint64(total)
}

func BoundCallOnce() uint64 {
    calls = 0
    total := 0
    for i := range bound() { total += i }
    return uint64(calls*100+total)
}

func BareBoundCallOnce() uint64 {
    calls = 0
    count := 0
    for range bound() { count++ }
    return uint64(calls*100+count)
}

func EmptySkipsTarget() uint64 {
    calls = 0
    var a [1]int
    for a[target()&0] = range zeroBound() {}
    return uint64(calls*100+a[0])
}

func EachTargetOnce() uint64 {
    calls = 0
    var a [1]int
    for a[target()&0] = range 3 {}
    return uint64(calls*100+a[0])
}

func OrderedEffects() uint64 {
    trace = 0
    var a [1]int
    for a[orderedTarget()&0] = range orderedBound() {}
    return uint64(trace*10+a[0])
}

func ContinueSwitchBreak() uint64 {
    total := 0
    for i := range 6 {
        switch i { case 1: continue; case 4: break }
        total += i
        if i == 4 { break }
    }
    return uint64(total)
}

func NestedRanges() uint64 {
    count := 0
    for range 3 {
        for j := range 3 { if j == 1 { break }; count++ }
    }
    return uint64(count)
}

func HugeUnsigned() uint64 {
    for i := range uint64(18446744073709551615) {
        if i == 3 { return 0xfedcba9876543210+i }
    }
    return 0
}

func HugeSigned() uint64 {
    total := 0
    for i := range int64(9223372036854775807) {
        total += int(i)
        if i == 2 { break }
    }
    return uint64(total)
}

func ContextualWideConstant() uint64 {
    var i uint64
    for i = range 1<<63 { if i == 2 { break } }
    return i
}

func ShiftContext(s uint) uint64 {
    var i uint8 = 9
    count := 0
    for i = range 1<<s { count++ }
    return uint64(i)*100+uint64(count)
}

func BodyScope() uint64 {
    total := 0
    for i := range 3 { i := i+10; total += i }
    return uint64(total)
}

func ExistingPointerTarget() uint64 {
    i := 17
    p := &i
    for *p = range 3 {}
    return uint64(i)
}

func EmptyNilTarget() uint64 {
    var p *int
    for *p = range 0 {}
    for *p = range -2 {}
    return 7
}

func NilConstantLength() uint64 {
    var p *[3]int
    total := 0
    for i := range len(*p) { total += i }
    return uint64(total)
}

func GuardedBound(p *int) uint64 {
    if p == nil { return 0 }
    total := 0
    for i := range *p { total += i }
    return uint64(total)
}

func GuardedCall() uint64 { n := 4; return GuardedBound(&n)+GuardedBound(nil) }

func SignedDynamic(n int64) uint64 {
    total := uint64(0)
    for i := range n { total += uint64(i) }
    return total
}

func main() {
    if DefaultBound() != 10 || UnsignedMaximum() != 32385 || SignedMaximum() != 8001 { for {} }
    if Nonpositive() != 0 || ExistingUntyped() != 254255 || NamedInteger() != 6 { for {} }
    if BoundSnapshot() != 101 || ShadowBound() != 406 || HiddenInduction() != 6 { for {} }
    if BoundCallOnce() != 103 || BareBoundCallOnce() != 103 || EmptySkipsTarget() != 100 { for {} }
    if EachTargetOnce() != 302 || OrderedEffects() != 1221 || ContinueSwitchBreak() != 9 { for {} }
    if NestedRanges() != 3 || HugeUnsigned() != 0xfedcba9876543213 || HugeSigned() != 3 { for {} }
    if ContextualWideConstant() != 2 || ShiftContext(8) != 900 || ShiftContext(2) != 304 { for {} }
    if BodyScope() != 33 || ExistingPointerTarget() != 2 || EmptyNilTarget() != 7 { for {} }
    if NilConstantLength() != 3 || GuardedCall() != 6 { for {} }
    if SignedDynamic(-9223372036854775808) != 0 || SignedDynamic(0) != 0 || SignedDynamic(5) != 10 { for {} }
}
