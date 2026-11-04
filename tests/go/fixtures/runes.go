package main

func DefaultRune() int32 {
	r := '界'
	return r
}

func MixedKinds() int32 {
	a := 'a' + 1
	b := 1 + 'a'
	return a + b
}

func ShiftContexts() uint64 {
	var n uint = 33
	var wide uint64 = 'A' << n
	var narrow int32 = 'A' << n
	return wide + uint64(narrow)
}

func LeftShiftKind() int {
	x := 1 << '\x04'
	return x
}

func WideConstant() uint64 {
	const r = 'A' << 40
	return r + 3
}

func RuneRange() int32 {
	var total int32
	for i := range '\x04' {
		var r int32 = i
		total += r
	}
	return total
}

func ExistingRange() uint8 {
	var i uint8
	var total uint8
	for i = range '\x04' {
		total += i
	}
	return total
}

func Pick[T comparable](a, b T) T {
	if a == b {
		return a
	}
	return b
}

func GenericKinds() int32 {
	a := Pick('A', 66)
	b := Pick(65, 'B')
	return a + b
}

func RuneString() uint64 {
	if string('\xff') == "ÿ" && len(string('\377')) == 2 {
		return '\xff'
	}
	return 0
}

func CaseRune() uint64 {
	x := 'B'
	switch x {
	case 'A':
		return 1
	case 66:
		return 2
	default:
		return 0
	}
}

func ArrayRunes() uint64 {
	a := [3]uint64{'A', '\377', '\u672c'}
	return a[0] + a[1] + a[2]
}

func main() {
	if DefaultRune() != 30028 || MixedKinds() != 196 || ShiftContexts() != 65<<33 ||
		LeftShiftKind() != 16 || WideConstant() != (65<<40)+3 || RuneRange() != 6 ||
		ExistingRange() != 6 || GenericKinds() != 132 || RuneString() != 255 ||
		CaseRune() != 2 || ArrayRunes() != 26732 {
		for {
		}
	}
}
