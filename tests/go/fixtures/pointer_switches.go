package main

// Expression switches compare a single captured pointer value. No helper
// relies on allocation identity for distinct zero-size variables or on the
// unspecified relative order of plain loads and calls within one expression.
type IntPointer *int
type Record struct { X int }

var trace int
var values [2]int
var globalValue int

func readOrNil(p *int) int {
	switch p {
	case nil:
		return 17
	default:
		return *p
	}
}

func NilAndLive() uint64 {
	x := 9
	return uint64(readOrNil(nil)*100 + readOrNil(&x))
}

func Identity() uint64 {
	x, y := 7, 7
	p := &x
	switch p {
	case nil:
		return 1
	case &y:
		return 2
	case &x:
		return 9
	}
	return 3
}

func NamedPointer() uint64 {
	x := 23
	var p IntPointer = &x
	switch p {
	case IntPointer(nil):
		return 1
	case &x:
		return uint64(*p)
	}
	return 2
}

func selectorIndex() uint8 { trace = trace*10 + 1; return 0 }
func firstIndex() uint8 { trace = trace*10 + 2; return 1 }
func secondIndex() uint8 { trace = trace*10 + 3; values[0] = 19; return 0 }
func skippedIndex() uint8 { trace = trace*10 + 4; return 0 }

func CaptureAndOrder() uint64 {
	trace = 0
	values = [2]int{5, 7}
	switch &values[selectorIndex() & 1] {
	default:
		return 1
	case &values[firstIndex() & 1]:
		return 2
	case &values[secondIndex() & 1], &values[skippedIndex() & 1]:
		return uint64(trace*100 + values[0])
	}
}

func FallthroughSkipsCase() uint64 {
	trace = 0
	x := 5
	p := &x
	switch p {
	case &x:
		x += 2
		fallthrough
	case &values[skippedIndex() & 1]:
		x *= 3
	}
	return uint64(trace*100 + x)
}

func InitAndShadow() uint64 {
	x := 11
	var p *int
	switch p := &x; p {
	case nil:
		return 1
	default:
		*p += 2
	}
	if p != nil { return 2 }
	return uint64(x)
}

func LoopControl() uint64 {
	x, y, sum := 2, 5, 0
	var p *int
	for i := 0; i < 3; i++ {
		if i == 0 { p = nil } else if i == 1 { p = &x } else { p = &y }
		switch p {
		case nil:
			continue
		case &x:
			sum += *p
			break
		default:
			sum += *p
		}
		sum++
	}
	return uint64(sum)
}

func TypedNil() uint64 {
	switch IntPointer(nil) {
	case nil:
		return 31
	case (*int)(nil):
		return 2
	default:
		return 3
	}
}

func RepeatedAliasCases() uint64 {
	x := 37
	p, q := &x, &x
	switch p {
	case q:
		return uint64(*p)
	case p:
		return 2
	}
	return 3
}

func equalParameter(p, q *int) int {
	if q == nil { return 0 }
	switch p {
	case q:
		return *p
	default:
		return 3
	}
}

func ParameterEquality() uint64 {
	x := 11
	return uint64(equalParameter(&x, &x)*100 + equalParameter(nil, &x)*10 + equalParameter(&x, nil))
}

func addressSelector(p *int) int {
	switch &globalValue {
	case p:
		return *p
	default:
		return 13
	}
}

func AddressSelectorGuard() uint64 {
	globalValue = 29
	return uint64(addressSelector(&globalValue)*100 + addressSelector(nil))
}

func updateRecord(p *Record) int {
	switch p {
	case nil:
		return 0
	default:
		p.X += 7
		return p.X
	}
}

func RecordUpdate() uint64 {
	r := Record{41}
	return uint64(updateRecord(&r)*100 + updateRecord(nil))
}

func WideAlias() uint64 {
	x := uint64(0xfedcba9876543210)
	p := &x
	switch p {
	case &x:
		*p += 7
		return x
	default:
		return 0
	}
}

func EmptyAndNoMatch() uint64 {
	x, y := 3, 5
	p := &x
	switch p {}
	switch p { case &y: return 1 }
	return uint64(*p)
}

func main() {
	if NilAndLive() != 1709 { for {} }
	if Identity() != 9 { for {} }
	if NamedPointer() != 23 { for {} }
	if CaptureAndOrder() != 12319 { for {} }
	if FallthroughSkipsCase() != 21 { for {} }
	if InitAndShadow() != 13 { for {} }
	if LoopControl() != 9 { for {} }
	if TypedNil() != 31 { for {} }
	if RepeatedAliasCases() != 37 { for {} }
	if ParameterEquality() != 1130 { for {} }
	if AddressSelectorGuard() != 2913 { for {} }
	if RecordUpdate() != 4800 { for {} }
	if WideAlias() != 0xfedcba9876543217 { for {} }
	if EmptyAndNoMatch() != 3 { for {} }
}
