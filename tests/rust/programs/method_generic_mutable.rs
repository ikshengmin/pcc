struct Cell<T>{value:T}
impl<T> Cell<T>{
    fn replace<U>(&mut self,next:T,x:U)->U{self.value=next;x}
    fn keep<U>(self:&mut Self,x:U)->U{x}
    fn empty<U>()->[U;0]{[]}
}
pub fn answer()->u64{
    let mut c=Cell::<u64>{value:1};
    let a=c.replace(2u64,40u64);
    let b=Cell::<u64>::replace::<u64>(&mut c,3,2);
    let yes=c.keep(true);
    let alias=&mut c;
    let alias_a=alias.replace(4u64,19u64);
    let alias_b=alias.replace(5u64,23u64);
    let z:[u32;0]=Cell::<u64>::empty();
    if a+b==42 && c.value==5 && alias_a+alias_b==42 && yes && z.len()==0{0xfedcba9876543210u64}else{0}
}
