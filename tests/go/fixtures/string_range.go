package main

// Authored spec controls, not rewritten upstream Go or an enry qualification.
type Text string
var Calls uint64
var Current string
func Make() string { Calls++; return Current }
func Target() uint8 { Calls++; return 0 }
func NeedIndex(x int) {}
func NeedRune(x rune) {}

func ValidScalars() uint64 {
    var n uint64
    for i, r := range "\x00A\x7f\u0080\u07ff\u0800\ud7ff\ue000\ufffd\uffff\U00010000\U0010ffff" {
        NeedIndex(i); NeedRune(r)
        switch n {
        case 0: if i != 0 || r != 0 { return 101 }
        case 1: if i != 1 || r != 65 { return 102 }
        case 2: if i != 2 || r != 127 { return 103 }
        case 3: if i != 3 || r != 128 { return 104 }
        case 4: if i != 5 || r != 2047 { return 105 }
        case 5: if i != 7 || r != 2048 { return 106 }
        case 6: if i != 10 || r != 55295 { return 107 }
        case 7: if i != 13 || r != 57344 { return 108 }
        case 8: if i != 16 || r != 65533 { return 109 }
        case 9: if i != 19 || r != 65535 { return 110 }
        case 10: if i != 22 || r != 65536 { return 111 }
        case 11: if i != 26 || r != 1114111 { return 112 }
        default: return 113
        }
        n++
    }
    return n
}

func InvalidOnly(s string) uint64 {
    var n uint64
    for i, r := range s {
        if i != int(n) || r != 65533 { return 999 }
        n++
    }
    return n
}
func InvalidLeads() uint64 { return InvalidOnly("\x80\xc0\xaf\xc1\xbf\xf5\x80\x80\x80\xff") }
func InvalidScalars() uint64 { return InvalidOnly("\xe0\x80\x80\xed\xa0\x80\xf0\x80\x80\x80\xf4\x90\x80\x80") }
func Truncated() uint64 { return InvalidOnly("\xc2")*100 + InvalidOnly("\xe2\x82")*10 + InvalidOnly("\xf0\x9f\x92") }
func LaterContinuations() uint64 {
    var n uint64
    for i, r := range "\xe2\x82A\xf0\x9f\x92B" {
        if i != int(n) { return 101 }
        switch i {
        case 2: if r != 65 { return 102 }
        case 6: if r != 66 { return 103 }
        default: if r != 65533 { return 104 }
        }
        n++
    }
    return n
}
func InvalidThenValid() uint64 {
    var n uint64
    for i, r := range "\xe2A\x00\xf0\x9fB\xc2\xa2" {
        switch n {
        case 0: if i != 0 || r != 65533 { return 101 }
        case 1: if i != 1 || r != 65 { return 102 }
        case 2: if i != 2 || r != 0 { return 103 }
        case 3: if i != 3 || r != 65533 { return 104 }
        case 4: if i != 4 || r != 65533 { return 105 }
        case 5: if i != 5 || r != 66 { return 106 }
        case 6: if i != 6 || r != 162 { return 107 }
        default: return 108
        }
        n++
    }
    return n
}
func IndexOnly() uint64 { var n uint64; for i := range "a\u00e9\U0001f600\x00" { n = n*10 + uint64(i) }; return n }
func BlankRune() uint64 { var n uint64; for i, _ := range "a\u00e9\U0001f600\x00" { n = n*10 + uint64(i) }; return n }
func Bare() uint64 { var n uint64; for range "a\u00e9\U0001f600\x00" { n++ }; return n }
func BothBlank() uint64 { var n uint64; for _, _ = range "a\u00e9\U0001f600\x00" { n++ }; return n }
func NamedString() uint64 { var n uint64; for i, r := range Text("\u00e9A") { n += uint64(i)+uint64(r) }; return n }
func RawString() uint64 { var n uint64; for _, r := range `é中` { n += uint64(r) }; return n }
func CaptureOnce() uint64 {
    Calls=0; Current="\u00e9A"; var n uint64
    for i, r := range Make() { Current=""; n += uint64(i)+uint64(r) }
    return Calls*1000+n
}
func IndexCaptureOnce() uint64 {
    Calls=0; Current="a\u00e9\U0001f600"; var n uint64
    for i := range Make() { Current=""; n += uint64(i) }
    return Calls*100+n
}
func BareCaptureOnce() uint64 {
    Calls=0; Current="\u00e9A"; var n uint64
    for range Make() { Current=""; n++ }
    return Calls*100+n
}
func EmptyCapture() uint64 {
    Calls=0; Current=""; var a [1]rune
    for _, a[Target()&0] = range Make() { Calls+=100 }
    return Calls
}
func ZeroString() uint64 { var s string; var n uint64; for range s { n++ }; return n }
func AssignmentOrder() uint64 {
    i:=0; a:=[2]rune{11,22}
    for i, a[uint8(i)&1] = range "AB" {}
    return uint64(i*10000)+uint64(a[0])*100+uint64(a[1])
}
func TargetOnce() uint64 {
    Calls=0; var a [1]rune
    for _, a[Target()&0] = range "\u00e9A" {}
    return Calls*1000+uint64(a[0])
}
func HiddenInduction() uint64 {
    var n uint64
    for i, r := range "a\u00e9\U0001f600" { n += uint64(i); i=99; r=0; _=r }
    return n
}
func ShadowSource() uint64 {
    s:="\u00e9A"; var n uint64
    for s := range s { n+=uint64(s) }
    return uint64(len(s))*100+n
}
func ContinueSwitchBreak() uint64 {
    var n uint64
    for i, r := range "a\u00e9\U0001f600Z" {
        switch r { case 233: continue; case 128512: break }
        n=n*10+uint64(i)
    }
    return n
}
func NestedBreak() uint64 { var n uint64; for range "\u00e9A" { for range "\U0001f600B" { n++; break } }; return n }
func EarlyReturn() uint64 { for _, r := range "\U0010ffffA" { return uint64(r) }; return 0 }
func EmptyTargets() uint64 { i:=17; r:=rune(19); for i,r=range "" {}; return uint64(i*100)+uint64(r) }
func EmptyPointerFlow() uint64 { var p *int; for range "" { _=*p }; return 7 }
func GuardedPointerFlow() uint64 { x:=7; p:=&x; for range "A" { _=*p }; return uint64(x) }

func main() {
    if ValidScalars()!=12 || InvalidLeads()!=10 || InvalidScalars()!=14 || Truncated()!=123 || InvalidThenValid()!=7 || LaterContinuations()!=7 { for {} }
    if IndexOnly()!=137 || BlankRune()!=137 || Bare()!=4 || BothBlank()!=4 || NamedString()!=300 || RawString()!=20246 { for {} }
    if CaptureOnce()!=1300 || IndexCaptureOnce()!=104 || BareCaptureOnce()!=102 || EmptyCapture()!=1 || ZeroString()!=0 { for {} }
    if AssignmentOrder()!=16622 || TargetOnce()!=2065 || HiddenInduction()!=4 || ShadowSource()!=302 { for {} }
    if ContinueSwitchBreak()!=37 || NestedBreak()!=2 || EarlyReturn()!=1114111 || EmptyTargets()!=1719 || EmptyPointerFlow()!=7 || GuardedPointerFlow()!=7 { for {} }
}
