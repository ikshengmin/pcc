#include <Python.h>
extern FILE *tmpfile(void);
extern int fclose(FILE *stream);
extern int fflush(FILE *stream);
extern int fseek(FILE *stream, long offset, int whence);
extern size_t fread(void *ptr, size_t size, size_t nmemb, FILE *stream);

static PyObject *objectapi_tuple_sum(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *tuple = PyTuple_New(2);
    if (tuple == NULL) return NULL;
    PyTuple_SET_ITEM(tuple, 0, PyLong_FromLong(11));
    PyTuple_SET_ITEM(tuple, 1, PyLong_FromLong(31));
    if (PyTuple_GET_SIZE(tuple) != 2) {
        Py_DECREF(tuple);
        return PyLong_FromLong(-1);
    }
    PyObject *a = PyTuple_GET_ITEM(tuple, 0);
    PyObject *b = PyTuple_GetItem(tuple, 1);
    long total = PyLong_AsLong(a) + PyLong_AsLong(b);
    Py_DECREF(tuple);
    return PyLong_FromLong(total);
}

static PyObject *objectapi_pack_sum(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *a = PyLong_FromLong(5);
    PyObject *b = PyLong_FromLong(9);
    PyObject *tuple = PyTuple_Pack(2, a, b);
    Py_DECREF(a);
    Py_DECREF(b);
    if (tuple == NULL) return NULL;
    long total = PyLong_AsLong(PyTuple_GET_ITEM(tuple, 0))
        + PyLong_AsLong(PyTuple_GET_ITEM(tuple, 1));
    Py_DECREF(tuple);
    return PyLong_FromLong(total);
}

static PyObject *objectapi_dict_answer(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *dict = PyDict_New();
    PyObject *value = PyLong_FromLong(41);
    if (dict == NULL || value == NULL) return NULL;
    if (PyDict_SetItemString(dict, "answer", value) != 0) return NULL;
    Py_DECREF(value);
    PyObject *found = PyDict_GetItemString(dict, "answer");
    if (found == NULL) return NULL;
    long answer = PyLong_AsLong(found) + 1;
    Py_DECREF(dict);
    return PyLong_FromLong(answer);
}

static PyObject *objectapi_list_sum(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *list = PyList_New(2);
    if (list == NULL) return NULL;
    PyList_SET_ITEM(list, 0, PyLong_FromLong(3));
    PyList_SET_ITEM(list, 1, PyLong_FromLong(4));
    PyObject *extra = PyLong_FromLong(5);
    if (extra == NULL) return NULL;
    if (PyList_Append(list, extra) != 0) return NULL;
    Py_DECREF(extra);
    if (PyList_GET_SIZE(list) != 3) {
        Py_DECREF(list);
        return PyLong_FromLong(-1);
    }
    long total = PyLong_AsLong(PyList_GET_ITEM(list, 0))
        + PyLong_AsLong(PyList_GetItem(list, 1))
        + PyLong_AsLong(PyList_GetItem(list, 2));
    Py_DECREF(list);
    return PyLong_FromLong(total);
}

static PyObject *objectapi_bytes_sum(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *bytes = PyBytes_FromString("ABC");
    if (bytes == NULL) return NULL;
    char *data = PyBytes_AsString(bytes);
    Py_ssize_t n = PyBytes_Size(bytes);
    if (data == NULL || n < 0) return NULL;
    long total = 0;
    for (Py_ssize_t i = 0; i < n; i++) total += (unsigned char)data[i];
    Py_DECREF(bytes);
    return PyLong_FromLong(total);
}

static PyObject *objectapi_bytes_size_sum(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *bytes = PyBytes_FromStringAndSize("DEF", 3);
    if (bytes == NULL) return NULL;
    char *data = NULL;
    Py_ssize_t n = 0;
    if (PyBytes_AsStringAndSize(bytes, &data, &n) != 0) return NULL;
    long total = n;
    for (Py_ssize_t i = 0; i < n; i++) total += (unsigned char)data[i];
    total += PyBytes_GET_SIZE(bytes) == 3 ? 1000 : 0;
    total += PyBytes_AS_STRING(bytes)[0] == 'D' ? 2000 : 0;
    Py_DECREF(bytes);
    return PyLong_FromLong(total);
}

static PyObject *objectapi_unicode_prefix(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    return PyUnicode_FromStringAndSize("abcdef", 3);
}

static PyObject *objectapi_unicode_utf8_size_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    const char payload[] = {'a', 'b', '\0', 'c', 'd'};
    PyObject *text = PyUnicode_FromStringAndSize(payload, 5);
    Py_ssize_t n = 0;
    long score = 0;
    if (text == NULL) return NULL;
    const char *raw = PyUnicode_AsUTF8AndSize(text, &n);
    if (raw == NULL) return NULL;
    score += n == 5 ? 100 : 0;
    score += raw[0] == 'a' ? 1 : 0;
    score += raw[2] == '\0' ? 2 : 0;
    score += raw[4] == 'd' ? 4 : 0;
    Py_DECREF(text);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_unicode_decode_length_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *bytes = PyBytes_FromStringAndSize("hello", 5);
    PyObject *text = NULL;
    PyObject *same = NULL;
    PyObject *bad = NULL;
    const char *raw = NULL;
    long score = 0;
    if (bytes == NULL) return NULL;
    text = PyUnicode_FromEncodedObject(bytes, "utf-8", NULL);
    Py_DECREF(bytes);
    if (text == NULL) return NULL;
    score += PyUnicode_GetLength(text) == 5 ? 1 : 0;
    raw = PyUnicode_AsUTF8(text);
    if (raw == NULL) return NULL;
    score += raw[0] == 'h' && raw[4] == 'o' ? 10 : 0;
    same = PyUnicode_FromEncodedObject(text, NULL, NULL);
    if (same == NULL) return NULL;
    score += same == text ? 100 : 0;
    Py_DECREF(same);
    bad = PyLong_FromLong(1);
    if (bad == NULL) return NULL;
    score += PyUnicode_FromEncodedObject(bad, NULL, NULL) == NULL && PyErr_ExceptionMatches(PyExc_TypeError) ? 1000 : 0;
    PyErr_Clear();
    Py_DECREF(bad);
    Py_DECREF(text);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_unicode_encode_concat_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *left = PyUnicode_FromString("ab");
    PyObject *right = PyUnicode_FromString("CD");
    PyObject *joined = NULL;
    PyObject *utf8 = NULL;
    PyObject *ascii = NULL;
    PyObject *encoded_utf8 = NULL;
    PyObject *latin1 = NULL;
    PyObject *non_ascii = NULL;
    PyObject *replacement = NULL;
    PyObject *replaced = NULL;
    PyObject *substring = NULL;
    PyObject *bad_ascii = NULL;
    PyObject *bad_encoded_ascii = NULL;
    char *raw = NULL;
    Py_ssize_t n = 0;
    long score = 0;
    if (left == NULL || right == NULL) return NULL;
    joined = PyUnicode_Concat(left, right);
    if (joined == NULL) return NULL;
    score += PyUnicode_GetLength(joined) == 4 ? 1 : 0;
    const char *joined_raw = PyUnicode_AsUTF8(joined);
    score += joined_raw != NULL && joined_raw[0] == 'a' && joined_raw[2] == 'C' ? 10 : 0;
    utf8 = PyUnicode_AsUTF8String(joined);
    ascii = PyUnicode_AsASCIIString(joined);
    if (utf8 == NULL || ascii == NULL) return NULL;
    if (PyBytes_AsStringAndSize(utf8, &raw, &n) != 0) return NULL;
    score += n == 4 ? 100 : 0;
    score += raw[2] == 'C' ? 1000 : 0;
    if (PyBytes_AsStringAndSize(ascii, &raw, &n) != 0) return NULL;
    score += n == 4 ? 10000 : 0;
    score += raw[3] == 'D' ? 100000 : 0;
    encoded_utf8 = PyUnicode_AsEncodedString(joined, "utf-8", NULL);
    if (encoded_utf8 == NULL) return NULL;
    if (PyBytes_AsStringAndSize(encoded_utf8, &raw, &n) != 0) return NULL;
    score += n == 4 && raw[1] == 'b' ? 2000000 : 0;
    score += PyUnicode_Tailmatch(joined, right, 0, -1, -1) == 1 && PyUnicode_Tailmatch(joined, left, 0, -1, 1) == 1 ? 32000000 : 0;
    replacement = PyUnicode_FromString("xy");
    if (replacement == NULL) return NULL;
    replaced = PyUnicode_Replace(joined, right, replacement, 1);
    if (replaced == NULL) return NULL;
    const char *replaced_raw = PyUnicode_AsUTF8(replaced);
    score += replaced_raw != NULL && replaced_raw[2] == 'x' && PyUnicode_GetLength(replaced) == 4 ? 64000000 : 0;
    substring = PyUnicode_Substring(replaced, 1, 3);
    if (substring == NULL) return NULL;
    const char *substring_raw = PyUnicode_AsUTF8(substring);
    score += substring_raw != NULL && substring_raw[0] == 'b' && substring_raw[1] == 'x' ? 128000000 : 0;
    score += PyUnicode_Contains(joined, right) == 1 ? 256000000 : 0;
    const char nonascii_raw[] = {'x', (char)0xc3, (char)0xa9};
    non_ascii = PyUnicode_FromStringAndSize(nonascii_raw, 3);
    if (non_ascii == NULL) return NULL;
    latin1 = PyUnicode_AsEncodedString(non_ascii, "latin1", NULL);
    if (latin1 == NULL) return NULL;
    if (PyBytes_AsStringAndSize(latin1, &raw, &n) != 0) return NULL;
    score += n == 2 ? 4000000 : 0;
    score += (unsigned char)raw[1] == 0xe9 ? 8000000 : 0;
    bad_ascii = PyUnicode_AsASCIIString(non_ascii);
    score += bad_ascii == NULL && PyErr_Occurred() != NULL ? 1000000 : 0;
    PyErr_Clear();
    bad_encoded_ascii = PyUnicode_AsEncodedString(non_ascii, "ascii", NULL);
    score += bad_encoded_ascii == NULL && PyErr_Occurred() != NULL ? 16000000 : 0;
    PyErr_Clear();
    Py_XDECREF(bad_encoded_ascii);
    Py_XDECREF(bad_ascii);
    Py_DECREF(non_ascii);
    Py_DECREF(substring);
    Py_DECREF(replaced);
    Py_DECREF(replacement);
    Py_DECREF(latin1);
    Py_DECREF(encoded_utf8);
    Py_DECREF(ascii);
    Py_DECREF(utf8);
    Py_DECREF(joined);
    Py_DECREF(right);
    Py_DECREF(left);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_unicode_macro_score(PyObject *Py_UNUSED(self), PyObject *Py_UNUSED(args)) {
    Py_UCS4 space = ' ';
    Py_UCS4 digit = '7';
    Py_UCS4 lower = 'a';
    Py_UCS4 upper = 'A';
    Py_UCS4 other = '?';
    PyObject *text = PyUnicode_FromString("abcd");
    long score = 0;
    if (text == NULL) return NULL;
    score += PyUnicode_GET_LENGTH(text) == 4 ? 1 : 0;
    score += Py_UNICODE_ISSPACE(space) ? 10 : 0;
    score += Py_UNICODE_ISDIGIT(digit) ? 100 : 0;
    score += Py_UNICODE_ISDECIMAL(digit) ? 1000 : 0;
    score += Py_UNICODE_ISNUMERIC(digit) ? 10000 : 0;
    score += Py_UNICODE_ISLOWER(lower) ? 100000 : 0;
    score += Py_UNICODE_ISUPPER(upper) ? 1000000 : 0;
    score += Py_UNICODE_ISTITLE(upper) ? 10000000 : 0;
    score += Py_UNICODE_ISALPHA(lower) && Py_UNICODE_ISALPHA(upper) ? 100000000 : 0;
    score += Py_UNICODE_ISALNUM(lower) && Py_UNICODE_ISALNUM(digit) && !Py_UNICODE_ISALNUM(other) ? 1000000000 : 0;
    Py_DECREF(text);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_unicode_kind_score(PyObject *Py_UNUSED(self), PyObject *Py_UNUSED(args)) {
    Py_UCS1 ucs1[] = {'A', 0xe9};
    Py_UCS2 ucs2[] = {0x03a9, 'b'};
    Py_UCS4 ucs4[] = {'A', 0x1f600};
    PyObject *text1 = NULL;
    PyObject *text2 = NULL;
    PyObject *text4 = NULL;
    PyObject *find_text = NULL;
    PyObject *find_needle = NULL;
    PyObject *empty = NULL;
    PyObject *ordinal = NULL;
    PyObject *bad = NULL;
    Py_UCS4 stack_buf[3] = {0, 0, 0};
    Py_UCS4 tiny_buf[1] = {0};
    Py_UCS4 *copy = NULL;
    const char *raw = NULL;
    Py_ssize_t n = 0;
    long score = 0;
    score += sizeof(Py_UCS1) == 1 && sizeof(Py_UCS2) == 2 && sizeof(Py_UCS4) == 4 && PyUnicode_1BYTE_KIND == 1 && PyUnicode_2BYTE_KIND == 2 && PyUnicode_4BYTE_KIND == 4 ? 1 : 0;
    text1 = PyUnicode_FromKindAndData(PyUnicode_1BYTE_KIND, ucs1, 2);
    if (text1 == NULL) return NULL;
    raw = PyUnicode_AsUTF8AndSize(text1, &n);
    score += raw != NULL && n == 3 && PyUnicode_GetLength(text1) == 2 && (unsigned char)raw[1] == 0xc3 && (unsigned char)raw[2] == 0xa9 ? 10 : 0;
    text2 = PyUnicode_FromKindAndData(PyUnicode_2BYTE_KIND, ucs2, 2);
    if (text2 == NULL) return NULL;
    raw = PyUnicode_AsUTF8AndSize(text2, &n);
    score += raw != NULL && n == 3 && PyUnicode_GetLength(text2) == 2 && (unsigned char)raw[0] == 0xce && (unsigned char)raw[1] == 0xa9 && raw[2] == 'b' ? 100 : 0;
    text4 = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, ucs4, 2);
    if (text4 == NULL) return NULL;
    raw = PyUnicode_AsUTF8AndSize(text4, &n);
    score += raw != NULL && n == 5 && PyUnicode_GetLength(text4) == 2 && raw[0] == 'A' && (unsigned char)raw[1] == 0xf0 && (unsigned char)raw[4] == 0x80 ? 1000 : 0;
    score += PyUnicode_AsUCS4(text4, stack_buf, 3, 1) == stack_buf && stack_buf[0] == 'A' && stack_buf[1] == 0x1f600 && stack_buf[2] == 0 ? 10000 : 0;
    copy = PyUnicode_AsUCS4Copy(text2);
    if (copy == NULL) return NULL;
    score += copy[0] == 0x03a9 && copy[1] == 'b' && copy[2] == 0 ? 100000 : 0;
    PyMem_Free(copy);
    copy = NULL;
    score += PyUnicode_AsUCS4(text4, tiny_buf, 1, 1) == NULL && PyErr_ExceptionMatches(PyExc_SystemError) ? 1000000 : 0;
    PyErr_Clear();
    ordinal = PyUnicode_FromOrdinal(0x2665);
    if (ordinal == NULL) return NULL;
    score += PyUnicode_GetLength(ordinal) == 1 && PyUnicode_ReadChar(ordinal, 0) == 0x2665 ? 100000000 : 0;
    score += PyUnicode_ReadChar(text4, 1) == 0x1f600 && PyUnicode_ReadChar(text2, 0) == 0x03a9 ? 1000000000 : 0;
    find_text = PyUnicode_FromString("aba");
    find_needle = PyUnicode_FromString("a");
    empty = PyUnicode_FromString("");
    if (find_text == NULL || find_needle == NULL || empty == NULL) return NULL;
    score += PyUnicode_FindChar(text4, 'A', 0, 10, 1) == 0 && PyUnicode_FindChar(text4, 0x1f600, 0, 10, 1) == 1 && PyUnicode_FindChar(find_text, 'a', 0, 3, -1) == 2 && PyUnicode_FindChar(text4, 'Z', 0, 10, 1) == -1 ? 10000000000L : 0;
    score += PyUnicode_Find(find_text, find_needle, 0, 3, 1) == 0 && PyUnicode_Find(find_text, find_needle, 0, 3, -1) == 2 && PyUnicode_Find(find_text, text2, 0, 3, 1) == -1 ? 1000000000000L : 0;
    score += PyUnicode_Count(find_text, find_needle, 0, 3) == 2 && PyUnicode_Count(find_text, empty, 0, 3) == 4 ? 10000000000000L : 0;
    score += PyUnicode_ReadChar(text4, 2) == (Py_UCS4)-1 && PyErr_ExceptionMatches(PyExc_IndexError) ? 100000000000L : 0;
    PyErr_Clear();
    bad = PyUnicode_FromKindAndData(3, ucs1, 1);
    score += bad == NULL && PyErr_ExceptionMatches(PyExc_ValueError) ? 10000000 : 0;
    PyErr_Clear();
    Py_XDECREF(copy);
    Py_XDECREF(bad);
    Py_DECREF(ordinal);
    Py_DECREF(empty);
    Py_DECREF(find_needle);
    Py_DECREF(find_text);
    Py_DECREF(text4);
    Py_DECREF(text2);
    Py_DECREF(text1);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_float_bool_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *flt = PyFloat_FromDouble(3.5);
    PyObject *truth = PyBool_FromLong(1);
    PyObject *falsehood = PyBool_FromLong(0);
    if (flt == NULL || truth == NULL || falsehood == NULL) return NULL;
    long score = (long)(PyFloat_AsDouble(flt) * 2.0);
    score += PyObject_IsTrue(truth) * 10;
    score += PyObject_IsTrue(falsehood);
    Py_DECREF(flt);
    Py_DECREF(truth);
    Py_DECREF(falsehood);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_scalar_complex_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *flt = PyFloat_FromDouble(2.5);
    PyObject *truth = PyBool_FromLong(1);
    PyObject *z = PyComplex_FromDoubles(3.0, 4.0);
    Py_complex raw;
    Py_complex made;
    Py_complex bad;
    PyObject *from_c = NULL;
    PyObject *text = NULL;
    long score = 0;
    if (flt == NULL || truth == NULL || z == NULL) return NULL;
    score += PyFloat_Check(flt) && PyFloat_CheckExact(flt) && !PyFloat_Check(truth) ? 1 : 0;
    score += PyBool_Check(truth) && !PyBool_Check(flt) ? 10 : 0;
    score += PyComplex_Check(z) && PyComplex_CheckExact(z) && !PyComplex_Check(flt) ? 100 : 0;
    score += PyComplex_RealAsDouble(z) == 3.0 ? 1000 : 0;
    score += PyComplex_ImagAsDouble(z) == 4.0 ? 10000 : 0;
    raw = PyComplex_AsCComplex(z);
    score += raw.real == 3.0 && raw.imag == 4.0 ? 100000 : 0;
    score += PyComplex_RealAsDouble(flt) == 2.5 && PyComplex_ImagAsDouble(flt) == 0.0 ? 1000000 : 0;
    made.real = 5.0;
    made.imag = 6.0;
    from_c = PyComplex_FromCComplex(made);
    if (from_c == NULL) return NULL;
    score += PyComplex_RealAsDouble(from_c) == 5.0 && PyComplex_ImagAsDouble(from_c) == 6.0 ? 10000000 : 0;
    text = PyUnicode_FromString("x");
    if (text == NULL) return NULL;
    bad = PyComplex_AsCComplex(text);
    score += bad.real == -1.0 && bad.imag == 0.0 && PyErr_ExceptionMatches(PyExc_TypeError) ? 100000000 : 0;
    PyErr_Clear();
    Py_DECREF(text);
    Py_DECREF(from_c);
    Py_DECREF(z);
    Py_DECREF(truth);
    Py_DECREF(flt);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_set_attr(PyObject *self, PyObject *args) {
    PyObject *obj = NULL;
    (void)self;
    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;
    PyObject *value = PyLong_FromLong(64);
    if (value == NULL) return NULL;
    if (PyObject_SetAttrString(obj, "answer", value) != 0) {
        Py_DECREF(value);
        return NULL;
    }
    Py_DECREF(value);
    return PyObject_GetAttrString(obj, "answer");
}

static PyObject *objectapi_has_attr(PyObject *self, PyObject *args) {
    PyObject *obj = NULL;
    const char *name = NULL;
    (void)self;
    if (!PyArg_ParseTuple(args, "Os", &obj, &name)) return NULL;
    return PyLong_FromLong(PyObject_HasAttrString(obj, name));
}

static PyObject *objectapi_format_error(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *name = PyUnicode_FromString("thing");
    if (name == NULL) return NULL;
    PyErr_Format(PyExc_ValueError, "bad %s %ld %.5R", "value", 7L, name);
    Py_DECREF(name);
    int ok = PyErr_Occurred() != NULL;
    PyErr_Clear();
    return PyLong_FromLong(ok);
}

static PyObject *objectapi_err_format_v(PyObject *type, const char *format, ...) {
    __builtin_va_list ap;
    __builtin_va_start(ap, format);
    PyObject *result = PyErr_FormatV(type, format, ap);
    __builtin_va_end(ap);
    return result;
}

static PyObject *objectapi_warning_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    score += PyErr_WarnEx(PyExc_UserWarning, "heads up", 1) == 0 ? 100 : 0;
    score += PyErr_WarnFormat(PyExc_RuntimeWarning, 1, "warn %s %ld", "value", 7L) == 0 ? 10000 : 0;
    score += PyErr_Occurred() == NULL ? 10 : 0;
    PyErr_SetString(PyExc_RuntimeError, "ignored");
    PyErr_WriteUnraisable(Py_None);
    score += PyErr_Occurred() == NULL ? 1 : 0;
    score += PyExc_Warning != NULL && PyExc_RuntimeWarning != NULL && PyExc_DeprecationWarning != NULL && PyExc_FutureWarning != NULL ? 1000 : 0;
    return PyLong_FromLong(score);
}

static PyObject *objectapi_gil_state_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    score += Py_IsInitialized() ? 1 : 0;
    PyGILState_STATE state = PyGILState_Ensure();
    score += PyGILState_Check() ? 10 : 0;
    PyGILState_Release(state);
    score += PyGILState_Check() ? 100 : 0;
    return PyLong_FromLong(score);
}

static PyObject *objectapi_error_helper_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyErr_SetNone(PyExc_MemoryError);
    score += PyErr_ExceptionMatches(PyExc_MemoryError) ? 10 : 0;
    PyErr_Clear();
    score += PyErr_Occurred() == NULL ? 1 : 0;
    PyErr_BadInternalCall();
    score += PyErr_ExceptionMatches(PyExc_SystemError) ? 100 : 0;
    PyErr_Clear();
    score += PyErr_Occurred() == NULL ? 1000 : 0;
    score += PyErr_SetFromErrno(PyExc_OSError) == NULL && PyErr_ExceptionMatches(PyExc_OSError) ? 10000 : 0;
    PyErr_Clear();
    PyObject *filename = PyUnicode_FromString("missing.dat");
    if (filename == NULL) return NULL;
    score += PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, filename) == NULL && PyErr_ExceptionMatches(PyExc_OSError) ? 100000 : 0;
    Py_DECREF(filename);
    PyErr_Clear();
    score += objectapi_err_format_v(PyExc_ValueError, "bad %s %ld", "value", 9L) == NULL && PyErr_ExceptionMatches(PyExc_ValueError) ? 1000000 : 0;
    PyErr_Clear();
    score += PyErr_CheckSignals() == 0 ? 10000000 : 0;
    PyErr_SetString(PyExc_RuntimeError, "printed error");
    PyErr_Print();
    score += PyErr_Occurred() == NULL ? 100000000 : 0;
    return PyLong_FromLong(score);
}

static PyObject *objectapi_exception_global_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    score += PyExc_BaseException != NULL && PyExc_Exception != NULL && PyExc_ArithmeticError != NULL && PyExc_LookupError != NULL && PyExc_OSError != NULL && PyExc_IOError != NULL && PyExc_AssertionError != NULL && PyExc_StopIteration != NULL && PyExc_StopAsyncIteration != NULL && PyExc_ZeroDivisionError != NULL && PyExc_ReferenceError != NULL && PyExc_BufferError != NULL && PyExc_ImportError != NULL && PyExc_ModuleNotFoundError != NULL && PyExc_ImportWarning != NULL && PyExc_FloatingPointError != NULL && PyExc_RecursionError != NULL && PyExc_UnicodeDecodeError != NULL ? 1 : 0;
    score += PyExc_OSError == PyExc_IOError ? 2 : 0;
    PyErr_SetString(PyExc_OSError, "os");
    score += PyErr_ExceptionMatches(PyExc_OSError) ? 4 : 0;
    PyErr_Clear();
    PyErr_SetString(PyExc_IOError, "io");
    score += PyErr_ExceptionMatches(PyExc_OSError) && PyErr_ExceptionMatches(PyExc_IOError) ? 8 : 0;
    PyErr_Clear();
    PyErr_SetString(PyExc_ZeroDivisionError, "zero");
    score += PyErr_ExceptionMatches(PyExc_ArithmeticError) && PyErr_ExceptionMatches(PyExc_ZeroDivisionError) ? 16 : 0;
    PyErr_Clear();
    PyErr_SetNone(PyExc_StopIteration);
    score += PyErr_ExceptionMatches(PyExc_Exception) && PyErr_ExceptionMatches(PyExc_StopIteration) ? 32 : 0;
    PyErr_Clear();
    PyErr_SetString(PyExc_AssertionError, "assert");
    score += PyErr_ExceptionMatches(PyExc_AssertionError) ? 64 : 0;
    PyErr_Clear();
    PyErr_SetString(PyExc_ReferenceError, "ref");
    score += PyErr_ExceptionMatches(PyExc_ReferenceError) ? 128 : 0;
    PyErr_Clear();
    PyErr_SetString(PyExc_ImportError, "import");
    score += PyErr_Occurred() != NULL ? 256 : 0;
    PyErr_Clear();
    PyErr_SetString(PyExc_BufferError, "buffer");
    score += PyErr_Occurred() != NULL ? 512 : 0;
    PyErr_Clear();
    PyErr_SetString(PyExc_RecursionError, "recursion");
    score += PyErr_ExceptionMatches(PyExc_RuntimeError) ? 1024 : 0;
    PyErr_Clear();
    PyErr_SetString(PyExc_UnicodeDecodeError, "unicode");
    score += PyErr_ExceptionMatches(PyExc_ValueError) ? 2048 : 0;
    PyErr_Clear();
    PyErr_SetString(PyExc_FloatingPointError, "float");
    score += PyErr_ExceptionMatches(PyExc_ArithmeticError) ? 4096 : 0;
    PyErr_Clear();
    return PyLong_FromLong(score);
}

static PyObject *objectapi_module_dict_magic(PyObject *self, PyObject *args) {
    (void)args;
    PyObject *dict = PyModule_GetDict(self);
    if (dict == NULL) return NULL;
    PyObject *magic = PyDict_GetItemString(dict, "MAGIC");
    if (magic == NULL) return NULL;
    return PyLong_FromLong(PyLong_AsLong(magic) + 1);
}

static PyObject *objectapi_bump_module_global(PyObject *self, PyObject *args) {
    (void)args;
    PyObject *dict = PyModule_GetDict(self);
    if (dict == NULL) return NULL;
    PyObject *current = PyDict_GetItemString(dict, "SEEN");
    long next = current == NULL ? 1 : PyLong_AsLong(current) + 1;
    PyObject *value = PyLong_FromLong(next);
    if (value == NULL) return NULL;
    if (PyDict_SetItemString(dict, "SEEN", value) != 0) {
        Py_DECREF(value);
        return NULL;
    }
    Py_DECREF(value);
    return PyLong_FromLong(next);
}

static PyObject *objectapi_delete_module_global(PyObject *self, PyObject *args) {
    (void)args;
    PyObject *dict = PyModule_GetDict(self);
    if (dict == NULL) return NULL;
    if (PyDict_DelItemString(dict, "SEEN") != 0) return NULL;
    return PyLong_FromLong(PyDict_GetItemString(dict, "SEEN") == NULL ? 1 : 0);
}

static PyObject *objectapi_str_repr_compare(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *text = PyUnicode_FromString("abc");
    PyObject *as_str = NULL;
    PyObject *as_repr = NULL;
    PyObject *raw = NULL;
    PyObject *as_bytes = NULL;
    PyObject *format_spec = NULL;
    PyObject *formatted = NULL;
    PyObject *rich = NULL;
    long score = 0;
    if (text == NULL) return NULL;
    as_str = PyObject_Str(text);
    as_repr = PyObject_Repr(text);
    raw = PyBytes_FromStringAndSize("xy", 2);
    format_spec = PyUnicode_FromString(">5");
    if (as_str == NULL || as_repr == NULL || raw == NULL || format_spec == NULL) return NULL;
    as_bytes = PyObject_Bytes(raw);
    if (as_bytes == NULL) return NULL;
    formatted = PyObject_Format(text, format_spec);
    if (formatted == NULL) return NULL;
    score += PyObject_RichCompareBool(text, as_str, Py_EQ) * 10;
    rich = PyObject_RichCompare(text, as_str, Py_EQ);
    if (rich == NULL) return NULL;
    score += PyObject_IsTrue(rich) * 100;
    const char *repr_raw = PyUnicode_AsUTF8(as_repr);
    if (repr_raw != NULL && repr_raw[0] == '\'') score += 1;
    char *bytes_raw = PyBytes_AsString(as_bytes);
    score += PyBytes_Size(as_bytes) == 2 ? 1000 : 0;
    score += bytes_raw != NULL && bytes_raw[0] == 'x' && bytes_raw[1] == 'y' ? 10000 : 0;
    const char *formatted_raw = PyUnicode_AsUTF8(formatted);
    score += formatted_raw != NULL && formatted_raw[0] == ' ' && formatted_raw[1] == ' ' && formatted_raw[2] == 'a' ? 100000 : 0;
    Py_DECREF(rich);
    Py_DECREF(formatted);
    Py_DECREF(format_spec);
    Py_DECREF(as_bytes);
    Py_DECREF(raw);
    Py_DECREF(as_repr);
    Py_DECREF(as_str);
    Py_DECREF(text);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_print_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *text = PyUnicode_FromString("abc");
    FILE *fp = NULL;
    char buf[16];
    size_t n = 0;
    long score = 0;
    if (text == NULL) return NULL;
    fp = tmpfile();
    if (fp == NULL) {
        Py_DECREF(text);
        PyErr_SetString(PyExc_OSError, "tmpfile failed");
        return NULL;
    }
    if (PyObject_Print(text, fp, Py_PRINT_RAW) != 0) {
        fclose(fp);
        Py_DECREF(text);
        return NULL;
    }
    if (PyObject_Print(text, fp, 0) != 0) {
        fclose(fp);
        Py_DECREF(text);
        return NULL;
    }
    fflush(fp);
    fseek(fp, 0, SEEK_SET);
    n = fread(buf, 1, sizeof(buf), fp);
    fclose(fp);
    Py_DECREF(text);
    score += n == 8 ? 1 : 0;
    score += n >= 3 && buf[0] == 'a' && buf[2] == 'c' ? 10 : 0;
    score += n >= 8 && buf[3] == '\'' && buf[7] == '\'' ? 100 : 0;
    return PyLong_FromLong(score);
}

static PyObject *objectapi_unsigned_sum(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *a = PyLong_FromUnsignedLong(5UL);
    PyObject *b = PyLong_FromUnsignedLongLong(6ULL);
    if (a == NULL || b == NULL) return NULL;
    long total = PyLong_AsLong(a) + PyLong_AsLong(b);
    Py_DECREF(a);
    Py_DECREF(b);
    return PyLong_FromLong(total);
}

static PyObject *objectapi_import_self_magic(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *module = PyImport_ImportModule("objectapi");
    if (module == NULL) return NULL;
    PyObject *magic = PyObject_GetAttrString(module, "MAGIC");
    Py_DECREF(module);
    if (magic == NULL) return NULL;
    long value = PyLong_AsLong(magic);
    Py_DECREF(magic);
    return PyLong_FromLong(value);
}

static PyObject *objectapi_sequence_score(PyObject *self, PyObject *args) {
    (void)args;
    PyObject *a = PyLong_FromLong(3);
    PyObject *b = PyLong_FromLong(4);
    PyObject *tuple = PyTuple_Pack(2, a, b);
    PyObject *list = NULL;
    PyObject *tuple2 = NULL;
    PyObject *fast = NULL;
    PyObject *item = NULL;
    PyObject *replacement = NULL;
    PyObject *concat = NULL;
    PyObject *repeat = NULL;
    PyObject *inplace_concat = NULL;
    PyObject *inplace_repeat = NULL;
    PyObject *bytes = NULL;
    PyObject *interned = NULL;
    long score = 0;
    Py_DECREF(a);
    Py_DECREF(b);
    if (tuple == NULL) return NULL;
    list = PySequence_List(tuple);
    tuple2 = PySequence_Tuple(list);
    fast = PySequence_Fast(list, "expected sequence");
    bytes = PyBytes_FromStringAndSize("x", 1);
    interned = PyUnicode_InternFromString("name");
    if (list == NULL || tuple2 == NULL || fast == NULL || bytes == NULL || interned == NULL) return NULL;
    PyObject *dict = PyModule_GetDict(self);
    if (dict == NULL) return NULL;
    score += PyTuple_Check(tuple) + PyTuple_CheckExact(tuple);
    score += PyList_Check(list) + PyList_CheckExact(list);
    score += PyDict_Check(dict) + PyDict_CheckExact(dict);
    score += PyBytes_Check(bytes) + PyBytes_CheckExact(bytes);
    score += PyUnicode_Check(interned) + PyUnicode_CheckExact(interned);
    score += PySequence_Check(list) + PySequence_Check(tuple2);
    score += PySequence_Size(list);
    score += PySequence_Length(tuple2);
    item = PySequence_GetItem(tuple2, 1);
    if (item == NULL) return NULL;
    score += PyLong_AsLong(item);
    PyObject **items = PySequence_Fast_ITEMS(fast);
    if (items == NULL) return NULL;
    score += PyLong_AsLong(items[0]);
    score += PySequence_Fast_GET_SIZE(fast);
    score += PyTuple_Check(tuple2);
    Py_DECREF(item);
    item = NULL;
    replacement = PyLong_FromLong(9);
    if (replacement == NULL) return NULL;
    if (PySequence_SetItem(list, 0, replacement) != 0) return NULL;
    Py_DECREF(replacement);
    item = PySequence_GetItem(list, 0);
    if (item == NULL) return NULL;
    score += PyLong_AsLong(item) == 9 ? 100 : 0;
    Py_DECREF(item);
    concat = PySequence_Concat(tuple, tuple2);
    if (concat == NULL) return NULL;
    score += PyTuple_Check(concat) && PySequence_Size(concat) == 4 ? 1000 : 0;
    Py_DECREF(concat);
    repeat = PySequence_Repeat(tuple, 2);
    if (repeat == NULL) return NULL;
    score += PyTuple_Check(repeat) && PySequence_Size(repeat) == 4 ? 10000 : 0;
    Py_DECREF(repeat);
    inplace_concat = PySequence_InPlaceConcat(tuple, tuple2);
    if (inplace_concat == NULL) return NULL;
    score += PyTuple_Check(inplace_concat) && PySequence_Size(inplace_concat) == 4 ? 20000 : 0;
    Py_DECREF(inplace_concat);
    inplace_repeat = PySequence_InPlaceRepeat(bytes, 3);
    if (inplace_repeat == NULL) return NULL;
    score += PyBytes_Check(inplace_repeat) && PyBytes_Size(inplace_repeat) == 3 ? 40000 : 0;
    Py_DECREF(inplace_repeat);
    Py_DECREF(interned);
    Py_DECREF(bytes);
    Py_DECREF(fast);
    Py_DECREF(tuple2);
    Py_DECREF(list);
    Py_DECREF(tuple);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_iter_contains_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *list = PyList_New(2);
    PyObject *dict = PyDict_New();
    PyObject *key = PyUnicode_FromString("k");
    PyObject *needle = PyLong_FromLong(4);
    PyObject *missing = PyLong_FromLong(5);
    PyObject *iter = NULL;
    PyObject *item = NULL;
    PyObject *self_iter = NULL;
    int status = 0;
    long score = 0;
    if (list == NULL || dict == NULL || key == NULL || needle == NULL || missing == NULL) return NULL;
    if (PyList_SetItem(list, 0, PyLong_FromLong(3)) != 0) return NULL;
    if (PyList_SetItem(list, 1, PyLong_FromLong(4)) != 0) return NULL;
    iter = PyObject_GetIter(list);
    if (iter == NULL) return NULL;
    score += PyIter_Check(iter) ? 1 : 0;
    score += !PyIter_Check(list) ? 10 : 0;
    item = PyIter_Next(iter);
    if (item == NULL) return NULL;
    score += PyLong_AsLong(item) == 3 ? 100 : 0;
    Py_DECREF(item);
    item = PyIter_Next(iter);
    if (item == NULL) return NULL;
    score += PyLong_AsLong(item) == 4 ? 1000 : 0;
    Py_DECREF(item);
    item = PyIter_Next(iter);
    score += item == NULL && PyErr_Occurred() == NULL ? 10000 : 0;
    Py_DECREF(iter);
    iter = PyObject_GetIter(list);
    if (iter == NULL) return NULL;
    status = PyIter_NextItem(iter, &item);
    if (status != 1 || item == NULL) return NULL;
    score += PyLong_AsLong(item) == 3 ? 100000000 : 0;
    Py_DECREF(item);
    status = PyIter_NextItem(iter, &item);
    if (status != 1 || item == NULL) return NULL;
    score += PyLong_AsLong(item) == 4 ? 200000000 : 0;
    Py_DECREF(item);
    item = needle;
    status = PyIter_NextItem(iter, &item);
    score += status == 0 && item == NULL && PyErr_Occurred() == NULL ? 400000000 : 0;
    self_iter = PyObject_SelfIter(iter);
    if (self_iter == NULL) return NULL;
    score += self_iter == iter ? 20000000 : 0;
    Py_DECREF(self_iter);
    item = needle;
    status = PyIter_NextItem(list, &item);
    score += status == -1 && item == NULL && PyErr_Occurred() != NULL ? 800000000 : 0;
    PyErr_Clear();
    score += PySequence_Contains(list, needle) == 1 ? 100000 : 0;
    score += PySequence_Contains(list, missing) == 0 ? 1000000 : 0;
    if (PyDict_SetItem(dict, key, needle) != 0) return NULL;
    score += PySequence_Contains(dict, key) == 1 ? 10000000 : 0;
    Py_DECREF(iter);
    Py_DECREF(missing);
    Py_DECREF(needle);
    Py_DECREF(key);
    Py_DECREF(dict);
    Py_DECREF(list);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_collection_views_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *dict = PyDict_New();
    PyObject *key1 = PyUnicode_FromString("a");
    PyObject *key2 = PyUnicode_FromString("b");
    PyObject *value1 = PyLong_FromLong(11);
    PyObject *value2 = PyLong_FromLong(22);
    PyObject *keys = NULL;
    PyObject *values = NULL;
    PyObject *items = NULL;
    PyObject *map_keys = NULL;
    PyObject *map_values = NULL;
    PyObject *map_items = NULL;
    PyObject *key_tuple = NULL;
    PyObject *item0 = NULL;
    PyObject *item0_key = NULL;
    PyObject *item0_value = NULL;
    PyObject *dict_value = NULL;
    long score = 0;
    if (dict == NULL || key1 == NULL || key2 == NULL || value1 == NULL || value2 == NULL) return NULL;
    if (PyDict_SetItem(dict, key1, value1) != 0) return NULL;
    if (PyDict_SetItem(dict, key2, value2) != 0) return NULL;
    keys = PyDict_Keys(dict);
    values = PyDict_Values(dict);
    items = PyDict_Items(dict);
    map_keys = PyMapping_Keys(dict);
    map_values = PyMapping_Values(dict);
    map_items = PyMapping_Items(dict);
    if (keys == NULL || values == NULL || items == NULL || map_keys == NULL || map_values == NULL || map_items == NULL) return NULL;
    key_tuple = PyList_AsTuple(keys);
    if (key_tuple == NULL) return NULL;
    score += PyList_Check(keys) ? 1 : 0;
    score += PyList_Size(keys) == 2 ? 10 : 0;
    score += PyList_Size(values) == 2 ? 100 : 0;
    score += PyList_Size(items) == 2 ? 1000 : 0;
    score += PyTuple_Check(key_tuple) ? 10000 : 0;
    score += PyTuple_Size(key_tuple) == 2 ? 100000 : 0;
    score += PySequence_Contains(key_tuple, key1) == 1 ? 1000000 : 0;
    score += PyList_Size(map_keys) == 2 ? 2 : 0;
    score += PyList_Size(map_values) == 2 ? 20 : 0;
    score += PyList_Size(map_items) == 2 ? 200 : 0;
    score += PyObject_LengthHint(values, 99) == 2 ? 300 : 0;
    score += PyObject_LengthHint(value1, 77) == 77 ? 400 : 0;
    item0 = PyList_GetItem(items, 0);
    if (item0 == NULL) return NULL;
    score += PyTuple_Check(item0) && PyTuple_Size(item0) == 2 ? 10000000 : 0;
    item0_key = PyTuple_GetItem(item0, 0);
    item0_value = PyTuple_GetItem(item0, 1);
    if (item0_key == NULL || item0_value == NULL) return NULL;
    dict_value = PyDict_GetItem(dict, item0_key);
    score += dict_value == item0_value ? 100000000 : 0;
    score += PySequence_Contains(values, value2) == 1 ? 1000000000 : 0;
    Py_DECREF(map_items);
    Py_DECREF(map_values);
    Py_DECREF(map_keys);
    Py_DECREF(key_tuple);
    Py_DECREF(items);
    Py_DECREF(values);
    Py_DECREF(keys);
    Py_DECREF(value2);
    Py_DECREF(value1);
    Py_DECREF(key2);
    Py_DECREF(key1);
    Py_DECREF(dict);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_set_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *one = PyLong_FromLong(1);
    PyObject *two = PyLong_FromLong(2);
    PyObject *three = PyLong_FromLong(3);
    PyObject *missing = PyLong_FromLong(99);
    PyObject *tuple = NULL;
    PyObject *set = NULL;
    long score = 0;
    if (one == NULL || two == NULL || three == NULL || missing == NULL) return NULL;
    tuple = PyTuple_Pack(3, one, two, two);
    if (tuple == NULL) return NULL;
    set = PySet_New(tuple);
    if (set == NULL) return NULL;
    score += PySet_Check(set) ? 1 : 0;
    score += PySet_CheckExact(set) ? 10 : 0;
    score += PyAnySet_Check(set) ? 100 : 0;
    score += PyAnySet_CheckExact(set) ? 1000 : 0;
    score += PySet_Size(set) == 2 ? 10000 : 0;
    score += PySet_GET_SIZE(set) == 2 ? 100000 : 0;
    score += PySet_Contains(set, two) == 1 ? 1000000 : 0;
    score += (PySet_Add(set, three) == 0 && PySet_Contains(set, three) == 1 && PySet_Size(set) == 3) ? 10000000 : 0;
    score += (PySet_Discard(set, two) == 1 && PySet_Contains(set, two) == 0 && PySet_Size(set) == 2) ? 100000000 : 0;
    score += PySet_Discard(set, missing) == 0 ? 1000000000 : 0;
    Py_DECREF(set);
    Py_DECREF(tuple);
    Py_DECREF(missing);
    Py_DECREF(three);
    Py_DECREF(two);
    Py_DECREF(one);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_attr_hash_score(PyObject *self, PyObject *args) {
    (void)args;
    PyObject *magic_name = PyUnicode_FromString("MAGIC");
    PyObject *magic = PyObject_GetAttr(self, magic_name);
    PyObject *attr_name = PyUnicode_FromString("LABEL2");
    PyObject *attr_value = PyUnicode_FromString("set");
    PyObject *missing_name = PyUnicode_FromString("MISSING_ATTR");
    PyObject *got = NULL;
    PyObject *method = NULL;
    PyObject *optional = NULL;
    long score = 0;
    if (magic_name == NULL || magic == NULL || attr_name == NULL || attr_value == NULL || missing_name == NULL) return NULL;
    score += PyLong_AsLong(magic);
    if (PyObject_SetAttr(self, attr_name, attr_value) != 0) return NULL;
    got = PyObject_GetAttr(self, attr_name);
    method = PyObject_GetAttrString(self, "tuple_sum");
    if (got == NULL || method == NULL) return NULL;
    score += (PyUnicode_CompareWithASCIIString(got, "set") == 0) ? 1 : 0;
    score += (PyUnicode_Compare(got, attr_value) == 0) ? 10 : 0;
    score += (PyObject_Hash(got) != -1) ? 100 : 0;
    score += PyCallable_Check(method) ? 1000 : 0;
    score += PyCallable_Check(self) ? 0 : 10000;
    score += PyObject_HasAttr(self, magic_name) ? 100000 : 0;
    score += PyObject_HasAttrWithError(self, attr_name) == 1 ? 200000 : 0;
    score += PyObject_HasAttrStringWithError(self, "LABEL2") == 1 ? 300000 : 0;
    score += PyObject_HasAttrWithError(self, missing_name) == 0 ? 400000 : 0;
    score += PyObject_HasAttrStringWithError(self, "MISSING_ATTR") == 0 ? 500000 : 0;
    score += PyObject_HasAttrStringWithError(NULL, "x") == -1 && PyErr_ExceptionMatches(PyExc_TypeError) ? 600000 : 0;
    PyErr_Clear();
    score += PyObject_HasAttr(self, missing_name) == 0 ? 700000 : 0;
    score += PyObject_GetOptionalAttr(self, attr_name, &optional) == 1 && optional != NULL ? 800000 : 0;
    Py_XDECREF(optional);
    optional = NULL;
    score += PyObject_GetOptionalAttrString(self, "LABEL2", &optional) == 1 && optional != NULL ? 900000 : 0;
    Py_XDECREF(optional);
    optional = NULL;
    score += PyObject_GetOptionalAttr(self, missing_name, &optional) == 0 && optional == NULL ? 1000000 : 0;
    score += PyObject_GetOptionalAttrString(NULL, "x", &optional) == -1 && PyErr_ExceptionMatches(PyExc_TypeError) ? 1100000 : 0;
    PyErr_Clear();
    Py_DECREF(method);
    Py_DECREF(got);
    Py_DECREF(missing_name);
    Py_DECREF(attr_value);
    Py_DECREF(attr_name);
    Py_DECREF(magic);
    Py_DECREF(magic_name);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_build_mem_kw_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    char *buf = (char *)PyMem_Malloc(3);
    char *zero = NULL;
    PyObject *built = NULL;
    PyObject *kw_args = NULL;
    PyObject *kwargs = NULL;
    PyObject *name = NULL;
    PyObject *value_obj = NULL;
    PyObject *owned = NULL;
    PyObject *again = NULL;
    PyObject *slot = NULL;
    PyObject *xslot = NULL;
    const char *built_text = NULL;
    char *kwlist[] = {"value", "name", NULL};
    long value = 0;
    const char *text = "unset";
    char *raw = NULL;
    Py_ssize_t raw_len = 0;
    if (buf == NULL) return PyErr_NoMemory();
    buf[0] = 'o'; buf[1] = 'k'; buf[2] = '\0';
    buf = (char *)PyMem_Realloc(buf, 4);
    if (buf == NULL) return PyErr_NoMemory();
    buf[2] = '!'; buf[3] = '\0';
    zero = (char *)PyMem_Calloc(2, 1);
    if (zero == NULL) return PyErr_NoMemory();
    built = Py_BuildValue("(is#y#)", 7, buf, (Py_ssize_t)3, "AB", (Py_ssize_t)2);
    PyMem_Free(buf);
    if (built == NULL) return NULL;
    score += PyTuple_Check(built) ? 1000 : 0;
    score += PyLong_AsLong(PyTuple_GET_ITEM(built, 0));
    built_text = PyUnicode_AsUTF8(PyTuple_GET_ITEM(built, 1));
    score += built_text[0] == 'o' && built_text[1] == 'k' && built_text[2] == '!' && built_text[3] == '\0' ? 100 : 0;
    if (PyBytes_AsStringAndSize(PyTuple_GET_ITEM(built, 2), &raw, &raw_len) != 0) return NULL;
    score += raw_len == 2 && raw[0] == 'A' && raw[1] == 'B' ? 200 : 0;
    score += zero[0] == '\0' && zero[1] == '\0' ? 1 : 0;
    PyMem_Free(zero);
    value_obj = PyLong_FromLong(10);
    kw_args = PyTuple_Pack(1, value_obj);
    Py_DECREF(value_obj);
    kwargs = PyDict_New();
    name = PyUnicode_FromString("kw");
    if (kw_args == NULL || kwargs == NULL || name == NULL) return NULL;
    if (PyDict_SetItemString(kwargs, "name", name) != 0) return NULL;
    Py_DECREF(name);
    if (!PyArg_ParseTupleAndKeywords(kw_args, kwargs, "l|s", kwlist, &value, &text)) return NULL;
    score += value;
    score += text[0] == 'k' && text[1] == 'w' && text[2] == '\0' ? 20 : 0;
    Py_DECREF(kwargs);
    Py_DECREF(kw_args);
    owned = PyLong_FromLong(5);
    again = Py_NewRef(owned);
    score += PyLong_AsLong(again);
    Py_DECREF(again);
    Py_CLEAR(owned);
    score += owned == NULL ? 30 : 0;
    score += Py_XNewRef(NULL) == NULL ? 40 : 0;
    Py_XDECREF(NULL);
    slot = PyLong_FromLong(1);
    Py_SETREF(slot, PyLong_FromLong(2));
    score += PyLong_AsLong(slot);
    Py_DECREF(slot);
    xslot = NULL;
    Py_XSETREF(xslot, PyLong_FromLong(3));
    score += PyLong_AsLong(xslot);
    Py_DECREF(xslot);
    PyErr_NoMemory();
    score += PyErr_Occurred() != NULL ? 50 : 0;
    PyErr_Clear();
    Py_DECREF(built);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_refcnt_macro_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *obj = PyUnicode_FromString("rc");
    if (obj == NULL) return NULL;
    Py_ssize_t refcnt = Py_REFCNT(obj);
    long score = 0;
    Py_INCREF(obj);
    score += Py_REFCNT(obj) == refcnt + 1 ? 1 : 0;
    Py_DECREF(obj);
    Py_SET_REFCNT(obj, Py_REFCNT(obj));
    score += Py_REFCNT(obj) == refcnt ? 10 : 0;
    Py_DECREF(obj);
    return PyLong_FromLong(score);
}

static int objectapi_vsnprintf_probe(char *buffer, size_t size, const char *format, ...) {
    __builtin_va_list ap;
    __builtin_va_start(ap, format);
    int result = PyOS_vsnprintf(buffer, size, format, ap);
    __builtin_va_end(ap);
    return result;
}

static PyObject *objectapi_memory_os_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    char formatted[32];
    char formatted_v[32];
    char *raw = (char *)PyMem_RawMalloc(3);
    char *zero = NULL;
    char *obj = NULL;
    char *obj_zero = NULL;
    char *macro_obj = NULL;
    char *del_obj = NULL;
    char *DEL_obj = NULL;
    char *mem_macro = NULL;
    int n = PyOS_snprintf(formatted, sizeof(formatted), "%s-%d", "np", 24);
    int nv = objectapi_vsnprintf_probe(formatted_v, sizeof(formatted_v), "%s-%d", "os", 7);
    score += n == 5 && formatted[0] == 'n' && formatted[4] == '4' ? 1 : 0;
    score += nv == 4 && formatted_v[0] == 'o' && formatted_v[3] == '7' ? 10 : 0;
    if (raw == NULL) return PyErr_NoMemory();
    raw[0] = 'a'; raw[1] = 'b'; raw[2] = '\0';
    raw = (char *)PyMem_RawRealloc(raw, 4);
    if (raw == NULL) return PyErr_NoMemory();
    raw[2] = 'c'; raw[3] = '\0';
    score += raw[0] == 'a' && raw[2] == 'c' ? 100 : 0;
    PyMem_RawFree(raw);
    zero = (char *)PyMem_RawCalloc(2, 1);
    if (zero == NULL) return PyErr_NoMemory();
    score += zero[0] == '\0' && zero[1] == '\0' ? 1000 : 0;
    PyMem_RawFree(zero);
    obj = (char *)PyObject_Malloc(2);
    if (obj == NULL) return PyErr_NoMemory();
    obj[0] = 'o'; obj[1] = '\0';
    obj = (char *)PyObject_Realloc(obj, 3);
    if (obj == NULL) return PyErr_NoMemory();
    obj[1] = 'k'; obj[2] = '\0';
    score += obj[0] == 'o' && obj[1] == 'k' ? 10000 : 0;
    PyObject_Free(obj);
    obj_zero = (char *)PyObject_Calloc(2, 1);
    if (obj_zero == NULL) return PyErr_NoMemory();
    score += obj_zero[0] == '\0' && obj_zero[1] == '\0' ? 100000 : 0;
    PyObject_Free(obj_zero);
    macro_obj = (char *)PyObject_MALLOC(2);
    if (macro_obj == NULL) return PyErr_NoMemory();
    macro_obj[0] = 'x'; macro_obj[1] = '\0';
    macro_obj = (char *)PyObject_REALLOC(macro_obj, 3);
    if (macro_obj == NULL) return PyErr_NoMemory();
    macro_obj[1] = 'y'; macro_obj[2] = '\0';
    score += macro_obj[0] == 'x' && macro_obj[1] == 'y' ? 1000000 : 0;
    PyObject_FREE(macro_obj);
    del_obj = (char *)PyObject_Malloc(1);
    DEL_obj = (char *)PyObject_Malloc(1);
    mem_macro = (char *)PyMem_Malloc(1);
    if (del_obj == NULL || DEL_obj == NULL || mem_macro == NULL) return PyErr_NoMemory();
    PyObject_Del(del_obj);
    PyObject_DEL(DEL_obj);
    PyMem_FREE(mem_macro);
    score += 10000000;
    return PyLong_FromLong(score);
}

static PyObject *objectapi_pythoncapi_compat_score(PyObject *self, PyObject *args) {
    (void)args;
    long score = 0;
    PyObject *abc = PyUnicode_FromString("abc");
    PyObject *abc0def = PyUnicode_FromStringAndSize("abc\0def", 7);
    PyObject *list = PyList_New(2);
    PyObject *dict = PyDict_New();
    PyObject *key = PyUnicode_FromString("key");
    PyObject *value = PyUnicode_FromString("default");
    PyObject *other = PyUnicode_FromString("other");
    PyObject *null_key = PyUnicode_FromString("null-result");
    PyObject *result = NULL;
    PyObject *item = NULL;
    PyObject *added = NULL;
    if (abc == NULL || abc0def == NULL || list == NULL || dict == NULL || key == NULL || value == NULL || other == NULL || null_key == NULL) return NULL;
    score += PyUnicode_EqualToUTF8(abc, "abc") ? 1 : 0;
    score += !PyUnicode_EqualToUTF8(abc, "ab") ? 10 : 0;
    score += PyUnicode_EqualToUTF8AndSize(abc0def, "abc\0def", 7) ? 100 : 0;
    score += !PyUnicode_EqualToUTF8(abc0def, "abc\0def") ? 1000 : 0;
    if (PyList_SetItem(list, 0, PyLong_FromLong(41)) != 0) return NULL;
    if (PyList_SetItem(list, 1, PyLong_FromLong(42)) != 0) return NULL;
    item = PyList_GetItemRef(list, 1);
    if (item == NULL) return NULL;
    score += PyLong_AsLong(item) == 42 ? 10000 : 0;
    Py_DECREF(item);
    if (PyDict_SetDefaultRef(dict, key, value, &result) != 0 || result != value) return NULL;
    score += 100000;
    Py_DECREF(result);
    result = NULL;
    if (PyDict_SetDefaultRef(dict, key, other, &result) != 1 || result != value) return NULL;
    score += 1000000;
    Py_DECREF(result);
    result = NULL;
    if (PyDict_SetDefaultRef(dict, null_key, other, NULL) != 0) return NULL;
    if (PyDict_SetDefaultRef(dict, key, other, NULL) != 1) return NULL;
    score += 10000000;
    if (PyModule_Add(self, "ADDED_BY_MODULE_ADD", PyUnicode_FromString("module-added")) != 0) return NULL;
    added = PyObject_GetAttrString(self, "ADDED_BY_MODULE_ADD");
    if (added == NULL) return NULL;
    score += PyUnicode_EqualToUTF8(added, "module-added") ? 100000000 : 0;
    Py_DECREF(added);
    Py_DECREF(other);
    Py_DECREF(null_key);
    Py_DECREF(value);
    Py_DECREF(key);
    Py_DECREF(dict);
    Py_DECREF(list);
    Py_DECREF(abc0def);
    Py_DECREF(abc);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_mapping_long_exc_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *dict = PyDict_New();
    PyObject *key = PyUnicode_FromString("a");
    PyObject *value = PyLong_FromLong(33);
    PyObject *got = NULL;
    PyObject *bval = NULL;
    PyObject *missing = NULL;
    PyObject *ss = NULL;
    PyObject *ull = NULL;
    PyObject *i32o = NULL;
    PyObject *i64o = NULL;
    PyObject *u32o = NULL;
    PyObject *u64o = NULL;
    PyObject *ptro = NULL;
    PyObject *neg = NULL;
    PyObject *truth = NULL;
    PyObject *ptype = NULL;
    PyObject *pvalue = NULL;
    PyObject *ptb = NULL;
    PyObject *next_key = NULL;
    PyObject *next_value = NULL;
    PyObject *ref = NULL;
    PyObject *missing_key = NULL;
    PyObject *ckey = NULL;
    PyObject *cval = NULL;
    Py_ssize_t pos = 0;
    int32_t i32 = 0;
    int64_t i64 = 0;
    uint32_t u32 = 0;
    uint64_t u64 = 0;
    char ptr_marker = 0;
    int overflow = 99;
    int optional_rc = 0;
    if (dict == NULL || key == NULL || value == NULL) return NULL;
    if (PyObject_SetItem(dict, key, value) != 0) return NULL;
    got = PyObject_GetItem(dict, key);
    if (got == NULL) return NULL;
    score += PyLong_AsLong(got);
    Py_DECREF(got);
    score += PyObject_Size(dict) * 10;
    score += PyMapping_Size(dict) == 1 ? 37000 : 0;
    bval = PyLong_FromLong(4);
    if (PyMapping_SetItemString(dict, "b", bval) != 0) return NULL;
    Py_DECREF(bval);
    got = PyMapping_GetItemString(dict, "b");
    if (got == NULL) return NULL;
    score += PyLong_AsLong(got);
    Py_DECREF(got);
    score += PyMapping_Length(dict) == 2 ? 38000 : 0;
    score += PyDict_Size(dict) == 2 ? 11000 : 0;
    score += PyDict_Contains(dict, key) == 1 ? 12000 : 0;
    score += PyDict_ContainsString(dict, "b") == 1 ? 13000 : 0;
    score += PyDict_ContainsString(dict, "missing") == 0 ? 14000 : 0;
    score += PyLong_AsLong(PyDict_GetItemWithError(dict, key)) == 33 ? 25000 : 0;
    if (PyDict_GetItemRef(dict, key, &ref) != 1 || ref == NULL) return NULL;
    score += PyLong_AsLong(ref) == 33 ? 26000 : 0;
    Py_DECREF(ref);
    ref = NULL;
    missing_key = PyUnicode_FromString("missing");
    if (missing_key == NULL) return NULL;
    score += PyDict_GetItemRef(dict, missing_key, &ref) == 0 && ref == NULL ? 27000 : 0;
    Py_DECREF(missing_key);
    missing_key = NULL;
    if (PyDict_GetItemStringRef(dict, "b", &ref) != 1 || ref == NULL) return NULL;
    score += PyLong_AsLong(ref) == 4 ? 28000 : 0;
    Py_DECREF(ref);
    ref = NULL;
    ckey = PyUnicode_FromString("c");
    cval = PyLong_FromLong(6);
    if (ckey == NULL || cval == NULL) return NULL;
    if (PyDict_SetItem(dict, ckey, cval) != 0) return NULL;
    Py_DECREF(cval);
    cval = NULL;
    if (PyDict_Pop(dict, ckey, &ref) != 1 || ref == NULL) return NULL;
    score += PyLong_AsLong(ref) == 6 ? 29000 : 0;
    Py_DECREF(ref);
    ref = NULL;
    score += PyDict_Contains(dict, ckey) == 0 ? 30000 : 0;
    score += PyDict_Pop(dict, ckey, &ref) == 0 && ref == NULL ? 31000 : 0;
    cval = PyLong_FromLong(8);
    if (cval == NULL) return NULL;
    if (PyDict_SetItem(dict, ckey, cval) != 0) return NULL;
    Py_DECREF(cval);
    cval = NULL;
    score += PyDict_Pop(dict, ckey, NULL) == 1 ? 33000 : 0;
    score += PyDict_Pop(dict, ckey, NULL) == 0 ? 34000 : 0;
    Py_DECREF(ckey);
    ckey = NULL;
    cval = PyLong_FromLong(7);
    if (cval == NULL) return NULL;
    if (PyMapping_SetItemString(dict, "d", cval) != 0) return NULL;
    Py_DECREF(cval);
    cval = NULL;
    score += PyDict_PopString(dict, "d", NULL) == 1 ? 35000 : 0;
    score += PyDict_PopString(dict, "d", &ref) == 0 && ref == NULL ? 36000 : 0;
    score += PyDict_GetItemWithError(NULL, key) == NULL && PyErr_ExceptionMatches(PyExc_TypeError) ? 32000 : 0;
    PyErr_Clear();
    score += PyMapping_Check(dict) ? 100 : 0;
    score += PyMapping_HasKeyString(dict, "b") ? 1000 : 0;
    optional_rc = PyMapping_GetOptionalItemString(dict, "missing", &missing);
    score += optional_rc == 0 && missing == NULL ? 2000 : 0;
    score += PyMapping_HasKeyStringWithError(dict, "b") == 1 ? 3000 : 0;
    if (PyDict_DelItem(dict, key) != 0) return NULL;
    score += PyDict_Contains(dict, key) == 0 ? 15000 : 0;
    score += PyDict_Size(dict) == 1 ? 16000 : 0;
    score += PyMapping_HasKey(dict, key) == 0 ? 4000 : 0;
    if (PyDict_SetItem(dict, key, value) != 0) return NULL;
    if (PyObject_DelItem(dict, key) != 0) return NULL;
    score += PyMapping_HasKey(dict, key) == 0 ? 50000 : 0;
    if (!PyDict_Next(dict, &pos, &next_key, &next_value)) return NULL;
    score += PyUnicode_CompareWithASCIIString(next_key, "b") == 0 && PyLong_AsLong(next_value) == 4 ? 23000 : 0;
    score += !PyDict_Next(dict, &pos, &next_key, &next_value) && pos > 0 ? 24000 : 0;
    got = PyLong_FromSize_t(12);
    if (got == NULL) return NULL;
    score += (long)PyLong_AsSize_t(got);
    score += PyLong_AsInt(got) == 12 ? 17000 : 0;
    Py_DECREF(got);
    i32o = PyLong_FromInt32((int32_t)-1234);
    if (i32o == NULL || PyLong_AsInt32(i32o, &i32) != 0) return NULL;
    score += i32 == (int32_t)-1234 ? 18000 : 0;
    Py_DECREF(i32o);
    u32o = PyLong_FromUInt32((uint32_t)4000000000U);
    if (u32o == NULL || PyLong_AsUInt32(u32o, &u32) != 0) return NULL;
    score += u32 == (uint32_t)4000000000U ? 19000 : 0;
    Py_DECREF(u32o);
    i64o = PyLong_FromInt64((int64_t)-1234567890123LL);
    if (i64o == NULL || PyLong_AsInt64(i64o, &i64) != 0) return NULL;
    score += i64 == (int64_t)-1234567890123LL ? 20000 : 0;
    Py_DECREF(i64o);
    u64o = PyLong_FromUInt64((uint64_t)9223372036854775813ULL);
    if (u64o == NULL || PyLong_AsUInt64(u64o, &u64) != 0) return NULL;
    score += u64 == (uint64_t)9223372036854775813ULL ? 21000 : 0;
    Py_DECREF(u64o);
    ptro = PyLong_FromVoidPtr((void *)&ptr_marker);
    if (ptro == NULL) return NULL;
    score += PyLong_AsVoidPtr(ptro) == (void *)&ptr_marker ? 22000 : 0;
    Py_DECREF(ptro);
    ss = PyLong_FromSsize_t((Py_ssize_t)-3);
    if (ss == NULL) return NULL;
    score += PyLong_AsLongAndOverflow(ss, &overflow) == -3 && overflow == 0 ? 5000 : 0;
    Py_DECREF(ss);
    ull = PyLong_FromUnsignedLongLong(42ULL);
    if (ull == NULL) return NULL;
    score += (long)PyLong_AsUnsignedLongLong(ull);
    score += (long)PyLong_AsUnsignedLong(ull);
    neg = PyLong_FromLong(-1);
    if (neg == NULL) return NULL;
    score += PyLong_AsUnsignedLongLongMask(neg) == (unsigned long long)-1 ? 6000 : 0;
    score += PyLong_Check(ull) ? 7000 : 0;
    truth = PyBool_FromLong(1);
    if (truth == NULL) return NULL;
    score += PyLong_Check(truth) ? 8000 : 0;
    score += !PyLong_CheckExact(truth) ? 9000 : 0;
    Py_DECREF(truth);
    Py_DECREF(neg);
    Py_DECREF(ull);
    PyErr_SetObject(PyExc_KeyError, key);
    score += PyErr_ExceptionMatches(PyExc_KeyError) ? 10000 : 0;
    PyErr_Fetch(&ptype, &pvalue, &ptb);
    score += ptype != NULL && pvalue != NULL ? 20000 : 0;
    PyErr_Restore(ptype, pvalue, ptb);
    score += PyErr_GivenExceptionMatches(PyErr_Occurred(), PyExc_KeyError) ? 30000 : 0;
    PyErr_Clear();
    Py_DECREF(value);
    Py_DECREF(key);
    Py_DECREF(dict);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_unicode_from_format_v(const char *format, ...) {
    __builtin_va_list ap;
    __builtin_va_start(ap, format);
    PyObject *result = PyUnicode_FromFormatV(format, ap);
    __builtin_va_end(ap);
    return result;
}

static PyObject *objectapi_call_type_format_score(PyObject *self, PyObject *args) {
    (void)args;
    PyObject *callable = NULL;
    PyObject *called = NULL;
    PyObject *method0 = NULL;
    PyObject *method1 = NULL;
    PyObject *method_noargs = NULL;
    PyObject *method_name = NULL;
    PyObject *formatted = NULL;
    PyObject *formatted_v = NULL;
    PyObject *type_obj = NULL;
    PyObject *label = NULL;
    long score = 0;
    callable = PyObject_GetAttrString(self, "tuple_sum");
    if (callable == NULL) return NULL;
    called = PyObject_CallFunction(callable, NULL);
    Py_DECREF(callable);
    if (called == NULL) return NULL;
    score += PyLong_AsLong(called);
    Py_DECREF(called);
    method0 = PyObject_CallMethod(self, "tuple_sum", NULL);
    if (method0 == NULL) return NULL;
    score += PyLong_AsLong(method0) * 10;
    Py_DECREF(method0);
    method1 = PyObject_CallMethod(self, "has_attr", "Os", self, "MAGIC");
    if (method1 == NULL) return NULL;
    score += PyLong_AsLong(method1) * 100;
    Py_DECREF(method1);
    method_name = PyUnicode_FromString("tuple_sum");
    if (method_name == NULL) return NULL;
    method_noargs = PyObject_CallMethodNoArgs(self, method_name);
    Py_DECREF(method_name);
    if (method_noargs == NULL) return NULL;
    score += PyLong_AsLong(method_noargs) * 1000;
    Py_DECREF(method_noargs);
    label = PyUnicode_FromString("ok");
    if (label == NULL) return NULL;
    formatted = PyUnicode_FromFormat("v=%ld/%S", 3L, label);
    if (formatted == NULL) return NULL;
    score += PyUnicode_CompareWithASCIIString(formatted, "v=3/ok") == 0 ? 10000 : 0;
    Py_DECREF(formatted);
    formatted_v = objectapi_unicode_from_format_v("vv=%ld/%S", 4L, label);
    if (formatted_v == NULL) return NULL;
    score += PyUnicode_CompareWithASCIIString(formatted_v, "vv=4/ok") == 0 ? 100000 : 0;
    Py_DECREF(formatted_v);
    Py_DECREF(label);
    type_obj = PyObject_Type(self);
    if (type_obj == NULL) return NULL;
    score += PyObject_IsInstance(self, type_obj) == 1 ? 20000 : 0;
    Py_DECREF(type_obj);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_call_helper_score(PyObject *self, PyObject *args) {
    PyObject *callable = NULL;
    PyObject *method_name = NULL;
    PyObject *result = NULL;
    PyObject *vc_args[1];
    PyObject *vc_method_args[2];
    long score = 0;
    (void)args;
    callable = PyObject_GetAttrString(self, "tuple_sum");
    if (callable == NULL) return NULL;
    result = PyObject_CallNoArgs(callable);
    Py_DECREF(callable);
    if (result == NULL) return NULL;
    score += PyLong_AsLong(result);
    Py_DECREF(result);
    callable = PyObject_GetAttrString(self, "set_attr");
    if (callable == NULL) return NULL;
    result = PyObject_CallOneArg(callable, self);
    Py_DECREF(callable);
    if (result == NULL) return NULL;
    score += PyLong_AsLong(result) * 10;
    Py_DECREF(result);
    method_name = PyUnicode_FromString("set_attr");
    if (method_name == NULL) return NULL;
    result = PyObject_CallMethodOneArg(self, method_name, self);
    Py_DECREF(method_name);
    if (result == NULL) return NULL;
    score += PyLong_AsLong(result) * 100;
    Py_DECREF(result);
    callable = PyObject_GetAttrString(self, "set_attr");
    if (callable == NULL) return NULL;
    vc_args[0] = self;
    result = PyObject_Vectorcall(callable, vc_args, 1, NULL);
    Py_DECREF(callable);
    if (result == NULL) return NULL;
    score += PyLong_AsLong(result) * 1000;
    Py_DECREF(result);
    method_name = PyUnicode_FromString("set_attr");
    if (method_name == NULL) return NULL;
    vc_method_args[0] = self;
    vc_method_args[1] = self;
    result = PyObject_VectorcallMethod(method_name, vc_method_args, 2, NULL);
    Py_DECREF(method_name);
    if (result == NULL) return NULL;
    score += PyLong_AsLong(result) * 10000;
    Py_DECREF(result);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_number_protocol_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *a = PyLong_FromLong(9);
    PyObject *b = PyLong_FromLong(4);
    PyObject *two = PyLong_FromLong(2);
    PyObject *text = PyUnicode_FromString("x");
    PyObject *add = NULL;
    PyObject *sub = NULL;
    PyObject *mul = NULL;
    PyObject *div = NULL;
    PyObject *floor_div = NULL;
    PyObject *rem = NULL;
    PyObject *pow_obj = NULL;
    PyObject *neg = NULL;
    PyObject *pos = NULL;
    PyObject *abs_obj = NULL;
    PyObject *index_true = NULL;
    PyObject *repeat = NULL;
    long score = 0;
    if (a == NULL || b == NULL || two == NULL || text == NULL) return NULL;
    add = PyNumber_Add(a, b);
    sub = PyNumber_Subtract(a, b);
    mul = PyNumber_Multiply(a, b);
    div = PyNumber_TrueDivide(a, b);
    floor_div = PyNumber_FloorDivide(a, b);
    rem = PyNumber_Remainder(a, b);
    pow_obj = PyNumber_Power(b, two, Py_None);
    neg = PyNumber_Negative(b);
    pos = PyNumber_Positive(b);
    abs_obj = PyNumber_Absolute(neg);
    index_true = PyNumber_Index(Py_True);
    repeat = PyNumber_Multiply(text, two);
    if (add == NULL || sub == NULL || mul == NULL || div == NULL || floor_div == NULL || rem == NULL || pow_obj == NULL || neg == NULL || pos == NULL || abs_obj == NULL || index_true == NULL || repeat == NULL) return NULL;
    score += PyLong_AsLong(add);
    score += PyLong_AsLong(sub) * 10;
    score += PyLong_AsLong(mul) * 100;
    score += (long)(PyFloat_AsDouble(div) * 100.0);
    score += PyLong_AsLong(floor_div) * 1000;
    score += PyLong_AsLong(rem) * 10000;
    score += PyLong_AsLong(pos) * 100000;
    score += PyLong_AsLong(pow_obj) * 100000;
    score += PyLong_AsLong(abs_obj) * 1000000;
    score += (long)PyNumber_AsSsize_t(b, PyExc_OverflowError) * 10000000;
    score += PyObject_Not(Py_False) * 100000000;
    score += PyIndex_Check(a) * 200000000;
    score += PyLong_AsLong(index_true) * 300000000;
    score += PyUnicode_CompareWithASCIIString(repeat, "xx") == 0 ? 400000000 : 0;
    Py_DECREF(repeat);
    Py_DECREF(index_true);
    Py_DECREF(abs_obj);
    Py_DECREF(pos);
    Py_DECREF(neg);
    Py_DECREF(pow_obj);
    Py_DECREF(rem);
    Py_DECREF(floor_div);
    Py_DECREF(div);
    Py_DECREF(mul);
    Py_DECREF(sub);
    Py_DECREF(add);
    Py_DECREF(text);
    Py_DECREF(two);
    Py_DECREF(b);
    Py_DECREF(a);
    return PyLong_FromLong(score);
}

static PyObject *objectapi_number_bitwise_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *one = PyLong_FromLong(1);
    PyObject *two = PyLong_FromLong(2);
    PyObject *three = PyLong_FromLong(3);
    PyObject *four = PyLong_FromLong(4);
    PyObject *six = PyLong_FromLong(6);
    PyObject *seven = PyLong_FromLong(7);
    PyObject *eight = PyLong_FromLong(8);
    PyObject *float_obj = PyFloat_FromDouble(6.5);
    PyObject *text = PyUnicode_FromString("x");
    PyObject *as_long = NULL;
    PyObject *as_float = NULL;
    PyObject *and_obj = NULL;
    PyObject *or_obj = NULL;
    PyObject *xor_obj = NULL;
    PyObject *lshift_obj = NULL;
    PyObject *rshift_obj = NULL;
    PyObject *invert_obj = NULL;
    PyObject *from_double = NULL;
    long score = 0;
    if (one == NULL || two == NULL || three == NULL || four == NULL || six == NULL || seven == NULL || eight == NULL || float_obj == NULL || text == NULL) return NULL;
    as_long = PyNumber_Long(float_obj);
    from_double = PyLong_FromDouble(-3.75);
    as_float = PyNumber_Float(seven);
    and_obj = PyNumber_And(six, three);
    or_obj = PyNumber_Or(four, one);
    xor_obj = PyNumber_Xor(seven, three);
    lshift_obj = PyNumber_Lshift(one, three);
    rshift_obj = PyNumber_Rshift(eight, two);
    invert_obj = PyNumber_Invert(two);
    if (as_long == NULL || from_double == NULL || as_float == NULL || and_obj == NULL || or_obj == NULL || xor_obj == NULL || lshift_obj == NULL || rshift_obj == NULL || invert_obj == NULL) return NULL;
    score += PyNumber_Check(seven) ? 1 : 0;
    score += !PyNumber_Check(text) ? 2 : 0;
    score += PyLong_AsLong(as_long) == 6 ? 4 : 0;
    score += PyFloat_AS_DOUBLE(as_float) == 7.0 ? 8 : 0;
    score += PyLong_AsDouble(seven) == 7.0 ? 16 : 0;
    score += PyFloat_AS_DOUBLE(float_obj) == 6.5 ? 32 : 0;
    score += PyLong_AsLong(and_obj) == 2 ? 64 : 0;
    score += PyLong_AsLong(or_obj) == 5 ? 128 : 0;
    score += PyLong_AsLong(xor_obj) == 4 ? 256 : 0;
    score += PyLong_AsLong(lshift_obj) == 8 ? 512 : 0;
    score += PyLong_AsLong(rshift_obj) == 2 ? 1024 : 0;
    score += PyLong_AsLong(invert_obj) == -3 ? 2048 : 0;
    score += PyLong_AsLong(from_double) == -3 ? 4096 : 0;
    Py_DECREF(invert_obj);
    Py_DECREF(rshift_obj);
    Py_DECREF(lshift_obj);
    Py_DECREF(xor_obj);
    Py_DECREF(or_obj);
    Py_DECREF(and_obj);
    Py_DECREF(as_float);
    Py_DECREF(from_double);
    Py_DECREF(as_long);
    Py_DECREF(text);
    Py_DECREF(float_obj);
    Py_DECREF(eight);
    Py_DECREF(seven);
    Py_DECREF(six);
    Py_DECREF(four);
    Py_DECREF(three);
    Py_DECREF(two);
    Py_DECREF(one);
    return PyLong_FromLong(score);
}

static PyMethodDef ObjectApiMethods[] = {
    {"tuple_sum", objectapi_tuple_sum, METH_VARARGS, "tuple C-API smoke"},
    {"pack_sum", objectapi_pack_sum, METH_VARARGS, "PyTuple_Pack smoke"},
    {"dict_answer", objectapi_dict_answer, METH_VARARGS, "dict C-API smoke"},
    {"list_sum", objectapi_list_sum, METH_VARARGS, "list C-API smoke"},
    {"bytes_sum", objectapi_bytes_sum, METH_VARARGS, "bytes C-API smoke"},
    {"bytes_size_sum", objectapi_bytes_size_sum, METH_VARARGS, "PyBytes_AsStringAndSize smoke"},
    {"unicode_prefix", objectapi_unicode_prefix, METH_VARARGS, "PyUnicode_FromStringAndSize smoke"},
    {"unicode_utf8_size_score", objectapi_unicode_utf8_size_score, METH_VARARGS, "PyUnicode_AsUTF8AndSize smoke"},
    {"unicode_decode_length_score", objectapi_unicode_decode_length_score, METH_VARARGS, "Unicode decode/length smoke"},
    {"unicode_encode_concat_score", objectapi_unicode_encode_concat_score, METH_VARARGS, "Unicode encode/concat C-API smoke"},
    {"unicode_macro_score", objectapi_unicode_macro_score, METH_VARARGS, "Unicode macro C-API smoke"},
    {"unicode_kind_score", objectapi_unicode_kind_score, METH_VARARGS, "Unicode kind/data C-API smoke"},
    {"float_bool_score", objectapi_float_bool_score, METH_VARARGS, "float/bool C-API smoke"},
    {"scalar_complex_score", objectapi_scalar_complex_score, METH_VARARGS, "scalar/complex C-API smoke"},
    {"set_attr", objectapi_set_attr, METH_VARARGS, "attr string smoke"},
    {"has_attr", objectapi_has_attr, METH_VARARGS, "has attr string smoke"},
    {"format_error", objectapi_format_error, METH_VARARGS, "PyErr_Format smoke"},
    {"warning_score", objectapi_warning_score, METH_VARARGS, "warning C-API smoke"},
    {"gil_state_score", objectapi_gil_state_score, METH_VARARGS, "GIL state C-API smoke"},
    {"error_helper_score", objectapi_error_helper_score, METH_VARARGS, "error helper C-API smoke"},
    {"exception_global_score", objectapi_exception_global_score, METH_VARARGS, "exception global C-API smoke"},
    {"module_dict_magic", objectapi_module_dict_magic, METH_VARARGS, "PyModule_GetDict smoke"},
    {"bump_module_global", objectapi_bump_module_global, METH_VARARGS, "mutate module globals"},
    {"delete_module_global", objectapi_delete_module_global, METH_VARARGS, "delete module global"},
    {"str_repr_compare", objectapi_str_repr_compare, METH_VARARGS, "str/repr/richcompare smoke"},
    {"print_score", objectapi_print_score, METH_VARARGS, "PyObject_Print smoke"},
    {"unsigned_sum", objectapi_unsigned_sum, METH_VARARGS, "unsigned long constructors smoke"},
    {"import_self_magic", objectapi_import_self_magic, METH_VARARGS, "PyImport_ImportModule smoke"},
    {"sequence_score", objectapi_sequence_score, METH_VARARGS, "sequence/type-check C-API smoke"},
    {"iter_contains_score", objectapi_iter_contains_score, METH_VARARGS, "iteration/containment C-API smoke"},
    {"collection_views_score", objectapi_collection_views_score, METH_VARARGS, "list/dict collection-view C-API smoke"},
    {"set_score", objectapi_set_score, METH_VARARGS, "set C-API smoke"},
    {"attr_hash_score", objectapi_attr_hash_score, METH_VARARGS, "object attr/hash C-API smoke"},
    {"build_mem_kw_score", objectapi_build_mem_kw_score, METH_VARARGS, "build/memory/keyword C-API smoke"},
    {"refcnt_macro_score", objectapi_refcnt_macro_score, METH_VARARGS, "refcount macro C-API smoke"},
    {"memory_os_score", objectapi_memory_os_score, METH_VARARGS, "raw/object memory and PyOS smoke"},
    {"pythoncapi_compat_score", objectapi_pythoncapi_compat_score, METH_VARARGS, "pythoncapi-compat C-API smoke"},
    {"mapping_long_exc_score", objectapi_mapping_long_exc_score, METH_VARARGS, "mapping/long/exception C-API smoke"},
    {"call_type_format_score", objectapi_call_type_format_score, METH_VARARGS, "call/type/format C-API smoke"},
    {"call_helper_score", objectapi_call_helper_score, METH_VARARGS, "call helper C-API smoke"},
    {"number_protocol_score", objectapi_number_protocol_score, METH_VARARGS, "abstract number protocol C-API smoke"},
    {"number_bitwise_score", objectapi_number_bitwise_score, METH_VARARGS, "number conversion/bitwise C-API smoke"},
    {NULL, NULL, 0, NULL},
};

static PyModuleDef objectapimodule = {
    PyModuleDef_HEAD_INIT, "objectapi", NULL, -1, ObjectApiMethods,
};

PyMODINIT_FUNC PyInit_objectapi(void) {
    PyObject *module = PyModule_Create(&objectapimodule);
    if (module == NULL) return NULL;
    if (PyModule_AddIntConstant(module, "MAGIC", 12) != 0) return NULL;
    if (PyModule_AddStringConstant(module, "LABEL", "ok") != 0) return NULL;
    PyObject *added_ref = PyLong_FromLong(77);
    if (added_ref == NULL) return NULL;
    if (PyModule_AddObjectRef(module, "ADDED_REF", added_ref) != 0) return NULL;
    PyObject *dict = PyModule_GetDict(module);
    PyObject *exc = PyErr_NewException("objectapi.CustomError", NULL, NULL);
    if (dict == NULL || exc == NULL) return NULL;
    if (PyDict_SetItemString(dict, "CustomError", exc) != 0) return NULL;
    Py_DECREF(exc);
    Py_DECREF(added_ref);
    return module;
}
