package main

type Entry struct {
    Hi uint64
    Lo uint16
    Flag bool
}
type Entries [2]Entry
type Pair struct { Values [2]uint64 }
type Text struct { Value string }

var table = [3]Entry{0: Entry{0xfedcba9876543210, 7, true}, 2: Entry{9, 11, false}}
var effects uint64

func GlobalRecord() uint64 {
    table[1] = Entry{0x8877665544332211, 13, true}
    return table[1].Hi
}

func change(a Entries) Entries {
    a[0].Hi = 0x8877665544332211
    return a
}

func Copies() uint64 {
    a := Entries{Entry{3, 5, true}, Entry{7, 11, false}}
    b := change(a)
    b[1].Hi = 17
    if a[0].Hi != 3 || a[1].Hi != 7 || b[1].Hi != 17 { return 0 }
    return b[0].Hi
}

func NestedRecord() uint64 {
    a := [...]Pair{Pair{[2]uint64{3, 5}}, 2: Pair{[2]uint64{7, 11}}}
    b := a
    b[2].Values[0] = 13
    if a[2].Values[0] != 7 || b[1].Values[0] != 0 { return 0 }
    return a[0].Values[1]*100 + b[2].Values[0]
}

func next() uint64 { effects++; return effects & 1 }
func value() uint64 { effects++; return effects * 10 }

func ParallelFields() uint64 {
    effects = 0
    a := [2]Entry{Entry{3, 5, true}, Entry{7, 11, false}}
    a[next() & 1].Hi, a[next() & 1].Hi = value(), value()
    return effects*10000 + a[0].Hi*100 + a[1].Hi
}

func ArrayRangeCopy() uint64 {
    a := [2]Entry{Entry{3, 5, true}, Entry{7, 11, false}}
    total := uint64(0)
    for _, v := range a { a[1].Hi = 99; total += v.Hi }
    return total
}

func ArrayRangePointer() uint64 {
    a := [2]Entry{Entry{3, 5, true}, Entry{7, 11, false}}
    p := &a
    total := uint64(0)
    for _, v := range p { a[1].Hi = 99; total += v.Hi }
    return total
}

func CompareAndSwitch() uint64 {
    a := [2]Text{Text{"a\x00b"}, Text{"\xff"}}
    b := [2]Text{Text{"a\x00b"}, Text{"\xff"}}
    if a != b { return 0 }
    b[0].Value = "other"
    if a == b { return 0 }
    switch a {
    case b: return 0
    case [2]Text{Text{"a\x00b"}, Text{"\xff"}}: return 42
    }
    return 0
}

func first[T any](a [2]T) T { return a[0] }
func zero[T any]() [0]T { var a [0]T; return a }

func GenericAndEmpty() uint64 {
    a := [2]Entry{Entry{0xfedcba9876543210, 5, true}, Entry{7, 11, false}}
    v := first(a)
    if len(zero[struct{X int}]()) != 0 || [0]Entry{} != [0]Entry{} { return 0 }
    return v.Hi
}

func EmptyRecords() uint64 {
    var a [3]struct{}
    total := 0
    for _, v := range a { _ = v; total++ }
    if a != [3]struct{}{} { return 0 }
    effects = 0
    _ = a[next() & 1]
    a[next() & 1] = struct{}{}
    return uint64(total*100 + len(a)*10) + effects
}

func main() {
    if GlobalRecord() != 0x8877665544332211 { for {} }
    if Copies() != 0x8877665544332211 { for {} }
    if NestedRecord() != 513 { for {} }
    if ParallelFields() != 44030 { for {} }
    if ArrayRangeCopy() != 10 { for {} }
    if ArrayRangePointer() != 102 { for {} }
    if CompareAndSwitch() != 42 { for {} }
    if GenericAndEmpty() != 0xfedcba9876543210 { for {} }
    if EmptyRecords() != 332 { for {} }
}
