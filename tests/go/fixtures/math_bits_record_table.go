// Authored observer around unchanged Go 1.27.3 math/bits test declarations.
// The declarations below are from bits_test.go lines 1310-1315, under the
// upstream BSD license in tests/go/upstream/go1.27.3/LICENSE. This is not the
// full upstream package, init function or its own tests.
package main

type entry = struct {
	nlz, ntz, pop int
}

// tab contains results for all uint8 values
var tab [256]entry

func RecordTable() uint64 {
    tab[0] = entry{8, 8, 0}
    tab[255] = entry{0, 0, 8}
    var index uint8 = 255
    return uint64(tab[0].nlz*100 + tab[0].ntz*10 + tab[index].pop)
}

func main() {
    if RecordTable() != 888 { for {} }
}
