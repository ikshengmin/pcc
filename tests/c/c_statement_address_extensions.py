"""Exact original C inputs with deliberately different PCC and GNU C results.

PROGRAMS in test_c_address_only.py remains the authoritative original source.
Each row pins the original byte SHA-256, diagnostic line/column of its outer
ampersand, and a separate, complete GNU-valid positive control. A control
never replaces its original. Locals only leave the block in an unevaluated
type query; evaluated controls take the address of a still-live outer object.
"""

# Original name: (original SHA-256, line, column, GNU/PCC control source).
OUTER_ADDRESS_EXTENSIONS = {
    'gnu-generic-address-local-const-shadow': (
        '87b0eec697e94eda1e309152089d35b0830eae8950e8b8e6c4bedb97c8006021', 1, 38,
        'int n;int main(void){return _Generic(({const int n=0;&n;}),const int*:42,default:1);}',
    ),
    'gnu-generic-address-local-array-scope': (
        '43eea13cad4109d9a50de89a852e45b142ffaef771cfaac9ded82a4621ef3a62', 1, 39,
        'int main(void){char a;return _Generic(({int a[3];&a;}),int(*)[3]:42,default:1);}',
    ),
    'gnu-whole-vla-scoped-address': (
        'b76e24fb5b75eac24a04cf81946db3d5f04faec4a8247142b67fb0257ef46d1a', 1, 45,
        'int main(void){int n=3;int a[n];if(sizeof(*(({&a;})))!=sizeof(a))return 1;return 42;}',
    ),
    'gnu-unevaluated-local-address-scope': (
        '6c8337cd35f772ce905b389a6e3f7b55653e2edd8f01a5477f908d73efa2a6bc', 1, 34,
        'int main(void){int x=0;if(sizeof(({int x=42;&x;}))!=sizeof(int*))return 1;return 42;}',
    ),
    'gnu-query-array-alias-bound-identity': (
        '5fcf9a36006ead976566a6692c2832b854779948bdc1a1e3cc6fe8cebe101fb2', 1, 55,
        'enum{N=3};typedef int A[N];int main(void){if(sizeof(*(({int N=1;A x;&_Generic(&x,int(*)[3]:x,default:0);})))!=3*sizeof(int))return 1;return 42;}',
    ),
    'gnu-query-multilevel-typedef-origin': (
        'abc5e3e1737bc493c629a997406a3ddbb40658758f95f3d3a058c47940048109', 1, 93,
        'typedef int K;typedef K Saved;int main(void){typedef char K;typedef Saved Again;if(sizeof(*(({Again x;&x;})))!=sizeof(int))return 1;return 42;}',
    ),
    'gnu-query-existing-nominal-typedef': (
        '3149f6e661fa6f0e182b313ee067fa28a55947b64f93e36b0af7145f2337d7d6', 1, 55,
        'typedef struct S{int n;} S;int main(void){if(sizeof(*(({S x;&x;})))!=sizeof(S))return 1;return 42;}',
    ),
    'gnu-query-initializer-not-evaluated': (
        'd601e0618af467fa2c5807bcc342967081204faf53c8b1aaa7a8eb13ae0f6ab5', 1, 41,
        'int side(void);int main(void){if(sizeof(({int x=side();&x;}))!=sizeof(int*))return 1;return 42;}',
    ),
    'gnu-query-local-typedef-shadow': (
        '534efc108d69435de49992be517bb6c3a82433eb3e2040eff2a20f7eead8c822', 1, 43,
        'typedef char K;int main(void){if(sizeof(*(({typedef long long K;K x;&x;})))!=sizeof(long long))return 1;return 42;}',
    ),
    'gnu-query-typedef-alias-declaration-binding': (
        '8015877bef28ef164e0bf423fe5def097e1157b63cd32d52fbc68a665497f3c2', 1, 58,
        'typedef int K;typedef K Saved;int main(void){if(sizeof(*(({typedef long long K;Saved x;&_Generic((Saved)0,int:x,default:0);})))!=sizeof(int))return 1;return 42;}',
    ),
    'gnu-query-local-fixed-array': (
        '99d9f5ae7f20bbbc96e2d08ffc5d196b165d0faf0bde120753034fef4b865509', 1, 43,
        'int side(void);int main(void){if(sizeof(*(({int a[3]={side(),0,0};&a;})))!=3*sizeof(int))return 1;return 42;}',
    ),
    'gnu-query-local-vla-pointer-no-bound-evaluation': (
        'b69c412aa8509ced827e5f81f94604075fa200bcb2fb32d2ba4dce54a875e967', 1, 41,
        'int side(void);int main(void){if(sizeof(({int n=side();int a[n];&a;}))!=sizeof(void*))return 1;return 42;}',
    ),
    'gnu-query-nested-vla-pointer-no-bound-evaluation': (
        'e1226485af3fe266570b0daf2f1e9ee32bb5611f136087089055a13a8142820a', 1, 41,
        'int side(void);int main(void){if(sizeof(({int n=side();int a[2][n];&a;}))!=sizeof(void*))return 1;return 42;}',
    ),
    'gnu-query-captured-vla-extent': (
        '42d06a97f65d3e90b2761c08580390729abd5cc594a928de4ac2261c75c18dfc', 1, 49,
        'int main(void){int n=3;int a[n];n=5;if(sizeof(*(({&a;})))!=3*sizeof(int))return 1;return 42;}',
    ),
    'gnu-statement-expression-address': (
        'ffa25cd1810a65ee863cc565ae8cd8224cefdbd83b2e191495a3c53796e5431e', 1, 43,
        'int calls=0;int main(void){int x=42;int*p=({calls++;&x;});if(calls!=1)return 1;return *p;}',
    ),
    'gnu-scoped-generic-address': (
        '60245c4464a311b6f018d6b90c37c0a0a98804930d51ded4ce2c977fe88262f9', 1, 31,
        'int main(void){int x=42;int*p=({int key=0;&_Generic(key,int:x,default:0);});return *p;}',
    ),
}
