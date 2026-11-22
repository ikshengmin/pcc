package main

import (
    "encoding/binary"
    "os"
    "runtime"
)

func main() {
    var out [112]byte
    pos := 0
    for _, n := range [...]uint32{0, 1, 10} {
        result := build(n)
        runtime.GC()
        if (result == nil) != (n == 0) || len(result) != int(n) {
            panic("wrong ownership or length")
        }
        binary.LittleEndian.PutUint64(out[pos:], uint64(len(result)))
        pos += 8
        for i, value := range result {
            if value != uintptr(i) {
                panic("wrong element")
            }
            binary.LittleEndian.PutUint64(out[pos:], uint64(value))
            pos += 8
        }
    }
    if _, err := os.Stdout.Write(out[:pos]); err != nil {
        panic(err)
    }
}
