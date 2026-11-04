package main

type Count int
type Other int
type Value interface { Value() int }
type Mutate interface { Value() int; Add(int) }
type Empty interface {}

func(c Count) Value() int { return int(c) }
func(c *Count) Add(n int) { *c += Count(n) }
func(o Other) Value() int { return int(o) }

type Left struct { Count }
type Right struct { Count }
type Diamond struct { Left; Right }
type Shallow struct { Diamond; Count }

func pass(v Value) Value { return v }

func main() {
    c := Count(7)
    var copied Value = c
    c = 9
    if copied.Value() != 7 || pass(copied).Value() != 7 { panic(1) }
    var mutable Mutate = &c
    mutable.Add(3)
    if c != 12 || mutable.Value() != 12 { panic(2) }
    var pointer *Count
    var typedNil Empty = pointer
    if typedNil == nil { panic(3) }
    var absent Empty
    if absent != nil { panic(4) }
    if Empty(Count(7)) == Empty(Other(7)) { panic(5) }
    var shallow Value = Shallow{Count: 11}
    if shallow.Value() != 11 { panic(6) }
}
