package main

type Byte uint8
type Numbers [3]uint64
type Holder struct { A [3]uint64 }
var Calls uint64
func Make() [3]uint64 { Calls++;return [3]uint64{5,7,11} }
func Empty() [0]uint64 { Calls++;return [0]uint64{} }
func Index()uint8 { Calls++;return 0 }
func Text()string { Calls++;return "abc" }
func (h Holder) Get()[3]uint64 { Calls++;return h.A }
func Sum()uint64 {var s uint64;for i,v:=range (Numbers{0xfedcba9876543200,5,7}){s+=uint64(i)+v};return s}
func Snapshot()uint64 {a:=[3]uint64{5,7,11};var s uint64;for i,v:=range a{if i==0{a[1]=99};s+=v};return s*100+a[1]}
func ValueCopy()uint64 {a:=[3]uint64{5,7,11};var s uint64;for _,v:=range a{v+=100;s+=v};return s*100+a[1]}
func CallOnce()uint64 {Calls=0;var s uint64;for _,v:=range Make(){s+=v};return Calls*100+s}
func IndexCallOnce()uint64 {Calls=0;var s uint64;for i:=range Make(){s+=uint64(i)};return Calls*100+s}
func BlankValueCallOnce()uint64 {Calls=0;var s uint64;for i,_:=range Make(){s+=uint64(i)};return Calls*100+s}
func BareCallOnce()uint64 {Calls=0;var n uint64;for range Make(){n++};return Calls*100+n}
func EmptyCallOnce()uint64 {Calls=0;for range Empty(){Calls+=100};return Calls}
func AssignmentOrder()uint64 {var i int;a:=[2]int{10,20};for i,a[uint8(i)&1]=range [2]int{7,9}{};return uint64(i*10000+a[0]*100+a[1])}
func AddressCallOnce()uint64 {Calls=0;var a [1]uint64;for _,a[Index()&0]=range [2]uint64{7,9}{};return Calls*100+a[0]}
func DuplicateAssignment()uint64 {var i int;var n uint64;for i,i=range [2]int{7,9}{n++};return n*100+uint64(i)}
func HiddenInduction()uint64 {var s uint64;for i:=range [3]int{}{s+=uint64(i);i=99};return s}
func ShadowRange()uint64 {a:=[3]int{};var s uint64;for a:=range a{s+=uint64(a)};return s+uint64(len(a))*100}
func ContinueAndSwitch()uint64 {var n uint64;for i:=range [4]int{}{switch i{case 1:continue;case 2:break};n=n*10+uint64(i+1)};return n}
func NestedBreak()uint64 {var n uint64;for range [3]int{}{for range [2]int{}{n++;break}};return n}
func EarlyBreak()uint64 {var n uint64;for _,v:=range [3]uint64{5,7,11}{n+=v;break};return n}
func LastAssigned()uint64 {i:=17;v:=19;for i,v=range [0]int{}{};return uint64(i*100+v)}
func NamedElement()uint64 {var total uint64;for _,v:=range [3]Byte{7,9,11}{total+=uint64(v)};return total}
func BooleanElement()uint64 {var mask uint64;for i,v:=range [4]bool{true,false,true,true}{if v{mask|=uint64(1)<<uint(i)}};return mask}
func LiteralCalls()uint64 {Calls=0;var n uint64;for range [2]int{int(Index()),int(Index())}{n++};return Calls*100+n}
func LengthCalls()uint64 {Calls=0;a:=len(Make());b:=cap(Make());return Calls*100+uint64(a*10+b)}
func ConstantLength()uint64 {const n=len([2]int{int(len("a")),2});const c=cap([3]int{});var a [n+c]int;return uint64(len(a)*10+cap(a))}
func NestedBuiltinCall()uint64 {Calls=0;var n uint64;for range [2]int{len(Text()),0}{n++};return Calls*100+n}
func MethodRange()uint64 {Calls=0;h:=Holder{[3]uint64{5,7,11}};var s uint64;for _,v:=range h.Get(){s+=v};return Calls*100+s}
func ReturnInside()uint64 {for _,v:=range [2]uint64{0x8877665544332211,7}{return v};return 0}
func main(){
 if Sum()!=0xfedcba987654320f || Snapshot()!=2399 || ValueCopy()!=32307 || CallOnce()!=123 || IndexCallOnce()!=103 || BlankValueCallOnce()!=103 || BareCallOnce()!=103 || EmptyCallOnce()!=1 {for{}}
 if AssignmentOrder()!=10920 || AddressCallOnce()!=209 || DuplicateAssignment()!=209 || HiddenInduction()!=3 || ShadowRange()!=303 || ContinueAndSwitch()!=134 || NestedBreak()!=3 || EarlyBreak()!=5 || LastAssigned()!=1719 {for{}}
 if NamedElement()!=27 || BooleanElement()!=13 || LiteralCalls()!=202 || LengthCalls()!=233 || ConstantLength()!=55 || NestedBuiltinCall()!=102 || MethodRange()!=123 || ReturnInside()!=0x8877665544332211 {for{}}
}
