package main

type Numbers [3]uint64
type NumberPointer *Numbers
type Alias = *Numbers
var targetCalls uint64
var output [2]int

func WideSum() uint64 {
 a := [2]uint64{0xfedcba9876543200, 15}
 p := &a
 var sum uint64
 for _, v := range p { sum += v }
 return sum
}
func LiveMutation() uint64 {
 a := [3]uint64{1, 2, 3}
 var sum uint64
 for i, v := range &a { sum += v; if i == 0 { a[1] = 20; a[2] = 30 } }
 return sum
}
func CapturedPointer() uint64 {
 a := [3]uint64{1, 2, 3}
 b := [3]uint64{7, 8, 9}
 p := &a
 var sum uint64
 for _, v := range p { sum += v; p = &b }
 return sum*10 + p[0]
}
func CapturedDespiteNil() uint64 {
 a := [2]uint64{4, 5}
 p := &a
 var sum uint64
 for _, v := range p { sum += v; p = nil }
 if p == nil { return sum + 100 }
 return 0
}
func NilIndices() uint64 {
 var p *[4]uint64
 var sum uint64
 for i := range p { sum += uint64(i) }
 return sum
}
func NilBare() uint64 {
 var p *[3]int
 var n uint64
 for range p { n++ }
 return n
}
func NilBlankValue() uint64 {
 var p *[4]int
 var sum uint64
 for i, _ := range p { sum += uint64(i) }
 return sum
}
func NilBlankAssignment() uint64 {
 var p *[3]int
 var n uint64
 for _, _ = range p { n++ }
 return n
}
func EmptyNilElements() uint64 {
 var p *[0]int
 var n uint64
 for _, v := range p { n += uint64(v) }
 return n + 7
}
func EmptyNilAssignment() uint64 {
 var p *[0]uint64
 i, v := 8, uint64(9)
 for i, v = range p { v++ }
 return uint64(i)*10 + v
}
func NamedArray() uint64 {
 a := Numbers{2, 4, 8}
 var p NumberPointer = &a
 var sum uint64
 for _, v := range p { sum += v }
 return sum
}
func AliasArray() uint64 {
 a := Numbers{3, 5, 7}
 var p Alias = &a
 var sum uint64
 for _, v := range p { sum += v }
 return sum
}
func Guarded(p *[2]uint64) uint64 {
 if p == nil { return 100 }
 var sum uint64
 for _, v := range p { sum += v }
 return sum
}
func GuardedCalls() uint64 {
 a := [2]uint64{2, 3}
 return Guarded(nil) + Guarded(&a)
}
func AssignmentTargets() uint64 {
 a := [3]uint64{2, 4, 6}
 i, v := 8, uint64(9)
 var sum uint64
 for i, v = range &a { sum += uint64(i)*10 + v; i = 99 }
 return sum + uint64(i)*100 + v
}
func nextTarget() uint64 { n := targetCalls; targetCalls++; return n & 1 }
func TargetsOnce() uint64 {
 targetCalls = 0
 output = [2]int{}
 a := [2]int{2, 3}
 for output[nextTarget() & 1], output[nextTarget() & 1] = range &a { }
 return targetCalls*100 + uint64(output[0])*10 + uint64(output[1])
}
func Control() uint64 {
 a := [4]uint64{1, 2, 3, 4}
 var sum uint64
 for i, v := range &a { if i == 1 { a[2] = 30; continue }; if i == 3 { break }; sum += v }
 return sum
}
func SignedValues() uint64 {
 a := [3]int8{-2, 3, 4}
 var sum int64
 for _, v := range &a { sum += int64(v) }
 return uint64(sum)
}
func BoolValues() uint64 {
 a := [3]bool{true, false, true}
 var n uint64
 for _, v := range &a { if v { n++ } }
 return n
}
func ShadowPointer() uint64 {
 a := [2]uint64{3, 5}
 p := &a
 var sum uint64
 for p, v := range p { sum += uint64(p) + v }
 return sum + p[0]
}
func main() {
 if WideSum()!=0xfedcba987654320f || LiveMutation()!=51 || CapturedPointer()!=67 || CapturedDespiteNil()!=109 { for {} }
 if NilIndices()!=6 || NilBare()!=3 || NilBlankValue()!=6 || NilBlankAssignment()!=3 || EmptyNilElements()!=7 || EmptyNilAssignment()!=89 { for {} }
 if NamedArray()!=14 || AliasArray()!=15 || GuardedCalls()!=105 || AssignmentTargets()!=9948 || TargetsOnce()!=413 { for {} }
 if Control()!=31 || SignedValues()!=5 || BoolValues()!=2 || ShadowPointer()!=12 { for {} }
}
