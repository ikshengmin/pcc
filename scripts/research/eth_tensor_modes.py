"""Bounded independent checks of manuscript Appendix D.12 and D.16.
Standard library only. Importing this module runs no audit.
"""
from fractions import Fraction as Q
from itertools import product
import random


def v2int(x):
    x=abs(x)
    return (x & -x).bit_length()-1 if x else 10**9


def v2(x):
    x=Q(x)
    return v2int(x.numerator)-v2int(x.denominator)


def residue(x,p):
    x=Q(x)
    if x.denominator%2!=1:
        raise ValueError('nonintegral two-adic coefficient')
    return x.numerator*pow(x.denominator,-1,1<<p)%(1<<p)


def cprime_zero(a):
    L=1<<a; out=1
    for q in range(-L,L):
        if q: out*=-q
    return out


def cauchy_value(a,x,y,T):
    """Evaluate the polynomial W, including at canceled roots."""
    L=1<<a; num=1; delta=y-x
    for q in range(-L,L):
        if q!=delta: num*=T-q
    return Q(num,cprime_zero(a))


def mode_coefficient(a,i,t,zeta,x,y0,b):
    L=1<<a; T=t+2*L*zeta; num=1
    for q in range(-L,L): num*=T-q
    denominator=T-y0+x
    return Q(num,cprime_zero(a)*denominator)*Q(1<<(i+1),denominator)**b


def check_mode_expansion():
    pairs=0; congruences=0; coefficient_bounds=0; zero_zeta=0
    for a in range(1,4):
        L=1<<a
        for t in range(-L,L):
            if not t: continue
            i=v2int(t)
            if i>=a: continue
            for zeta in [-2,-1,0,1,2]:
                T=t+2*L*zeta
                for x in range(L):
                    for y in range(L):
                        if ((x>>i)&1)!=((y>>i)&1): continue
                        y0=y% (1<<(i+1)); j=y//(1<<(i+1))
                        assert v2int(t-y0+x)<=i
                        coeffs=[mode_coefficient(a,i,t,zeta,x,y0,b) for b in range(7)]
                        for b,c in enumerate(coeffs):
                            assert v2(c)>=b+1, (a,t,zeta,x,y,b)
                            coefficient_bounds+=1
                        exact=cauchy_value(a,x,y,T)
                        for p in range(1,9):
                            approx=sum((coeffs[b]*j**b for b in range(p-1)),Q(0))
                            assert v2(exact-approx)>=p, (a,t,zeta,x,y,p,exact,approx)
                            congruences+=1
                        pairs+=1
                        zero_zeta+=int(zeta==0)
    return {'crossing_pair_cases':pairs,'modular_tail_checks':congruences,
            'mode_coefficient_valuation_checks':coefficient_bounds,'zero_zeta_pair_cases':zero_zeta}


def bounded_tuples(weights,cutoff):
    """Enumerate all nonnegative exponent tuples of weight < cutoff."""
    out=[0]*len(weights)
    def rec(pos,budget):
        if pos==len(weights):
            yield tuple(out); return
        for value in range(budget//weights[pos]+1):
            out[pos]=value
            yield from rec(pos+1,budget-value*weights[pos])
    if cutoff>0:
        yield from rec(0,cutoff-1)


def table_value(table,bits,indices):
    index=sum(bits[x]<<j for j,x in enumerate(indices))
    return table[index]


def direct_contraction(a,t,zeta,p,rowtables,coltables):
    L=1<<a; modulus=1<<p; T=t+2*L*zeta
    W=[[residue(cauchy_value(a,x,y,T),p) for y in range(L)] for x in range(L)]
    rowports=[[x for x in range(L) if x%2==g] for g in range(2)]
    colports=[[y for y in range(L) if 1-y%2==g] for g in range(2)]
    total=0; assignments=0
    for Y in product(range(2),repeat=L):
        A=table_value(rowtables[0],Y,rowports[0])*table_value(rowtables[1],Y,rowports[1])
        for X in product(range(2),repeat=L):
            term=A*table_value(coltables[0],X,colports[0])*table_value(coltables[1],X,colports[1])
            for x in range(L):
                z=sum(W[x][y]*X[y] for y in range(L))
                term*= z if Y[x] else 1-z
                term%=modulus
            total=(total+term)%modulus; assignments+=1
    return total,assignments


def packet_polynomial(a,t,zeta,p,g,Y,X):
    """Coefficient polynomial of the row factors targeted at group g."""
    L=1<<a; modulus=1<<p; T=t+2*L*zeta; B=p-1
    poly={(0,)*B:1}
    for x in range(L):
        if x%2!=g: continue
        local=sum(residue(cauchy_value(a,x,y,T),p)*X[y] for y in range(L) if 1-y%2==g)%modulus
        sign=1 if Y[x] else -1
        terms=[((0,)*B,local if Y[x] else 1-local)]
        for b in range(B):
            ex=[0]*B; ex[b]=1
            terms.append((tuple(ex),sign*residue(mode_coefficient(a,0,t,zeta,x,g,b),p)))
        nxt={}
        for left,c1 in poly.items():
            for right,c2 in terms:
                ex=tuple(x+y for x,y in zip(left,right))
                nxt[ex]=(nxt.get(ex,0)+c1*c2)%modulus
        poly={e:c for e,c in nxt.items() if c}
    return poly


def group_entry_data(a,t,zeta,p,g,rowtable,coltable,wrong_zero_power=False):
    """Precompute each child assignment independently of beta."""
    L=1<<a; B=p-1
    rows=[x for x in range(L) if x%2==g]
    cols=[y for y in range(L) if 1-y%2==g]
    data=[]
    for rb in product(range(2),repeat=len(rows)):
        Y=[0]*L
        for x,b in zip(rows,rb): Y[x]=b
        for cb in product(range(2),repeat=len(cols)):
            X=[0]*L
            for y,b in zip(cols,cb): X[y]=b
            weight=table_value(rowtable,Y,rows)*table_value(coltable,X,cols)
            coeffs=packet_polynomial(a,t,zeta,p,g,Y,X)
            source=[]
            # Source tile has y0=1-g; j=(y-y0)/2.
            for b in range(B):
                value=0
                for y in cols:
                    j=(y-(1-g))//2
                    power=0 if wrong_zero_power and j==0 and b==0 else j**b
                    value+=power*X[y]
                source.append(value)
            data.append((weight,coeffs,source))
    return data


def split_contraction(a,t,zeta,p,rowtables,coltables,cutoff=None,wrong_zero_power=False):
    if a!=2 or v2int(t)!=0:
        raise ValueError('this bounded D.16 instance uses a=2 and coordinate i=0')
    modulus=1<<p; B=p-1
    data=[group_entry_data(a,t,zeta,p,g,rowtables[g],coltables[g],wrong_zero_power) for g in range(2)]
    tuples=0; total=0
    for beta in bounded_tuples(list(range(1,p))*2,p if cutoff is None else cutoff):
        bytile=[beta[:B],beta[B:]]
        childvalues=[]
        for g in range(2):
            target=bytile[g]; sourceexps=bytile[1-g]; child=0
            for weight,coeffs,source in data[g]:
                value=weight*coeffs.get(target,0)
                for b,ex in enumerate(sourceexps): value*=pow(source[b],ex,modulus)
                child=(child+value)%modulus
            childvalues.append(child)
        total=(total+childvalues[0]*childvalues[1])%modulus; tuples+=1
    return total,tuples


def check_child_identity():
    rng=random.Random(20261027); cases=0; assignments=0; tuples=0
    missing_modes=[]; zero_power=[]
    for case in range(4):
        rowtables=[[rng.randrange(8) for _ in range(4)] for g in range(2)]
        coltables=[[rng.randrange(8) for _ in range(4)] for g in range(2)]
        for t,zeta,p in [(1,0,4),(1,1,4),(-1,1,5),(1,-1,6)]:
            oracle,na=direct_contraction(2,t,zeta,p,rowtables,coltables)
            actual,nb=split_contraction(2,t,zeta,p,rowtables,coltables)
            assert actual==oracle, (case,t,zeta,p,oracle,actual)
            cases+=1; assignments+=na; tuples+=nb
            if zeta:
                mutant,_=split_contraction(2,t,zeta,p,rowtables,coltables,cutoff=1)
                if mutant!=oracle: missing_modes.append({'case':case,'t':t,'zeta':zeta,'p':p,'oracle':oracle,'mutant':mutant})
                mutant,_=split_contraction(2,t,zeta,p,rowtables,coltables,wrong_zero_power=True)
                if mutant!=oracle: zero_power.append({'case':case,'t':t,'zeta':zeta,'p':p,'oracle':oracle,'mutant':mutant})
    assert len(missing_modes) == 7, 'zero-mode-only mutation coverage changed'
    assert len(zero_power) == 4, 'wrong 0**0 mutation coverage changed'
    return {'child_identity_cases':cases,'direct_boolean_assignments':assignments,'weighted_tuples_processed':tuples,
            'negative_controls':{'zero_mode_only_detected':len(missing_modes),'zero_mode_only_example':missing_modes[:1],
                                 'wrong_zero_power_detected':len(zero_power),'wrong_zero_power_example':zero_power[:1]}}


def run_audit():
    return {'status':'PASS','mode_expansion':check_mode_expansion(),'child_identity':check_child_identity(),
            'scope':'D.12 bounded scalar exact arithmetic and one-packet two-group D.16; not full recursive contraction, packing or extension-ring Lagrange',
            'seed':20261027}


if __name__=='__main__':
    import json
    print(json.dumps(run_audit(),indent=2))
