extern long host_add(long value);

long extension_counter = 7;
long extension_zero;

long extension_entry(long value) {
    return host_add(value) + 1;
}

long extension_bump(long value) {
    extension_zero += value;
    extension_counter += value;
    return extension_counter + extension_zero;
}
