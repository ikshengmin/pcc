package main

type _ interface{}
type _ interface { Value() uint64 }
type _ uint64
type _ = uint64
type _ = Later
type Later uint64
type _ struct { Value uint64 `json:"value"` }
type _ [2]uint64
type _ *uint64
type _ = interface { Compare(uint64) bool }
type ResultAlias = Later

const _ = 7
func _() { panic("blank function is not an initializer") }

func Result() uint64 {
	var x ResultAlias = 0xfedcba9876543210
	return uint64(x)
}

func main() {
	if Result() != 0xfedcba9876543210 { for {} }
}
