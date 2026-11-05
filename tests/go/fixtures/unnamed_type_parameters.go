// Original PCC regression fixture, repository MIT license.
// https://go.dev/ref/spec#Type_parameter_declarations
// https://go.dev/ref/spec#Blank_identifier
// https://go.dev/ref/spec#Instantiations
package main

type Number uint64
var trace uint64

func One[_ any](x uint64) uint64 { return x }
func Repeated[_, _ any](x uint64) uint64 { return x }
func Partial[_ any, T any](x T) T { return x }
func Trailing[T any, _ comparable](x T) T { return x }
func BlankValueParameter[_ any](_ uint64, x uint64) uint64 { return x }
func BlankResult[_ any]() (_ uint64) { return }
func Forward[T any](x T) T { return Partial[int](x) }
func Recur[_ any, T any](x T, n uint64) T {
    if n == 0 { return x }
    return Recur[int](x, n-1)
}
func mark(digit uint64) uint64 { trace = trace*10 + digit; return digit }

func Result() uint64 {
    trace = 0
    value := One[int](0xfedcba9876543210)
    value = Repeated[string, uint8](value)
    value = uint64(Partial[bool](Number(value)))
    value = Trailing[uint64, string](value)
    value = Forward(value)
    value = Recur[bool](value, 3)
    result := BlankValueParameter[int](mark(1), mark(2))
    if trace != 12 || result != 2 || BlankResult[string]() != 0 { return 0 }
    return value + result
}

func main() {
    if Result() != 0xfedcba9876543212 { for {} }
}
