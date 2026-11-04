package main

type Tagged struct {
	X uint64 `json:"x"`
	Y, Z int64 "pair"
	Name string `name`
	Array [2]int64 `array`
	Ready bool `ready`
}

type Other struct {
	X uint64 `other:"x"`
	Y, Z int64
	Name string "other name"
	Array [2]int64
	Ready bool ""
}

type Raw = struct { X uint64 `json:"x"` }
type Escaped = struct { X uint64 "json:\"x\"" }
type Empty = struct { X uint64 "" }
type Absent = struct { X uint64 }

type NestedA struct {
	Inner struct { X uint64 `inner-a`; A [2]int64 `array-a` } `outer-a`
}
type NestedB struct {
	Inner struct { X uint64 `inner-b`; A [2]int64 `array-b` } `outer-b`
}

var Stored = Other(Tagged{X: 0xfedcba9876543210, Y: -7, Z: 9,
	Name: "tagged", Array: [2]int64{11, 13}, Ready: true})
var Calls uint64

func Identity[T any](value T) T { return value }

func (value Tagged) Sum() int64 { return value.Y + value.Z + value.Array[0] }

func AliasIdentity() uint64 {
	var raw Raw = Raw{X: 42}
	var escaped Escaped = raw
	var empty Empty = Empty{X: 7}
	var absent Absent = empty
	return escaped.X + absent.X
}

func CopiesAndMethods() uint64 {
	a := Tagged{X: 0xfedcba9876543210, Y: -7, Z: 9,
		Name: "tagged", Array: [2]int64{11, 13}, Ready: true}
	b := Other(a)
	b.Y = 100
	b.Array[0] = 101
	if a.Sum() != 13 || b.Y != 100 || a.Array[0] != 11 ||
		b.Name != "tagged" || !b.Ready || b.Z != 9 { panic("copy or method") }
	return b.X
}

func NestedConversion() uint64 {
	a := NestedA{Inner: struct { X uint64 `inner-a`; A [2]int64 `array-a` }{
		X: 0x123456789abcdef0, A: [2]int64{3, 5}}}
	b := NestedB(a)
	b.Inner.A[1] = 8
	if a.Inner.A[1] != 5 || b.Inner.A[1] != 8 { panic("nested copy") }
	return b.Inner.X
}

func PackageConversion() uint64 {
	if Stored.Y != -7 || Stored.Z != 9 || Stored.Name != "tagged" ||
		Stored.Array[0] != 11 || Stored.Array[1] != 13 || !Stored.Ready { panic("global") }
	return Stored.X
}

func GenericInstances() uint64 {
	a := Identity(struct { X uint64 `a` }{1})
	b := Identity(struct { X uint64 `b` }{2})
	c := Identity(struct { X uint64 "" }{3})
	d := Identity(struct { X uint64 }{4})
	return a.X*1000 + b.X*100 + c.X*10 + d.X
}

func produce() Tagged {
	Calls++
	return Tagged{X: 37}
}

func ConversionEffects() uint64 {
	Calls = 0
	value := Other(produce())
	if Calls != 1 { panic("conversion evaluation") }
	return value.X + Calls
}

func CompareAfterConversion() uint64 {
	a := Tagged{X: 17, Name: "same", Array: [2]int64{3, 5}}
	b := Other(a)
	if a != Tagged(b) { panic("tagged comparison") }
	switch a {
	case Tagged(b): return 73
	default: return 0
	}
}

func main() {
	if AliasIdentity() != 49 || CopiesAndMethods() != 0xfedcba9876543210 ||
		NestedConversion() != 0x123456789abcdef0 || PackageConversion() != 0xfedcba9876543210 ||
		GenericInstances() != 1234 || ConversionEffects() != 38 ||
		CompareAfterConversion() != 73 { panic("struct tags") }
}
