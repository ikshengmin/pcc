struct R{x:u64}
struct Pair{a:R,b:R}
pub fn answer()->u64{
    let original=R{x:0xfedcba9876543210};
    let _=original;
    let saved=original;
    let _=original;
    let _=original.x;
    let pair=Pair{a:R{x:17},b:R{x:25}};
    let first=pair.a;
    let _=pair;
    let _=pair.a;
    let _=pair.b;
    let array=[R{x:1},R{x:2}];
    let _=array[0];
    let other=array;
    if first.x+pair.b.x==42u64 && other[0].x+other[1].x==3u64{saved.x}else{0}
}
