// Original PCC specification fixture for single evaluation and move joins.
#![no_std]
struct Parcel { value:u64 }
fn take(p:Parcel) -> u64 {p.value}
fn select(x:i64) -> i64 {match x { -9 => -1, 0 | 1 => 1, _ => 2 }}
pub extern "C" fn answer() -> u64 {
    let mut count=0;
    let marker=match {count+=1; count} { 1 => {count+=10; 7}, _ => {count+=100; 9} };
    if count!=11 || marker!=7 || select(-9)!=-1 || select(1)!=1 || select(77)!=2 {return 0;}
    let mut p=Parcel{value:0xfedcba9876543210};
    let mut i=0;
    while i<2 {
        match i { 0 => {let q=p; p=q;}, _ => {let q=p; p=q;} }
        i+=1;
    }
    match count { 0 => take(p), _ => {let q=p; take(q)} }
}
