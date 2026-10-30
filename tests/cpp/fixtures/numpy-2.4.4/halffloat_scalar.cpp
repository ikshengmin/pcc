/* Exact NumPy 2.4.4 halffloat.cpp function definitions; see README.md and LICENSE.txt. */

int npy_half_iszero(npy_half h)
{
    return (h&0x7fff) == 0;
}

int npy_half_isinf(npy_half h)
{
    return ((h&0x7fffu) == 0x7c00u);
}

int npy_half_isfinite(npy_half h)
{
    return ((h&0x7c00u) != 0x7c00u);
}

int npy_half_signbit(npy_half h)
{
    return (h&0x8000u) != 0;
}

npy_half npy_half_copysign(npy_half x, npy_half y)
{
    return (x&0x7fffu) | (y&0x8000u);
}


