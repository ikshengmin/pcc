package main

// Direct methods use the same non-retaining pointer arguments as free calls.
// Every dereference is guarded in the original source, not inferred from callers.
type Counter struct { N uint64; Pad [2]uint64 }
type Alias = Counter
type CP = *Counter

func (c *Counter) Add(n uint64) uint64 {
	if c == nil { return 7 }
	c.N += n
	return c.N
}
func (c Counter) Value(n uint64) uint64 { return c.N + n }
func (c CP) AliasOnly(n uint64) uint64 {
	if c == nil { return 0 }
	c.N += n * 2
	return c.N
}
func (*Counter) Unnamed() uint64 { return 23 }
func (c *Counter) Forward(n uint64) uint64 { return c.Add(n) }
func (c *Counter) ResetLocal() { c = nil; if c != nil { c.N = 99 } }
func (c *Counter) Grow(n uint64) uint64 {
	if c == nil { return 0 }
	if n == 0 { return c.N }
	c.N++
	return c.Grow(n - 1)
}
func (c *Counter) Swap(other *Counter) uint64 {
	if c == nil || other == nil { return 0 }
	c.N, other.N = other.N, c.N
	return c.N * 100 + other.N
}
func (c *Counter) Pair(n uint64) (uint64, uint64) {
	if c == nil { return 0, n }
	c.N += n
	return c.N, n
}

type Word uint64
func (w *Word) Bump(n uint64) uint64 {
	if w == nil { return 17 }
	*w += Word(n)
	return uint64(*w)
}
type Flag bool
func (f *Flag) Toggle() uint64 {
	if f == nil { return 0 }
	*f = !*f
	if *f { return 1 }
	return 0
}
type Label string
func (s *Label) Set(v Label) uint64 {
	if s == nil { return 0 }
	*s = v
	return uint64(len(*s))
}
type Vector [2]uint64
func (v *Vector) Sum() uint64 {
	if v == nil { return 0 }
	return v[0] + v[1]
}

var trace uint64
var global Counter
func next() uint { trace = trace * 10 + 1; return 0 }
func argument() uint64 { trace = trace * 10 + 2; return 5 }
func touch(c *Counter) uint64 { if c != nil { c.N = 9 }; return 0 }

func ImplicitStruct() uint64 { c := Counter{N:5}; c.Add(7); return c.N }
func ExplicitPointer() uint64 { c := Counter{N:0xfedcba9876543210}; p := &c; return p.Add(3) }
func NilReceiver() uint64 { var p *Counter; return p.Add(99) }
func NilExpression() uint64 { return (*Counter).Add(nil, 99) }
func AliasReceiver() uint64 { c := Alias{N:2}; return CP.AliasOnly(&c, 5) }
func ReceiverIdentity() uint64 { c := Counter{N:10}; p := &c; p.ResetLocal(); return p.Add(2) }
func SharedReceiver() uint64 { c := Counter{N:9}; return c.Swap(&c) }
func DistinctReceivers() uint64 { c := Counter{N:1}; d := Counter{N:2}; return c.Swap(&d) }
func ReceiverOnce() uint64 {
	trace = 0
	a := [2]Word{3, 4}
	a[next() & 1].Bump(argument())
	return trace * 100 + uint64(a[0])
}
func NilStillArguments() uint64 { trace = 0; var p *Counter; n := p.Add(argument()); return trace * 100 + n }
func ArgumentMutation() uint64 { c := Counter{N:1}; p := &c; return p.Add(touch(p)) }
func ValueWrapper() uint64 { c := Counter{N:1}; return (*Counter).Value(&c, touch(&c)) }
func ValueSnapshot() uint64 { c := Counter{N:1}; v := c; n := v.Value(touch(&c)); return n * 100 + c.N }
func FieldReceiver() uint64 {
	var b struct { C Counter }
	b.C.N = 5
	return b.C.Add(6)
}
func ArrayReceiver() uint64 { a := [2]Word{3, 4}; return a[1].Bump(8) }
func RecursiveReceiver() uint64 { c := Counter{N:2}; return c.Grow(4) }
func MultipleResults() uint64 { c := Counter{N:1}; a, b := c.Pair(7); return a * 100 + b * 10 + c.N }
func BooleanReceiver() uint64 { var f Flag; a := f.Toggle(); b := f.Toggle(); return a * 10 + b }
func StringReceiver() uint64 { s := Label("a"); return s.Set("bytes") }
func ArrayPointerReceiver() uint64 { v := Vector{8, 13}; return v.Sum() }
func NilForward() uint64 { var p *Counter; return p.Forward(2) }
func UnnamedNil() uint64 { var p *Counter; return p.Unnamed() }
func GlobalReceiver() uint64 { global = Counter{N:3}; global.Add(4); return global.N }

func main() {
	if ImplicitStruct()!=12 || ExplicitPointer()!=0xfedcba9876543213 || NilReceiver()!=7 || NilExpression()!=7 { for {} }
	if AliasReceiver()!=12 || ReceiverIdentity()!=12 || SharedReceiver()!=909 || DistinctReceivers()!=201 { for {} }
	if ReceiverOnce()!=1208 || NilStillArguments()!=207 || ArgumentMutation()!=9 || ValueWrapper()!=9 || ValueSnapshot()!=109 { for {} }
	if FieldReceiver()!=11 || ArrayReceiver()!=12 || RecursiveReceiver()!=6 || MultipleResults()!=878 { for {} }
	if BooleanReceiver()!=10 || StringReceiver()!=5 || ArrayPointerReceiver()!=21 || NilForward()!=7 || UnnamedNil()!=23 || GlobalReceiver()!=7 { for {} }
}
