// Original PCC regression program; no generated or rewritten upstream source.
package main

const TableSize = 3
var Table = [TableSize]uint64{0xfedcba9876543210, 7, 9}
var Sparse = [...]uint16{3: 65535, 5}
var Wide = [256]uint64{255: 0x8123456789abcdef}
var Counter int64
var Flag = true

func CopyLocal() uint64 {
	a := Table
	b := a
	a[0] = 3
	return b[0]
}
func ChangeArgument(a [3]uint64) uint64 {
	a[0] = 13
	return a[0]
}
func CopyArgument() uint64 {
	a := Table
	_ = ChangeArgument(a)
	return a[0]
}
func Make() [3]uint64 { return [3]uint64{0x8877665544332211, 4, 6} }
func CopyResult() uint64 {
	a := Make()
	b := a
	a[0] = 2
	return b[0]
}
func TemporaryIndex() uint64 { return Make()[0] }
func Lookup(x uint8) uint64 { return Wide[x] }
func Masked(x int64) uint64 { return Table[x & 1] }
func Shifted(x uint64) uint64 { return Table[x >> 63] }
func Zero() int64 { var a [3]int64; return a[0] + a[1] + a[2] }
func SparseResult() uint64 { return uint64(Sparse[3])<<32 | uint64(Sparse[4]) }
func PassEmpty(x [0]int64) [0]int64 { return x }
func Empty() int64 { a := PassEmpty([0]int64{}); _ = a; return 71 }
func Boolean() int64 {
	a := [2]bool{1: Flag}
	if !a[0] && a[1] { return 93 }
	return 0
}
func Next() uint8 { Counter = Counter*10 + 1; return 0 }
func Value() int64 { Counter = Counter*10 + 2; return 8 }
func Parallel() int64 {
	Counter = 0
	a := [2]int64{4, 5}
	i := uint8(0)
	i, a[i & 1] = 1, 6
	a[Next() & 1], a[1] = Value(), a[0]
	return Counter*1000 + a[0]*100 + a[1]*10 + int64(i)
}
func Compound() int64 {
	Counter = 0
	a := [2]int64{4, 5}
	a[Next() & 1] += Value()
	return Counter*100 + a[0]
}
func Swap() uint64 {
	a := [3]uint64{11, 12, 13}
	b := Table
	a, b = b, a
	return a[0] ^ b[0]
}
func MutateGlobal() uint64 {
    saved := Table
    Table[1] = 0xffff000011112222
    value := Table[1]
    Table = saved
    return value
}
func Snapshot(a [3]uint64, ignored int64) uint64 { return a[0] }
func ReadTable() [3]uint64 { return Table }
func Bump() int64 { Table[0] = 99; return 0 }
func ArgumentSnapshot() uint64 {
    saved := Table
    value := Snapshot(ReadTable(), Bump())
    Table = saved
    return value
}
func Result() int64 { return int64(CopyLocal()) }
func main() {
    if MutateGlobal() != 0xffff000011112222 { for {} }
    if ArgumentSnapshot() != 0xfedcba9876543210 { for {} }
	if CopyLocal() != 0xfedcba9876543210 { for {} }
	if CopyArgument() != 0xfedcba9876543210 { for {} }
	if CopyResult() != 0x8877665544332211 { for {} }
	if TemporaryIndex() != 0x8877665544332211 { for {} }
	if Lookup(255) != 0x8123456789abcdef { for {} }
	if Masked(-1) != 7 || Shifted(0xffffffffffffffff) != 7 { for {} }
	if Zero() != 0 || Empty() != 71 || Boolean() != 93 { for {} }
	if SparseResult() != 281470681743365 { for {} }
	if Parallel() != 12861 || Compound() != 1212 { for {} }
	if Swap() != 0xfedcba987654321b { for {} }
}
