// Original PCC regression fixture, repository MIT license.
// https://go.dev/ref/spec#Type_constraints
// https://go.dev/ref/spec#Interface_types
// https://go.dev/ref/spec#Type_inference
package main

type Empty interface{}
type Alias = Empty
type Through interface { Empty; Alias }
type AnyAlias = any
type AnyDefined any
type Number uint64
var trace uint64

func Identity[T Through](x T) T { return x }
func Pair[T Alias, U AnyAlias](x T, y U) (T, U) { return x, y }
func Forward[T Empty](x T) T { return Identity(x) }
func Recur[T AnyDefined](x T, n uint64) T {
    if n == 0 { return x }
    return Recur(x, n-1)
}
func Unnamed[_ Alias, T Through](x T) T { return x }
func mark(digit uint64) uint64 { trace = trace*10 + digit; return digit }

func Result() uint64 {
    trace = 0
    value := Identity(Number(0xfedcba9876543210))
    value = Forward(value)
    value = Recur(value, 3)
    value = Unnamed[string](value)
    a, b := Pair[uint64](mark(1), mark(2))
    if trace != 12 || a != 1 || b != 2 { return 0 }
    record := Identity(struct{ Value uint64 }{7})
    array := Identity([2]uint64{3, 5})
    if record.Value != 7 || array[0] != 3 || array[1] != 5 { return 0 }
    return uint64(value) + a + b
}

func main() {
    if Result() != 0xfedcba9876543213 { for {} }
}
