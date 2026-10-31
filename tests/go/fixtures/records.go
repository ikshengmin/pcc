package main

type Word uint64
type Signed int8
type Flag bool
type Text string
type Alias = Word
type Pair struct { A, B uint64 }
type Other Pair
type Triple struct { A, B, C uint64 }
type Nested struct { P Pair; Tail uint64 }
type WithArray struct { A [2]uint64; Tail uint64 }
type WithText struct { Text string; Number uint64 }
type Empty struct {}
type Table [2]Word
type MixedRecord struct { Small Signed; Flag Flag; Wide Word }

var Global = Pair{B: 7, A: 0xfedcba9876543210}
var TextGlobal = WithText{Text: "a\x00z", Number: 0x8877665544332211}
var ZeroGlobal Nested
var Ticks uint64

func Pass(p Pair) Pair { p.B = 99; return p }
func PairValue() Pair { return Pair{0xfedcba9876543210, 7} }
func ReadGlobal() Pair { return Global }
func Bump() uint64 { Global.A = 9; return 0 }
func UseSnapshot(p Pair, ignored uint64) uint64 { return p.A }
func Tick() uint64 { Ticks++; return Ticks }
func Scalar() uint64 { var w Word=0xfedcba9876543210; var a Alias=w; return uint64(a) }
func Narrow() int64 { var s Signed=-128; s--; return int64(s) }
func Boolean() uint64 { var f Flag=true; if f && !Flag(false) { return 71 }; return 0 }
func String() uint64 { var t Text="z\x00"; if t > "a" { return uint64(len(t)) }; return 0 }
func CopyLocal() uint64 { p:=PairValue(); q:=p; p.A=0; return q.A }
func CopyArgument() uint64 { p:=PairValue(); q:=Pass(p); return p.B*100+q.B }
func TemporaryField() uint64 { return PairValue().A }
func Returned24() Triple { return Triple{0xfedcba9876543210, 7, 0x8877665544332211} }
func LargeResult() uint64 { return Returned24().C }
func NestedCopy() uint64 { a:=Nested{P:PairValue(),Tail:3}; b:=a; a.P.A=0; return b.P.A }
func ArrayField() uint64 { a:=WithArray{A:[2]uint64{3,0x8877665544332211},Tail:7}; b:=a; a.A[1]=0; return b.A[1] }
func NamedArray() uint64 { a:=Table{Word(7),0xfedcba9876543210}; var b [2]Word=a; a[1]=0; return uint64(b[1]) }
func Explicit() uint64 { a:=PairValue(); b:=Other(a); return b.A }
func Anonymous() uint64 { var a struct{A,B uint64}=PairValue(); var b Pair=a; return b.A }
func Zero() uint64 { var a Nested; return a.P.A+a.P.B+a.Tail+ZeroGlobal.P.A }
func TextCopy() uint64 { a:=TextGlobal; b:=a; a.Text="other"; if b.Text=="a\x00z" { return b.Number }; return 0 }
func Order() uint64 { Ticks=0; p:=Pair{B:Tick(),A:Tick()}; return p.A*10+p.B }
func Parallel() uint64 { p:=Pair{1,2}; p.A,p.B=p.B,p.A; return p.A*10+p.B }
func Compound() uint64 { Ticks=0; p:=WithArray{}; p.A[Tick()&1]+=Tick(); return Ticks*100+p.A[1] }
func Snapshot() uint64 { return UseSnapshot(ReadGlobal(),Bump()) }
func EmptyPass(e Empty) Empty { return e }
func EmptyValue() uint64 { e:=EmptyPass(Empty{}); _=e; return 73 }
func Results()(Pair,Word){return PairValue(),11}
func Multi() uint64 { p,w:=Results(); return p.B+uint64(w) }
func Unnamed(Pair, Word) uint64 { return 79 }
func UnnamedParameters() uint64 { return Unnamed(Pair{},3) }
func Shadow() uint64 { Pair:=PairValue(); return Pair.A }
func MixedFields() uint64 { x:=MixedRecord{Small:-1,Flag:true,Wide:0xfedcba9876543210}; y:=x; x.Small=0; if y.Flag {return uint64(int64(y.Small)) ^ uint64(y.Wide)}; return 0 }
func Pass24(p Triple) Triple { p.A=0; return p }
func LargeArgument() uint64 { p:=Returned24(); q:=Pass24(p); if p.A!=0xfedcba9876543210 || q.A!=0 {return 0}; return q.C }
func main(){
 if MixedFields()!=0x0123456789abcdef || LargeArgument()!=0x8877665544332211 {for{}}
 if Scalar()!=0xfedcba9876543210 || Narrow()!=127 || Boolean()!=71 || String()!=2 {for{}}
 if CopyLocal()!=0xfedcba9876543210 || CopyArgument()!=799 || TemporaryField()!=0xfedcba9876543210 {for{}}
 if LargeResult()!=0x8877665544332211 || NestedCopy()!=0xfedcba9876543210 || ArrayField()!=0x8877665544332211 {for{}}
 if NamedArray()!=0xfedcba9876543210 || Explicit()!=0xfedcba9876543210 || Anonymous()!=0xfedcba9876543210 {for{}}
 if Zero()!=0 || TextCopy()!=0x8877665544332211 || Order()!=21 || Parallel()!=21 || Compound()!=202 {for{}}
 if Snapshot()!=0xfedcba9876543210 || EmptyValue()!=73 || Multi()!=18 || UnnamedParameters()!=79 || Shadow()!=0xfedcba9876543210 {for{}}
}
