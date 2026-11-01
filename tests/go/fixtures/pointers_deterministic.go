package main

// Original complete programs for Go Pointer_types, Address_operators,
// Assignments, Comparison_operators, Selectors, and Index_expressions.
// Failure remains an observable timeout, not an emulated panic runtime.

type Pair struct { A int; B int }
type Word uint64
type IntPointer *int
type IntPointerAlias = *int
type Counter int
func (c Counter) Add(n int) int { c += Counter(n); return int(c) }

var pointerCalls int
var globalPoint int
func pointerIndex() uint8 { pointerCalls++; return 1 }
func pointerRight() int { pointerCalls = pointerCalls*10+2; return 3 }

func WideAlias() uint64 {
    x := uint64(0xfedcba9876543210)
    p := &x
    q := p
    *q += 7
    return *p
}
func Rebind() int {
    x, y := 3, 7
    p := &x
    q := p
    *q = 5
    p = &y
    *p += 2
    return x*100+y
}
func Parallel() int {
    x, y := 3, 7
    p := &x
    p, *p = &y, 13
    *p, p = 17, &x
    return *p*100+y
}
func Equal() int {
    x, y := 1, 2
    p := &x
    q := p
    n := 0
    if p == &x { n += 1 }
    if p != &y { n += 2 }
    if p == q { n += 4 }
    if p != nil { n += 8 }
    if nil != p { n += 16 }
    if (*int)(nil) == nil { n += 32 }
    return n
}
func RecordSnapshot() int {
    x := Pair{3, 4}
    p := &x
    old := *p
    p.A = 9
    (*p).B = 11
    return old.A*1000+old.B*100+x.A*10+x.B
}
func RecordStore() int {
    x := Pair{1, 2}
    p := &x
    *p = Pair{8, 9}
    q := &p.B
    *q += 1
    return x.A*100+x.B
}
func ArrayStore() int {
    a := [3]int{1, 2, 3}
    p := &a
    old := *p
    p[1] = 9
    *p = [3]int{4, 5, 6}
    return old[1]*1000+p[1]*100+len(p)*10+cap(p)
}
func IndexOnce() int {
    pointerCalls = 0
    var a [256]int
    p := &a[pointerIndex()]
    *p = 7
    (*p)++
    return pointerCalls*100+a[1]
}
func CompoundOnce() int {
    pointerCalls = 0
    var a [256]int
    a[1] = 10
    *(&a[pointerIndex()]) += pointerRight()
    return pointerCalls*100+a[1]
}
func ShortRebind() int {
    x, y := 3, 4
    p := &x
    p, q := &y, p
    *p = 7
    *q = 9
    return x*100+y
}
func NestedScope() int {
    x := 1
    p := &x
    { q := p; *q += 4; r := &*q; *r *= 3 }
    return *p
}
func IterationLocal() int {
    total := 0
    for i := 0; i < 4; i++ {
        x := i
        p := &x
        *p += 2
        total += x
    }
    return total
}
func OuterLoopBinding() int {
    i := 0
    p := &i
    for ; i < 3; i++ { *p += 1 }
    return i
}
func NamedPointer() int {
    x := 1
    var p IntPointer = &x
    var q IntPointerAlias = p
    *q += 2
    r := (*int)(p)
    *r += 4
    if p != q { for {} }
    return *p
}
func NamedValue() uint64 {
    x := Word(0xfedcba9876543210)
    p := &x
    *p += 9
    return uint64(*p)
}
func ValueMethod() int {
    x := Counter(4)
    p := &x
    n := p.Add(7)
    m := (*Counter).Add(p, 2)
    return n*100+m*10+int(*p)
}
func StaticString() int {
    x := "first"
    p := &x
    *p = "changed"
    return len(x)
}
func BoolAlias() int {
    x := false
    p := &x
    *p = !*p
    if x { return 19 }
    return 0
}
func GlobalAddress() int {
    globalPoint = 3
    p := &globalPoint
    *p += 5
    return globalPoint
}
func ParameterAddress(x int) int {
    p := &x
    *p += 5
    return x
}
func ResultAddress() (x int) {
    p := &x
    *p = 23
    return
}
func DirectNilTypes() int {
    n := len((*[3]int)(nil))+cap((*[3]int)(nil))
    if (*struct{X int})(nil) == nil { n++ }
    return n
}
var receiverGlobal Counter
func (c Counter) Snapshot(n int) int { return int(c) }
func receiverChange() int { receiverGlobal=9; return 0 }
func ExplicitReceiverOrder() int {
    receiverGlobal=1
    p:=&receiverGlobal
    return (*Counter).Snapshot(p,receiverChange())
}
// Explicit statement boundaries make the receiver snapshot independent of
// the unspecified relative order of a plain dereference and an argument call.
func CapturedReceiverOrder() int {
    receiverGlobal=1
    p:=&receiverGlobal
    captured:=*p
    argument:=receiverChange()
    return captured.Snapshot(argument)
}
func main() {
    if WideAlias() != 0xfedcba9876543217 { for {} }
    if Rebind() != 509 { for {} }
    if Parallel() != 1317 { for {} }
    if Equal() != 63 { for {} }
    if RecordSnapshot() != 3501 { for {} }
    if RecordStore() != 810 { for {} }
    if ArrayStore() != 2533 { for {} }
    if IndexOnce() != 108 { for {} }
    if CompoundOnce() != 1213 { for {} }
    if ShortRebind() != 907 { for {} }
    if NestedScope() != 15 { for {} }
    if IterationLocal() != 14 { for {} }
    if OuterLoopBinding() != 4 { for {} }
    if NamedPointer() != 7 { for {} }
    if NamedValue() != 0xfedcba9876543219 { for {} }
    if ValueMethod() != 1164 { for {} }
    if StaticString() != 7 { for {} }
    if BoolAlias() != 19 { for {} }
    if GlobalAddress() != 8 { for {} }
    if ParameterAddress(4) != 9 { for {} }
    if ResultAddress() != 23 { for {} }
    if DirectNilTypes() != 7 { for {} }
    if ExplicitReceiverOrder() != 9 { for {} }
    if CapturedReceiverOrder() != 1 { for {} }
}
