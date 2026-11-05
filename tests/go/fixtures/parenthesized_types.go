package main

type Count uint64

const (
	Base (((uint64))) = 0xfedcba9876543210 + iota
	Next
)
const Limit (int) = 3
var Global ((Count)) = 7
var Data ([2]uint64) = [2]uint64{11, 13}
var Pair (struct { X int; Y uint64 }) = struct { X int; Y uint64 }{17, 19}

func Result() uint64 {
	var local ((uint64)) = Base
	var p (*uint64) = &local
	var n (int)
	var row ([2]int) = [2]int{23, 29}
	var record (struct { V uint64 }) = struct { V uint64 }{31}
	const (
		first (int) = 3 + iota
		second
	)
	if Next != Base+1 || Limit != 3 || Global != 7 || Data[0] != 11 || Data[1] != 13 {
		for {}
	}
	if Pair.X != 17 || Pair.Y != 19 || n != 0 || row[0] != 23 || row[1] != 29 {
		for {}
	}
	if record.V != 31 || first != 3 || second != 4 {
		for {}
	}
	*p = Next
	return local
}

func main() {
	if Result() != 0xfedcba9876543211 {
		for {}
	}
}
