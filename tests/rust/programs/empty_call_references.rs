fn bump(p:&mut u64,x:u64)->u64{*p=*p*10+x;x}
fn pair<A,B>(a:A,b:B)->(A,B){(a,b)}
struct Value{x:u64}
impl Value{fn put(&mut self,x:u64){self.x=x;}fn read(&self)->u64{self.x}}
pub fn answer()->u64{
    let mut trace=0u64;
    let data=pair::<>(bump::<>(&mut trace,1),bump::<>(&mut trace,2));
    let mut value=Value{x:0};
    value.put::<>(if bump::<>(&mut trace,3)==3 {0xfedcba9876543210}else{0});
    if trace==123 && data.0==1 && data.1==2 {value.read::<>()}else{0}
}
