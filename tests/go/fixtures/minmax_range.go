package main

var trace uint64

func next(x uint64) uint64 { trace = trace*10+x; return x }

func ArrayNoIndex() uint64 {
	trace = 0
	var count uint64
	for range [2]uint64{min(next(3), next(1)), max(next(4), next(2))} {
		count++
	}
	return trace*10+count
}

func ArrayOneIndex() uint64 {
	trace = 0
	var sum uint64
	for i := range [2]uint64{max(next(2), next(5)), min(next(7), next(1))} {
		sum += uint64(i)+1
	}
	return trace*10+sum
}

func ArrayBlankValue() uint64 {
	trace = 0
	var sum uint64
	for i, _ := range [1]uint64{min(next(6), next(4))} {
		sum += uint64(i)+1
	}
	return trace*10+sum
}

func IntegerBound() uint64 {
	trace = 0
	var sum uint64
	for i := range max(next(2), next(3)) {
		sum += i
	}
	return trace*10+sum
}

func VariableMin(a, b int) int {
	n := 0
	for range [1]int{min(a, b)} { n++ }
	return n
}

func VariableMax(a, b int) int {
	n := 0
	for range [1]int{max(a, b)} { n++ }
	return n
}

func ConstantMin() int {
	n := 0
	for range [1]int{min(2, 1)} { n++ }
	return n
}

func ConstantMax() int {
	n := 0
	for range [1]int{max(2, 1)} { n++ }
	return n
}

func main() {
	if ArrayNoIndex() != 31422 || ArrayOneIndex() != 25713 { for {} }
	if ArrayBlankValue() != 641 || IntegerBound() != 233 { for {} }
	if VariableMin(3, 1) != 1 || VariableMax(3, 1) != 1 { for {} }
	if ConstantMin() != 1 || ConstantMax() != 1 { for {} }
}
