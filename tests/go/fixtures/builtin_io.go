package main

type Signed int64
type Unsigned uint64
type Text string
type Truth bool
type Counter uint64
func(Counter)print()uint64{return 42}
var order uint64
func mark(n uint64)int64{order=order*10+n;return int64(n)}
func multiple()(int8,string,bool){return -8,"multi",true}
func checked(p *uint64)uint64{if p==nil{panic("nil")};return *p}
func main(){
    print("hello, world\n")
    println()
    print("A",1,true,"B")
    println(" C",int8(-128),uint8(255),int16(-32768),uint16(65535))
    println(Signed(-9223372036854775808),Unsigned(18446744073709551615))
    println(int32(-2147483648),uint32(4294967295),int64(0),uint64(0))
    println(Truth(false),Text("nul\x00tail"))
    print(mark(1),mark(2),mark(3));println(" order",order)
    println(multiple())
    print("");println(int32(937),Counter(1).print())
    var x uint64=9;println(checked(&x))
}
