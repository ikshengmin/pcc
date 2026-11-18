"""Shared C frontend compatibility predefinitions for preprocessing and compilation."""

# GCC/Clang extensions pycparser does not understand, mapped to plain C.
# Both preprocessors apply them: the host ``cc -E`` path for the LLVM
# backend and the owned preprocessor for the self backend.  The owned path
# ran without them after the self backend stopped using ``cc -E``, so
# ``__attribute__``/``__builtin_*`` sources stopped parsing there.
_C_EXTENSION_COMPAT_DEFINES = (
    "-D__attribute(x)=",
    "-D__attribute__(x)=",
    "-D__extension__=",
    "-D__FUNCTION__=__func__",
    "-D__inline=inline",
    "-D__inline__=inline",
    "-D__restrict=restrict",
    "-D__restrict__=restrict",
    "-D_Atomic(x)=x",
    "-Dasm(...)=",
    "-D__asm__(...)=",
    "-D__asm(...)=",
    "-D_Nonnull=",
    "-D_Nullable=",
    "-D_Null_unspecified=",
    # __nonnull is a header-owned macro, not a nullability keyword.  In
    # glibc it takes an argument list; predefining it as an empty object-like
    # macro suppresses that definition and leaves stray ((1, 2)) suffixes on
    # ordinary function declarations.  __attribute__ above handles its result.
    "-D__nullable=",
    "-D__null_unspecified=",
    "-D__int128_t=__int128",
    "-D__uint128_t=unsigned __int128",
    "-D__builtin_memcpy=memcpy",
    "-D__builtin_memmove=memmove",
    "-D__builtin_memcmp=memcmp",
    "-D__builtin_memchr=memchr",
    "-D__builtin_memset=memset",
    "-D__builtin_malloc=malloc",
    "-D__builtin_free=free",
    "-D__builtin_abs=abs",
    "-D__builtin_bzero(p,n)=memset(p,0,n)",
    "-D__builtin___memcpy_chk(a,b,c,d)=memcpy(a,b,c)",
    "-D__builtin___memmove_chk(a,b,c,d)=memmove(a,b,c)",
    "-D__builtin___memset_chk(a,b,c,d)=memset(a,b,c)",
    "-D__builtin___strcpy_chk(a,b,c)=strcpy(a,b)",
    "-D__builtin___strcat_chk(a,b,c)=strcat(a,b)",
    "-D__builtin___strncpy_chk(a,b,c,d)=strncpy(a,b,c)",
    "-D__builtin___strncat_chk(a,b,c,d)=strncat(a,b,c)",
    "-D__builtin___strlcpy_chk(a,b,c,d)=strlcpy(a,b,c)",
    "-D__builtin___strlcat_chk(a,b,c,d)=strlcat(a,b,c)",
    "-D__builtin___printf_chk(flag,fmt,...)=printf(fmt,##__VA_ARGS__)",
    "-D__builtin___fprintf_chk(stream,flag,fmt,...)=fprintf(stream,fmt,##__VA_ARGS__)",
    "-D__builtin___sprintf_chk(buf,flag,obj,fmt,...)=sprintf(buf,fmt,##__VA_ARGS__)",
    "-D__builtin___snprintf_chk(buf,size,flag,obj,fmt,...)=snprintf(buf,size,fmt,##__VA_ARGS__)",
    "-D__builtin___vsprintf_chk(buf,flag,obj,fmt,ap)=vsprintf(buf,fmt,ap)",
    "-D__builtin___vsnprintf_chk(buf,size,flag,obj,fmt,ap)=vsnprintf(buf,size,fmt,ap)",
    "-D__builtin_printf=printf",
    "-D__builtin_fprintf=fprintf",
    "-D__builtin_abort()=abort()",
    "-D__builtin_return_address(level)=((void*)0)",
    "-D__builtin_choose_expr(cond,a,b)=((cond)?(a):(b))",
    "-D__builtin_strlen(x)=strlen(x)",
    "-D__builtin_strcmp(a,b)=strcmp(a,b)",
    "-D__sync_add_and_fetch(ptr,val)=(*(ptr)+=(val))",
    "-D__builtin_object_size(ptr,type)=((size_t)-1)",
    "-D__builtin_fabsf=fabsf",
    "-D__builtin_fabs=fabs",
    "-D__builtin_fabsl=fabsl",
    "-D__builtin_inff()=(1e39f)",
    "-D__builtin_inf()=(1e309)",
    "-D__builtin_infl()=((long double)1e309)",
    "-D__builtin_huge_val()=(1e309)",
    "-D__builtin_huge_valf()=(1e39f)",
    "-D__builtin_huge_vall()=((long double)1e309)",
    "-D_Alignas(x)=_Alignas(x)",
    "-Dalignas(x)=_Alignas(x)",
)


# The owned parser reads GNU attributes and asm statements; c_gnu_attributes
# and codegen_AsmStatement decide each one's meaning. Erasing them would
# silently drop packed/aligned/mode, asm operand assignments and real asm.
_OWNED_C_EXTENSION_COMPAT_DEFINES = tuple(
    define for define in _C_EXTENSION_COMPAT_DEFINES
    if not define.startswith(("-D__attribute__(", "-D__attribute(", "-Dasm(", "-D__asm__(", "-D__asm("))
)
