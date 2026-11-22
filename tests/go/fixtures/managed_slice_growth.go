package main

import (
    "encoding/binary"
    "os"
    "runtime"
)

func main() {
    old := []uintptr{3, 5}
    full := old[:len(old):len(old)]
    grown := append(full, ^uintptr(0))
    runtime.GC()
    grown[0], full[1] = 7, 11
    observations := [5]uintptr{full[0], full[1], grown[0], grown[1], grown[2]}
    var out [40]byte
    for i, word := range observations {
        binary.LittleEndian.PutUint64(out[i*8:], uint64(word))
    }
    if _, err := os.Stdout.Write(out[:]); err != nil {
        panic(err)
    }
}
