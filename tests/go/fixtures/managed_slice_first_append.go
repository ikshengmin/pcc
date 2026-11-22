package main

import (
    "encoding/binary"
    "os"
    "runtime"
)

func main() {
    inputs := [3]uintptr{0, 1 << 63, ^uintptr(0)}
    var out [24]byte
    for i, input := range inputs {
        result := first(input)
        runtime.GC()
        if result == nil || len(result) != 1 || result[0] != input {
            panic("first append did not survive the producer")
        }
        binary.LittleEndian.PutUint64(out[i*8:], uint64(result[0]))
    }
    if _, err := os.Stdout.Write(out[:]); err != nil {
        panic(err)
    }
}
