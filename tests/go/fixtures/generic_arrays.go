package main

type Number uint64
type Alias = Number
type Values [2]Number
type Other [2]Number
var trace uint64

func First[T any](values [2]T) T { return values[0] }
func Replace[T any](values [2]T, replacement T) [2]T {
    values[1] = replacement
    return values
}
func Build[T any](a, b T) [2]T { return [...]T{a, b} }
func Zeros[T any]() [3]T { var values [3]T; return values }
func Empty[T any](values [0]T) int { return len(values) + cap(values) }
func Equal[T comparable](a, b [2]T) bool { return a == b }
func Select[T comparable](a, b [2]T) bool {
    switch a { case b: return true; default: return false }
}
func Forward[T any](values [2]T) T { return First(values) }
func ExplicitForward[T any](values [2]T) [2]T { return Replace[T](values, First[T](values)) }
func Recur[T any](values [2]T, n uint64) T {
    if n == 0 { return values[1] }
    return Recur(values, n-1)
}
func Partial[T, U any](a [2]T, b [2]U) (T, U) { return a[1], b[0] }
func Last[T any](values [2]T) T {
    var result T
    for _, value := range values { result = value }
    return result
}
func Later[T any](first T, values [2]T) T { return first }
func ArrayAndValue() ([2]uint64, uint64) { return [2]uint64{31, 37}, 41 }
func left() [2]uint64 { trace = trace*10+1; return [2]uint64{7, 9} }
func right() uint64 { trace = trace*10+2; return 11 }
func large[T any](a,b,c,d,e,f uint64, values [3]T) [3]T { return values }
func lengths[T any](values [2]T) uint64 {
    // Both expressions are compile-time constants: no call hidden inside.
    const size = len(values) + cap(values)
    return uint64(size)
}

func NamedInference() uint64 {
    a := Values{0xfedcba9876543210, 7}
    var result Number = First(a)
    var explicit Number = First[Alias](Other{3, 5})
    return uint64(result) + uint64(explicit)
}
func CopyAndResult() uint64 {
    a := [2]uint64{13, 17}
    b := Replace(a, 19)
    a[0] = 1
    return b[0]*100 + b[1]
}
func LocalAndLiteral() uint64 {
    a := Build(uint64(23), uint64(29))
    z := Zeros[uint64]()
    if z != ([3]uint64{}) { return 0 }
    return a[0]*100 + Last(a)
}
func ConstraintChecks() uint64 {
    a := [2]bool{true, false}
    if !Equal(a,a) || !Select(a,a) || Equal(a, [2]bool{}) { return 0 }
    return 43
}
func AbstractForwarding() uint64 {
    a := Values{Number(47), Number(53)}
    b := ExplicitForward(a)
    return uint64(Forward(a))*100 + uint64(b[1])
}
func RecursiveAndMultiple() uint64 {
    a := Recur([2]uint64{59,61}, 3)
    b := Replace(ArrayAndValue())
    return a*100 + b[1]
}
func PartialInference() uint64 {
    a,b := Partial[uint8]([2]uint8{1,67}, [2]uint64{71,73})
    return uint64(a)*100+b
}
func ConstantsAfterArrays() uint64 {
    return uint64(Later(79, [2]uint8{}))*100 + uint64(Later(uint8(83), [2]uint8{}))
}
func EvaluationOnce() uint64 {
    trace = 0
    a := Replace(left(), right())
    return trace*10000 + a[0]*100 + a[1]
}
func ZeroLengthAndSize() uint64 {
    return uint64(Empty([0]Number{})) + lengths([2]uint64{})
}
func StackArrayResult() uint64 {
    a := large(1,2,3,4,5,6, [3]uint64{0x8877665544332200,7,10})
    return a[0]+a[1]+a[2]
}

func main() {
    if NamedInference() != 0xfedcba9876543213 { for {} }
    if CopyAndResult() != 1319 { for {} }
    if LocalAndLiteral() != 2329 { for {} }
    if ConstraintChecks() != 43 { for {} }
    if AbstractForwarding() != 4747 { for {} }
    if RecursiveAndMultiple() != 6141 { for {} }
    if PartialInference() != 6771 { for {} }
    if ConstantsAfterArrays() != 7983 { for {} }
    if EvaluationOnce() != 120711 { for {} }
    if ZeroLengthAndSize() != 4 { for {} }
    if StackArrayResult() != 0x8877665544332211 { for {} }
}
