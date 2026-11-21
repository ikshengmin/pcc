"""Ordinary CPython witness for the same string operations and lifetime values."""
a,b="Ada:","pro"; total=0
for r in range(3):
    for i in range(30*64):
        v=a+b
        total+=len(v)
        if (r,i)==(0,0): direct=v
        if (r,i)==(0,64): remembered=v
    assert (direct[4],remembered[6])==("p","o")
assert total==40320
print(total)
