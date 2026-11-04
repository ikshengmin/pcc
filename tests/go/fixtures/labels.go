package main

func Forward() uint64 {
	x := uint64(0)
	goto Done
	x = 99
Done:
	x += 7
	return x
}

func Backward() uint64 {
	x := uint64(0)
Again:
	x++
	if x < 5 {
		goto Again
	}
	return x
}

func AfterReturn() uint64 {
	goto Done
	return 99
Done:
	return (1 << 56) + 37
}

func OuterBreak() uint64 {
	x := uint64(0)
Outer:
	for i := 0; i < 4; i++ {
		for j := 0; j < 4; j++ {
			x++
			if j == 1 {
				break Outer
			}
		}
	}
	return x
}

func OuterContinue() uint64 {
	x := uint64(0)
Outer:
	for i := 0; i < 4; i++ {
		for j := 0; j < 3; j++ {
			x++
			continue Outer
		}
		x += 100
	}
	return x
}

func SwitchBreak() uint64 {
	x := uint64(0)
Choice:
	switch 1 {
	case 1:
		for {
			x = 17
			break Choice
		}
	default:
		x = 99
	}
	return x
}

func ContinueRange() uint64 {
	x := uint64(0)
Outer:
	for _, v := range [3]uint64{2, 3, 5} {
		for {
			x += v
			continue Outer
		}
	}
	return x
}

func ContinueString() uint64 {
	x := uint64(0)
Outer:
	for _, r := range "Aé" {
		for {
			x += uint64(r)
			continue Outer
		}
	}
	return x
}

func FallthroughLabel() uint64 {
	x := uint64(0)
	switch 2 {
	case 2:
		goto Next
	Next:
		fallthrough
	default:
		x = 7
	}
	return x
}

func BlockExit() uint64 {
	x := uint64(0)
	{
		x = 4
		goto Done
		x = 55
	}
Done:
	return x
}

func Names() uint64 {
	L := uint64(0)
L:
	L++
	if L < 3 {
		goto L
	}
_:
	L++
_:
	return L
}

func Generic[T any](x T) T {
	goto Return
Return:
	return x
}

func GenericResult() uint64 {
	return Generic(uint64(1<<50) + 9)
}

func CaseLocalGoto() uint64 {
	x := uint64(0)
	switch 1 {
	case 1:
	Again:
		x++
		if x < 3 {
			goto Again
		}
		break
	default:
		x = 99
	}
	return x
}

func main() {
	if Forward() != 7 || Backward() != 5 || AfterReturn() != (1<<56)+37 ||
		OuterBreak() != 2 || OuterContinue() != 4 || SwitchBreak() != 17 ||
		ContinueRange() != 10 || ContinueString() != 298 || FallthroughLabel() != 7 ||
		BlockExit() != 4 || Names() != 4 || GenericResult() != (1<<50)+9 || CaseLocalGoto() != 3 {
		for {
		}
	}
}
