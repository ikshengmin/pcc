// Original PCC regression fixture, repository MIT license.
package main

const wordBits = 32 << (^uint(0) >> 63)
func verify(ok bool) { if !ok { for {} } }

func inc8(x int8) int8 { return x + 1 }
func inc16(x int16) int16 { return x + 1 }
func inc32(x int32) int32 { return x + 1 }
func inc64(x int64) int64 { return x + 1 }
func incU8(x uint8) uint8 { return x + 1 }
func incU16(x uint16) uint16 { return x + 1 }
func incU32(x uint32) uint32 { return x + 1 }
func incU64(x uint64) uint64 { return x + 1 }
func divU64(x uint64) uint64 { return x / 3 }
func remU64(x uint64) uint64 { return x % 3 }
func divI8(x int8) int8 { return x / -1 }
func mul8(x int8) int8 { return x * 2 }
func mulU8(x uint8) uint8 { return x * 3 }
func quotient8(x int8) int8 { return x / 6 }
func remainder8(x int8) int8 { return x % 6 }
func quotientU8(x uint8) uint8 { return x / 3 }
func remainderU16(x uint16) uint16 { return x % 7 }
func remI16(x int16) int16 { return x % -1 }
func less(x uint64, y uint64) bool { return x < y }
func signedWide(x int8) uint64 { return uint64(x) }
func unsignedWide(x uint8) int64 { return int64(x) }
func narrow(x int64) int8 { return int8(x) }
func narrowU(x int64) uint16 { return uint16(x) }
func aliasByte(x byte) uint8 { return x }
func aliasRune(x rune) int32 { return x }
func pointerBits(x uintptr) uint64 { return uint64(x) }
func invert(x uint16) uint16 { return ^x }
func left8(x uint8, count uint64) uint8 { return x << count }
func right8(x int8, count uint64) int8 { return x >> count }
func rightU8(x uint8, count uint64) uint8 { return x >> count }
func left64(x uint64, count uint8) uint64 { return x << count }
func context(count uint) uint8 { return (1 << count) + (2 << count) }
func convertedContext(count uint64) uint8 { return uint8(1 << count) }
func comparedContext(count uint64) bool { return (1 << count) > 0 }

func Result() int64 {
	verify(wordBits == 64)
	verify(inc8(127) == -128 && inc16(32767) == -32768)
	verify(inc32(2147483647) == -2147483648)
	verify(inc64(9223372036854775807) == -9223372036854775808)
	verify(incU8(255) == 0 && incU16(65535) == 0)
	verify(incU32(4294967295) == 0 && incU64(18446744073709551615) == 0)
	verify(divU64(18446744073709551615) == 6148914691236517205)
	verify(remU64(18446744073709551614) == 2)
	verify(divI8(-128) == -128 && remI16(-32768) == 0)
	verify(mul8(-128) == 0 && mulU8(200) == 88)
	verify(quotient8(-29) == -4 && remainder8(-29) == -5)
	verify(quotientU8(255) == 85 && remainderU16(65535) == 1)
	verify(less(1, 18446744073709551615) && !less(18446744073709551615, 1))
	verify(signedWide(-1) == 18446744073709551615)
	verify(unsignedWide(255) == 255 && narrow(511) == -1 && narrowU(-1) == 65535)
	verify(aliasByte(255) == 255 && aliasRune(-2147483648) == -2147483648)
	verify(pointerBits(18446744073709551615) == 18446744073709551615)
	verify(invert(0) == 65535 && ^uint8(0) == 255)
	verify(left8(255, 7) == 128 && left8(255, 8) == 0)
	verify(left8(255, 18446744073709551615) == 0)
	verify(right8(-128, 7) == -1 && right8(-128, 18446744073709551615) == -1)
	verify(rightU8(255, 7) == 1 && rightU8(255, 8) == 0)
	verify(left64(1, 63) == 9223372036854775808 && left64(1, 64) == 0)
	verify(context(7) == 128 && context(8) == 0)
	verify(convertedContext(7) == 128 && convertedContext(8) == 0)
	verify(comparedContext(62) && !comparedContext(63))
	return int64(signedWide(-1))
}
func main() { verify(Result() == -1) }
