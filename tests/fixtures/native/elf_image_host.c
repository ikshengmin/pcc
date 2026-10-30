/* Exact raw mapper ABI: i64 status, opaque pointers, explicit image owner. */
extern long pcc_linux_elf_image_open(const char *, const void *, void **);
extern void *pcc_linux_elf_image_symbol(void *, const char *);
extern long pcc_linux_elf_image_close(void *);
extern unsigned char __pcc_elf_host_exports[];
extern long write(int, const void *, unsigned long);

long host_add(long value) {
    return value + 5;
}

int main(int argc, char **argv) {
    void *image = (void *)1;
    long status;
    long (*entry)(long);
    long (*bump)(long);
    long *counter;
    long *zero;

    if (argc == 2) {
        /* This executable is linked with the linker's real empty table. */
        status = pcc_linux_elf_image_open(argv[1], __pcc_elf_host_exports, &image);
        if (status != 3 || image != (void *)0) return 31;
        if (pcc_linux_elf_image_symbol(image, "extension_entry") != (void *)0) return 32;
        if (pcc_linux_elf_image_close(image) != 0) return 33;
        if (write(1, "missing-host status=3 null-owner\n", 33) != 33) return 34;
        return 0;
    }
    if (argc != 3) return 64;

    status = pcc_linux_elf_image_open(argv[1], __pcc_elf_host_exports, &image);
    if (status != 0 || image == (void *)0) return 10;
    entry = (long (*)(long))pcc_linux_elf_image_symbol(image, "extension_entry");
    bump = (long (*)(long))pcc_linux_elf_image_symbol(image, "extension_bump");
    counter = (long *)pcc_linux_elf_image_symbol(image, "extension_counter");
    zero = (long *)pcc_linux_elf_image_symbol(image, "extension_zero");
    if (entry == (void *)0 || bump == (void *)0 || counter == (void *)0 || zero == (void *)0) return 11;
    if (entry(36) != 42) return 12;
    if (*counter != 7 || *zero != 0) return 13;
    if (bump(9) != 25 || *counter != 16 || *zero != 9) return 14;
    if (pcc_linux_elf_image_symbol(image, "absent") != (void *)0) return 15;
    if (pcc_linux_elf_image_symbol(image, "host_add") != (void *)0) return 16;
    if (pcc_linux_elf_image_close(image) != 0) return 17;
    image = (void *)0;
    if (write(1, "map-call-data-close result=42 data=16 bss=9\n", 44) != 44) return 18;

    image = (void *)1;
    status = pcc_linux_elf_image_open(argv[2], __pcc_elf_host_exports, &image);
    if (status != 1 || image != (void *)0) return 21;
    if (pcc_linux_elf_image_close(image) != 0) return 22;
    if (write(1, "malformed status=1 null-owner\n", 30) != 30) return 23;
    return 0;
}
