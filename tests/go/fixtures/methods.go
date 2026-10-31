package main

type Counter struct { Base uint64 }
type Alias = Counter
type Triple struct { A,B,C uint64 }
type Word uint64
type Flag bool
type Text string
var Global = Counter{0xfedcba9876543200}
var Calls uint64
func (c Counter) Add(n uint64) uint64 { c.Base+=n; return c.Base }
func (c Counter) Read() uint64 { return c.Base }
func (c Alias) Times(n uint64) uint64 { return c.Base*n }
func (Counter) Constant() uint64 { return 73 }
func (_ Counter) Blank() uint64 { return 79 }
func (c Counter) Pair(n uint64)(uint64,uint64){return c.Base,n}
func (c Counter) Combine(a,b uint64)uint64{return c.Base+a+b}
func (c Counter) Recurse(n uint64)uint64{if n==0{return c.Base};return c.Recurse(n-1)+1}
func (c Counter) len()uint64{return c.Base}
func (c Counter) init()uint64{return c.Base+1}
func (t Triple) Change()Triple{t.A=0;return t}
func (w Word) Twice()Word{return w+w}
func (f Flag) Not()Flag{return !f}
func (s Text) Size()uint64{return uint64(len(s))}
func Make()Counter{Calls++;return Global}
func Mutate()uint64{Global.Base=0;return 16}
func Inputs()(uint64,uint64){Calls++;return 11,13}
func Add(n uint64)uint64{return n+1}
func ValueCopy()uint64{c:=Counter{7};v:=c.Add(3);return c.Base*100+v}
func Temporary()uint64{return Counter{0xfedcba9876543200}.Add(16)}
func MethodExpression()uint64{return Counter.Add(Counter{0xfedcba9876543200},16)}
func AliasMethod()uint64{c:=Counter{7};return c.Times(11)}
func Unnamed()uint64{return Counter{}.Constant()+Counter{}.Blank()}
func MultiReturn()uint64{a,b:=Counter{7}.Pair(13);return a*100+b}
func MultiArgument()uint64{Calls=0;v:=Counter{7}.Combine(Inputs());return Calls*100+v}
func ReceiverSnapshot()uint64{Calls=0;v:=Make().Add(Mutate());return v+Calls-1}
func LargeReceiver()uint64{t:=Triple{7,9,0x8877665544332211};v:=t.Change();if t.A!=7||v.A!=0{return 0};return v.C}
func ScalarReceiver()uint64{return uint64(Word(0x7edcba9876543210).Twice())}
func BooleanReceiver()uint64{if Flag(false).Not(){return 83};return 0}
func StringReceiver()uint64{return Text("a\x00z").Size()}
func Recursion()uint64{return Counter{7}.Recurse(11)}
func Namespaces()uint64{return Add(3)+Counter{7}.Add(11)}
func PredeclaredMethod()uint64{return Counter{7}.len()+Counter{11}.init()}
func Parenthesized()uint64{return (Counter.Add)(Counter{7},11)+(Counter{13}.Add)(17)}
func main(){
 if ValueCopy()!=710||Temporary()!=0xfedcba9876543210||MethodExpression()!=0xfedcba9876543210{for{}}
 if AliasMethod()!=77||Unnamed()!=152||MultiReturn()!=713||MultiArgument()!=131{for{}}
 if ReceiverSnapshot()!=0xfedcba9876543210||LargeReceiver()!=0x8877665544332211{for{}}
 if ScalarReceiver()!=0xfdb97530eca86420||BooleanReceiver()!=83||StringReceiver()!=3||Recursion()!=18{for{}}
 if Namespaces()!=22||PredeclaredMethod()!=19||Parenthesized()!=48{for{}}
}
