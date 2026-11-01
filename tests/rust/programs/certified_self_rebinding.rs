struct R{n:u64}
impl R{fn replace<'a>(mut self:&'a mut Self,other:&'a mut Self){self=other;self.n+=3;}}
pub fn answer()->u64{let mut a=R{n:7};let mut b=R{n:0xfedcba987654320d};a.replace(&mut b);if a.n!=7{return 0;}b.n}
