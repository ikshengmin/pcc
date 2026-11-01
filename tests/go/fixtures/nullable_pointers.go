package main

func read(p *int) int {
	if p == nil { return 17 }
	return *p
}
func forward(p *int) int { return read(p) }
func NilCall() int { return forward(nil) }
func LiveCall() int { x := 23; return forward(&x) }
func ZeroAndCopies() int {
	var p *int
	q := p
	if q != nil { return 90 }
	p = nil
	if p == nil { return 7 }
	return 91
}
func Rebind() int {
	var p *int
	x := 31
	p = &x
	if p != nil { *p += 2 }
	p = nil
	return x
}
func alias(p, q *uint64) uint64 {
	if p == nil || q == nil { return 0 }
	*p = 0xfedcba9876543210
	*q += 7
	return *p
}
func Aliased() uint64 { var x uint64; return alias(&x, &x) }
func Distinct() uint64 { var x, y uint64; return alias(&x, &y) }
func short(p *int) int {
	if p != nil && *p == 8 { return 1 }
	if p == nil || *p == 9 { return 2 }
	return 3
}
func ShortCircuits() int {
	x, y := 8, 9
	return 100*short(&x) + 10*short(&y) + short(nil)
}
func ParallelTargets() int {
	x := 1
	p := &x
	p, *p = nil, 2
	if p == nil { return x }
	return 90
}
func DuplicateTargets() int {
	x := 1
	var p *int
	p, p = &x, nil
	if p == nil { return x }
	return 90
}
func SwapNullable() int {
	x := 3
	var p *int
	q := &x
	p, q = q, p
	if p != nil && q == nil { return *p }
	return 90
}
func continuePost(p *int) int {
	if p == nil { return 0 }
	total := 0
	for ; p != nil; p = nil {
		*p += 2
		total = *p
		continue
	}
	return total
}
func ContinuePost() int { x := 3; return continuePost(&x) + continuePost(nil) }
func breakFlow(p *int) int {
	for {
		if p == nil { return 7 }
		break
	}
	return *p
}
func BreakFlow() int { x := 21; return 10*breakFlow(&x) + breakFlow(nil) }
func switchGuard(p *int) int {
	switch {
	case p == nil:
		return 17
	default:
		return *p
	}
}
func SwitchGuard() int { x := 4; return 100*switchGuard(&x) + switchGuard(nil) }
func switchFall(p *int) int {
	if p == nil { return 0 }
	switch {
	case p != nil:
		*p += 2
		fallthrough
	default:
		*p += 3
	}
	return *p
}
func SwitchFallthrough() int { x := 5; return switchFall(&x) }
func switchBreak(p *int) int {
	if p == nil { return 0 }
	for i := 0; i < 2; i++ {
		switch i {
		case 0:
			*p += 1
			break
		default:
			*p += 2
		}
	}
	return *p
}
func NestedBreakTargets() int { x := 10; return switchBreak(&x) }
func noEval(p *[3]int) int { return len(*p) + cap(*p) }
func noEvalRange(p *[3]int) int {
	s := 0
	for i := range *p { s += i }
	return s
}
func UnevaluatedNil() int { return 10*noEval(nil) + noEvalRange(nil) }
func arraySum(p *[3]int) int {
	if p == nil { return 0 }
	s := 0
	for _, v := range *p { s += v }
	return s
}
func ArrayParameter() int { a := [3]int{3, 5, 7}; return arraySum(&a) + arraySum(nil) }
func guardAddress(p *int) int {
	if p == nil { return 0 }
	q := &*p
	*q += 4
	return *p
}
func ExplicitAddress() int { x := 5; return guardAddress(&x) + guardAddress(nil) }
type Pair struct { X int; Y int }
func recordUpdate(p *Pair) int {
	if p == nil { return 0 }
	p.X, p.Y = p.Y, p.X
	return p.X*100 + p.Y
}
func RecordParameter() int { p := Pair{3, 7}; return recordUpdate(&p) + recordUpdate(nil) }
func recursiveA(p *int, n int) int {
	if n == 0 { return read(p) }
	return recursiveB(p, n-1)
}
func recursiveB(p *int, n int) int {
	if n == 0 { return read(p) }
	return recursiveA(p, n-1)
}
func RecursiveCalls() int { x := 6; return recursiveA(&x, 4)*100 + recursiveB(nil, 3) }
func scoped(p *int) int {
	if p == nil { return 0 }
	{ q := p; *q += 2 }
	return *p
}
func ScopedAlias() int { x := 11; return scoped(&x) }
type Value int
func (v Value) Add(n int) int { return int(v) + n }
func guardedMethod(p *Value) int {
	if p == nil { return 0 }
	return p.Add(3)
}
func ValueMethodGuard() int { v := Value(12); return guardedMethod(&v) + guardedMethod(nil) }
var methodGlobal Value
func changeMethodGlobal() int { methodGlobal = 9; return 2 }
func delayedMethod(p *Value) int {
	if p == nil { return 0 }
	return (*Value).Add(p, changeMethodGlobal())
}
func DelayedMethodGuard() int { methodGlobal = 1; return delayedMethod(&methodGlobal) }
func initialize(p *int) int {
	if p == nil { return 0 }
	x := 8
	p = &x
	return *p
}
func RebindParameter() int { x := 99; y := initialize(&x); return x*100 + y }
func LegacyNilStorage() int {
	var p *int
	_ = p
	var q *int = nil
	_ = q
	x := 1
	r := &x
	r = nil
	_ = r
	return x
}
func unusedPointerParameter(p *int) {}
func LegacyUnusedParameter() int { unusedPointerParameter(nil); return 1 }
// Positions seven/eight exercise stack-passed pointers in the PCC x86_64 SysV lane.
func stackPointers(a,b,c,d,e,f int, p,q *int) int {
	if p == nil || q == nil { return 0 }
	*p += a+b+c+d+e+f
	*q += 1
	return *p*100 + *q
}
func StackParameters() int {
	x,y := 1,2
	return stackPointers(1,2,3,4,5,6,&x,&y) + stackPointers(1,2,3,4,5,6,nil,nil)
}
func StackAliasing() int { x:=1; return stackPointers(1,2,3,4,5,6,&x,&x) }
type IntPointer *int
func namedPointer(p IntPointer) int { if p==nil { return 5 }; return *p }
func NamedPointerParameter() int { x:=7; return 100*namedPointer(IntPointer(&x)) + namedPointer(nil) }
type Flag bool
func convertedGuard(p *int) int { if Flag(p!=nil) { return *p }; return 4 }
func ConvertedGuards() int { x:=6; return 100*convertedGuard(&x) + convertedGuard(nil) }
func main() {
	if StackParameters()!=2203 || StackAliasing()!=2323 || NamedPointerParameter()!=705 || ConvertedGuards()!=604 { for {} }
	if NilCall()!=17 || LiveCall()!=23 || ZeroAndCopies()!=7 || Rebind()!=33 { for {} }
	if Aliased()!=0xfedcba9876543217 || Distinct()!=0xfedcba9876543210 { for {} }
	if ShortCircuits()!=122 || ParallelTargets()!=2 || DuplicateTargets()!=1 || SwapNullable()!=3 { for {} }
	if ContinuePost()!=5 || BreakFlow()!=217 || SwitchGuard()!=417 || SwitchFallthrough()!=10 { for {} }
	if NestedBreakTargets()!=13 || UnevaluatedNil()!=63 || ArrayParameter()!=15 || ExplicitAddress()!=9 { for {} }
	if RecordParameter()!=703 || RecursiveCalls()!=617 || ScopedAlias()!=13 || ValueMethodGuard()!=15 { for {} }
	if DelayedMethodGuard()!=11 || RebindParameter()!=9908 || LegacyNilStorage()!=1 || LegacyUnusedParameter()!=1 { for {} }
}
