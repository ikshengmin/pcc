// Original PCC regression fixture, repository MIT license.
// https://go.dev/ref/spec#If_statements
// https://go.dev/ref/spec#Empty_statements
// https://go.dev/ref/spec#Order_of_evaluation
package main

var trace int64

func mark(digit int64, value bool) bool {
    trace = trace*10 + digit
    return value
}

func Result() int64 {
    trace = 0
    var result int64
    if ; mark(1, false) {
        return -1
    } else if ; mark(2, true) {
        result = 7
    } else {
        return -2
    }
    if ; mark(3, false) && mark(9, true) {
        return -3
    }
    if ; mark(4, true) || mark(9, false) {
        result += 11
    }
    if n := int64(5); false {
        return -4
    } else if ; n == 5 {
        result += 13
    } else {
        return -5
    }
    if ;
        true {
        if ; trace != 1234 || result != 31 {
            return -6
        }
    }
    if ; (struct{ X int }{3}).X != 3 {
        return -7
    }
    return 0x7edcba9876543210 + result
}

func main() {
    if Result() != 0x7edcba987654322f {
        for {}
    }
}
