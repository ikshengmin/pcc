package main

func Value() uint64 {
    a := [3]uint64{0xfedcba9876543200, 7, 9}
    var sum uint64
    for _, value := range a { sum += value }
    return sum
}

func main() {
    if Value() != 0xfedcba9876543210 { for {} }
}
