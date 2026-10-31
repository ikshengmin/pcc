// Original PCC specification fixture for declaration groups and iota.
package main

const (
    _ = iota
    A, B = iota, 1 << iota
    C, D
    E uint64 = 0xfedcba9876543210 + iota
    F
)
const Reset = iota
const Forward = Later + iota
const Later = 7
const (
    Snapshot = Remote + iota
    _ = iota
    Remote = iota
    Context = Snapshot + iota
)
const ()
var ()
var (
    Count int64
    Table = [2]uint64{E, F}
    Seed int64 = Later
)
func PackageIota() uint64 { return F }
func PackagePairs() int64 { return A*1000+B*100+C*10+D }
func DependencyContext() int64 { return Snapshot*100+Remote*10+Context }
func ResetIota() int64 { return Reset + Forward*10 + Later }
func LocalGroup() int64 {
    const (
        first = iota + 10
        second
        x, y = iota, iota
        z, w
    )
    return first*100000+second*1000+x*100+y*10+z+w
}
func Scoped() int64 {
    const x = 5
    value := int64(0)
    {
        const (x = x + iota + 7; y)
        value = x*100+y
    }
    return value*10+x
}
func Tick() int64 { Count++; return Count }
func VariableGroup() int64 {
    Count=0
    var (a=Tick(); b=Tick(); c int64)
    return a*100+b*10+c
}
func RepeatedBoolean() int64 {
    const (first = true; second)
    if first && second {return 91}
    return 0
}
func ArrayConstants() uint64 { return Table[1] }
func TypedRepeat() int64 {
    const (a int8 = -128+iota; b; c)
    return int64(a)*10000+int64(b)*100+int64(c)
}
func BlankChecked() int64 { const (_=iota; _; value); return value }
func main() {
    if PackageIota()!=0xfedcba9876543214 || ArrayConstants()!=0xfedcba9876543214 {for{}}
    if PackagePairs()!=1224 || ResetIota()!=77 || LocalGroup()!=1011226 {for{}}
    if Scoped()!=12205 || VariableGroup()!=120 || RepeatedBoolean()!=91 {for{}}
    if DependencyContext()!=225 {for{}}
    if TypedRepeat()!=-1292826 || BlankChecked()!=2 || Seed!=7 {for{}}
}
