// Original component self-check, not math/bits package or upstream test completion.
package main
import "pccbitsref"
func Result(x uint8) uint32 { return bits.Read(x) }
func main() {
    for i:=uint(0);i<256;i++ {
        n:=uint8(i)
        trailing:=uint32(8)
        if n!=0 {
            trailing=0
            v:=n
            for v&1==0 { trailing++;v>>=1 }
        }
        ones,reverse,length:=uint32(0),uint32(0),uint32(0)
        v:=n
        for bit:=uint(0);bit<8;bit++ {ones+=uint32(v&1);reverse=reverse<<1|uint32(v&1);v>>=1}
        v=n
        for v!=0 {length++;v>>=1}
        expected:=trailing|ones<<8|reverse<<16|length<<24
        if Result(n)!=expected {for{}}
    }
}
