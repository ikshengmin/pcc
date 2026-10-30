typedef unsigned long pthread_t;
int pthread_create(pthread_t *, const void *, void *(*)(void *), void *);
int pthread_join(pthread_t, void **);
char *strerror(int);
int pcc_errno_get(void);
void pcc_errno_set(int);
int pcc_errno_message_into(int, char *, long);

static int same(const char *left, const char *right) {
    if (!left || !right) return 0;
    while (*left && *left == *right) { left++; right++; }
    return *left == *right;
}

struct task { int error; int failed; };
static void *worker(void *raw) {
    struct task *task = (struct task *)raw;
    const char *expected = task->error == 2 ? "No such file or directory" : "Permission denied";
    pcc_errno_set(task->error);
    for (int i = 0; i < 1000; i++) {
        const char *known = strerror(task->error);
        const char *unknown = strerror(-1);
        if (!same(known, expected) || !same(unknown, "Unknown error") ||
            pcc_errno_get() != task->error) task->failed = 1;
    }
    return (void *)0;
}

int main(void) {
    struct task left = {2, 0}, right = {13, 0};
    pthread_t a, b;
    char formatted[64];
    pcc_errno_set(123);
    const char *known = strerror(9);
    const char *unknown = strerror(2147483647);
    if (!same(strerror(-2147483647 - 1), "Unknown error")) return 10;
    if (!same(strerror(41), "Unknown error") || !same(strerror(58), "Unknown error")) return 11;
    if (!same(known, "Bad file descriptor") || !same(unknown, "Unknown error")) return 12;
    if (pcc_errno_get() != 123) return 13;
    if (pcc_errno_message_into(9999, formatted, 64) != 0 ||
        !same(formatted, "Unknown error 9999")) return 14;
    if (pthread_create(&a, (void *)0, worker, &left) != 0) return 15;
    if (pthread_create(&b, (void *)0, worker, &right) != 0) return 16;
    if (pthread_join(a, (void *)0) != 0 || pthread_join(b, (void *)0) != 0) return 17;
    if (left.failed || right.failed || pcc_errno_get() != 123) return 18;
    if (!same(known, "Bad file descriptor") || !same(unknown, "Unknown error")) return 19;
    return 0;
}
