package main

import (
    "encoding/binary"
    "os"
    "runtime"
)

func main() {
    owner := make([]uintptr, 2, 4)
    owner[0], owner[1] = 3, 5
    alias := owner[:cap(owner)]
    owner = append(owner, ^uintptr(0))
    runtime.GC()
    observed := [5]uintptr{alias[0], alias[1], alias[2], alias[3]}
    owner = nil
    result := append(alias[:3], 11)
    observed[4] = result[3]
    var out [40]byte
    for i, word := range observed {
        binary.LittleEndian.PutUint64(out[i*8:], uint64(word))
    }
    if _, err := os.Stdout.Write(out[:]); err != nil {
        panic(err)
    }
}
