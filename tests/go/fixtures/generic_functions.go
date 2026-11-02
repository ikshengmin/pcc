package main

type Number uint64
type Values [2]uint64
type Record struct { Value uint64; Text string }
var trace uint64

func Identity[T any](value T) T { return value }
func Equal[T comparable](left, right T) bool { return left == right }
func Zero[T any]() T { var value T; return value }
func Swap[T, U any](left T, right U) (U, T) { return right, left }
func Recur[T any](value T, n uint64) T {
    if n == 0 { return value }
    return Recur(value, n-1)
}
func MutualA[T any](value T, n uint64) T {
    if n == 0 { return value }
    return MutualB[T](value, n-1)
}
func MutualB[T any](value T, n uint64) T { return MutualA(value, n) }
func Forward[T any](value T) T { return Identity[T](Identity(value)) }
func First[T any](left, right T) T { return left }
func PickStack[T any](a,b,c,d,e,f uint64, x,y T) T { return x }
func Update[T any](value T, p,q *int) T {
    if p != nil { *p += 1 }
    if q != nil { *q += 2 }
    return value
}
func Select[T comparable](x,y T) bool {
    switch x { case y: return true; default: return false }
}
func NamedResult[T any](value T) (result T) { result = value; return }
func Unused[T any](value T) T { return value }
func Shadow[T any](value T) T { { T := 3; _ = T }; return T(value) }
func pair() (uint64,uint64) { return 43, 44 }
func left() uint64 { trace = trace*10+1; return 7 }
func right() uint64 { trace = trace*10+2; return 9 }

func Scalar() uint64 { return Identity[uint64](0xfedcba9876543210) }
func Named() uint64 {
    value := Number(0x8877665544332211)
    return uint64(Identity(value))
}
func ArrayCopy() uint64 {
    value := Values{0xfedcba9876543210, 7}
    other := Identity(value)
    value[0] = 3
    return other[0] + other[1]
}
func StructCopy() uint64 {
    value := Record{0x8877665544332211, "a\x00b"}
    other := Identity[Record](value)
    value.Value = 0
    if other.Text != "a\x00b" { return 0 }
    return other.Value
}
func TypedBeforeConstants() uint64 {
    a := First(1, uint8(7))
    b := First(uint8(8), 2)
    return uint64(a)*100 + uint64(b)
}
func Comparisons() uint64 {
    result := uint64(0)
    if Equal(3, 3) { result += 1 }
    if Equal(true, true) { result += 2 }
    if Equal("a\x00b", "a\x00b") { result += 4 }
    if Equal(Values{1,2}, Values{1,2}) { result += 8 }
    if !Equal(Record{7,"x"}, Record{7,"y"}) { result += 16 }
    return result
}
func Zeros() uint64 {
    if Zero[int]() != 0 || Zero[bool]() || Zero[string]() != "" { return 0 }
    if Zero[Values]() != (Values{}) || Zero[Record]() != (Record{}) { return 0 }
    return 19
}
func MultipleAndPartial() uint64 {
    text, value := Swap[uint64](23, "abc")
    if text != "abc" { return 0 }
    a,b := Swap(value, uint64(29))
    return a*100+b
}
func Recursion() uint64 { return Recur(uint64(31), 4) + MutualA(uint64(37), 3) }
func Forwarding() uint64 { return uint64(Forward(Number(41))) + First(pair()) }
func EvaluationOrder() uint64 {
    trace = 0
    value := First(left(), right())
    return trace*100 + value
}
func StackArguments() uint64 { return PickStack(1,2,3,4,5,6,uint64(47),uint64(53)) }
func AliasEffects() uint64 {
    x := 5
    value := Update(uint64(59), &x, &x)
    _ = Update[uint64](0, nil, nil)
    return uint64(x)*100 + value
}
func SwitchValues() uint64 {
    if !Select(Values{1,2}, Values{1,2}) { return 0 }
    if Select(Record{1,"a"}, Record{1,"b"}) { return 0 }
    return 61
}
func NamesAndTrailingComma() uint64 { return NamedResult(Shadow(Identity[uint64,](67))) }

func main() {
    if Scalar() != 0xfedcba9876543210 { for {} }
    if Named() != 0x8877665544332211 { for {} }
    if ArrayCopy() != 0xfedcba9876543217 { for {} }
    if StructCopy() != 0x8877665544332211 { for {} }
    if TypedBeforeConstants() != 108 { for {} }
    if Comparisons() != 31 { for {} }
    if Zeros() != 19 { for {} }
    if MultipleAndPartial() != 2923 { for {} }
    if Recursion() != 68 { for {} }
    if Forwarding() != 84 { for {} }
    if EvaluationOrder() != 1207 { for {} }
    if StackArguments() != 47 { for {} }
    if AliasEffects() != 859 { for {} }
    if SwitchValues() != 61 { for {} }
    if NamesAndTrailingComma() != 67 { for {} }
}
