package main

import (
    "os"
    "runtime"
)

func produce() string {
    storage := [8]byte{77, 65, 0, 255, 195, 169, 90, 88}
    result := string(storage[1:7])
    for i := range storage {
        storage[i] = 0
    }
    return result
}

func main() {
    result := produce()
    runtime.GC()
    if len(result) != 6 || result != "A\x00\xfféZ" {
        panic("escaped byte view")
    }
    if _, err := os.Stdout.Write([]byte(result)); err != nil {
        panic(err)
    }
}
