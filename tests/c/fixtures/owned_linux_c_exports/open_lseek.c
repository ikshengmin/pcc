/* Ordinary variadic C ABI calls; the fixture is compiled and linked by pcc. */
int open(const char *, int, ...);
int close(int);
long read(int, void *, unsigned long);
long write(int, const void *, unsigned long);
long lseek(int, long, int);
int pcc_errno_get(void);
void pcc_errno_set(int);

int main(int argc, char **argv) {
    char byte = 0;
    int fd;
    if (argc != 5) return 10;
    pcc_errno_set(123);
    fd = open(argv[1], 2 | 64 | 128, 0600);
    if (fd < 0 || pcc_errno_get() != 123) return 11;
    if (write(fd, "abc", 3) != 3) return 12;
    if (lseek(fd, 4294967297L, 0) != 4294967297L) return 13;
    if (lseek(fd, -4294967296L, 1) != 1L) return 14;
    if (read(fd, &byte, 1) != 1 || byte != 'b') return 15;
    if (lseek(fd, -1L, 2) != 2L || read(fd, &byte, 1) != 1 || byte != 'c') return 16;
    if (pcc_errno_get() != 123) return 17;
    if (lseek(fd, 0L, 99) != -1L || pcc_errno_get() != 22) return 18;
    if (close(fd) != 0) return 19;
    fd = open(argv[1], 1 | 1024);
    if (fd < 0 || write(fd, "d", 1) != 1 || close(fd) != 0) return 20;
    fd = open(argv[1], 0);
    if (fd < 0 || lseek(fd, 0L, 2) != 4L || close(fd) != 0) return 21;
    if (open(argv[1], 64 | 128, 0600) != -1 || pcc_errno_get() != 17) return 22;
    if (open(argv[2], 0) != -1 || pcc_errno_get() != 2) return 23;
    if (open((void *)0, 0) != -1 || pcc_errno_get() != 14) return 24;
    if (lseek(-1, 0L, 0) != -1L || pcc_errno_get() != 9) return 25;
    fd = open(argv[3], 1 | 64 | 128, 0);
    if (fd < 0 || close(fd) != 0) return 26;
    fd = open(argv[4], 65536); /* x86-64 O_DIRECTORY, no third argument. */
    if (fd < 0 || close(fd) != 0) return 27;
    /* Exercise O_TMPFILE's mode argument without requiring filesystem support. */
    if (open(argv[2], 4259840 | 2, 0600) != -1 || pcc_errno_get() != 2) return 28;
    return 0;
}
