// N4659 [depr.c.headers]: C++ <complex.h> has the effect of <ccomplex>.
// Do not import the C99 complex/I macros or substitute PCC's C header.
#ifndef _PCC_CXX_COMPLEX_H
#define _PCC_CXX_COMPLEX_H
#include <ccomplex>
#endif
