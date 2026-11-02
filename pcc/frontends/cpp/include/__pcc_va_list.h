// SysV x86_64 parameter-array shape shared with the existing C ABI and
// pcc/backend/self_backend_sysv_varargs.py. No void-pointer replacement.
#ifndef _PCC_CXX_VA_LIST
#define _PCC_CXX_VA_LIST
#if !defined(__linux__) || !defined(__x86_64__)
#error owned C++ va_list currently requires the Linux x86_64 ABI
#endif
struct __pcc_sysv_va_list {
    unsigned int gp_offset;
    unsigned int fp_offset;
    void *overflow_arg_area;
    void *reg_save_area;
};
typedef __pcc_sysv_va_list va_list[1];
#endif
