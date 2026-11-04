package main

type Count int
type Alias = Count
type Read interface { Read(Count) Count }
type Same interface { Read(Alias) Alias }
type Combined interface { Read; Same }
type Empty interface {}
type Anything = any
type Node interface { Next() Node }
type Left interface { Right() Right }
type Right interface { Left() Left }
type Variadic interface { Apply(head int, tail ...Count) (value Count, ok bool) }
type Arrays interface { Read(x [2]Count) [3]Count }
type ErrorLike interface { Error() string }
type ErrorAlias = error

func Result() int64 {
    var e ErrorLike
    var original error = e
    var alias ErrorAlias = original
    if alias != nil { return -1 }
    return 42
}

func main() {
    if Result() != 42 { panic(1) }
}
