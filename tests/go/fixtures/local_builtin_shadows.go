// Complete authored program. This is not a replacement math/bits test package.
package main

var visits int

type Counter int64
func (len Counter) Value() int64 { return int64(len) }
func (c Counter) len() int64 { return int64(c) }

func OriginalShape(nlz, k int) int64 { len := 8 - nlz; return int64(len + k) }
func Short() int64 {
	len := len("abc")
	return int64(len)
}
func Constant() int64 {
	const len = len("abc")
	return int64(len)
}
func Variable() int64 {
	var len = len("four")
	return int64(len)
}
func Scope() int64 {
	var total int64
	{ len := int64(39); total += len }
	return total + int64(len("abc"))
}
func Parameters(len, cap int64) int64 { return len + cap }
func Named() (panic int64) { panic = 42; return }
func Range() int64 {
	var total int64
	for len := range len("abcd") { total += int64(len) }
	return total + 36
}
func ArrayRange() int64 {
	var total int64
	for len, cap := range [2]int{5, 7} { total += int64(len + cap) }
	return total
}
func IfScope() int64 {
	var total int64
	if len := len("abc"); len == 3 { total = int64(len) }
	return total + int64(len("ab"))
}
func ForScope() int64 {
	var total int64
	for cap := 0; cap < 3; cap++ { total += int64(cap) }
	return total + int64(cap([2]int{}))
}
func SwitchScope() int64 {
	var total int64
	switch len := len("abc"); len { case 3: total = 39 }
	return total + int64(len("abc"))
}
func touch() string { visits++; return "abc" }
func InitializerSideEffects() int64 {
	visits = 0
	len := len(touch())
	return int64(visits*10 + len)
}
func ValueMethod() int64 {
	len := Counter(42)
	return len.len() + len.Value() - 42
}
func id[T any](len T) T { return len }
func Generic() int64 { return id(int64(42)) }
func Wide() uint64 {
	len := uint64(0xfedcba9876543210)
	return len
}
func AllNames() int64 {
	append, cap, clear, close, complex, copy, delete, imag, len := int64(1), int64(1), int64(1), int64(1), int64(1), int64(1), int64(1), int64(1), int64(1)
	make, max, min, new, panic, print, println, real, recover := int64(1), int64(1), int64(1), int64(1), int64(1), int64(1), int64(1), int64(1), int64(1)
	return append + cap + clear + close + complex + copy + delete + imag + len + make + max + min + new + panic + print + println + real + recover
}
func main() {
	if OriginalShape(3, 37) != 42 || Short() != 3 || Constant() != 3 || Variable() != 4 || Scope() != 42 || Parameters(35, 7) != 42 || Named() != 42 { for {} }
	if Range() != 42 || ArrayRange() != 13 || IfScope() != 5 || ForScope() != 5 || SwitchScope() != 42 || InitializerSideEffects() != 13 { for {} }
	if ValueMethod() != 42 || Generic() != 42 || Wide() != 0xfedcba9876543210 || AllNames() != 18 { for {} }
}
