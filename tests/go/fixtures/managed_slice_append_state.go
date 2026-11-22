package main

import (
    "encoding/binary"
    "os"
    "runtime"
)

func collect(n int) []uintptr {
    var state []uintptr
    var old []uintptr
    for i := 0; i < n; i++ {
        state = append(state, (1<<63)|uintptr(i))
        if i == 0 {
            old = state
        }
        runtime.GC()
    }
    if n > 0 && old[0] != (1 << 63) {
        panic("first alias changed")
    }
    return state
}

func main() {
    state := collect(9)
    var out [72]byte
    for i, word := range state {
        binary.LittleEndian.PutUint64(out[i*8:], uint64(word))
    }
    if _, err := os.Stdout.Write(out[:]); err != nil {
        panic(err)
    }
}
