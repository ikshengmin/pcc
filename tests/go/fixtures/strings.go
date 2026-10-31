// Original PCC specification fixture. Go strings are immutable byte sequences.
package main
const Text = "\x00\xffA\u00e9\U0001F642\t\n\\\"xyz"
const Raw = `line
α`
var Global string = "\xfflong"
var Empty string
var Count int64
func Byte(i uint8) uint8 { return Text[i&15] }
func LiteralLength() int64 { return int64(len(Text)) }
func RawLength() int64 { return int64(len(Raw)) }
func RawByte() uint8 { return Raw[5] }
func Identity(s string) string { return s }
func ValueLength() int64 { return int64(len(Identity(Text))) }
func Zero() (s string) { return }
func ZeroLength() int64 { return int64(len(Zero())+len(Empty)) }
func Both() (s string,n int64) { return Text,7 }
func MultipleLength() int64 {s,n:=Both();return int64(len(s))+n}
func Read() string { return Global }
func Shorten() int { Global="z";return 0 }
func Use(s string, ignored int) int {return len(s)}
func Snapshot() int64 {Global="abcd";return int64(Use(Read(),Shorten()))}
func GlobalLength() int64 {return int64(len(Global))}
func Counted() string {Count++;return Text}
func LenOnce() int64 {Count=0;n:=len(Counted());return Count*100+int64(n)}
func Left() string {Count=Count*10+1;return ""}
func Right() string {Count=Count*10+2;return ""}
func CompareOrder() int64 {Count=0;_=Left()==Right();return Count}
func Compare(a,b string) int64 {if a==b{return 0};if a<b{return -1};return 1}
func Prefix() int64 {return Compare("a","aa")}
func Unsigned() int64 {return Compare("\xff","\x7f")}
func Nul() int64 {return Compare("a\x00b","a\x00c")}
func Unicode() int64 {return Compare("é","e")}
func EmptyFirst() int64 {return Compare("","a")}
func Equal() int64 {return Compare("same\x00é","same\x00é")}
func Relations() int64 {
    a,b:="a","b"
    value:=int64(0)
    if a<b {value+=1};if a<=b {value+=2};if a==b {value+=4}
    if a!=b {value+=8};if a>b {value+=16};if a>=b {value+=32}
    return value
}
func Replacement() uint64 {
    const s=string(0xd800)
    return uint64(s[0])|uint64(s[1])<<8|uint64(s[2])<<16
}
func Codepoint() uint64 {
    const s=string(0x1f642)
    return uint64(s[0])|uint64(s[1])<<8|uint64(s[2])<<16|uint64(s[3])<<24
}
func Folded() int64 {
    const s = "a"+"\x00"+"é"
    if s=="a\x00é" && "\xff">"\x7f" && s<"b" {return int64(len(s))}
    return 0
}
func main() {
    if Byte(0)!=0 || Byte(1)!=255 || Byte(3)!=195 || Byte(15)!=122 {for{}}
    if LiteralLength()!=16 || RawLength()!=7 || RawByte()!=206 || ValueLength()!=16 {for{}}
    if ZeroLength()!=0 || MultipleLength()!=23 || GlobalLength()!=5 {for{}}
    if Snapshot()!=4 || LenOnce()!=116 || CompareOrder()!=12 {for{}}
    if Prefix()!=-1 || Unsigned()!=1 || Nul()!=-1 || Unicode()!=1 || EmptyFirst()!=-1 || Equal()!=0 {for{}}
    if Relations()!=11 || Replacement()!=0xbdbfef || Codepoint()!=0x82999ff0 || Folded()!=4 {for{}}
}
