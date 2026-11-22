package main

func build(n uint32) []uintptr {
    var result []uintptr
    for index := uint32(0); index < n; index++ {
        result = append(result, uintptr(index))
    }
    return result
}
