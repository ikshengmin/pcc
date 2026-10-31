// Original PCC regression fixture, repository MIT license.
package main

const high = 1 << 100
const base = high >> 96

func verify(ok bool) {
	if !ok { for {} }
}

func fib(n int) int {
	if n < 2 { return n }
	return fib(n-1) + fib(n-2)
}

func gcd(a, b int) int {
	for a != b {
		if a > b { a -= b } else { b -= a }
	}
	return a
}

func arithmetic(x int64) int64 {
	return (x/6)*100 + x%6
}

func wrapping(x int64) int64 { return x + 1 }
func negate(x int64) int64 { return x / -1 }
func remainder(x int64) int64 { return x % -1 }
func left(x int64) int64 { return x << 80 }
func right(x int64) int64 { return x >> 80 }

func Result() int64 {
	var zero int
	verify(zero == 0)
	verify(base == 16)
	verify(fib(12) == 144)
	verify(gcd(84, 30) == 6)
	verify(arithmetic(-29) == -405)
	verify(wrapping(9223372036854775807) == -9223372036854775808)
	verify(negate(-9223372036854775808) == -9223372036854775808)
	verify(remainder(-9223372036854775808) == 0)
	verify(left(9) == 0 && right(-9) == -1 && right(9) == 0)
	var mask int64 = 0xff
	mask &^= 0b1010
	verify(mask == 245)
	return arithmetic(-29) * 100000000 + int64(fib(12))
}

func main() { verify(Result() == -40499999856) }
