package main

type Count uint16
type Text string

var trace uint64
var staticLimit = max(4, 2)

func next(x uint64) uint64 { trace = trace*10+x; return x }
func text(x uint64, s string) string { trace = trace*10+x; return s }
func pair() (int16, int16) { trace++; return -32000, 31000 }
func words() (string, string) { trace++; return "z", "a" }

func UnsignedMin(a, b uint64) uint64 { return min(a, b) }
func UnsignedMax(a, b uint64) uint64 { return max(a, b) }
func SignedMin(a, b int64) int64 { return min(a, b) }
func SignedMax(a, b int64) int64 { return max(a, b) }
func NarrowMin(a, b int8) int8 { return min(a, b) }
func Named() Count { var x Count = 60000; return min(x, 2, 65535) }
func RuneKind() int32 { x := min('A', 66); y := max(65, 'B'); return x+y }
func OneShift(s uint) int { return min(1 << s) }
func RuneShift(s uint) int32 { return max('A' << s) }
func TypedShift(s uint) uint64 { return max(1 << s, uint64(3)) }
func ExactConstant() uint64 {
	const small = min(1 << 100, 7)
	const large = max(1 << 80, 1)
	return small + uint64(large >> 32)
}
func Ordered() uint64 {
	trace = 0
	x := min(next(3), next(1), next(2))
	return trace*10+x
}
func MultiResult() uint64 {
	trace = 0
	x := min(pair())
	if x != -32000 { for {} }
	return trace
}
func StringOrder() uint64 {
	trace = 0
	x := min(text(3, "a\x00z"), text(1, "a\x00"), text(2, "\xff"))
	if x != "a\x00" { for {} }
	return trace*10+uint64(len(x))
}
func StringMaxBytes() uint64 {
	var a Text = "\x80"
	x := max("\x7f", a, "")
	var y Text = x
	if y != "\x80" { for {} }
	return 128
}
func StringTie() uint64 {
	trace = 0
	x := min(text(1, "same"), text(2, "same"))
	if x != "same" { for {} }
	return trace
}
func StringResults() uint64 {
	trace = 0
	x := max(words())
	if x != "z" { for {} }
	return trace
}
func MinLenCall() uint64 {
	trace = 0
	x := len([1]uint64{min(next(3), next(1))})
	return trace*10+uint64(x)
}
func BlankBodies() uint64 { return 42 }
func _() { trace = 999; panic("a blank function is never an initializer") }
func _() { L: goto L }
func _[T any](x T) T { return x }
func _[T comparable](x T) T { return x }

func main() {
	if staticLimit != 4 || trace != 0 { for {} }
	if UnsignedMin(0xffffffffffffffff, 0x8000000000000000) != 0x8000000000000000 { for {} }
	if UnsignedMax(0xffffffffffffffff, 0x8000000000000000) != 0xffffffffffffffff { for {} }
	if SignedMin(-9223372036854775808, 9223372036854775807) != -9223372036854775808 { for {} }
	if SignedMax(-9223372036854775808, 9223372036854775807) != 9223372036854775807 { for {} }
	if NarrowMin(-128, 127) != -128 || Named() != 2 || RuneKind() != 131 { for {} }
	if OneShift(64) != 0 || RuneShift(33) != 0 || TypedShift(63) != 0x8000000000000000 { for {} }
	if ExactConstant() != 281474976710663 { for {} }
	if Ordered() != 3121 || MultiResult() != 1 || StringOrder() != 3122 { for {} }
	if StringMaxBytes() != 128 || StringTie() != 12 || StringResults() != 1 { for {} }
	if MinLenCall() != 311 || BlankBodies() != 42 { for {} }
}
