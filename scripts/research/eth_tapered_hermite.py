"""Exact scalar specialization of Appendix D.4.4 (D.9)-(D.10).

Rational coefficients, extension degree e=1, V degree zero only. The bounded
check does not validate the extension ring, the tensor recursion or its time
bound. Seed and domains are fixed; no work is done merely by importing.
"""

from fractions import Fraction as Q
from math import comb
import random

def trim(f):
    f=list(f)
    while len(f)>1 and f[-1]==0: f.pop()
    return f

def add(f,g):
    h=[Q(0)]*max(len(f),len(g))
    for j,v in enumerate(f): h[j]+=v
    for j,v in enumerate(g): h[j]+=v
    return trim(h)

def mul(f,g,N=None):
    if N is None: N=len(f)+len(g)-1
    h=[Q(0)]*min(N,len(f)+len(g)-1)
    for i,x in enumerate(f):
        for j,y in enumerate(g[:max(0,N-i)]): h[i+j]+=x*y
    return trim(h)

def power(f,n,N=None):
    h=[Q(1)]
    for _ in range(n): h=mul(h,f,N)
    return h

def translate(f,t,alpha,N):
    return [sum((f[j]*comb(j,i)*t**(j-i)*alpha**i for j in range(i,len(f))),Q(0)) for i in range(N)]

def invert_series(f,N):
    assert f[0]==1
    h=[Q(1)]+[Q(0)]*(N-1)
    for j in range(1,N): h[j]=-sum((f[i]*h[j-i] for i in range(1,min(j+1,len(f)))),Q(0))
    return h

def evalp(f,x):
    h=Q(0)
    for v in reversed(f): h=h*x+v
    return h

def v2int(x):
    x=abs(x)
    if not x: return 10**9
    return (x & -x).bit_length()-1

def v2(x):
    x=Q(x)
    return v2int(x.numerator)-v2int(x.denominator)

def modq(x,p):
    x=Q(x)
    assert x.denominator%2==1, (x,p)
    return (x.numerator*pow(x.denominator,-1,1<<p))%(1<<p)

def cauchy(a,delta):
    L=1<<a; S=range(-L,L); den=1
    for q in S:
        if q: den*= -q
    f=[Q(1)]
    for q in S:
        if q!=delta: f=mul(f,[-Q(q),Q(1)])
    return [v/den for v in f]



def audit_tapered_hermite():
    rng = random.Random(20261027)
    # Exhaust every taper node in these bounded parameter ranges, rather than random nodes.
    valuation_nodes=0
    for a in range(1,7):
        L=1<<a; S=list(range(-L,L))
        for u in range(1,a+1):
            for M in sorted({u,u+1,2*u+1}):
                h=(3*M+u-1)//u
                qmult={t:h*max(0,u-v2int(t)) for t in S if t}
                for t,qt in qmult.items():
                    if not qt: continue
                    i=v2int(t); D=L//(1<<i)
                    direct=sum(qq*(v2int(q)-v2int(t-q)) for q,qq in qmult.items() if q!=t)
                    formula=sum(Q(D*j,1<<j)*(h*max(0,u-i-j)-h*max(0,u-i)) for j in range(1,a-i+1))
                    assert direct==formula, (a,u,M,t,direct,formula)
                    assert direct>=-6*D*h
                    assert sum(qmult.values())>M*(2*L-1)
                    valuation_nodes+=1

    cases=[]; reconstructions=0; exact_identities=0; jet_coefficients=0; local_integrality_coefficients=0
    negative_underprecision_failures=[]; negative_missing_scale_failures=[]
    for a in range(1,4):
        L=1<<a; S=list(range(-L,L)); alpha=2*L
        for u in range(1,a+1):
            for M in sorted({u,u+1}):
                h=(3*M+u-1)//u; qmult={t:h*max(0,u-v2int(t)) for t in S if t}
                # Two independent polynomial families: maximal-degree monomials and products of Cauchy kernels.
                for family in ['integer_dense_polynomial','rational_cauchy_product']:
                    if family=='integer_dense_polynomial':
                        F=[Q(rng.randint(-5,5)) for _ in range(M*(2*L-1)+1)]
                        F[-1]=Q(1)
                    else:
                        F=[Q(rng.randint(-5,5))]
                        for j in range(5):
                            term=[Q(rng.randint(-5,5))]
                            for z in range(M):
                                delta=0 if j<2 else rng.randrange(-(L-1),L)
                                term=mul(term,cauchy(a,delta))
                            F=add(F,term)
                    assert len(F)-1<=M*(2*L-1)
                    for t in S:
                        loc=translate(F,t,alpha,len(F))
                        assert all(v2(x)>=0 for x in loc), (a,u,M,family,t)
                        local_integrality_coefficients+=len(loc)
                    exact=Q(0); modular_data=[]
                    for t,qt in qmult.items():
                        if not qt: continue
                        E0=Q(1); Ejet=[Q(1)]
                        for q,qq in qmult.items():
                            if q==t or not qq: continue
                            E0*=Q(-q,t-q)**qq
                            Ejet=mul(Ejet,power([Q(1),Q(alpha,t-q)],qq,qt),qt)
                        inv=invert_series(Ejet,qt)
                        assert all(v2(x)>=0 for x in inv)
                        jet=translate(F,t,alpha,qt); jet_coefficients+=len(jet)
                        quotient=mul(jet,inv,qt)
                        exact+=E0*evalp(quotient,Q(-t,alpha))
                        modular_data.append((t,qt,E0,inv,jet))
                    assert exact==F[0], (a,u,M,family,exact,F[0])
                    exact_identities+=1
                    for p in [1,3,7]:
                        for lift in ['canonical','perturbed']:
                            recovered=Q(0)
                            for t,qt,E0,inv,jet in modular_data:
                                D=L//(1<<v2int(t)); pt=p+8*h*D+8*M*(a+1); modulus=1<<pt
                                jin=[modq(x,pt) for x in jet]; ii=[modq(x,pt) for x in inv]
                                quot=[sum(jin[z]*ii[j-z] for z in range(j+1))%modulus for j in range(qt)]
                                if lift=='perturbed': quot=[x+rng.randint(-3,3)*modulus for x in quot]
                                recovered+=E0*evalp(quot,Q(-t,alpha))
                            assert v2(recovered-F[0])>=p, (a,u,M,family,p,lift)
                            assert modq(recovered,p)==modq(F[0],p)
                            reconstructions+=1
                    # Negative controls: deliberately violate precision and local-coordinate scaling.
                    pbad=3; wrong=Q(0); wrong_scale=Q(0)
                    for t,qt,E0,inv,jet in modular_data:
                        modulus=1<<pbad
                        jin=[modq(x,pbad) for x in jet]; ii=[modq(x,pbad) for x in inv]
                        quot=[sum(jin[z]*ii[j-z] for z in range(j+1))%modulus for j in range(qt)]
                        wrong+=E0*evalp(quot,Q(-t,alpha))
                        exactquot=mul(jet,inv,qt)
                        wrong_scale+=E0*evalp(exactquot,Q(-t))
                    if v2(wrong-F[0])<pbad:
                        negative_underprecision_failures.append({'a':a,'u':u,'M':M,'family':family,'error_v2':v2(wrong-F[0]),'target_p':pbad})
                    if wrong_scale!=F[0]:
                        negative_missing_scale_failures.append({'a':a,'u':u,'M':M,'family':family})
                    cases.append({'a':a,'u':u,'M':M,'family':family,'degree':len(F)-1})
    assert len(negative_underprecision_failures) == 24
    assert len(negative_missing_scale_failures) == 24
    return ({'status':'PASS','valuation_nodes_exhaustively_checked':valuation_nodes,'polynomial_cases':len(cases),'exact_Hermite_identities':exact_identities,'modular_reconstructions':reconstructions,'input_jet_coefficients':jet_coefficients,'local_integrality_coefficients':local_integrality_coefficients,'negative_controls':{'underprecision_detected':len(negative_underprecision_failures),'underprecision_example':negative_underprecision_failures[:1],'missing_alpha_scale_detected':len(negative_missing_scale_failures)},'seed':20261027,'coverage':{'a_valuation':'1..6','a_recovery':'1..3','precisions':[1,3,7],'lift_types':['canonical','perturbed by random signed multiples of 2^pt'],'polynomials':['maximal-degree integer polynomial','sum of products of rational Cauchy kernels']}})
