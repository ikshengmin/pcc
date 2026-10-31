package main

var Trace uint64
var Global uint64
func Mark(n uint64)uint64{Trace=Trace*10+n;return n}
func Change()uint64{Global=9;return 7}
func Ordered()uint64{Trace=0;var n uint64;switch Mark(2){default:n=9;case Mark(1),Mark(2):n=7;case Mark(3):n=8};return Trace*10+n}
func Snapshot()uint64{Global=7;switch Global{case Change():return Global*10+1;default:return 0}}
func NoImplicitFallthrough()uint64{var n uint64;switch 1{case 1:n=7;case 2:n=90};return n}
func FallthroughMiddle()uint64{var n uint64;switch 1{case 1:n=7;fallthrough;default:n+=11;fallthrough;case 3:n*=2};return n}
func FallthroughSkipsTest()uint64{Trace=0;var n uint64;switch 1{case 1:fallthrough;case Mark(9):n=7};return Trace*10+n}
func InitScope()uint64{x:=uint64(40);var n uint64;switch x:=uint64(2);x{case 2:x++;n=x;default:n=9};return x*100+n}
func ClauseScopes()uint64{var n uint64;switch 1{case 1:x:=uint64(7);n=x;case 2:x:=uint64(9);n=x};return n}
func ContinueOuter()uint64{var n uint64;for i:=uint64(0);i<5;i++{switch i{case 1,3:continue;default:n+=i};n+=10};return n}
func BreakTargets()uint64{var n uint64;for i:=uint64(0);i<3;i++{switch i{case 0:for{n++;break};break;default:n+=10};n+=100};return n}
func InfiniteLoopTermination()uint64{for{switch 1{case 1:break};return 83}}
func AllReturn()uint64{switch 1{case 1:fallthrough;case 2:return 0xfedcba9876543210;default:return 7}}
type Tag uint8
func NamedSelector()uint64{var tag Tag=255;switch tag{case 1:return 1;case 255:return 0x8877665544332211;default:return 0}}
func Text()string{return "a\x00z"}
func Strings()uint64{switch Text(){case "a":return 1;case "a\x00z":return 0xfedcba9876543210;default:return 0}}
func BooleanSwitch()uint64{switch{case false:return 1;case 1<2:return 83;default:return 0}}
func EmptySwitch()uint64{Trace=0;switch Mark(7){};return Trace}
func DefaultAfterTests()uint64{Trace=0;var n uint64;switch Mark(2){default:n=9;case Mark(1):n=7;case Mark(3):n=8};return Trace*10+n}
func NamedReturn()(r uint64){switch;{default:r=79;return}}
func NestedFallthrough()uint64{var n uint64;switch 1{case 1:switch 2{case 2:n=7;break};fallthrough;case 3:n+=11};return n}
func main(){
 if Ordered()!=2127||Snapshot()!=91||NoImplicitFallthrough()!=7{for{}}
 if FallthroughMiddle()!=36||FallthroughSkipsTest()!=7||InitScope()!=4003||ClauseScopes()!=7{for{}}
 if ContinueOuter()!=36||BreakTargets()!=321||InfiniteLoopTermination()!=83{for{}}
 if AllReturn()!=0xfedcba9876543210||NamedSelector()!=0x8877665544332211||Strings()!=0xfedcba9876543210{for{}}
 if BooleanSwitch()!=83||EmptySwitch()!=7||DefaultAfterTests()!=2139||NamedReturn()!=79||NestedFallthrough()!=18{for{}}
}
