// Original PCC regression program for Go result lists and return semantics.
package main

var Count int64
func Named() (x int64) { x = 9; return }
func Blank() (_ int64) { return }
func BlankExplicit() (_ int64) { return 33 }
func Zeros() (a, b int64) { return }
func Pair() (uint64, int8) { return 0xfedcba9876543210, -128 }
func Forward() (uint64, int8) { return Pair() }
func Triple() (uint64, uint64, uint64) { return 11, 22, 0x8123456789abcdef }
func Mixed() (a [3]uint64, b uint64) {
    a = [3]uint64{0x8877665544332211, 4, 6}
    b = 0xffeeddccbbaa9988
    return
}
func First() uint64 { a, _ := Forward(); return a }
func Second() int64 { _, b := Forward(); return int64(b) }
func Third() uint64 { _, _, c := Triple(); return c }
func ArrayResult() uint64 { a, _ := Mixed(); b := a; a[0] = 9; return b[0] }
func ScalarAlongArray() uint64 { _, b := Mixed(); return b }
func Join(a uint64, b int8) uint64 { return a }
func ExpandedArguments() uint64 { return Join(Pair()) }
func Twice() (int64, int64) { Count++; return 3, 4 }
func Once() int64 { Count=0; a,b:=Twice(); return Count*100+a*10+b }
func Index() uint8 { Count++; return 0 }
func Indexed() int64 {
    Count=0
    var a [2]int64
    a[Index()&1], a[1] = Twice()
    return Count*100+a[0]*10+a[1]
}
func One() int64 { Count=Count*10+1; return 11 }
func Two() int64 { Count=Count*10+2; return 22 }
func Evaluation() (a, b int64) { return One(), Two() }
func ReturnOrder() int64 { Count=0; a,b:=Evaluation(); return Count*10000+a*100+b }
func Declarations() int64 {
    Count=0
    var a,b int64=Twice()
    a,c:=Twice()
    return Count*1000+a*100+b*10+c
}
func ShadowExplicit() (x int64) { { x:=int64(23); return x } }
func Sum(n int64) (total int64) { for i:=int64(0);i<n;i++ {total+=i}; return }
func Parenthesized() (int64) { return 17 }
func Empty() () {}
func ZeroResult() int64 { a,b:=Zeros(); return a+b }
func Result() int64 { return int64(First()) }
func main() {
    Empty()
    if Named()!=9 || Blank()!=0 || BlankExplicit()!=33 || ZeroResult()!=0 {for{}}
    if First()!=0xfedcba9876543210 || Second()!=-128 || Third()!=0x8123456789abcdef {for{}}
    if ArrayResult()!=0x8877665544332211 || ScalarAlongArray()!=0xffeeddccbbaa9988 {for{}}
    if ExpandedArguments()!=0xfedcba9876543210 || Once()!=134 || Indexed()!=234 {for{}}
    if ReturnOrder()!=121122 || Declarations()!=2344 {for{}}
    if ShadowExplicit()!=23 || Sum(10)!=45 || Parenthesized()!=17 {for{}}
}
