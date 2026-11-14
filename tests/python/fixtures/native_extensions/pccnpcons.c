#define PY_SSIZE_T_CLEAN
#include <stdint.h>
#include <numpy/arrayobject.h>
#include <numpy/ufuncobject.h>

static PyObject *pccnpcons_table_shape_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    if (PyArray_API == NULL || PyUFunc_API == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "NumPy C API tables were not imported");
        return NULL;
    }
    long count = 0;
    for (int i = 0; i < 23; i++) {
        if (PyArray_API[i] != NULL) count++;
    }
    if (PyUFunc_API[0] != NULL) count += 100;
    return PyLong_FromLong(count);
}

static PyObject *pccnpcons_type_object_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyTypeObject *array_type = &PyArray_Type;
    PyTypeObject *descr_type = &PyArrayDescr_Type;
    long score = 0;
    if (array_type != NULL && descr_type != NULL && array_type != descr_type) score += 1;
    if (Py_TYPE((PyObject *)array_type) == &PyType_Type && Py_TYPE((PyObject *)descr_type) == &PyType_Type) score += 10;
    if ((PyType_GetFlags(array_type) & Py_TPFLAGS_READY) && (PyType_GetFlags(descr_type) & Py_TPFLAGS_READY)) score += 100;
    if (array_type->tp_name != NULL && strcmp(array_type->tp_name, "numpy.ndarray") == 0) score += 1000;
    if (descr_type->tp_name != NULL && strcmp(descr_type->tp_name, "numpy.dtype") == 0) score += 10000;
    if (PyType_IsSubtype(array_type, &PyBaseObject_Type) && PyType_IsSubtype(descr_type, &PyBaseObject_Type)) score += 100000;
    npy_intp dims[1] = {1};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    if (PyObject_TypeCheck(arr, array_type)) score += 1000000;
    if (Py_TYPE(arr) == array_type) score += 10000000;
    PyArray_Descr *descr = PyArray_DescrFromType(NPY_INT);
    if (descr != NULL && PyObject_TypeCheck((PyObject *)descr, descr_type)) score += 100000000;
    PyArray_Descr *arr_descr = PyArray_DESCR((PyArrayObject *)arr);
    if (arr_descr != NULL && Py_TYPE((PyObject *)arr_descr) == descr_type) score += 1000000000;
    Py_DECREF(arr);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_type_object_from_type_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *bool_type = PyArray_TypeObjectFromType(NPY_BOOL);
    PyObject *int_type = PyArray_TypeObjectFromType(NPY_INT);
    PyObject *uint_type = PyArray_TypeObjectFromType(NPY_UINT);
    PyObject *double_type = PyArray_TypeObjectFromType(NPY_DOUBLE);
    PyObject *complex_type = PyArray_TypeObjectFromType(NPY_CDOUBLE);
    PyObject *string_type = PyArray_TypeObjectFromType(NPY_STRING);
    PyObject *object_type = PyArray_TypeObjectFromType(NPY_OBJECT);
    long score = 0;
    if (bool_type == (PyObject *)&PyBool_Type) score += 1;
    if (int_type == (PyObject *)&PyLong_Type && uint_type == (PyObject *)&PyLong_Type) score += 10;
    if (double_type == (PyObject *)&PyFloat_Type) score += 100;
    if (complex_type == (PyObject *)&PyComplex_Type) score += 1000;
    if (string_type == (PyObject *)&PyBytes_Type) score += 10000;
    if (object_type == (PyObject *)&PyBaseObject_Type) score += 100000;
    PyObject *bad = PyArray_TypeObjectFromType(9999);
    if (bad == NULL && PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 1000000;
    Py_XDECREF(bad);
    PyErr_Clear();
    Py_XDECREF(bool_type);
    Py_XDECREF(int_type);
    Py_XDECREF(uint_type);
    Py_XDECREF(double_type);
    Py_XDECREF(complex_type);
    Py_XDECREF(string_type);
    Py_XDECREF(object_type);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_unsupported_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyArray_Descr *descr = PyArray_DescrFromType(NPY_VOID);
    if (descr != NULL) {
        PyErr_SetString(PyExc_RuntimeError, "PyArray_DescrFromType unexpectedly succeeded");
        return NULL;
    }
    if (PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 1;
    PyErr_Clear();
    PyObject *result = PyUFunc_FromFuncAndData(NULL, NULL, NULL, 0, 1, 1, 0, "stub", "stub", 0);
    if (result != NULL) return result;
    if (PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 100;
    PyErr_Clear();
    return PyLong_FromLong(score);
}

static void pccnpcons_add_double_loop(char **args, const npy_intp *dimensions, const npy_intp *steps, void *data) {
    (void)data;
    char *lhs = args[0];
    char *rhs = args[1];
    char *out = args[2];
    for (npy_intp i = 0; i < dimensions[0]; i++) {
        *(double *)out = *(double *)lhs + *(double *)rhs;
        lhs += steps[0];
        rhs += steps[1];
        out += steps[2];
    }
}

static PyObject *pccnpcons_ufunc_registration_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyUFuncGenericFunction funcs[1] = {pccnpcons_add_double_loop};
    void *loop_data[1] = {NULL};
    char types[3] = {NPY_DOUBLE, NPY_DOUBLE, NPY_DOUBLE};
    PyObject *ufunc = PyUFunc_FromFuncAndData(funcs, loop_data, types, 1, 2, 1, 0, "pccadd", "doc", 0);
    if (ufunc == NULL) return NULL;
    long score = 0;
    if (!PyCapsule_CheckExact(ufunc)) score += 1;
    npy_intp dims[1] = {3};
    PyObject *lhs_obj = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    PyObject *rhs_obj = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    if (lhs_obj == NULL || rhs_obj == NULL) {
        Py_XDECREF(lhs_obj);
        Py_XDECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    double *lhs = (double *)PyArray_DATA((PyArrayObject *)lhs_obj);
    double *rhs = (double *)PyArray_DATA((PyArrayObject *)rhs_obj);
    if (lhs != NULL && rhs != NULL) {
        lhs[0] = 1.5;
        lhs[1] = 2.5;
        lhs[2] = 3.5;
        rhs[0] = 10.0;
        rhs[1] = 20.0;
        rhs[2] = 30.0;
        score += 10;
    }
    PyObject *out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, rhs_obj, NULL);
    if (out_obj == NULL) {
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    if (PyArray_Check(out_obj) && PyArray_SIZE((PyArrayObject *)out_obj) == 3) score += 100;
    PyArray_Descr *out_descr = PyArray_DESCR((PyArrayObject *)out_obj);
    if (out_descr != NULL && out_descr->type_num == NPY_DOUBLE) score += 1000;
    double *out = (double *)PyArray_DATA((PyArrayObject *)out_obj);
    if (out != NULL && out[0] == 11.5 && out[1] == 22.5 && out[2] == 33.5) score += 10000;
    Py_DECREF(out_obj);
    PyObject *scalar = PyFloat_FromDouble(7.25);
    if (scalar == NULL) {
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, scalar, NULL);
    if (out_obj == NULL) {
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);
    if (out != NULL && out[0] == 8.75 && out[1] == 9.75 && out[2] == 10.75) score += 100000;
    Py_DECREF(out_obj);
    out_obj = PyObject_CallFunctionObjArgs(ufunc, scalar, rhs_obj, NULL);
    if (out_obj == NULL) {
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);
    if (out != NULL && out[0] == 17.25 && out[1] == 27.25 && out[2] == 37.25) score += 1000000;
    Py_DECREF(out_obj);
    PyObject *scalar2 = PyFloat_FromDouble(0.75);
    if (scalar2 == NULL) {
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    PyObject *scalar_result = PyObject_CallFunctionObjArgs(ufunc, scalar, scalar2, NULL);
    if (scalar_result == NULL) {
        Py_DECREF(scalar2);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    if (PyFloat_Check(scalar_result) && PyFloat_AsDouble(scalar_result) == 8.0 && PyErr_Occurred() == NULL) score += 10000000;
    Py_DECREF(scalar_result);
    Py_DECREF(scalar2);
    PyObject *int_scalar = PyLong_FromLong(4);
    if (int_scalar == NULL) {
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, int_scalar, NULL);
    if (out_obj == NULL) {
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);
    if (out != NULL && out[0] == 5.5 && out[1] == 6.5 && out[2] == 7.5) score += 100000000;
    Py_DECREF(out_obj);
    scalar_result = PyObject_CallFunctionObjArgs(ufunc, scalar, int_scalar, NULL);
    if (scalar_result == NULL) {
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    if (PyFloat_Check(scalar_result) && PyFloat_AsDouble(scalar_result) == 11.25 && PyErr_Occurred() == NULL) score += 1000000000;
    Py_DECREF(scalar_result);
    PyObject *int_arr_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    if (int_arr_obj == NULL) {
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    int *int_arr = (int *)PyArray_DATA((PyArrayObject *)int_arr_obj);
    if (int_arr != NULL) {
        int_arr[0] = 4;
        int_arr[1] = 5;
        int_arr[2] = 6;
    }
    out_obj = PyObject_CallFunctionObjArgs(ufunc, int_arr_obj, rhs_obj, NULL);
    if (out_obj == NULL) {
        Py_DECREF(int_arr_obj);
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);
    if (out != NULL && out[0] == 14.0 && out[1] == 25.0 && out[2] == 36.0) score += 10000000000L;
    Py_DECREF(out_obj);
    out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, int_arr_obj, NULL);
    if (out_obj == NULL) {
        Py_DECREF(int_arr_obj);
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);
    if (out != NULL && out[0] == 5.5 && out[1] == 7.5 && out[2] == 9.5) score += 100000000000L;
    Py_DECREF(out_obj);
    npy_intp one_dim[1] = {1};
    PyObject *one_obj = PyArray_SimpleNew(1, one_dim, NPY_DOUBLE);
    if (one_obj == NULL) {
        Py_DECREF(int_arr_obj);
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    double *one = (double *)PyArray_DATA((PyArrayObject *)one_obj);
    if (one != NULL) one[0] = 100.0;
    out_obj = PyObject_CallFunctionObjArgs(ufunc, one_obj, rhs_obj, NULL);
    if (out_obj == NULL) {
        Py_DECREF(one_obj);
        Py_DECREF(int_arr_obj);
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);
    if (out != NULL && out[0] == 110.0 && out[1] == 120.0 && out[2] == 130.0) score += 1000000000000L;
    Py_DECREF(out_obj);
    out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, one_obj, NULL);
    if (out_obj == NULL) {
        Py_DECREF(one_obj);
        Py_DECREF(int_arr_obj);
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);
    if (out != NULL && out[0] == 101.5 && out[1] == 102.5 && out[2] == 103.5) score += 10000000000000L;
    Py_DECREF(out_obj);
    npy_intp two_dim[1] = {2};
    PyObject *two_obj = PyArray_SimpleNew(1, two_dim, NPY_DOUBLE);
    if (two_obj == NULL) {
        Py_DECREF(one_obj);
        Py_DECREF(int_arr_obj);
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        return NULL;
    }
    PyObject *bad_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, two_obj, NULL);
    if (bad_obj == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) {
        PyErr_Clear();
        score += 100000000000000L;
    } else {
        Py_XDECREF(bad_obj);
        Py_DECREF(two_obj);
        Py_DECREF(one_obj);
        Py_DECREF(int_arr_obj);
        Py_DECREF(int_scalar);
        Py_DECREF(scalar);
        Py_DECREF(lhs_obj);
        Py_DECREF(rhs_obj);
        Py_DECREF(ufunc);
        PyErr_SetString(PyExc_AssertionError, "incompatible ufunc shapes should fail");
        return NULL;
    }
    Py_DECREF(two_obj);
    Py_DECREF(one_obj);
    Py_DECREF(int_arr_obj);
    Py_DECREF(int_scalar);
    Py_DECREF(scalar);
    Py_DECREF(lhs_obj);
    Py_DECREF(rhs_obj);
    Py_DECREF(ufunc);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_inferred_dtype_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *i0 = PyLong_FromLong(14);
    PyObject *i1 = PyLong_FromLong(15);
    PyObject *i2 = PyLong_FromLong(16);
    if (i0 == NULL || i1 == NULL || i2 == NULL) {
        Py_XDECREF(i0);
        Py_XDECREF(i1);
        Py_XDECREF(i2);
        return NULL;
    }
    PyObject *int_seq = PyTuple_Pack(3, i0, i1, i2);
    Py_DECREF(i0);
    Py_DECREF(i1);
    Py_DECREF(i2);
    if (int_seq == NULL) return NULL;
    PyObject *int_arr_obj = PyArray_FromAny(int_seq, NULL, 1, 1, 0, NULL);
    Py_DECREF(int_seq);
    if (int_arr_obj == NULL) return NULL;
    PyArrayObject *int_arr = (PyArrayObject *)int_arr_obj;
    npy_intp *int_dims = PyArray_DIMS(int_arr);
    PyArray_Descr *int_descr = PyArray_DESCR(int_arr);
    long *int_data = (long *)PyArray_DATA(int_arr);
    if (PyArray_NDIM(int_arr) == 1 && int_dims != NULL && int_dims[0] == 3) score += 1;
    if (int_descr != NULL && int_descr->type_num == NPY_LONG && int_descr->elsize == (int)sizeof(long)) score += 10;
    if (int_data != NULL && int_data[0] == 14 && int_data[2] == 16) score += 100;
    PyObject *int_item = int_data == NULL ? NULL : PyArray_GETITEM(int_arr, &int_data[2]);
    if (int_item == NULL) {
        Py_DECREF(int_arr_obj);
        return NULL;
    }
    if (PyLong_AsLong(int_item) == 16 && PyErr_Occurred() == NULL) score += 1000;
    Py_DECREF(int_item);
    Py_DECREF(int_arr_obj);
    unsigned long max_ulong = (unsigned long)-1;
    PyObject *u0 = PyLong_FromUnsignedLong(max_ulong);
    PyObject *u1 = PyLong_FromUnsignedLong(7UL);
    if (u0 == NULL || u1 == NULL) {
        Py_XDECREF(u0);
        Py_XDECREF(u1);
        return NULL;
    }
    PyObject *ulong_seq = PyTuple_Pack(2, u0, u1);
    Py_DECREF(u0);
    Py_DECREF(u1);
    if (ulong_seq == NULL) return NULL;
    PyObject *ulong_arr_obj = PyArray_FromAny(ulong_seq, NULL, 1, 1, 0, NULL);
    Py_DECREF(ulong_seq);
    if (ulong_arr_obj == NULL) return NULL;
    PyArrayObject *ulong_arr = (PyArrayObject *)ulong_arr_obj;
    PyArray_Descr *ulong_descr = PyArray_DESCR(ulong_arr);
    unsigned long *ulong_data = (unsigned long *)PyArray_DATA(ulong_arr);
    if (ulong_descr != NULL && ulong_descr->type_num == NPY_ULONG && ulong_descr->elsize == (int)sizeof(unsigned long)) score += 100000000L;
    if (ulong_data != NULL && ulong_data[0] == max_ulong && ulong_data[1] == 7UL) score += 1000000000L;
    PyObject *ulong_item = ulong_data == NULL ? NULL : PyArray_GETITEM(ulong_arr, &ulong_data[0]);
    if (ulong_item == NULL) {
        Py_DECREF(ulong_arr_obj);
        return NULL;
    }
    if (PyLong_AsUnsignedLong(ulong_item) == max_ulong && PyErr_Occurred() == NULL) score += 10000000000L;
    Py_DECREF(ulong_item);
    Py_DECREF(ulong_arr_obj);
    PyObject *n0 = PyLong_FromLong(-1);
    PyObject *n1 = PyLong_FromUnsignedLong(max_ulong);
    if (n0 == NULL || n1 == NULL) {
        Py_XDECREF(n0);
        Py_XDECREF(n1);
        return NULL;
    }
    PyObject *mixed_int_seq = PyTuple_Pack(2, n0, n1);
    Py_DECREF(n0);
    Py_DECREF(n1);
    if (mixed_int_seq == NULL) return NULL;
    PyObject *mixed_int_arr_obj = PyArray_FromAny(mixed_int_seq, NULL, 1, 1, 0, NULL);
    Py_DECREF(mixed_int_seq);
    if (mixed_int_arr_obj == NULL) return NULL;
    PyArrayObject *mixed_int_arr = (PyArrayObject *)mixed_int_arr_obj;
    PyArray_Descr *mixed_int_descr = PyArray_DESCR(mixed_int_arr);
    PyObject **mixed_int_data = (PyObject **)PyArray_DATA(mixed_int_arr);
    if (mixed_int_descr != NULL && mixed_int_descr->type_num == NPY_OBJECT) score += 100000000000L;
    PyObject *mixed_neg_item = mixed_int_data == NULL ? NULL : PyArray_GETITEM(mixed_int_arr, &mixed_int_data[0]);
    if (mixed_neg_item == NULL) {
        Py_DECREF(mixed_int_arr_obj);
        return NULL;
    }
    if (PyLong_AsLong(mixed_neg_item) == -1 && PyErr_Occurred() == NULL) score += 1000000000000L;
    Py_DECREF(mixed_neg_item);
    PyObject *mixed_uint_item = mixed_int_data == NULL ? NULL : PyArray_GETITEM(mixed_int_arr, &mixed_int_data[1]);
    if (mixed_uint_item == NULL) {
        Py_DECREF(mixed_int_arr_obj);
        return NULL;
    }
    if (PyLong_AsUnsignedLong(mixed_uint_item) == max_ulong && PyErr_Occurred() == NULL) score += 10000000000000L;
    Py_DECREF(mixed_uint_item);
    Py_DECREF(mixed_int_arr_obj);
    PyObject *z0 = PyComplex_FromDoubles(1.5, 2.5);
    PyObject *z1 = PyLong_FromLong(3);
    if (z0 == NULL || z1 == NULL) {
        Py_XDECREF(z0);
        Py_XDECREF(z1);
        return NULL;
    }
    PyObject *complex_seq = PyTuple_Pack(2, z0, z1);
    Py_DECREF(z0);
    Py_DECREF(z1);
    if (complex_seq == NULL) return NULL;
    PyObject *complex_arr_obj = PyArray_FromAny(complex_seq, NULL, 1, 1, 0, NULL);
    Py_DECREF(complex_seq);
    if (complex_arr_obj == NULL) return NULL;
    PyArrayObject *complex_arr = (PyArrayObject *)complex_arr_obj;
    PyArray_Descr *complex_descr = PyArray_DESCR(complex_arr);
    Py_complex *complex_data = (Py_complex *)PyArray_DATA(complex_arr);
    if (complex_descr != NULL && complex_descr->type_num == NPY_CDOUBLE && complex_descr->elsize == (int)sizeof(Py_complex)) score += 100000000000000L;
    if (complex_data != NULL && complex_data[0].real == 1.5 && complex_data[0].imag == 2.5 && complex_data[1].real == 3.0 && complex_data[1].imag == 0.0) score += 1000000000000000L;
    PyObject *complex_item = complex_data == NULL ? NULL : PyArray_GETITEM(complex_arr, &complex_data[0]);
    if (complex_item == NULL) {
        Py_DECREF(complex_arr_obj);
        return NULL;
    }
    if (PyComplex_RealAsDouble(complex_item) == 1.5 && PyComplex_ImagAsDouble(complex_item) == 2.5 && PyErr_Occurred() == NULL) score += 10000000000000000L;
    Py_DECREF(complex_item);
    Py_DECREF(complex_arr_obj);
    PyObject *m0 = PyLong_FromLong(2);
    PyObject *m1 = PyFloat_FromDouble(2.5);
    if (m0 == NULL || m1 == NULL) {
        Py_XDECREF(m0);
        Py_XDECREF(m1);
        return NULL;
    }
    PyObject *mixed_seq = PyTuple_Pack(2, m0, m1);
    Py_DECREF(m0);
    Py_DECREF(m1);
    if (mixed_seq == NULL) return NULL;
    PyObject *mixed_arr_obj = PyArray_FromAny(mixed_seq, NULL, 1, 1, 0, NULL);
    Py_DECREF(mixed_seq);
    if (mixed_arr_obj == NULL) return NULL;
    PyArrayObject *mixed_arr = (PyArrayObject *)mixed_arr_obj;
    PyArray_Descr *mixed_descr = PyArray_DESCR(mixed_arr);
    double *mixed_data = (double *)PyArray_DATA(mixed_arr);
    if (mixed_descr != NULL && mixed_descr->type_num == NPY_DOUBLE) score += 10000;
    if (mixed_data != NULL && mixed_data[0] == 2.0 && mixed_data[1] == 2.5) score += 100000;
    Py_DECREF(mixed_arr_obj);
    PyObject *s0 = PyUnicode_FromString("auto");
    PyObject *s1 = PyBytes_FromString("bytes");
    if (s0 == NULL || s1 == NULL) {
        Py_XDECREF(s0);
        Py_XDECREF(s1);
        return NULL;
    }
    PyObject *str_seq = PyTuple_Pack(2, s0, s1);
    Py_DECREF(s0);
    Py_DECREF(s1);
    if (str_seq == NULL) return NULL;
    PyObject *str_arr_obj = PyArray_FromAny(str_seq, NULL, 1, 1, 0, NULL);
    Py_DECREF(str_seq);
    if (str_arr_obj == NULL) return NULL;
    PyArrayObject *str_arr = (PyArrayObject *)str_arr_obj;
    PyArray_Descr *str_descr = PyArray_DESCR(str_arr);
    char *str_data = (char *)PyArray_DATA(str_arr);
    if (str_descr != NULL && str_descr->type_num == NPY_STRING && str_descr->elsize == 5 && PyArray_ITEMSIZE(str_arr) == 5) score += 1000000;
    PyObject *str_item = str_data == NULL ? NULL : PyArray_GETITEM(str_arr, &str_data[0]);
    if (str_item == NULL) {
        Py_DECREF(str_arr_obj);
        return NULL;
    }
    char *str_raw = NULL;
    Py_ssize_t str_len = 0;
    if (PyBytes_AsStringAndSize(str_item, &str_raw, &str_len) == 0 && str_len == 4 && str_raw[0] == 'a' && str_raw[1] == 'u' && str_raw[2] == 't' && str_raw[3] == 'o' && str_data[5] == 'b' && str_data[9] == 's') score += 10000000;
    Py_DECREF(str_item);
    Py_DECREF(str_arr_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyArray_Descr *descr = PyArray_DescrFromType(NPY_INT);
    if (descr != NULL && descr->type_num == NPY_INT) score += 1;
    if (descr != NULL && descr->elsize == (int)sizeof(int)) score += 10;
    if (descr != NULL && descr->kind == 'i') score += 100;
    if (descr != NULL && descr->type == 'i') score += 1000;
    npy_intp dims[1] = {1};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    if (arr == NULL) return NULL;
    PyArray_Descr *arr_descr = PyArray_DESCR((PyArrayObject *)arr);
    if (arr_descr != NULL && arr_descr->type_num == NPY_DOUBLE) score += 10000;
    if (arr_descr != NULL && arr_descr->elsize == (int)sizeof(double)) score += 100000;
    if (arr_descr != NULL && arr_descr->kind == 'f' && arr_descr->type == 'd') score += 1000000;
    Py_DECREF(arr);
    PyArray_Descr *obj_descr = PyArray_DescrFromType(NPY_OBJECT);
    if (obj_descr != NULL && obj_descr->type_num == NPY_OBJECT && obj_descr->kind == 'O' && obj_descr->type == 'O' && obj_descr->elsize == (int)sizeof(PyObject *)) score += 10000000;
    PyArray_Descr *cfloat_descr = PyArray_DescrFromType(NPY_CFLOAT);
    if (cfloat_descr != NULL && cfloat_descr->type_num == NPY_CFLOAT && cfloat_descr->kind == 'c' && cfloat_descr->type == 'F' && cfloat_descr->elsize == (int)sizeof(npy_cfloat)) score += 100000000L;
    PyArray_Descr *clongdouble_descr = PyArray_DescrFromType(NPY_CLONGDOUBLE);
    if (clongdouble_descr != NULL && clongdouble_descr->type_num == NPY_CLONGDOUBLE && clongdouble_descr->kind == 'c' && clongdouble_descr->type == 'G' && clongdouble_descr->elsize == (int)sizeof(npy_clongdouble)) score += 1000000000L;
    npy_intp complex_dims[1] = {1};
    PyObject *cfloat_arr_obj = PyArray_SimpleNew(1, complex_dims, NPY_CFLOAT);
    PyObject *clongdouble_arr_obj = PyArray_SimpleNew(1, complex_dims, NPY_CLONGDOUBLE);
    if (cfloat_arr_obj == NULL || clongdouble_arr_obj == NULL) {
        Py_XDECREF(cfloat_arr_obj);
        Py_XDECREF(clongdouble_arr_obj);
        return NULL;
    }
    npy_cfloat *cfloat_data = (npy_cfloat *)PyArray_DATA((PyArrayObject *)cfloat_arr_obj);
    npy_clongdouble *clongdouble_data = (npy_clongdouble *)PyArray_DATA((PyArrayObject *)clongdouble_arr_obj);
    PyObject *cfloat_value = PyComplex_FromDoubles(1.25, -2.5);
    PyObject *clongdouble_value = PyComplex_FromDoubles(4.5, -5.5);
    if (cfloat_data == NULL || clongdouble_data == NULL || cfloat_value == NULL || clongdouble_value == NULL) {
        Py_XDECREF(cfloat_value);
        Py_XDECREF(clongdouble_value);
        Py_DECREF(cfloat_arr_obj);
        Py_DECREF(clongdouble_arr_obj);
        return NULL;
    }
    if (PyArray_SETITEM((PyArrayObject *)cfloat_arr_obj, cfloat_data, cfloat_value) == 0 && cfloat_data[0].real == 1.25f && cfloat_data[0].imag == -2.5f) score += 10000000000L;
    if (PyArray_SETITEM((PyArrayObject *)clongdouble_arr_obj, clongdouble_data, clongdouble_value) == 0 && clongdouble_data[0].real == (long double)4.5 && clongdouble_data[0].imag == (long double)-5.5) score += 100000000000L;
    Py_DECREF(cfloat_value);
    Py_DECREF(clongdouble_value);
    PyObject *cfloat_item = PyArray_GETITEM((PyArrayObject *)cfloat_arr_obj, cfloat_data);
    PyObject *clongdouble_item = PyArray_GETITEM((PyArrayObject *)clongdouble_arr_obj, clongdouble_data);
    if (cfloat_item == NULL || clongdouble_item == NULL) {
        Py_XDECREF(cfloat_item);
        Py_XDECREF(clongdouble_item);
        Py_DECREF(cfloat_arr_obj);
        Py_DECREF(clongdouble_arr_obj);
        return NULL;
    }
    if (PyComplex_RealAsDouble(cfloat_item) == 1.25 && PyComplex_ImagAsDouble(cfloat_item) == -2.5 && PyErr_Occurred() == NULL) score += 1000000000000L;
    if (PyComplex_RealAsDouble(clongdouble_item) == 4.5 && PyComplex_ImagAsDouble(clongdouble_item) == -5.5 && PyErr_Occurred() == NULL) score += 10000000000000L;
    Py_DECREF(cfloat_item);
    Py_DECREF(clongdouble_item);
    Py_DECREF(cfloat_arr_obj);
    Py_DECREF(clongdouble_arr_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_fromany_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 2};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);
    if (arr == NULL) return NULL;
    PyArray_Descr *int_descr = PyArray_DescrFromType(NPY_INT);
    PyArray_Descr *double_descr = PyArray_DescrFromType(NPY_DOUBLE);
    PyArray_Descr *object_descr = PyArray_DescrFromType(NPY_OBJECT);
    PyObject *same = PyArray_FromAny(arr, int_descr, 1, 2, 0, NULL);
    if (same == arr) score += 1;
    Py_XDECREF(same);
    same = PyArray_FromAny(arr, NULL, 0, 0, 0, NULL);
    if (same == arr) score += 10;
    Py_XDECREF(same);
    int *arr_data = (int *)PyArray_DATA((PyArrayObject *)arr);
    if (arr_data == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    arr_data[3] = 33;
    PyObject *casted = PyArray_FromAny(arr, double_descr, 0, 0, 0, NULL);
    if (casted == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyArrayObject *casted_arr = (PyArrayObject *)casted;
    npy_intp *casted_dims = PyArray_DIMS(casted_arr);
    double *casted_data = (double *)PyArray_DATA(casted_arr);
    if (casted != arr && PyArray_NDIM(casted_arr) == 2 && casted_dims != NULL && casted_dims[0] == 2 && casted_dims[1] == 2 && casted_data != NULL && casted_data[3] == 33.0) score += 100;
    Py_DECREF(casted);
    if (PyArray_FromAny(arr, int_descr, 3, 0, 0, NULL) == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 1000;
    PyErr_Clear();
    if (PyArray_FromAny(Py_None, int_descr, 0, 0, 0, NULL) == NULL && PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 10000;
    PyErr_Clear();
    PyObject *v0 = PyLong_FromLong(4);
    PyObject *v1 = PyLong_FromLong(5);
    PyObject *v2 = PyLong_FromLong(6);
    if (v0 == NULL || v1 == NULL || v2 == NULL) {
        Py_XDECREF(v0);
        Py_XDECREF(v1);
        Py_XDECREF(v2);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *seq = PyTuple_Pack(3, v0, v1, v2);
    Py_DECREF(v0);
    Py_DECREF(v1);
    Py_DECREF(v2);
    if (seq == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *coerced = PyArray_FromAny(seq, int_descr, 1, 1, 0, NULL);
    Py_DECREF(seq);
    if (coerced == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyArrayObject *coerced_arr = (PyArrayObject *)coerced;
    npy_intp *coerced_dims = PyArray_DIMS(coerced_arr);
    int *coerced_data = (int *)PyArray_DATA(coerced_arr);
    if (PyArray_NDIM(coerced_arr) == 1 && coerced_dims != NULL && coerced_dims[0] == 3) score += 100000;
    if (coerced_data == NULL) {
        Py_DECREF(coerced);
        Py_DECREF(arr);
        return NULL;
    }
    if (coerced_data != NULL && coerced_data[0] == 4 && coerced_data[1] == 5 && coerced_data[2] == 6) score += 1000000;
    PyObject *coerced_item = PyArray_GETITEM(coerced_arr, &coerced_data[1]);
    if (coerced_item == NULL) {
        Py_DECREF(coerced);
        Py_DECREF(arr);
        return NULL;
    }
    if (PyLong_AsLong(coerced_item) == 5 && PyErr_Occurred() == NULL) score += 10000000;
    Py_DECREF(coerced_item);
    Py_DECREF(coerced);
    PyObject *m00 = PyLong_FromLong(11);
    PyObject *m01 = PyLong_FromLong(12);
    PyObject *m10 = PyLong_FromLong(21);
    PyObject *m11 = PyLong_FromLong(22);
    if (m00 == NULL || m01 == NULL || m10 == NULL || m11 == NULL) {
        Py_XDECREF(m00);
        Py_XDECREF(m01);
        Py_XDECREF(m10);
        Py_XDECREF(m11);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *row0 = PyTuple_Pack(2, m00, m01);
    PyObject *row1 = PyTuple_Pack(2, m10, m11);
    Py_DECREF(m00);
    Py_DECREF(m01);
    Py_DECREF(m10);
    Py_DECREF(m11);
    if (row0 == NULL || row1 == NULL) {
        Py_XDECREF(row0);
        Py_XDECREF(row1);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *matrix_seq = PyTuple_Pack(2, row0, row1);
    Py_DECREF(row0);
    Py_DECREF(row1);
    if (matrix_seq == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *matrix = PyArray_FromAny(matrix_seq, int_descr, 2, 2, 0, NULL);
    Py_DECREF(matrix_seq);
    if (matrix == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyArrayObject *matrix_arr = (PyArrayObject *)matrix;
    npy_intp *matrix_dims = PyArray_DIMS(matrix_arr);
    int *matrix_data = (int *)PyArray_DATA(matrix_arr);
    if (matrix_data == NULL) {
        Py_DECREF(matrix);
        Py_DECREF(arr);
        return NULL;
    }
    if (PyArray_NDIM(matrix_arr) == 2 && matrix_dims != NULL && matrix_dims[0] == 2 && matrix_dims[1] == 2 && matrix_data[0] == 11 && matrix_data[3] == 22) score += 100000000;
    PyObject *matrix_item = PyArray_GETITEM(matrix_arr, &matrix_data[3]);
    if (matrix_item == NULL) {
        Py_DECREF(matrix);
        Py_DECREF(arr);
        return NULL;
    }
    if (PyLong_AsLong(matrix_item) == 22 && PyErr_Occurred() == NULL) score += 1000000000;
    Py_DECREF(matrix_item);
    Py_DECREF(matrix);
    PyObject *c0 = PyLong_FromLong(31);
    PyObject *c1 = PyLong_FromLong(32);
    PyObject *c2 = PyLong_FromLong(41);
    PyObject *c3 = PyLong_FromLong(42);
    if (c0 == NULL || c1 == NULL || c2 == NULL || c3 == NULL) {
        Py_XDECREF(c0);
        Py_XDECREF(c1);
        Py_XDECREF(c2);
        Py_XDECREF(c3);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *cube_row0 = PyTuple_Pack(2, c0, c1);
    PyObject *cube_row1 = PyTuple_Pack(2, c2, c3);
    Py_DECREF(c0);
    Py_DECREF(c1);
    Py_DECREF(c2);
    Py_DECREF(c3);
    if (cube_row0 == NULL || cube_row1 == NULL) {
        Py_XDECREF(cube_row0);
        Py_XDECREF(cube_row1);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *cube_plane0 = PyTuple_Pack(1, cube_row0);
    PyObject *cube_plane1 = PyTuple_Pack(1, cube_row1);
    Py_DECREF(cube_row0);
    Py_DECREF(cube_row1);
    if (cube_plane0 == NULL || cube_plane1 == NULL) {
        Py_XDECREF(cube_plane0);
        Py_XDECREF(cube_plane1);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *cube_seq = PyTuple_Pack(2, cube_plane0, cube_plane1);
    Py_DECREF(cube_plane0);
    Py_DECREF(cube_plane1);
    if (cube_seq == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *cube = PyArray_FromAny(cube_seq, int_descr, 3, 3, 0, NULL);
    Py_DECREF(cube_seq);
    if (cube == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyArrayObject *cube_arr = (PyArrayObject *)cube;
    npy_intp *cube_dims = PyArray_DIMS(cube_arr);
    int *cube_data = (int *)PyArray_DATA(cube_arr);
    if (cube_data == NULL) {
        Py_DECREF(cube);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *cube_item = PyArray_GETITEM(cube_arr, &cube_data[3]);
    if (cube_item == NULL) {
        Py_DECREF(cube);
        Py_DECREF(arr);
        return NULL;
    }
    if (PyArray_NDIM(cube_arr) == 3 && cube_dims != NULL && cube_dims[0] == 2 && cube_dims[1] == 1 && cube_dims[2] == 2 && cube_data[0] == 31 && cube_data[3] == 42 && PyLong_AsLong(cube_item) == 42 && PyErr_Occurred() == NULL) score += 10000000000L;
    Py_DECREF(cube_item);
    Py_DECREF(cube);
    PyObject *obj0 = PyLong_FromLong(123);
    PyObject *obj1 = PyLong_FromLong(456);
    if (obj0 == NULL || obj1 == NULL) {
        Py_XDECREF(obj0);
        Py_XDECREF(obj1);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *obj_seq = PyTuple_Pack(2, obj0, obj1);
    Py_DECREF(obj0);
    Py_DECREF(obj1);
    if (obj_seq == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *obj_arr_obj = PyArray_FromAny(obj_seq, object_descr, 1, 1, 0, NULL);
    Py_DECREF(obj_seq);
    if (obj_arr_obj == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyArrayObject *obj_arr = (PyArrayObject *)obj_arr_obj;
    npy_intp *obj_dims = PyArray_DIMS(obj_arr);
    PyObject **obj_data = (PyObject **)PyArray_DATA(obj_arr);
    if (PyArray_NDIM(obj_arr) == 1 && obj_dims != NULL && obj_dims[0] == 2 && obj_data != NULL && obj_data[0] != NULL && obj_data[1] != NULL) score += 100000000000L;
    PyObject *obj_item = obj_data == NULL ? NULL : PyArray_GETITEM(obj_arr, &obj_data[1]);
    if (obj_item == NULL) {
        Py_DECREF(obj_arr_obj);
        Py_DECREF(arr);
        return NULL;
    }
    if (PyLong_AsLong(obj_item) == 456 && PyErr_Occurred() == NULL) score += 1000000000000L;
    Py_DECREF(obj_item);
    Py_DECREF(obj_arr_obj);
    PyObject *str0 = PyUnicode_FromString("left");
    PyObject *str1 = PyUnicode_FromString("right");
    if (str0 == NULL || str1 == NULL) {
        Py_XDECREF(str0);
        Py_XDECREF(str1);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *str_seq = PyTuple_Pack(2, str0, str1);
    Py_DECREF(str0);
    Py_DECREF(str1);
    if (str_seq == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *str_arr_obj = PyArray_FromAny(str_seq, object_descr, 1, 1, 0, NULL);
    Py_DECREF(str_seq);
    if (str_arr_obj == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyArrayObject *str_arr = (PyArrayObject *)str_arr_obj;
    npy_intp *str_dims = PyArray_DIMS(str_arr);
    PyObject **str_data = (PyObject **)PyArray_DATA(str_arr);
    if (PyArray_NDIM(str_arr) == 1 && str_dims != NULL && str_dims[0] == 2 && str_data != NULL && str_data[0] != NULL && str_data[1] != NULL) score += 10000000000000L;
    PyObject *str_item = str_data == NULL ? NULL : PyArray_GETITEM(str_arr, &str_data[1]);
    if (str_item == NULL) {
        Py_DECREF(str_arr_obj);
        Py_DECREF(arr);
        return NULL;
    }
    const char *str_raw = PyUnicode_AsUTF8(str_item);
    if (str_raw != NULL && str_raw[0] == 'r' && str_raw[1] == 'i' && str_raw[2] == 'g' && str_raw[3] == 'h' && str_raw[4] == 't' && str_raw[5] == '\0') score += 100000000000000L;
    Py_DECREF(str_item);
    Py_DECREF(str_arr_obj);
    PyObject *rg0 = PyLong_FromLong(7);
    PyObject *rg1 = PyLong_FromLong(8);
    PyObject *rg2 = PyLong_FromLong(9);
    if (rg0 == NULL || rg1 == NULL || rg2 == NULL) {
        Py_XDECREF(rg0);
        Py_XDECREF(rg1);
        Py_XDECREF(rg2);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *rag_row0 = PyTuple_Pack(2, rg0, rg1);
    PyObject *rag_row1 = PyTuple_Pack(1, rg2);
    Py_DECREF(rg0);
    Py_DECREF(rg1);
    Py_DECREF(rg2);
    if (rag_row0 == NULL || rag_row1 == NULL) {
        Py_XDECREF(rag_row0);
        Py_XDECREF(rag_row1);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *rag_seq = PyTuple_Pack(2, rag_row0, rag_row1);
    Py_DECREF(rag_row0);
    Py_DECREF(rag_row1);
    if (rag_seq == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *rag_arr_obj = PyArray_FromAny(rag_seq, object_descr, 1, 1, 0, NULL);
    Py_DECREF(rag_seq);
    if (rag_arr_obj == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyArrayObject *rag_arr = (PyArrayObject *)rag_arr_obj;
    npy_intp *rag_dims = PyArray_DIMS(rag_arr);
    PyObject **rag_data = (PyObject **)PyArray_DATA(rag_arr);
    if (PyArray_NDIM(rag_arr) == 1 && rag_dims != NULL && rag_dims[0] == 2 && rag_data != NULL && rag_data[0] != NULL && rag_data[1] != NULL) score += 1000000000000000L;
    PyObject *rag_item = rag_data == NULL ? NULL : PyArray_GETITEM(rag_arr, &rag_data[1]);
    if (rag_item == NULL) {
        Py_DECREF(rag_arr_obj);
        Py_DECREF(arr);
        return NULL;
    }
    if (PySequence_Size(rag_item) == 1 && PyErr_Occurred() == NULL) score += 10000000000000000L;
    Py_DECREF(rag_item);
    Py_DECREF(rag_arr_obj);
    static char mv_raw[3] = {1, 2, 3};
    PyObject *mv = PyMemoryView_FromMemory(mv_raw, 3, PyBUF_READ);
    if (mv == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *mv_seq = PyTuple_Pack(1, mv);
    Py_DECREF(mv);
    if (mv_seq == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *mv_arr_obj = PyArray_FromAny(mv_seq, object_descr, 1, 1, 0, NULL);
    Py_DECREF(mv_seq);
    if (mv_arr_obj == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyArrayObject *mv_arr = (PyArrayObject *)mv_arr_obj;
    npy_intp *mv_dims = PyArray_DIMS(mv_arr);
    PyObject **mv_data = (PyObject **)PyArray_DATA(mv_arr);
    if (PyArray_NDIM(mv_arr) == 1 && mv_dims != NULL && mv_dims[0] == 1 && mv_data != NULL && mv_data[0] != NULL) score += 100000000000000000L;
    PyObject *mv_item = mv_data == NULL ? NULL : PyArray_GETITEM(mv_arr, &mv_data[0]);
    if (mv_item == NULL) {
        Py_DECREF(mv_arr_obj);
        Py_DECREF(arr);
        return NULL;
    }
    if (PyMemoryView_Check(mv_item) && PySequence_Size(mv_item) == 3 && PyErr_Occurred() == NULL) score += 1000000000000000000L;
    Py_DECREF(mv_item);
    Py_DECREF(mv_arr_obj);
    Py_DECREF(arr);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_array_metadata_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[2] = {2, 3};
    PyObject *owned = PyArray_SimpleNew(2, dims, NPY_INT);
    if (owned == NULL) return NULL;
    PyObject *value = NULL;
    PyArrayObject *arr = (PyArrayObject *)owned;
    long score = 0;
    npy_intp *owned_dims = PyArray_DIMS(arr);
    npy_intp *owned_strides = PyArray_STRIDES(arr);
    int *owned_data = (int *)PyArray_DATA(arr);
    if (PyArray_NDIM(arr) == 2) score += 1;
    if (owned_dims != NULL && owned_dims[0] == 2 && owned_dims[1] == 3) score += 10;
    if (PyArray_DIM(arr, 1) == 3) score += 10000000000L;
    if (owned_strides != NULL && owned_strides[0] == 3 * (npy_intp)sizeof(int) && owned_strides[1] == (npy_intp)sizeof(int)) score += 100;
    if (PyArray_SIZE(arr) == 6) score += 100000000000L;
    if (PyArray_ITEMSIZE(arr) == (int)sizeof(int)) score += 1000000000000L;
    if (PyArray_TYPE(arr) == NPY_INT) score += 1000000000000000L;
    if (PyArray_NBYTES(arr) == 6 * (npy_intp)sizeof(int)) score += 10000000000000000L;
    if ((PyArray_FLAGS(arr) & NPY_ARRAY_OWNDATA) && PyArray_CHKFLAGS(arr, NPY_ARRAY_CARRAY)) score += 100000000000000000L;
    if (PyArray_ISCONTIGUOUS(arr) && PyArray_IS_C_CONTIGUOUS(arr) && PyArray_ISCARRAY(arr) && PyArray_ISALIGNED(arr) && PyArray_ISWRITEABLE(arr)) score += 1000000000000000000L;
    if (PyArray_Check(owned) && PyArray_CheckExact(owned) && !PyArray_Check(Py_None)) score += 10000000000000L;
    if (owned_data != NULL) {
        owned_data[5] = 41;
        if (((int *)PyArray_DATA(arr))[5] == 41) score += 1000;
        if (PyArray_BYTES(arr) == owned_data) score += 100000000000000L;
        value = PyArray_GETITEM(arr, &owned_data[5]);
        if (value == NULL) {
            Py_DECREF(owned);
            return NULL;
        }
        if (PyLong_AsLong(value) == 41) score += 1000000;
        Py_DECREF(value);
        value = PyLong_FromLong(77);
        if (value == NULL) {
            Py_DECREF(owned);
            return NULL;
        }
        if (PyArray_SETITEM(arr, &owned_data[0], value) == 0 && owned_data[0] == 77) score += 10000000;
        Py_DECREF(value);
        if (PyErr_Occurred() != NULL) {
            Py_DECREF(owned);
            return NULL;
        }
    }
    int raw[6] = {1, 2, 3, 4, 5, 6};
    PyObject *borrowed = PyArray_SimpleNewFromData(2, dims, NPY_INT, raw);
    if (borrowed == NULL) {
        Py_DECREF(owned);
        return NULL;
    }
    PyArrayObject *borrowed_arr = (PyArrayObject *)borrowed;
    if (PyArray_DATA(borrowed_arr) == raw) score += 10000;
    if (((int *)PyArray_DATA(borrowed_arr))[4] == 5) score += 100000;
    value = PyLong_FromLong(99);
    if (value == NULL) {
        Py_DECREF(borrowed);
        Py_DECREF(owned);
        return NULL;
    }
    if (PyArray_SETITEM(borrowed_arr, &raw[2], value) == 0 && raw[2] == 99) score += 100000000;
    Py_DECREF(value);
    if (PyErr_Occurred() != NULL) {
        Py_DECREF(borrowed);
        Py_DECREF(owned);
        return NULL;
    }
    value = PyArray_GETITEM(borrowed_arr, &raw[2]);
    if (value == NULL) {
        Py_DECREF(borrowed);
        Py_DECREF(owned);
        return NULL;
    }
    if (PyLong_AsLong(value) == 99) score += 1000000000;
    Py_DECREF(value);
    Py_DECREF(borrowed);
    Py_DECREF(owned);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_pointer_metadata_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[2] = {2, 3};
    PyObject *owned = PyArray_SimpleNew(2, dims, NPY_INT);
    if (owned == NULL) return NULL;
    PyArrayObject *arr = (PyArrayObject *)owned;
    int *owned_data = (int *)PyArray_DATA(arr);
    long score = 0;
    if (PyArray_STRIDE(arr, 0) == 3 * (npy_intp)sizeof(int)) score += 1;
    if (PyArray_STRIDE(arr, 1) == (npy_intp)sizeof(int)) score += 10;
    if (owned_data != NULL && PyArray_GETPTR1(arr, 5) == &owned_data[5]) score += 100;
    if (owned_data != NULL && PyArray_GETPTR2(arr, 1, 2) == &owned_data[5]) score += 1000;
    if (owned_data != NULL && PyArray_GETPTR2(arr, 0, 0) == &owned_data[0]) score += 10000;
    Py_DECREF(owned);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_dtype_field_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {4};
    PyObject *owned = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    if (owned == NULL) return NULL;
    PyArrayObject *arr = (PyArrayObject *)owned;
    PyArray_Descr *descr = PyArray_DTYPE(arr);
    long score = 0;
    if (descr != NULL && descr == PyArray_DESCR(arr)) score += 1;
    if (descr != NULL && PyDataType_TYPE(descr) == NPY_DOUBLE) score += 10;
    if (descr != NULL && PyDataType_KIND(descr) == 'f') score += 100;
    if (descr != NULL && PyDataType_ELSIZE(descr) == (int)sizeof(double)) score += 1000;
    if (descr != NULL && PyDataType_ALIGNMENT(descr) == (int)sizeof(double)) score += 10000;
    Py_DECREF(owned);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_dtype_classification_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {1};
    PyObject *double_obj = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    PyObject *int_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *obj_obj = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    PyArray_Descr *cdouble_descr = PyArray_DescrFromType(NPY_CDOUBLE);
    PyArray_Descr *string_descr = PyArray_DescrFromType(NPY_STRING);
    PyArray_Descr *bool_descr = PyArray_DescrFromType(NPY_BOOL);
    if (double_obj == NULL || int_obj == NULL || obj_obj == NULL || cdouble_descr == NULL || string_descr == NULL || bool_descr == NULL) {
        Py_XDECREF(double_obj);
        Py_XDECREF(int_obj);
        Py_XDECREF(obj_obj);
        return NULL;
    }
    PyArrayObject *double_arr = (PyArrayObject *)double_obj;
    PyArrayObject *int_arr = (PyArrayObject *)int_obj;
    PyArrayObject *obj_arr = (PyArrayObject *)obj_obj;
    PyArray_Descr *double_descr = PyArray_DTYPE(double_arr);
    PyArray_Descr *int_descr = PyArray_DTYPE(int_arr);
    PyArray_Descr *obj_descr = PyArray_DTYPE(obj_arr);
    long score = 0;
    if (PyTypeNum_ISFLOAT(NPY_DOUBLE) && PyDataType_ISFLOAT(double_descr) && PyArray_ISFLOAT(double_arr)) score += 1;
    if (PyTypeNum_ISNUMBER(NPY_DOUBLE) && PyDataType_ISNUMBER(double_descr) && PyArray_ISNUMBER(double_arr)) score += 10;
    if (PyTypeNum_ISINTEGER(NPY_INT) && PyDataType_ISINTEGER(int_descr) && PyArray_ISINTEGER(int_arr)) score += 100;
    if (PyTypeNum_ISSIGNED(NPY_INT) && PyDataType_ISSIGNED(int_descr) && PyArray_ISSIGNED(int_arr)) score += 1000;
    if (PyTypeNum_ISUNSIGNED(NPY_UINT) && !PyDataType_ISUNSIGNED(int_descr) && !PyArray_ISUNSIGNED(int_arr)) score += 10000;
    if (PyTypeNum_ISCOMPLEX(NPY_CDOUBLE) && PyDataType_ISCOMPLEX(cdouble_descr)) score += 100000;
    if (PyTypeNum_ISOBJECT(NPY_OBJECT) && PyDataType_ISOBJECT(obj_descr) && PyArray_ISOBJECT(obj_arr)) score += 1000000;
    if (PyTypeNum_ISSTRING(NPY_STRING) && PyDataType_ISSTRING(string_descr) && PyTypeNum_ISFLEXIBLE(NPY_STRING)) score += 10000000;
    if (PyTypeNum_ISBOOL(NPY_BOOL) && PyDataType_ISBOOL(bool_descr)) score += 100000000;
    Py_DECREF(double_obj);
    Py_DECREF(int_obj);
    Py_DECREF(obj_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_scalar_kind_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {1};
    PyObject *pos_obj = PyArray_SimpleNew(1, dims, NPY_LONG);
    PyObject *neg_obj = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (pos_obj == NULL || neg_obj == NULL) {
        Py_XDECREF(pos_obj);
        Py_XDECREF(neg_obj);
        return NULL;
    }
    long *pos_data = (long *)PyArray_DATA((PyArrayObject *)pos_obj);
    long *neg_data = (long *)PyArray_DATA((PyArrayObject *)neg_obj);
    pos_data[0] = 7;
    neg_data[0] = -3;
    PyArrayObject *pos_arr = (PyArrayObject *)pos_obj;
    PyArrayObject *neg_arr = (PyArrayObject *)neg_obj;
    long score = 0;
    if (PyArray_ScalarKind(NPY_BOOL, NULL) == NPY_BOOL_SCALAR) score += 1;
    if (PyArray_ScalarKind(NPY_LONG, NULL) == NPY_INTPOS_SCALAR) score += 10;
    if (PyArray_ScalarKind(NPY_LONG, &pos_arr) == NPY_INTPOS_SCALAR) score += 100;
    if (PyArray_ScalarKind(NPY_LONG, &neg_arr) == NPY_INTNEG_SCALAR) score += 1000;
    if (PyArray_ScalarKind(NPY_DOUBLE, NULL) == NPY_FLOAT_SCALAR) score += 10000;
    if (PyArray_ScalarKind(NPY_CDOUBLE, NULL) == NPY_COMPLEX_SCALAR) score += 100000;
    if (PyArray_ScalarKind(NPY_OBJECT, NULL) == NPY_OBJECT_SCALAR) score += 1000000;
    if (PyArray_ScalarKind(9999, NULL) == NPY_NOSCALAR) score += 10000000;
    Py_DECREF(neg_obj);
    Py_DECREF(pos_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_check_any_scalar_exact_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *py_int = PyLong_FromLong(7);
    PyObject *py_float = PyFloat_FromDouble(2.5);
    npy_intp dims[1] = {1};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_LONG);
    PyObject *zero_dim = PyArray_SimpleNew(0, NULL, NPY_LONG);
    if (py_int == NULL || py_float == NULL || arr == NULL || zero_dim == NULL) {
        Py_XDECREF(zero_dim);
        Py_XDECREF(arr);
        Py_XDECREF(py_float);
        Py_XDECREF(py_int);
        return NULL;
    }
    if (PyArray_CheckAnyScalarExact(py_int) == 0 && PyErr_Occurred() == NULL) score += 1;
    if (PyArray_CheckAnyScalarExact(py_float) == 0 && PyErr_Occurred() == NULL) score += 10;
    if (PyArray_CheckAnyScalarExact(arr) == 0 && PyErr_Occurred() == NULL) score += 100;
    if (PyArray_CheckAnyScalarExact(zero_dim) == 0 && PyErr_Occurred() == NULL) score += 1000;
    if (PyArray_CheckAnyScalarExact(NULL) == 0 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_DECREF(zero_dim);
    Py_DECREF(arr);
    Py_DECREF(py_float);
    Py_DECREF(py_int);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_can_coerce_scalar_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    if (PyArray_CanCoerceScalar(NPY_INT, NPY_LONG, NPY_INTPOS_SCALAR)) score += 1;
    if (PyArray_CanCoerceScalar(NPY_INT, NPY_LONG, NPY_INTNEG_SCALAR)) score += 10;
    if (!PyArray_CanCoerceScalar(NPY_INT, NPY_UINT, NPY_INTNEG_SCALAR)) score += 100;
    if (PyArray_CanCoerceScalar(NPY_INT, NPY_DOUBLE, NPY_NOSCALAR)) score += 1000;
    if (!PyArray_CanCoerceScalar(NPY_DOUBLE, NPY_INT, NPY_NOSCALAR)) score += 10000;
    if (!PyArray_CanCoerceScalar(NPY_OBJECT, NPY_DOUBLE, NPY_OBJECT_SCALAR)) score += 100000;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_can_cast_scalar_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyTypeObject *bool_type = (PyTypeObject *)PyArray_TypeObjectFromType(NPY_BOOL);
    PyTypeObject *long_type = (PyTypeObject *)PyArray_TypeObjectFromType(NPY_LONG);
    PyTypeObject *double_type = (PyTypeObject *)PyArray_TypeObjectFromType(NPY_DOUBLE);
    PyTypeObject *complex_type = (PyTypeObject *)PyArray_TypeObjectFromType(NPY_CDOUBLE);
    PyTypeObject *string_type = (PyTypeObject *)PyArray_TypeObjectFromType(NPY_STRING);
    PyTypeObject *object_type = (PyTypeObject *)PyArray_TypeObjectFromType(NPY_OBJECT);
    if (bool_type == NULL || long_type == NULL || double_type == NULL ||
        complex_type == NULL || string_type == NULL || object_type == NULL) {
        Py_XDECREF((PyObject *)object_type);
        Py_XDECREF((PyObject *)string_type);
        Py_XDECREF((PyObject *)complex_type);
        Py_XDECREF((PyObject *)double_type);
        Py_XDECREF((PyObject *)long_type);
        Py_XDECREF((PyObject *)bool_type);
        return NULL;
    }
    long score = 0;
    if (PyArray_CanCastScalar(bool_type, long_type)) score += 1;
    if (!PyArray_CanCastScalar(long_type, double_type)) score += 10;
    if (PyArray_CanCastScalar(double_type, complex_type)) score += 100;
    if (!PyArray_CanCastScalar(complex_type, double_type)) score += 1000;
    if (PyArray_CanCastScalar(string_type, object_type)) score += 10000;
    if (!PyArray_CanCastScalar(&PyList_Type, double_type) && PyErr_Occurred() == NULL) score += 100000;
    Py_DECREF((PyObject *)object_type);
    Py_DECREF((PyObject *)string_type);
    Py_DECREF((PyObject *)complex_type);
    Py_DECREF((PyObject *)double_type);
    Py_DECREF((PyObject *)long_type);
    Py_DECREF((PyObject *)bool_type);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_convert_common_type_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *int_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *double_obj = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    PyObject *seq = NULL;
    PyObject *empty = NULL;
    PyArrayObject **converted = NULL;
    PyArrayObject **empty_converted = NULL;
    if (int_obj == NULL || double_obj == NULL) goto fail;
    int *int_data = (int *)PyArray_DATA((PyArrayObject *)int_obj);
    double *double_data = (double *)PyArray_DATA((PyArrayObject *)double_obj);
    int_data[0] = 1;
    int_data[1] = 2;
    double_data[0] = 1.5;
    double_data[1] = 2.5;
    seq = PyTuple_Pack(2, int_obj, double_obj);
    if (seq == NULL) goto fail;
    int n = -1;
    converted = PyArray_ConvertToCommonType(seq, &n);
    if (converted == NULL) goto fail;
    long score = 0;
    if (n == 2 && converted[0] != NULL && converted[1] != NULL) score += 1;
    if (PyArray_TYPE(converted[0]) == NPY_DOUBLE && PyArray_TYPE(converted[1]) == NPY_DOUBLE) score += 10;
    if (PyArray_ISCARRAY(converted[0]) && PyArray_ISCARRAY(converted[1])) score += 100;
    double *conv0 = (double *)PyArray_DATA(converted[0]);
    double *conv1 = (double *)PyArray_DATA(converted[1]);
    if (conv0[0] == 1.0 && conv0[1] == 2.0) score += 1000;
    if (conv1[0] == 1.5 && conv1[1] == 2.5) score += 10000;
    empty = PyTuple_New(0);
    if (empty == NULL) goto fail;
    int empty_n = 123;
    empty_converted = PyArray_ConvertToCommonType(empty, &empty_n);
    if (empty_converted == NULL && empty_n == 0 && PyErr_ExceptionMatches(PyExc_ValueError)) {
        score += 100000;
        PyErr_Clear();
    } else {
        if (empty_converted != NULL) PyDataMem_FREE(empty_converted);
        PyErr_SetString(PyExc_AssertionError, "empty ConvertToCommonType should fail");
        goto fail;
    }
    Py_DECREF(converted[0]);
    Py_DECREF(converted[1]);
    PyDataMem_FREE(converted);
    Py_DECREF(empty);
    Py_DECREF(seq);
    Py_DECREF(double_obj);
    Py_DECREF(int_obj);
    return PyLong_FromLong(score);
fail:
    if (empty_converted != NULL) PyDataMem_FREE(empty_converted);
    if (converted != NULL) {
        if (converted[0] != NULL) Py_DECREF(converted[0]);
        if (converted[1] != NULL) Py_DECREF(converted[1]);
        PyDataMem_FREE(converted);
    }
    Py_XDECREF(empty);
    Py_XDECREF(seq);
    Py_XDECREF(double_obj);
    Py_XDECREF(int_obj);
    return NULL;
}

static PyObject *pccnpcons_state_byteorder_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp two_dims[2] = {2, 3};
    npy_intp one_dim[1] = {3};
    PyObject *two_obj = PyArray_SimpleNew(2, two_dims, NPY_DOUBLE);
    PyObject *one_obj = PyArray_SimpleNew(1, one_dim, NPY_DOUBLE);
    PyObject *s0 = PyUnicode_FromString("abc");
    PyObject *s1 = PyBytes_FromString("de");
    if (two_obj == NULL || one_obj == NULL || s0 == NULL || s1 == NULL) {
        Py_XDECREF(two_obj);
        Py_XDECREF(one_obj);
        Py_XDECREF(s0);
        Py_XDECREF(s1);
        return NULL;
    }
    PyObject *str_seq = PyTuple_Pack(2, s0, s1);
    Py_DECREF(s0);
    Py_DECREF(s1);
    if (str_seq == NULL) {
        Py_DECREF(two_obj);
        Py_DECREF(one_obj);
        return NULL;
    }
    PyObject *str_obj = PyArray_FromAny(str_seq, NULL, 1, 1, 0, NULL);
    Py_DECREF(str_seq);
    if (str_obj == NULL) {
        Py_DECREF(two_obj);
        Py_DECREF(one_obj);
        return NULL;
    }
    PyArrayObject *two_arr = (PyArrayObject *)two_obj;
    PyArrayObject *one_arr = (PyArrayObject *)one_obj;
    PyArrayObject *str_arr = (PyArrayObject *)str_obj;
    PyArray_Descr *two_descr = PyArray_DESCR(two_arr);
    PyArray_Descr *str_descr = PyArray_DESCR(str_arr);
    long score = 0;
    if (PyArray_ISONESEGMENT(two_arr)) score += 1;
    if (!PyArray_ISFORTRAN(two_arr) && PyArray_FORTRAN_IF(two_arr) == 0) score += 10;
    if (PyArray_SAFEALIGNEDCOPY(two_arr)) score += 100;
    if (PyArray_IS_F_CONTIGUOUS(one_arr) && PyArray_ISFARRAY(one_arr) && PyArray_ISFARRAY_RO(one_arr)) score += 1000;
    if (PyArray_ISCARRAY_RO(two_arr) && PyArray_ISBEHAVED(two_arr) && PyArray_ISBEHAVED_RO(two_arr)) score += 10000;
    if (PyArray_ISNBO(two_descr->byteorder) && PyArray_IsNativeByteOrder('=')) score += 100000;
    if (PyArray_ISNOTSWAPPED(two_arr) && !PyArray_ISBYTESWAPPED(two_arr)) score += 1000000;
    if (PyDataType_ISNOTSWAPPED(two_descr) && !PyDataType_ISBYTESWAPPED(two_descr)) score += 10000000;
    if (PyArray_ISVARIABLE(str_arr) && !PyArray_SAFEALIGNEDCOPY(str_arr)) score += 100000000;
    if (PyDataType_ISNOTSWAPPED(str_descr) && !PyDataType_ISBYTESWAPPED(str_descr)) score += 1000000000L;
    Py_DECREF(str_obj);
    Py_DECREF(two_obj);
    Py_DECREF(one_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_from_macro_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *v0 = PyLong_FromLong(4);
    PyObject *v1 = PyLong_FromLong(5);
    PyObject *v2 = PyLong_FromLong(6);
    if (v0 == NULL || v1 == NULL || v2 == NULL) {
        Py_XDECREF(v0);
        Py_XDECREF(v1);
        Py_XDECREF(v2);
        return NULL;
    }
    PyObject *seq = PyTuple_Pack(3, v0, v1, v2);
    Py_DECREF(v0);
    Py_DECREF(v1);
    Py_DECREF(v2);
    if (seq == NULL) return NULL;
    long score = 0;
    PyObject *from_o = PyArray_FROM_O(seq);
    if (from_o != NULL && PyArray_NDIM((PyArrayObject *)from_o) == 1 && PyArray_TYPE((PyArrayObject *)from_o) == NPY_LONG) score += 1;
    Py_XDECREF(from_o);
    PyObject *from_of = PyArray_FROM_OF(seq, NPY_ARRAY_ENSUREARRAY);
    if (from_of != NULL && PyArray_NDIM((PyArrayObject *)from_of) == 1 && PyArray_TYPE((PyArrayObject *)from_of) == NPY_LONG) score += 10;
    Py_XDECREF(from_of);
    PyObject *from_ot = PyArray_FROM_OT(seq, NPY_DOUBLE);
    if (from_ot != NULL && PyArray_TYPE((PyArrayObject *)from_ot) == NPY_DOUBLE) score += 100;
    Py_XDECREF(from_ot);
    PyObject *from_otf = PyArray_FROM_OTF(seq, NPY_INT, NPY_ARRAY_ENSURECOPY);
    if (from_otf != NULL && PyArray_TYPE((PyArrayObject *)from_otf) == NPY_INT) score += 1000;
    Py_XDECREF(from_otf);
    PyObject *fromany = PyArray_FROMANY(seq, NPY_INT, 1, 1, 0);
    if (fromany != NULL && PyArray_NDIM((PyArrayObject *)fromany) == 1 && PyArray_SIZE((PyArrayObject *)fromany) == 3) score += 10000;
    Py_XDECREF(fromany);
    PyObject *contig_any = PyArray_ContiguousFromAny(seq, NPY_DOUBLE, 1, 1);
    if (contig_any != NULL && PyArray_TYPE((PyArrayObject *)contig_any) == NPY_DOUBLE && PyArray_ISCARRAY((PyArrayObject *)contig_any)) score += 100000;
    Py_XDECREF(contig_any);
    PyObject *from_object = PyArray_FromObject(seq, NPY_INT, 1, 1);
    if (from_object != NULL && PyArray_TYPE((PyArrayObject *)from_object) == NPY_INT) score += 1000000;
    Py_XDECREF(from_object);
    PyObject *contig_object = PyArray_ContiguousFromObject(seq, NPY_DOUBLE, 1, 1);
    if (contig_object != NULL && PyArray_TYPE((PyArrayObject *)contig_object) == NPY_DOUBLE) score += 10000000;
    Py_XDECREF(contig_object);
    PyObject *copy_object = PyArray_CopyFromObject(seq, NPY_INT, 1, 1);
    if (copy_object != NULL && PyArray_TYPE((PyArrayObject *)copy_object) == NPY_INT) score += 100000000;
    Py_XDECREF(copy_object);
    PyObject *check_any = PyArray_CheckFromAny(seq, PyArray_DescrFromType(NPY_DOUBLE), 1, 1, NPY_ARRAY_ALIGNED, NULL);
    if (check_any != NULL && PyArray_TYPE((PyArrayObject *)check_any) == NPY_DOUBLE && PyArray_NDIM((PyArrayObject *)check_any) == 1) score += 1000000000L;
    Py_XDECREF(check_any);
    Py_DECREF(seq);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_fill_byteorder_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {4};
    PyObject *arr_obj = PyArray_SimpleNew(1, dims, NPY_BYTE);
    if (arr_obj == NULL) return NULL;
    PyArrayObject *arr = (PyArrayObject *)arr_obj;
    signed char *data = (signed char *)PyArray_DATA(arr);
    PyArray_Descr *descr = PyArray_DESCR(arr);
    long score = 0;
    if (data != NULL) {
        PyArray_FILLWBYTE(arr, 0x7f);
        if (data[0] == 0x7f && data[1] == 0x7f && data[2] == 0x7f && data[3] == 0x7f) score += 1;
        PyArray_FILLWBYTE(arr, 0);
        if (data[0] == 0 && data[1] == 0 && data[2] == 0 && data[3] == 0) score += 10;
    }
    if (descr != NULL && PyArray_EquivByteorders(descr->byteorder, NPY_NATBYTE)) score += 100;
    if (PyArray_EquivByteorders('|', NPY_NATBYTE)) score += 1000;
    if (!PyArray_EquivByteorders(NPY_NATBYTE, NPY_OPPBYTE)) score += 10000;
    Py_DECREF(arr_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_shape_compare_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims_a[2] = {2, 3};
    npy_intp dims_b[2] = {2, 3};
    npy_intp dims_c[2] = {3, 2};
    npy_intp dims_d[1] = {6};
    PyObject *a_obj = PyArray_SimpleNew(2, dims_a, NPY_INT);
    PyObject *b_obj = PyArray_SimpleNew(2, dims_b, NPY_INT);
    PyObject *c_obj = PyArray_SimpleNew(2, dims_c, NPY_INT);
    PyObject *d_obj = PyArray_SimpleNew(1, dims_d, NPY_INT);
    if (a_obj == NULL || b_obj == NULL || c_obj == NULL || d_obj == NULL) {
        Py_XDECREF(a_obj);
        Py_XDECREF(b_obj);
        Py_XDECREF(c_obj);
        Py_XDECREF(d_obj);
        return NULL;
    }
    PyArrayObject *a = (PyArrayObject *)a_obj;
    PyArrayObject *b = (PyArrayObject *)b_obj;
    PyArrayObject *c = (PyArrayObject *)c_obj;
    PyArrayObject *d = (PyArrayObject *)d_obj;
    npy_intp *shape = PyArray_SHAPE(a);
    long score = 0;
    if (shape != NULL && shape[0] == 2 && shape[1] == 3) score += 1;
    if (PyArray_CompareLists(PyArray_DIMS(a), PyArray_DIMS(b), PyArray_NDIM(a))) score += 10;
    if (!PyArray_CompareLists(PyArray_DIMS(a), PyArray_DIMS(c), PyArray_NDIM(a))) score += 100;
    if (PyArray_SAMESHAPE(a, b)) score += 1000;
    if (!PyArray_SAMESHAPE(a, c)) score += 10000;
    if (!PyArray_SAMESHAPE(a, d)) score += 100000;
    Py_DECREF(a_obj);
    Py_DECREF(b_obj);
    Py_DECREF(c_obj);
    Py_DECREF(d_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_empty_zeros_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[2] = {2, 2};
    PyObject *empty_obj = PyArray_EMPTY(2, dims, NPY_INT, 0);
    PyObject *zeros_obj = PyArray_ZEROS(2, dims, NPY_BYTE, 0);
    if (empty_obj == NULL || zeros_obj == NULL) {
        Py_XDECREF(empty_obj);
        Py_XDECREF(zeros_obj);
        return NULL;
    }
    PyArrayObject *empty_arr = (PyArrayObject *)empty_obj;
    PyArrayObject *zeros_arr = (PyArrayObject *)zeros_obj;
    signed char *zeros_data = (signed char *)PyArray_DATA(zeros_arr);
    long score = 0;
    if (PyArray_TYPE(empty_arr) == NPY_INT && PyArray_NDIM(empty_arr) == 2 && PyArray_SIZE(empty_arr) == 4) score += 1;
    if (PyArray_TYPE(zeros_arr) == NPY_BYTE && PyArray_NDIM(zeros_arr) == 2 && PyArray_SIZE(zeros_arr) == 4) score += 10;
    if (zeros_data != NULL && zeros_data[0] == 0 && zeros_data[1] == 0 && zeros_data[2] == 0 && zeros_data[3] == 0) score += 100;
    if (PyArray_SAMESHAPE(empty_arr, zeros_arr)) score += 1000;
    PyObject *fortran_obj = PyArray_EMPTY(2, dims, NPY_INT, 1);
    if (fortran_obj == NULL && PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 10000;
    Py_XDECREF(fortran_obj);
    PyErr_Clear();
    if (PyArray_CompareLists(PyArray_SHAPE(empty_arr), dims, 2)) score += 100000;
    Py_DECREF(empty_obj);
    Py_DECREF(zeros_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_equiv_types_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {3};
    PyObject *int_a_obj = PyArray_EMPTY(1, dims, NPY_INT, 0);
    PyObject *int_b_obj = PyArray_ZEROS(1, dims, NPY_INT, 0);
    PyObject *double_obj = PyArray_EMPTY(1, dims, NPY_DOUBLE, 0);
    if (int_a_obj == NULL || int_b_obj == NULL || double_obj == NULL) {
        Py_XDECREF(int_a_obj);
        Py_XDECREF(int_b_obj);
        Py_XDECREF(double_obj);
        return NULL;
    }
    PyArrayObject *int_a = (PyArrayObject *)int_a_obj;
    PyArrayObject *int_b = (PyArrayObject *)int_b_obj;
    PyArrayObject *dbl = (PyArrayObject *)double_obj;
    long score = 0;
    if (PyArray_EquivTypes(PyArray_DESCR(int_a), PyArray_DESCR(int_b))) score += 1;
    if (!PyArray_EquivTypes(PyArray_DESCR(int_a), PyArray_DESCR(dbl))) score += 10;
    if (PyArray_EquivArrTypes(int_a, int_b)) score += 100;
    if (!PyArray_EquivArrTypes(int_a, dbl)) score += 1000;
    if (!PyArray_EquivTypes(PyArray_DESCR(int_a), NULL)) score += 10000;
    if (PyArray_EquivTypenums(NPY_INT, NPY_INT)) score += 100000;
    if (!PyArray_EquivTypenums(NPY_INT, NPY_DOUBLE)) score += 1000000;
    if (!PyArray_EquivTypenums(9999, NPY_INT) && PyErr_Occurred() == NULL) score += 10000000;
    Py_DECREF(int_a_obj);
    Py_DECREF(int_b_obj);
    Py_DECREF(double_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_new_from_descr_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {3};
    PyObject *descr_obj = PyArray_SimpleNewFromDescr(1, dims, PyArray_DescrFromType(NPY_DOUBLE));
    if (descr_obj == NULL) return NULL;
    PyArrayObject *descr_arr = (PyArrayObject *)descr_obj;
    signed char raw[3] = {4, 5, 6};
    PyObject *data_obj = PyArray_NewFromDescr((PyTypeObject *)PyArray_API[0], PyArray_DescrFromType(NPY_BYTE), 1, dims, NULL, raw, 0, NULL);
    if (data_obj == NULL) {
        Py_DECREF(descr_obj);
        return NULL;
    }
    PyArrayObject *data_arr = (PyArrayObject *)data_obj;
    long score = 0;
    if (PyArray_TYPE(descr_arr) == NPY_DOUBLE && PyArray_NDIM(descr_arr) == 1 && PyArray_SIZE(descr_arr) == 3) score += 1;
    if (PyArray_ITEMSIZE(descr_arr) == (int)sizeof(double)) score += 10;
    if (PyArray_TYPE(data_arr) == NPY_BYTE && PyArray_DATA(data_arr) == raw) score += 100;
    npy_intp strides[1] = {1};
    PyObject *strided_obj = PyArray_NewFromDescr((PyTypeObject *)PyArray_API[0], PyArray_DescrFromType(NPY_BYTE), 1, dims, strides, NULL, 0, NULL);
    if (strided_obj == NULL && PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 1000;
    Py_XDECREF(strided_obj);
    PyErr_Clear();
    PyObject *null_descr_obj = PyArray_NewFromDescr((PyTypeObject *)PyArray_API[0], NULL, 1, dims, NULL, NULL, 0, NULL);
    if (null_descr_obj == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 10000;
    Py_XDECREF(null_descr_obj);
    PyErr_Clear();
    Py_DECREF(descr_obj);
    Py_DECREF(data_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_new_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[2] = {2, 2};
    PyObject *owned_obj = PyArray_New(&PyArray_Type, 2, dims, NPY_INT, NULL, NULL, 0, 0, NULL);
    if (owned_obj == NULL) return NULL;
    PyArrayObject *owned = (PyArrayObject *)owned_obj;
    int *owned_data = (int *)PyArray_DATA(owned);
    signed char raw[3] = {1, 2, 3};
    npy_intp raw_dims[1] = {3};
    PyObject *external_obj = PyArray_New(&PyArray_Type, 1, raw_dims, NPY_BYTE, NULL, raw, 0, 0, NULL);
    if (external_obj == NULL) {
        Py_DECREF(owned_obj);
        return NULL;
    }
    PyArrayObject *external = (PyArrayObject *)external_obj;
    long score = 0;
    if (PyArray_TYPE(owned) == NPY_INT && PyArray_NDIM(owned) == 2 && PyArray_SIZE(owned) == 4) score += 1;
    if (PyArray_ITEMSIZE(owned) == (int)sizeof(int) && owned_data != NULL) {
        owned_data[0] = 7;
        owned_data[3] = 10;
        if (owned_data[0] == 7 && owned_data[3] == 10) score += 10;
    }
    if (PyArray_TYPE(external) == NPY_BYTE && PyArray_DATA(external) == raw && PyArray_SIZE(external) == 3) score += 100;
    raw[1] = 9;
    if (((signed char *)PyArray_DATA(external))[1] == 9) score += 1000;
    npy_intp strides[1] = {1};
    PyObject *strided_obj = PyArray_New(&PyArray_Type, 1, raw_dims, NPY_BYTE, strides, NULL, 0, 0, NULL);
    if (strided_obj == NULL && PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 10000;
    Py_XDECREF(strided_obj);
    PyErr_Clear();
    PyObject *null_subtype_obj = PyArray_New(NULL, 1, raw_dims, NPY_BYTE, NULL, NULL, 0, 0, NULL);
    if (null_subtype_obj == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 100000;
    Py_XDECREF(null_subtype_obj);
    PyErr_Clear();
    Py_DECREF(external_obj);
    Py_DECREF(owned_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_array_check_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *arr_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *non_arr = PyLong_FromLong(7);
    if (arr_obj == NULL || non_arr == NULL) {
        Py_XDECREF(arr_obj);
        Py_XDECREF(non_arr);
        return NULL;
    }
    long score = 0;
    if (PyArray_Check(arr_obj)) score += 1;
    if (PyArray_CheckExact(arr_obj)) score += 10;
    if (!PyArray_Check(non_arr)) score += 100;
    if (!PyArray_CheckExact(non_arr)) score += 1000;
    Py_DECREF(arr_obj);
    Py_DECREF(non_arr);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_size_itemsize_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp int_dims[2] = {2, 3};
    npy_intp double_dims[1] = {4};
    PyObject *int_obj = PyArray_SimpleNew(2, int_dims, NPY_INT);
    PyObject *double_obj = PyArray_SimpleNew(1, double_dims, NPY_DOUBLE);
    if (int_obj == NULL || double_obj == NULL) {
        Py_XDECREF(int_obj);
        Py_XDECREF(double_obj);
        return NULL;
    }
    PyArrayObject *int_arr = (PyArrayObject *)int_obj;
    PyArrayObject *double_arr = (PyArrayObject *)double_obj;
    long score = 0;
    if (PyArray_SIZE(int_arr) == 6) score += 1;
    if (PyArray_ITEMSIZE(int_arr) == (int)sizeof(int)) score += 10;
    if (PyArray_SIZE(double_arr) == 4) score += 100;
    if (PyArray_ITEMSIZE(double_arr) == (int)sizeof(double)) score += 1000;
    Py_DECREF(int_obj);
    Py_DECREF(double_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_array_size_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);
    PyObject *list = PyList_New(2);
    if (arr == NULL || list == NULL) {
        Py_XDECREF(arr);
        Py_XDECREF(list);
        return NULL;
    }
    long score = 0;
    if (PyArray_Size(arr) == 6) score += 1;
    if (PyArray_Size(Py_None) == 0) score += 10;
    if (PyArray_Size(list) == 0) score += 100;
    if (PyArray_Size(NULL) == 0) score += 1000;
    Py_DECREF(list);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_accessor_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[2] = {2, 3};
    PyObject *obj = PyArray_SimpleNew(2, dims, NPY_INT);
    if (obj == NULL) return NULL;
    PyArrayObject *arr = (PyArrayObject *)obj;
    npy_intp *arr_dims = PyArray_DIMS(arr);
    npy_intp *arr_strides = PyArray_STRIDES(arr);
    int *arr_data = (int *)PyArray_DATA(arr);
    PyArray_Descr *descr = PyArray_DESCR(arr);
    long score = 0;
    if (PyArray_NDIM(arr) == 2) score += 1;
    if (arr_dims != NULL && arr_dims[0] == 2 && arr_dims[1] == 3) score += 10;
    if (arr_strides != NULL && arr_strides[0] == 3 * (npy_intp)sizeof(int) && arr_strides[1] == (npy_intp)sizeof(int)) score += 100;
    if (arr_data != NULL) score += 1000;
    if (descr != NULL && descr->type_num == NPY_INT) score += 10000;
    if (PyArray_DIM(arr, 0) == 2 && PyArray_DIM(arr, 1) == 3) score += 100000;
    if (PyArray_BYTES(arr) == (void *)arr_data) score += 1000000;
    Py_DECREF(obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_core_provider_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {3};
    PyArray_Descr *descr = PyArray_DescrFromType(NPY_INT);
    PyObject *owned = PyArray_SimpleNew(1, dims, NPY_INT);
    int raw[3] = {1, 2, 3};
    PyObject *borrowed = PyArray_SimpleNewFromData(1, dims, NPY_INT, raw);
    PyObject *v0 = PyLong_FromLong(5);
    PyObject *v1 = PyLong_FromLong(6);
    PyObject *seq = NULL;
    PyObject *from_any = NULL;
    PyObject *item = NULL;
    PyObject *replacement = NULL;
    if (owned == NULL || borrowed == NULL || v0 == NULL || v1 == NULL) {
        Py_XDECREF(owned);
        Py_XDECREF(borrowed);
        Py_XDECREF(v0);
        Py_XDECREF(v1);
        return NULL;
    }
    seq = PyTuple_Pack(2, v0, v1);
    Py_DECREF(v0);
    Py_DECREF(v1);
    if (seq == NULL) {
        Py_DECREF(owned);
        Py_DECREF(borrowed);
        return NULL;
    }
    from_any = PyArray_FromAny(seq, NULL, 1, 1, 0, NULL);
    Py_DECREF(seq);
    if (from_any == NULL) {
        Py_DECREF(owned);
        Py_DECREF(borrowed);
        return NULL;
    }
    PyArrayObject *owned_arr = (PyArrayObject *)owned;
    PyArrayObject *borrowed_arr = (PyArrayObject *)borrowed;
    PyArrayObject *from_any_arr = (PyArrayObject *)from_any;
    long score = 0;
    if (descr != NULL && descr->type_num == NPY_INT) score += 1;
    if (PyArray_NDIM(owned_arr) == 1 && PyArray_SIZE(owned_arr) == 3 && PyArray_TYPE(owned_arr) == NPY_INT) score += 10;
    if (PyArray_DATA(borrowed_arr) == raw && PyArray_SIZE(borrowed_arr) == 3) score += 100;
    if (PyArray_NDIM(from_any_arr) == 1 && PyArray_SIZE(from_any_arr) == 2 && PyArray_TYPE(from_any_arr) == NPY_LONG) score += 1000;
    item = PyArray_GETITEM(borrowed_arr, &raw[1]);
    if (item != NULL && PyLong_AsLong(item) == 2) score += 10000;
    Py_XDECREF(item);
    replacement = PyLong_FromLong(9);
    if (replacement == NULL) {
        Py_DECREF(from_any);
        Py_DECREF(owned);
        Py_DECREF(borrowed);
        return NULL;
    }
    if (PyArray_SETITEM(borrowed_arr, &raw[2], replacement) == 0 && raw[2] == 9) score += 100000;
    Py_DECREF(replacement);
    if (PyErr_Occurred() != NULL) {
        Py_DECREF(from_any);
        Py_DECREF(owned);
        Py_DECREF(borrowed);
        return NULL;
    }
    Py_DECREF(from_any);
    Py_DECREF(owned);
    Py_DECREF(borrowed);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_base_object_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    int raw[2] = {4, 5};
    PyObject *owned = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *borrowed = PyArray_SimpleNewFromData(1, dims, NPY_INT, raw);
    PyObject *base = PyBytes_FromString("owner");
    if (owned == NULL || borrowed == NULL || base == NULL) {
        Py_XDECREF(owned);
        Py_XDECREF(borrowed);
        Py_XDECREF(base);
        return NULL;
    }
    PyArrayObject *owned_arr = (PyArrayObject *)owned;
    PyArrayObject *borrowed_arr = (PyArrayObject *)borrowed;
    long score = 0;
    if (PyArray_BASE(owned_arr) == NULL) score += 1;
    if (PyArray_SetBaseObject(borrowed_arr, base) == 0) score += 10;
    if (PyArray_BASE(borrowed_arr) == base) score += 100;
    if (Py_REFCNT(base) == 1) score += 1000;
    PyObject *base2 = PyLong_FromLong(99);
    PyObject *with_base = PyArray_NewFromDescr((PyTypeObject *)PyArray_API[0], PyArray_DescrFromType(NPY_BYTE), 1, dims, NULL, raw, 0, base2);
    if (with_base == NULL) {
        Py_DECREF(owned);
        Py_DECREF(borrowed);
        Py_XDECREF(base2);
        return NULL;
    }
    if (PyArray_BASE((PyArrayObject *)with_base) == base2) score += 10000;
    Py_DECREF(with_base);
    Py_DECREF(owned);
    Py_DECREF(borrowed);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_return_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *arr_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *scalar_arr_obj = PyArray_SimpleNew(0, NULL, NPY_INT);
    if (arr_obj == NULL || scalar_arr_obj == NULL) {
        Py_XDECREF(arr_obj);
        Py_XDECREF(scalar_arr_obj);
        return NULL;
    }
    PyArrayObject *arr = (PyArrayObject *)arr_obj;
    PyArrayObject *scalar_arr = (PyArrayObject *)scalar_arr_obj;
    int *arr_data = (int *)PyArray_DATA(arr);
    int *scalar_data = (int *)PyArray_DATA(scalar_arr);
    if (arr_data == NULL || scalar_data == NULL) {
        Py_DECREF(arr_obj);
        Py_DECREF(scalar_arr_obj);
        return NULL;
    }
    arr_data[0] = 7;
    arr_data[1] = 8;
    scalar_data[0] = 42;
    long score = 0;
    PyObject *arr_ret = PyArray_Return(arr);
    if (arr_ret == arr_obj) score += 1;
    if (arr_ret != NULL && PyArray_Check(arr_ret) && PyArray_SIZE((PyArrayObject *)arr_ret) == 2) score += 10;
    Py_DECREF(arr_ret);
    PyObject *scalar_ret = PyArray_Return(scalar_arr);
    if (scalar_ret != scalar_arr_obj) score += 100;
    if (scalar_ret != NULL && PyLong_Check(scalar_ret) && PyLong_AsLong(scalar_ret) == 42) score += 1000;
    Py_DECREF(scalar_ret);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_flags_mutation_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *obj = PyArray_SimpleNew(1, dims, NPY_INT);
    if (obj == NULL) return NULL;
    PyArrayObject *arr = (PyArrayObject *)obj;
    long score = 0;
    if (PyArray_ISWRITEABLE(arr)) score += 1;
    PyArray_CLEARFLAGS(arr, NPY_ARRAY_WRITEABLE);
    if (!PyArray_ISWRITEABLE(arr)) score += 10;
    PyArray_ENABLEFLAGS(arr, NPY_ARRAY_WRITEABLE);
    if (PyArray_ISWRITEABLE(arr)) score += 100;
    PyArray_CLEARFLAGS(arr, NPY_ARRAY_ALIGNED);
    if (!PyArray_ISALIGNED(arr)) score += 1000;
    PyArray_UpdateFlags(arr, NPY_ARRAY_ALIGNED);
    if (PyArray_ISALIGNED(arr)) score += 10000;
    PyArray_CLEARFLAGS(arr, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_F_CONTIGUOUS);
    if (!PyArray_ISCONTIGUOUS(arr) && !PyArray_IS_F_CONTIGUOUS(arr)) score += 100000;
    PyArray_UpdateFlags(arr, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_F_CONTIGUOUS);
    if (PyArray_ISCONTIGUOUS(arr) && PyArray_IS_F_CONTIGUOUS(arr)) score += 1000000;
    Py_DECREF(obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_copy_into_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {3};
    npy_intp mismatch_dims[1] = {2};
    PyObject *src_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *dst_obj = PyArray_ZEROS(1, dims, NPY_INT, 0);
    PyObject *mismatch_obj = PyArray_ZEROS(1, mismatch_dims, NPY_INT, 0);
    if (src_obj == NULL || dst_obj == NULL || mismatch_obj == NULL) {
        Py_XDECREF(src_obj);
        Py_XDECREF(dst_obj);
        Py_XDECREF(mismatch_obj);
        return NULL;
    }
    PyArrayObject *src = (PyArrayObject *)src_obj;
    PyArrayObject *dst = (PyArrayObject *)dst_obj;
    PyArrayObject *mismatch = (PyArrayObject *)mismatch_obj;
    int *src_data = (int *)PyArray_DATA(src);
    int *dst_data = (int *)PyArray_DATA(dst);
    if (src_data == NULL || dst_data == NULL) {
        Py_DECREF(src_obj);
        Py_DECREF(dst_obj);
        Py_DECREF(mismatch_obj);
        return NULL;
    }
    src_data[0] = 3;
    src_data[1] = 4;
    src_data[2] = 5;
    long score = 0;
    if (PyArray_CopyInto(dst, src) == 0) score += 1;
    if (dst_data[0] == 3 && dst_data[1] == 4 && dst_data[2] == 5) score += 10;
    src_data[0] = 6;
    src_data[1] = 7;
    src_data[2] = 8;
    if (PyArray_CopyAnyInto(dst, src) == 0) score += 100;
    if (dst_data[0] == 6 && dst_data[1] == 7 && dst_data[2] == 8) score += 1000;
    if (PyArray_CopyInto(mismatch, src) != 0 && PyErr_ExceptionMatches(PyExc_ValueError)) score += 10000;
    PyErr_Clear();
    Py_DECREF(src_obj);
    Py_DECREF(dst_obj);
    Py_DECREF(mismatch_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_to_scalar_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *obj = PyArray_SimpleNew(1, dims, NPY_INT);
    if (obj == NULL) return NULL;
    PyArrayObject *arr = (PyArrayObject *)obj;
    int *data = (int *)PyArray_DATA(arr);
    if (data == NULL) {
        Py_DECREF(obj);
        return NULL;
    }
    data[0] = 11;
    data[1] = 12;
    PyObject *scalar0 = PyArray_ToScalar((void *)data, arr);
    PyObject *scalar1 = PyArray_ToScalar((void *)(data + 1), arr);
    if (scalar0 == NULL || scalar1 == NULL) {
        Py_XDECREF(scalar0);
        Py_XDECREF(scalar1);
        Py_DECREF(obj);
        return NULL;
    }
    long score = 0;
    if (PyLong_Check(scalar0) && PyLong_AsLong(scalar0) == 11) score += 1;
    if (PyLong_Check(scalar1) && PyLong_AsLong(scalar1) == 12) score += 10;
    Py_DECREF(scalar0);
    Py_DECREF(scalar1);
    PyObject *bad = PyArray_ToScalar(NULL, arr);
    if (bad == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 100;
    Py_XDECREF(bad);
    PyErr_Clear();
    Py_DECREF(obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_copy_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *src_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *obj_obj = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (src_obj == NULL || obj_obj == NULL) {
        Py_XDECREF(src_obj);
        Py_XDECREF(obj_obj);
        return NULL;
    }
    PyArrayObject *src = (PyArrayObject *)src_obj;
    int *src_data = (int *)PyArray_DATA(src);
    if (src_data == NULL) {
        Py_DECREF(src_obj);
        Py_DECREF(obj_obj);
        return NULL;
    }
    src_data[0] = 21;
    src_data[1] = 22;
    PyObject *copy_obj = PyArray_Copy(src);
    if (copy_obj == NULL) {
        Py_DECREF(src_obj);
        Py_DECREF(obj_obj);
        return NULL;
    }
    PyArrayObject *copy = (PyArrayObject *)copy_obj;
    int *copy_data = (int *)PyArray_DATA(copy);
    long score = 0;
    if (copy_obj != src_obj && PyArray_CheckExact(copy) && PyArray_SIZE(copy) == 2) score += 1;
    if (copy_data != NULL && copy_data[0] == 21 && copy_data[1] == 22) score += 10;
    src_data[0] = 99;
    if (copy_data != NULL && copy_data[0] == 21) score += 100;
    PyObject *obj_copy = PyArray_Copy((PyArrayObject *)obj_obj);
    if (obj_copy == NULL && PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 1000;
    Py_XDECREF(obj_copy);
    PyErr_Clear();
    Py_DECREF(copy_obj);
    Py_DECREF(src_obj);
    Py_DECREF(obj_obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_new_copy_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *src_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *obj_obj = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (src_obj == NULL || obj_obj == NULL) {
        Py_XDECREF(src_obj);
        Py_XDECREF(obj_obj);
        return NULL;
    }
    PyArrayObject *src = (PyArrayObject *)src_obj;
    int *src_data = (int *)PyArray_DATA(src);
    if (src_data == NULL) {
        Py_DECREF(src_obj);
        Py_DECREF(obj_obj);
        return NULL;
    }
    src_data[0] = 41;
    src_data[1] = 42;
    PyObject *copy_obj = PyArray_NewCopy(src, NPY_KEEPORDER);
    if (copy_obj == NULL) {
        Py_DECREF(src_obj);
        Py_DECREF(obj_obj);
        return NULL;
    }
    PyArrayObject *copy = (PyArrayObject *)copy_obj;
    int *copy_data = (int *)PyArray_DATA(copy);
    long score = 0;
    if (copy_obj != src_obj && PyArray_CheckExact(copy) && PyArray_SIZE(copy) == 2) score += 1;
    if (copy_data != NULL && copy_data[0] == 41 && copy_data[1] == 42) score += 10;
    src_data[0] = 99;
    if (copy_data != NULL && copy_data[0] == 41) score += 100;
    PyObject *obj_copy = PyArray_NewCopy((PyArrayObject *)obj_obj, NPY_KEEPORDER);
    if (obj_copy == NULL && PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 1000;
    Py_XDECREF(obj_copy);
    PyErr_Clear();
    PyObject *null_copy = PyArray_NewCopy(NULL, NPY_CORDER);
    if (null_copy == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 10000;
    Py_XDECREF(null_copy);
    PyErr_Clear();
    Py_DECREF(copy_obj);
    Py_DECREF(src_obj);
    Py_DECREF(obj_obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_copy_object_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *dest_obj = PyArray_SimpleNew(1, dims, NPY_LONG);
    PyObject *src_obj = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (dest_obj == NULL || src_obj == NULL) {
        Py_XDECREF(dest_obj);
        Py_XDECREF(src_obj);
        return NULL;
    }
    long *src = (long *)PyArray_DATA((PyArrayObject *)src_obj);
    long *dest = (long *)PyArray_DATA((PyArrayObject *)dest_obj);
    src[0] = 1; src[1] = 2; src[2] = 3;
    if (PyArray_CopyObject((PyArrayObject *)dest_obj, src_obj) == 0 && dest[0] == 1 && dest[1] == 2 && dest[2] == 3) score += 1;
    PyObject *list = PyList_New(3);
    if (list == NULL) { Py_DECREF(dest_obj); Py_DECREF(src_obj); return NULL; }
    if (PyList_SetItem(list, 0, PyLong_FromLong(4)) != 0 ||
        PyList_SetItem(list, 1, PyLong_FromLong(5)) != 0 ||
        PyList_SetItem(list, 2, PyLong_FromLong(6)) != 0) {
        Py_DECREF(list);
        Py_DECREF(dest_obj);
        Py_DECREF(src_obj);
        return NULL;
    }
    if (PyArray_CopyObject((PyArrayObject *)dest_obj, list) == 0 && dest[0] == 4 && dest[1] == 5 && dest[2] == 6) score += 10;
    Py_DECREF(list);
    PyObject *scalar = PyLong_FromLong(9);
    if (scalar == NULL) { Py_DECREF(dest_obj); Py_DECREF(src_obj); return NULL; }
    if (PyArray_CopyObject((PyArrayObject *)dest_obj, scalar) == 0 && dest[0] == 9 && dest[1] == 9 && dest[2] == 9) score += 100;
    Py_DECREF(scalar);
    PyObject *short_list = PyList_New(2);
    if (short_list == NULL) { Py_DECREF(dest_obj); Py_DECREF(src_obj); return NULL; }
    if (PyList_SetItem(short_list, 0, PyLong_FromLong(7)) != 0 ||
        PyList_SetItem(short_list, 1, PyLong_FromLong(8)) != 0) {
        Py_DECREF(short_list);
        Py_DECREF(dest_obj);
        Py_DECREF(src_obj);
        return NULL;
    }
    if (PyArray_CopyObject((PyArrayObject *)dest_obj, short_list) != 0 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_DECREF(short_list);
    Py_DECREF(dest_obj);
    Py_DECREF(src_obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_resize_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 1; data[1] = 2; data[2] = 3;
    npy_intp grow_dims[1] = {5};
    PyArray_Dims grow = {grow_dims, 1};
    PyObject *ret = PyArray_Resize((PyArrayObject *)arr, &grow, 0, NPY_CORDER);
    data = (int *)PyArray_DATA((PyArrayObject *)arr);
    if (ret == Py_None && PyArray_SIZE((PyArrayObject *)arr) == 5 && data[0] == 1 && data[1] == 2 && data[2] == 3 && data[3] == 0 && data[4] == 0) score += 1;
    Py_XDECREF(ret);
    npy_intp shrink_dims[1] = {2};
    PyArray_Dims shrink = {shrink_dims, 1};
    ret = PyArray_Resize((PyArrayObject *)arr, &shrink, 0, NPY_CORDER);
    data = (int *)PyArray_DATA((PyArrayObject *)arr);
    if (ret == Py_None && PyArray_SIZE((PyArrayObject *)arr) == 2 && data[0] == 1 && data[1] == 2) score += 10;
    Py_XDECREF(ret);
    npy_intp matrix_dims[2] = {2, 2};
    PyArray_Dims matrix = {matrix_dims, 2};
    ret = PyArray_Resize((PyArrayObject *)arr, &matrix, 0, NPY_CORDER);
    data = (int *)PyArray_DATA((PyArrayObject *)arr);
    npy_intp *shape = PyArray_DIMS((PyArrayObject *)arr);
    npy_intp *strides = PyArray_STRIDES((PyArrayObject *)arr);
    if (ret == Py_None && PyArray_NDIM((PyArrayObject *)arr) == 2 && shape[0] == 2 && shape[1] == 2 &&
        strides[0] == 2 * (npy_intp)sizeof(int) && strides[1] == (npy_intp)sizeof(int) &&
        data[0] == 1 && data[1] == 2 && data[2] == 0 && data[3] == 0) score += 100;
    Py_XDECREF(ret);
    int raw[2] = {7, 8};
    npy_intp view_dims[1] = {2};
    PyObject *view = PyArray_SimpleNewFromData(1, view_dims, NPY_INT, raw);
    if (view == NULL) { Py_DECREF(arr); return NULL; }
    npy_intp reject_dims[1] = {3};
    PyArray_Dims reject = {reject_dims, 1};
    ret = PyArray_Resize((PyArrayObject *)view, &reject, 0, NPY_CORDER);
    if (ret == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(ret);
    Py_DECREF(view);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_new_like_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *proto = PyArray_SimpleNew(2, dims, NPY_INT);
    if (proto == NULL) return NULL;
    PyObject *same = PyArray_NewLikeArray((PyArrayObject *)proto, NPY_CORDER, NULL, 0);
    if (same != NULL && same != proto && PyArray_NDIM((PyArrayObject *)same) == 2 &&
        PyArray_DIMS((PyArrayObject *)same)[0] == 2 && PyArray_DIMS((PyArrayObject *)same)[1] == 3 &&
        PyArray_TYPE((PyArrayObject *)same) == NPY_INT && PyArray_ITEMSIZE((PyArrayObject *)same) == (npy_intp)sizeof(int) &&
        PyArray_DATA((PyArrayObject *)same) != PyArray_DATA((PyArrayObject *)proto)) score += 1;
    Py_XDECREF(same);
    PyArray_Descr *descr = PyArray_DescrNewFromType(NPY_DOUBLE);
    if (descr == NULL) { Py_DECREF(proto); return NULL; }
    PyObject *typed = PyArray_NewLikeArray((PyArrayObject *)proto, NPY_ANYORDER, descr, 0);
    if (typed != NULL && PyArray_NDIM((PyArrayObject *)typed) == 2 &&
        PyArray_DIMS((PyArrayObject *)typed)[0] == 2 && PyArray_DIMS((PyArrayObject *)typed)[1] == 3 &&
        PyArray_TYPE((PyArrayObject *)typed) == NPY_DOUBLE && PyArray_ITEMSIZE((PyArrayObject *)typed) == (npy_intp)sizeof(double)) score += 10;
    Py_XDECREF(typed);
    npy_intp one_dims[1] = {4};
    PyObject *one = PyArray_SimpleNew(1, one_dims, NPY_INT);
    if (one == NULL) { Py_DECREF(proto); return NULL; }
    PyObject *one_like = PyArray_NewLikeArray((PyArrayObject *)one, NPY_FORTRANORDER, NULL, 0);
    if (one_like != NULL && PyArray_NDIM((PyArrayObject *)one_like) == 1 &&
        PyArray_DIMS((PyArrayObject *)one_like)[0] == 4 && PyArray_TYPE((PyArrayObject *)one_like) == NPY_INT) score += 100;
    Py_XDECREF(one_like);
    PyObject *fortran_reject = PyArray_NewLikeArray((PyArrayObject *)proto, NPY_FORTRANORDER, NULL, 0);
    if (fortran_reject == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(fortran_reject);
    Py_DECREF(one);
    Py_DECREF(proto);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_view_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 2};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 9;
    PyObject *view = PyArray_View((PyArrayObject *)arr, NULL, NULL);
    if (view != NULL && view != arr && PyArray_DATA((PyArrayObject *)view) == PyArray_DATA((PyArrayObject *)arr) &&
        PyArray_BASE((PyArrayObject *)view) == arr && PyArray_NDIM((PyArrayObject *)view) == 2 &&
        PyArray_DIMS((PyArrayObject *)view)[0] == 2 && PyArray_DIMS((PyArrayObject *)view)[1] == 2 &&
        PyArray_STRIDES((PyArrayObject *)view)[0] == 2 * (npy_intp)sizeof(int) &&
        PyArray_STRIDES((PyArrayObject *)view)[1] == (npy_intp)sizeof(int) &&
        PyArray_TYPE((PyArrayObject *)view) == NPY_INT && ((int *)PyArray_DATA((PyArrayObject *)view))[0] == 9) score += 1;
    Py_XDECREF(view);
    PyArray_Descr *descr = PyArray_DescrNewFromType(NPY_DOUBLE);
    if (descr == NULL) { Py_DECREF(arr); return NULL; }
    PyObject *typed = PyArray_View((PyArrayObject *)arr, descr, NULL);
    if (typed == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10; }
    Py_XDECREF(typed);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_squeeze_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[3] = {1, 2, 1};
    PyObject *arr = PyArray_SimpleNew(3, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 4; data[1] = 5;
    PyObject *sq = PyArray_Squeeze((PyArrayObject *)arr);
    if (sq != NULL && sq != arr && PyArray_BASE((PyArrayObject *)sq) == arr &&
        PyArray_DATA((PyArrayObject *)sq) == PyArray_DATA((PyArrayObject *)arr) &&
        PyArray_NDIM((PyArrayObject *)sq) == 1 && PyArray_DIMS((PyArrayObject *)sq)[0] == 2 &&
        PyArray_STRIDES((PyArrayObject *)sq)[0] == (npy_intp)sizeof(int) &&
        ((int *)PyArray_DATA((PyArrayObject *)sq))[1] == 5) score += 1;
    Py_XDECREF(sq);
    npy_intp scalar_dims[1] = {1};
    PyObject *scalar = PyArray_SimpleNew(1, scalar_dims, NPY_INT);
    if (scalar == NULL) { Py_DECREF(arr); return NULL; }
    ((int *)PyArray_DATA((PyArrayObject *)scalar))[0] = 7;
    PyObject *scalar_sq = PyArray_Squeeze((PyArrayObject *)scalar);
    if (scalar_sq != NULL && PyArray_NDIM((PyArrayObject *)scalar_sq) == 0 &&
        PyArray_BASE((PyArrayObject *)scalar_sq) == scalar &&
        PyArray_DATA((PyArrayObject *)scalar_sq) == PyArray_DATA((PyArrayObject *)scalar) &&
        ((int *)PyArray_DATA((PyArrayObject *)scalar_sq))[0] == 7) score += 10;
    Py_XDECREF(scalar_sq);
    npy_intp full_dims[2] = {2, 3};
    PyObject *full = PyArray_SimpleNew(2, full_dims, NPY_INT);
    if (full == NULL) { Py_DECREF(scalar); Py_DECREF(arr); return NULL; }
    PyObject *same = PyArray_Squeeze((PyArrayObject *)full);
    if (same == full) score += 100;
    Py_XDECREF(same);
    Py_DECREF(full);
    Py_DECREF(scalar);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_transpose_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 1; data[1] = 2; data[2] = 3; data[3] = 4; data[4] = 5; data[5] = 6;
    PyObject *tr = PyArray_Transpose((PyArrayObject *)arr, NULL);
    if (tr != NULL && tr != arr && PyArray_BASE((PyArrayObject *)tr) == arr &&
        PyArray_DATA((PyArrayObject *)tr) == PyArray_DATA((PyArrayObject *)arr) &&
        PyArray_NDIM((PyArrayObject *)tr) == 2 && PyArray_DIMS((PyArrayObject *)tr)[0] == 3 &&
        PyArray_DIMS((PyArrayObject *)tr)[1] == 2 &&
        PyArray_STRIDES((PyArrayObject *)tr)[0] == (npy_intp)sizeof(int) &&
        PyArray_STRIDES((PyArrayObject *)tr)[1] == 3 * (npy_intp)sizeof(int) &&
        ((int *)PyArray_DATA((PyArrayObject *)tr))[0] == 1) score += 1;
    Py_XDECREF(tr);
    npy_intp identity_axes[2] = {0, 1};
    PyArray_Dims identity = {identity_axes, 2};
    PyObject *same_order = PyArray_Transpose((PyArrayObject *)arr, &identity);
    if (same_order != NULL && same_order != arr && PyArray_BASE((PyArrayObject *)same_order) == arr &&
        PyArray_DIMS((PyArrayObject *)same_order)[0] == 2 && PyArray_DIMS((PyArrayObject *)same_order)[1] == 3 &&
        PyArray_STRIDES((PyArrayObject *)same_order)[0] == 3 * (npy_intp)sizeof(int) &&
        PyArray_STRIDES((PyArrayObject *)same_order)[1] == (npy_intp)sizeof(int)) score += 10;
    Py_XDECREF(same_order);
    npy_intp repeated_axes[2] = {0, 0};
    PyArray_Dims repeated = {repeated_axes, 2};
    PyObject *bad = PyArray_Transpose((PyArrayObject *)arr, &repeated);
    if (bad == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(bad);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_swap_axes_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    if (data == NULL) { Py_DECREF(arr); return NULL; }
    data[0] = 1; data[1] = 2; data[2] = 3; data[3] = 4; data[4] = 5; data[5] = 6;
    PyObject *swapped = PyArray_SwapAxes((PyArrayObject *)arr, 0, 1);
    if (swapped != NULL && swapped != arr && PyArray_BASE((PyArrayObject *)swapped) == arr &&
        PyArray_DATA((PyArrayObject *)swapped) == data && PyArray_NDIM((PyArrayObject *)swapped) == 2 &&
        PyArray_DIMS((PyArrayObject *)swapped)[0] == 3 && PyArray_DIMS((PyArrayObject *)swapped)[1] == 2 &&
        PyArray_STRIDES((PyArrayObject *)swapped)[0] == (npy_intp)sizeof(int) &&
        PyArray_STRIDES((PyArrayObject *)swapped)[1] == 3 * (npy_intp)sizeof(int)) score += 1;
    Py_XDECREF(swapped);
    swapped = PyArray_SwapAxes((PyArrayObject *)arr, -1, 0);
    if (swapped != NULL && PyArray_DIMS((PyArrayObject *)swapped)[0] == 3 &&
        PyArray_DIMS((PyArrayObject *)swapped)[1] == 2 && PyArray_BASE((PyArrayObject *)swapped) == arr) score += 10;
    Py_XDECREF(swapped);
    swapped = PyArray_SwapAxes((PyArrayObject *)arr, 1, 1);
    if (swapped != NULL && swapped != arr && PyArray_BASE((PyArrayObject *)swapped) == arr &&
        PyArray_DIMS((PyArrayObject *)swapped)[0] == 2 && PyArray_DIMS((PyArrayObject *)swapped)[1] == 3 &&
        PyArray_STRIDES((PyArrayObject *)swapped)[0] == 3 * (npy_intp)sizeof(int) &&
        PyArray_STRIDES((PyArrayObject *)swapped)[1] == (npy_intp)sizeof(int)) score += 100;
    Py_XDECREF(swapped);
    npy_intp dims3[3] = {2, 3, 4};
    PyObject *arr3 = PyArray_SimpleNew(3, dims3, NPY_INT);
    if (arr3 == NULL) { Py_DECREF(arr); return NULL; }
    swapped = PyArray_SwapAxes((PyArrayObject *)arr3, 0, 2);
    if (swapped != NULL && PyArray_NDIM((PyArrayObject *)swapped) == 3 &&
        PyArray_DIMS((PyArrayObject *)swapped)[0] == 4 && PyArray_DIMS((PyArrayObject *)swapped)[1] == 3 && PyArray_DIMS((PyArrayObject *)swapped)[2] == 2 &&
        PyArray_STRIDES((PyArrayObject *)swapped)[0] == (npy_intp)sizeof(int) &&
        PyArray_STRIDES((PyArrayObject *)swapped)[1] == 4 * (npy_intp)sizeof(int) &&
        PyArray_STRIDES((PyArrayObject *)swapped)[2] == 12 * (npy_intp)sizeof(int) &&
        PyArray_BASE((PyArrayObject *)swapped) == arr3) score += 1000;
    Py_XDECREF(swapped);
    swapped = PyArray_SwapAxes((PyArrayObject *)arr, 2, 0);
    if (swapped == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(swapped);
    swapped = PyArray_SwapAxes((PyArrayObject *)Py_None, 0, 1);
    if (swapped == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(swapped);
    PyObject *tr = PyArray_Transpose((PyArrayObject *)arr, NULL);
    if (tr == NULL) { Py_DECREF(arr3); Py_DECREF(arr); return NULL; }
    swapped = PyArray_SwapAxes((PyArrayObject *)tr, 0, 1);
    if (swapped == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(swapped);
    Py_DECREF(tr);
    Py_DECREF(arr3);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_ravel_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 1; data[1] = 2; data[2] = 3; data[3] = 4; data[4] = 5; data[5] = 6;
    PyObject *flat = PyArray_Ravel((PyArrayObject *)arr, NPY_CORDER);
    if (flat != NULL && flat != arr && PyArray_BASE((PyArrayObject *)flat) == arr &&
        PyArray_DATA((PyArrayObject *)flat) == PyArray_DATA((PyArrayObject *)arr) &&
        PyArray_NDIM((PyArrayObject *)flat) == 1 && PyArray_DIMS((PyArrayObject *)flat)[0] == 6 &&
        PyArray_STRIDES((PyArrayObject *)flat)[0] == (npy_intp)sizeof(int) &&
        ((int *)PyArray_DATA((PyArrayObject *)flat))[5] == 6) score += 1;
    Py_XDECREF(flat);
    PyObject *any = PyArray_Ravel((PyArrayObject *)arr, NPY_ANYORDER);
    if (any != NULL && PyArray_BASE((PyArrayObject *)any) == arr &&
        PyArray_NDIM((PyArrayObject *)any) == 1 && PyArray_DIMS((PyArrayObject *)any)[0] == 6 &&
        PyArray_STRIDES((PyArrayObject *)any)[0] == (npy_intp)sizeof(int)) score += 10;
    Py_XDECREF(any);
    PyObject *bad = PyArray_Ravel((PyArrayObject *)arr, NPY_FORTRANORDER);
    if (bad == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(bad);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_flatten_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 1; data[1] = 2; data[2] = 3; data[3] = 4; data[4] = 5; data[5] = 6;
    PyObject *flat = PyArray_Flatten((PyArrayObject *)arr, NPY_CORDER);
    if (flat != NULL && flat != arr && PyArray_BASE((PyArrayObject *)flat) == NULL &&
        PyArray_DATA((PyArrayObject *)flat) != PyArray_DATA((PyArrayObject *)arr) &&
        PyArray_NDIM((PyArrayObject *)flat) == 1 && PyArray_DIMS((PyArrayObject *)flat)[0] == 6 &&
        PyArray_STRIDES((PyArrayObject *)flat)[0] == (npy_intp)sizeof(int) &&
        ((int *)PyArray_DATA((PyArrayObject *)flat))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)flat))[5] == 6) score += 1;
    data[0] = 99;
    if (flat != NULL && ((int *)PyArray_DATA((PyArrayObject *)flat))[0] == 1) score += 10;
    Py_XDECREF(flat);
    PyObject *any = PyArray_Flatten((PyArrayObject *)arr, NPY_ANYORDER);
    if (any != NULL && PyArray_BASE((PyArrayObject *)any) == NULL &&
        PyArray_NDIM((PyArrayObject *)any) == 1 && PyArray_DIMS((PyArrayObject *)any)[0] == 6 &&
        ((int *)PyArray_DATA((PyArrayObject *)any))[0] == 99) score += 100;
    Py_XDECREF(any);
    PyObject *bad = PyArray_Flatten((PyArrayObject *)arr, NPY_FORTRANORDER);
    if (bad == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(bad);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_take_from_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {4};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 10; data[1] = 20; data[2] = 30; data[3] = 40;
    npy_intp idx_dims[1] = {3};
    PyObject *idx = PyArray_SimpleNew(1, idx_dims, NPY_LONG);
    if (idx == NULL) { Py_DECREF(arr); return NULL; }
    long *idx_data = (long *)PyArray_DATA((PyArrayObject *)idx);
    idx_data[0] = 2; idx_data[1] = 0; idx_data[2] = -1;
    PyObject *taken = PyArray_TakeFrom((PyArrayObject *)arr, idx, 0, NULL, NPY_RAISE);
    if (taken != NULL && taken != arr && PyArray_BASE((PyArrayObject *)taken) == NULL &&
        PyArray_DATA((PyArrayObject *)taken) != PyArray_DATA((PyArrayObject *)arr) &&
        PyArray_NDIM((PyArrayObject *)taken) == 1 && PyArray_DIMS((PyArrayObject *)taken)[0] == 3 &&
        PyArray_TYPE((PyArrayObject *)taken) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)taken))[0] == 30 &&
        ((int *)PyArray_DATA((PyArrayObject *)taken))[1] == 10 &&
        ((int *)PyArray_DATA((PyArrayObject *)taken))[2] == 40) score += 1;
    data[2] = 99;
    if (taken != NULL && ((int *)PyArray_DATA((PyArrayObject *)taken))[0] == 30) score += 10;
    Py_XDECREF(taken);
    idx_data[0] = 4; idx_data[1] = 0; idx_data[2] = 1;
    PyObject *bad = PyArray_TakeFrom((PyArrayObject *)arr, idx, 0, NULL, NPY_RAISE);
    if (bad == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(bad);
    idx_data[0] = 1; idx_data[1] = 2; idx_data[2] = 3;
    PyObject *clip = PyArray_TakeFrom((PyArrayObject *)arr, idx, 0, NULL, NPY_CLIP);
    if (clip == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(clip);
    Py_DECREF(idx);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_put_to_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {4};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 10; data[1] = 20; data[2] = 30; data[3] = 40;
    npy_intp idx_dims[1] = {3};
    PyObject *idx = PyArray_SimpleNew(1, idx_dims, NPY_LONG);
    PyObject *vals = PyArray_SimpleNew(1, idx_dims, NPY_INT);
    if (idx == NULL || vals == NULL) {
        Py_DECREF(arr);
        Py_XDECREF(idx);
        Py_XDECREF(vals);
        return NULL;
    }
    long *idx_data = (long *)PyArray_DATA((PyArrayObject *)idx);
    int *val_data = (int *)PyArray_DATA((PyArrayObject *)vals);
    idx_data[0] = 2; idx_data[1] = 0; idx_data[2] = -1;
    val_data[0] = 300; val_data[1] = 100; val_data[2] = 400;
    PyObject *ret = PyArray_PutTo((PyArrayObject *)arr, vals, idx, NPY_RAISE);
    if (ret == Py_None && data[0] == 100 && data[1] == 20 && data[2] == 300 && data[3] == 400) score += 1;
    Py_XDECREF(ret);
    PyObject *scalar = PyLong_FromLong(7);
    if (scalar == NULL) {
        Py_DECREF(vals);
        Py_DECREF(idx);
        Py_DECREF(arr);
        return NULL;
    }
    ret = PyArray_PutTo((PyArrayObject *)arr, scalar, idx, NPY_RAISE);
    if (ret == Py_None && data[0] == 7 && data[2] == 7 && data[3] == 7) score += 10;
    Py_XDECREF(ret);
    Py_DECREF(scalar);
    idx_data[0] = 4; idx_data[1] = 0; idx_data[2] = 1;
    ret = PyArray_PutTo((PyArrayObject *)arr, vals, idx, NPY_RAISE);
    if (ret == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(ret);
    idx_data[0] = 1; idx_data[1] = 2; idx_data[2] = 3;
    ret = PyArray_PutTo((PyArrayObject *)arr, vals, idx, NPY_CLIP);
    if (ret == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(ret);
    Py_DECREF(vals);
    Py_DECREF(idx);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_put_mask_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {5};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *mask = PyArray_SimpleNew(1, dims, NPY_BOOL);
    npy_intp val_dims[1] = {2};
    PyObject *vals = PyArray_SimpleNew(1, val_dims, NPY_INT);
    if (arr == NULL || mask == NULL || vals == NULL) {
        Py_XDECREF(arr);
        Py_XDECREF(mask);
        Py_XDECREF(vals);
        return NULL;
    }
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    unsigned char *mask_data = (unsigned char *)PyArray_DATA((PyArrayObject *)mask);
    int *val_data = (int *)PyArray_DATA((PyArrayObject *)vals);
    data[0] = 1; data[1] = 2; data[2] = 3; data[3] = 4; data[4] = 5;
    mask_data[0] = 1; mask_data[1] = 1; mask_data[2] = 1; mask_data[3] = 0; mask_data[4] = 1;
    val_data[0] = 10; val_data[1] = 20;
    PyObject *ret = PyArray_PutMask((PyArrayObject *)arr, vals, mask);
    if (ret == Py_None && data[0] == 10 && data[1] == 20 && data[2] == 10 && data[3] == 4 && data[4] == 10) score += 1;
    Py_XDECREF(ret);
    mask_data[0] = 0; mask_data[1] = 1; mask_data[2] = 0; mask_data[3] = 1; mask_data[4] = 0;
    PyObject *scalar = PyLong_FromLong(7);
    if (scalar == NULL) {
        Py_DECREF(vals);
        Py_DECREF(mask);
        Py_DECREF(arr);
        return NULL;
    }
    ret = PyArray_PutMask((PyArrayObject *)arr, scalar, mask);
    if (ret == Py_None && data[0] == 10 && data[1] == 7 && data[2] == 10 && data[3] == 7 && data[4] == 10) score += 10;
    Py_XDECREF(ret);
    Py_DECREF(scalar);
    npy_intp bad_dims[1] = {4};
    PyObject *bad_mask = PyArray_SimpleNew(1, bad_dims, NPY_BOOL);
    ret = PyArray_PutMask((PyArrayObject *)arr, vals, bad_mask);
    if (ret == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(ret);
    Py_XDECREF(bad_mask);
    Py_DECREF(vals);
    Py_DECREF(mask);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_repeat_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *reps = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (arr == NULL || reps == NULL) {
        Py_XDECREF(arr);
        Py_XDECREF(reps);
        return NULL;
    }
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    long *rep_data = (long *)PyArray_DATA((PyArrayObject *)reps);
    data[0] = 3; data[1] = 4; data[2] = 5;
    rep_data[0] = 1; rep_data[1] = 2; rep_data[2] = 0;
    PyObject *out = PyArray_Repeat((PyArrayObject *)arr, reps, 0);
    if (out != NULL && out != arr && PyArray_BASE((PyArrayObject *)out) == NULL &&
        PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 3 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 4 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 4) score += 1;
    data[1] = 99;
    if (out != NULL && ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 4) score += 10;
    Py_XDECREF(out);
    PyObject *scalar = PyLong_FromLong(2);
    if (scalar == NULL) {
        Py_DECREF(reps);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Repeat((PyArrayObject *)arr, scalar, -1);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 6 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 99 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 99 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[4] == 5 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[5] == 5) score += 100;
    Py_XDECREF(out);
    Py_DECREF(scalar);
    rep_data[0] = -1; rep_data[1] = 1; rep_data[2] = 1;
    out = PyArray_Repeat((PyArrayObject *)arr, reps, 0);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(out);
    Py_DECREF(reps);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_choose_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {4};
    PyObject *idx = PyArray_SimpleNew(1, dims, NPY_LONG);
    PyObject *left = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *right = PyArray_SimpleNew(1, dims, NPY_INT);
    if (idx == NULL || left == NULL || right == NULL) {
        Py_XDECREF(idx);
        Py_XDECREF(left);
        Py_XDECREF(right);
        return NULL;
    }
    long *idx_data = (long *)PyArray_DATA((PyArrayObject *)idx);
    int *left_data = (int *)PyArray_DATA((PyArrayObject *)left);
    int *right_data = (int *)PyArray_DATA((PyArrayObject *)right);
    idx_data[0] = 0; idx_data[1] = 1; idx_data[2] = 0; idx_data[3] = 1;
    left_data[0] = 10; left_data[1] = 20; left_data[2] = 30; left_data[3] = 40;
    right_data[0] = 100; right_data[1] = 200; right_data[2] = 300; right_data[3] = 400;
    PyObject *choices = PyTuple_Pack(2, left, right);
    if (choices == NULL) {
        Py_DECREF(right);
        Py_DECREF(left);
        Py_DECREF(idx);
        return NULL;
    }
    PyObject *out = PyArray_Choose((PyArrayObject *)idx, choices, NULL, NPY_RAISE);
    if (out != NULL && out != left && out != right && PyArray_BASE((PyArrayObject *)out) == NULL &&
        PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 4 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 10 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 200 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 30 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 400) score += 1;
    right_data[1] = 999;
    if (out != NULL && ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 200) score += 10;
    Py_XDECREF(out);
    idx_data[1] = 2;
    out = PyArray_Choose((PyArrayObject *)idx, choices, NULL, NPY_RAISE);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(out);
    idx_data[1] = 1;
    out = PyArray_Choose((PyArrayObject *)idx, choices, NULL, NPY_CLIP);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(out);
    Py_DECREF(choices);
    Py_DECREF(right);
    Py_DECREF(left);
    Py_DECREF(idx);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_concatenate_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2[1] = {2};
    npy_intp dims3[1] = {3};
    PyObject *left = PyArray_SimpleNew(1, dims2, NPY_INT);
    PyObject *right = PyArray_SimpleNew(1, dims3, NPY_INT);
    if (left == NULL || right == NULL) {
        Py_XDECREF(left);
        Py_XDECREF(right);
        return NULL;
    }
    int *left_data = (int *)PyArray_DATA((PyArrayObject *)left);
    int *right_data = (int *)PyArray_DATA((PyArrayObject *)right);
    left_data[0] = 1; left_data[1] = 2;
    right_data[0] = 3; right_data[1] = 4; right_data[2] = 5;
    PyObject *seq = PyTuple_Pack(2, left, right);
    if (seq == NULL) {
        Py_DECREF(right);
        Py_DECREF(left);
        return NULL;
    }
    PyObject *out = PyArray_Concatenate(seq, 0);
    if (out != NULL && out != left && out != right && PyArray_BASE((PyArrayObject *)out) == NULL &&
        PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 5 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 2 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 4 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[4] == 5) score += 1;
    left_data[1] = 99;
    right_data[0] = 88;
    if (out != NULL &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 2 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 3) score += 10;
    Py_XDECREF(out);
    Py_DECREF(seq);
    Py_DECREF(right);
    Py_DECREF(left);

    npy_intp row_dims[2] = {1, 2};
    npy_intp matrix_dims[2] = {2, 2};
    PyObject *top = PyArray_SimpleNew(2, row_dims, NPY_LONG);
    PyObject *bottom = PyArray_SimpleNew(2, matrix_dims, NPY_LONG);
    if (top == NULL || bottom == NULL) {
        Py_XDECREF(top);
        Py_XDECREF(bottom);
        return NULL;
    }
    long *top_data = (long *)PyArray_DATA((PyArrayObject *)top);
    long *bottom_data = (long *)PyArray_DATA((PyArrayObject *)bottom);
    top_data[0] = 1; top_data[1] = 2;
    bottom_data[0] = 3; bottom_data[1] = 4; bottom_data[2] = 5; bottom_data[3] = 6;
    seq = PyTuple_Pack(2, top, bottom);
    if (seq == NULL) {
        Py_DECREF(bottom);
        Py_DECREF(top);
        return NULL;
    }
    out = PyArray_Concatenate(seq, 0);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 2 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 3 && PyArray_DIMS((PyArrayObject *)out)[1] == 2 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[0] == 1 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[1] == 2 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[2] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[3] == 4 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[4] == 5 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[5] == 6) score += 100;
    Py_XDECREF(out);
    Py_DECREF(seq);
    Py_DECREF(bottom);
    Py_DECREF(top);

    npy_intp wide_dims[2] = {2, 2};
    npy_intp col_dims[2] = {2, 1};
    PyObject *wide = PyArray_SimpleNew(2, wide_dims, NPY_INT);
    PyObject *col = PyArray_SimpleNew(2, col_dims, NPY_INT);
    if (wide == NULL || col == NULL) {
        Py_XDECREF(wide);
        Py_XDECREF(col);
        return NULL;
    }
    int *wide_data = (int *)PyArray_DATA((PyArrayObject *)wide);
    int *col_data = (int *)PyArray_DATA((PyArrayObject *)col);
    wide_data[0] = 1; wide_data[1] = 2; wide_data[2] = 3; wide_data[3] = 4;
    col_data[0] = 10; col_data[1] = 20;
    seq = PyTuple_Pack(2, wide, col);
    if (seq == NULL) {
        Py_DECREF(col);
        Py_DECREF(wide);
        return NULL;
    }
    out = PyArray_Concatenate(seq, 1);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 2 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_DIMS((PyArrayObject *)out)[1] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 2 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 10 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[4] == 4 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[5] == 20) score += 1000;
    Py_XDECREF(out);
    out = PyArray_Concatenate(seq, -1);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 2 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_DIMS((PyArrayObject *)out)[1] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 10 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[5] == 20) score += 10000;
    Py_XDECREF(out);
    out = PyArray_Concatenate(seq, 0);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_DECREF(seq);

    PyObject *double_arr = PyArray_SimpleNew(2, wide_dims, NPY_DOUBLE);
    seq = PyTuple_Pack(2, wide, double_arr);
    out = PyArray_Concatenate(seq, 0);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(out);
    Py_XDECREF(seq);
    Py_XDECREF(double_arr);
    PyObject *empty = PyTuple_New(0);
    out = PyArray_Concatenate(empty, 0);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(out);
    Py_XDECREF(empty);
    Py_DECREF(col);
    Py_DECREF(wide);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_arange_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *out = PyArray_Arange(1.0, 6.0, 2.0, NPY_INT);
    if (out != NULL && PyArray_BASE((PyArrayObject *)out) == NULL &&
        PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 3 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 5) score += 1;
    Py_XDECREF(out);
    out = PyArray_Arange(0.0, 1.0, 0.25, NPY_DOUBLE);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 4 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_DOUBLE &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[0] == 0.0 &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[1] == 0.25 &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[2] == 0.5 &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[3] == 0.75) score += 10;
    Py_XDECREF(out);
    out = PyArray_Arange(5.0, 1.0, -2.0, NPY_LONG);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 2 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_LONG &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[0] == 5 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[1] == 3) score += 100;
    Py_XDECREF(out);
    out = PyArray_Arange(5.0, 1.0, 1.0, NPY_INT);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 0 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT) score += 1000;
    Py_XDECREF(out);
    out = PyArray_Arange(0.0, 1.0, 0.0, NPY_INT);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    out = PyArray_Arange(0.0, 2.0, 1.0, NPY_OBJECT);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_arange_obj_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *start = PyLong_FromLong(2);
    PyObject *stop = PyLong_FromLong(7);
    PyObject *step = PyLong_FromLong(2);
    if (start == NULL || stop == NULL || step == NULL) {
        Py_XDECREF(start);
        Py_XDECREF(stop);
        Py_XDECREF(step);
        return NULL;
    }
    PyObject *out = PyArray_ArangeObj(start, stop, step, PyArray_DescrFromType(NPY_INT));
    if (out != NULL && PyArray_BASE((PyArrayObject *)out) == NULL &&
        PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 3 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 2 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 4 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 6) score += 1;
    Py_XDECREF(out);
    Py_DECREF(step);
    Py_DECREF(stop);
    Py_DECREF(start);

    start = PyLong_FromLong(2);
    stop = PyLong_FromLong(5);
    if (start == NULL || stop == NULL) {
        Py_XDECREF(start);
        Py_XDECREF(stop);
        return NULL;
    }
    out = PyArray_ArangeObj(start, stop, NULL, PyArray_DescrFromType(NPY_LONG));
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 3 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_LONG &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[0] == 2 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[1] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[2] == 4) score += 10;
    Py_XDECREF(out);
    Py_DECREF(stop);
    Py_DECREF(start);

    start = PyFloat_FromDouble(0.0);
    stop = PyFloat_FromDouble(1.0);
    step = PyFloat_FromDouble(0.5);
    if (start == NULL || stop == NULL || step == NULL) {
        Py_XDECREF(start);
        Py_XDECREF(stop);
        Py_XDECREF(step);
        return NULL;
    }
    out = PyArray_ArangeObj(start, stop, step, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 2 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_DOUBLE &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[0] == 0.0 &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[1] == 0.5) score += 100;
    Py_XDECREF(out);
    Py_DECREF(step);
    Py_DECREF(stop);
    Py_DECREF(start);

    start = PyLong_FromLong(5);
    stop = PyLong_FromLong(1);
    step = PyLong_FromLong(-2);
    if (start == NULL || stop == NULL || step == NULL) {
        Py_XDECREF(start);
        Py_XDECREF(stop);
        Py_XDECREF(step);
        return NULL;
    }
    out = PyArray_ArangeObj(start, stop, step, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 2 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_LONG &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[0] == 5 &&
        ((long *)PyArray_DATA((PyArrayObject *)out))[1] == 3) score += 1000;
    Py_XDECREF(out);
    Py_DECREF(step);
    Py_DECREF(stop);
    Py_DECREF(start);

    start = PyLong_FromLong(0);
    stop = PyLong_FromLong(3);
    step = PyLong_FromLong(0);
    if (start == NULL || stop == NULL || step == NULL) {
        Py_XDECREF(start);
        Py_XDECREF(stop);
        Py_XDECREF(step);
        return NULL;
    }
    out = PyArray_ArangeObj(start, stop, step, NULL);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    Py_DECREF(step);
    Py_DECREF(stop);
    Py_DECREF(start);

    start = PyLong_FromLong(0);
    stop = PyLong_FromLong(2);
    if (start == NULL || stop == NULL) {
        Py_XDECREF(start);
        Py_XDECREF(stop);
        return NULL;
    }
    out = PyArray_ArangeObj(start, stop, NULL, PyArray_DescrFromType(NPY_OBJECT));
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_DECREF(stop);
    Py_DECREF(start);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_inner_product_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims3[1] = {3};
    PyObject *lhs = PyArray_SimpleNew(1, dims3, NPY_INT);
    PyObject *rhs = PyArray_SimpleNew(1, dims3, NPY_INT);
    if (lhs == NULL || rhs == NULL) {
        Py_XDECREF(lhs);
        Py_XDECREF(rhs);
        return NULL;
    }
    int *lhs_i = (int *)PyArray_DATA((PyArrayObject *)lhs);
    int *rhs_i = (int *)PyArray_DATA((PyArrayObject *)rhs);
    lhs_i[0] = 1; lhs_i[1] = 2; lhs_i[2] = 3;
    rhs_i[0] = 4; rhs_i[1] = 5; rhs_i[2] = 6;
    PyObject *out = PyArray_InnerProduct(lhs, rhs);
    if (out != NULL && PyLong_AsLong(out) == 32) score += 1;
    Py_XDECREF(out);

    PyObject *dlhs = PyArray_SimpleNew(1, dims3, NPY_DOUBLE);
    PyObject *drhs = PyArray_SimpleNew(1, dims3, NPY_DOUBLE);
    if (dlhs == NULL || drhs == NULL) {
        Py_XDECREF(dlhs);
        Py_XDECREF(drhs);
        Py_DECREF(rhs);
        Py_DECREF(lhs);
        return NULL;
    }
    double *lhs_d = (double *)PyArray_DATA((PyArrayObject *)dlhs);
    double *rhs_d = (double *)PyArray_DATA((PyArrayObject *)drhs);
    lhs_d[0] = 1.5; lhs_d[1] = 2.0; lhs_d[2] = -1.0;
    rhs_d[0] = 2.0; rhs_d[1] = 3.0; rhs_d[2] = 4.0;
    out = PyArray_InnerProduct(dlhs, drhs);
    if (out != NULL && PyFloat_AsDouble(out) > 4.99 && PyFloat_AsDouble(out) < 5.01) score += 10;
    Py_XDECREF(out);

    npy_intp dims2[1] = {2};
    PyObject *short_arr = PyArray_SimpleNew(1, dims2, NPY_INT);
    out = short_arr != NULL ? PyArray_InnerProduct(lhs, short_arr) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(out);
    Py_XDECREF(short_arr);

    npy_intp dims2d[2] = {1, 3};
    PyObject *matrix = PyArray_SimpleNew(2, dims2d, NPY_INT);
    out = matrix != NULL ? PyArray_InnerProduct(matrix, rhs) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(out);
    Py_XDECREF(matrix);

    PyObject *objarr = PyArray_SimpleNew(1, dims3, NPY_OBJECT);
    out = objarr != NULL ? PyArray_InnerProduct(objarr, rhs) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    Py_XDECREF(objarr);

    PyObject *carr = PyArray_SimpleNew(1, dims3, NPY_CDOUBLE);
    out = carr != NULL ? PyArray_InnerProduct(carr, rhs) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_XDECREF(carr);

    Py_DECREF(drhs);
    Py_DECREF(dlhs);
    Py_DECREF(rhs);
    Py_DECREF(lhs);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_matrix_product_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims3[1] = {3};
    PyObject *vec_lhs = PyArray_SimpleNew(1, dims3, NPY_INT);
    PyObject *vec_rhs = PyArray_SimpleNew(1, dims3, NPY_INT);
    if (vec_lhs == NULL || vec_rhs == NULL) {
        Py_XDECREF(vec_lhs);
        Py_XDECREF(vec_rhs);
        return NULL;
    }
    int *vec_lhs_i = (int *)PyArray_DATA((PyArrayObject *)vec_lhs);
    int *vec_rhs_i = (int *)PyArray_DATA((PyArrayObject *)vec_rhs);
    vec_lhs_i[0] = 1; vec_lhs_i[1] = 2; vec_lhs_i[2] = 3;
    vec_rhs_i[0] = 4; vec_rhs_i[1] = 5; vec_rhs_i[2] = 6;
    PyObject *out = PyArray_MatrixProduct(vec_lhs, vec_rhs);
    if (out != NULL && PyLong_AsLong(out) == 32) score += 1;
    Py_XDECREF(out);

    npy_intp lhs_dims[2] = {2, 3};
    npy_intp rhs_dims[2] = {3, 2};
    PyObject *mat_lhs = PyArray_SimpleNew(2, lhs_dims, NPY_INT);
    PyObject *mat_rhs = PyArray_SimpleNew(2, rhs_dims, NPY_INT);
    if (mat_lhs == NULL || mat_rhs == NULL) {
        Py_XDECREF(mat_lhs);
        Py_XDECREF(mat_rhs);
        Py_DECREF(vec_rhs);
        Py_DECREF(vec_lhs);
        return NULL;
    }
    int *mat_lhs_i = (int *)PyArray_DATA((PyArrayObject *)mat_lhs);
    int *mat_rhs_i = (int *)PyArray_DATA((PyArrayObject *)mat_rhs);
    for (int i = 0; i < 6; i++) { mat_lhs_i[i] = i + 1; mat_rhs_i[i] = i + 7; }
    out = PyArray_MatrixProduct(mat_lhs, mat_rhs);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 2 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_DIMS((PyArrayObject *)out)[1] == 2 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *prod = (long *)PyArray_DATA((PyArrayObject *)out);
        if (prod[0] == 58 && prod[1] == 64 && prod[2] == 139 && prod[3] == 154) score += 10;
    }
    Py_XDECREF(out);

    out = PyArray_MatrixProduct(vec_lhs, mat_rhs);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *prod = (long *)PyArray_DATA((PyArrayObject *)out);
        if (prod[0] == 58 && prod[1] == 64) score += 1000000;
    }
    Py_XDECREF(out);

    out = PyArray_MatrixProduct(mat_lhs, vec_rhs);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *prod = (long *)PyArray_DATA((PyArrayObject *)out);
        if (prod[0] == 32 && prod[1] == 77) score += 10000000;
    }
    Py_XDECREF(out);

    PyObject *mat_rhs_d = PyArray_SimpleNew(2, rhs_dims, NPY_DOUBLE);
    if (mat_rhs_d == NULL) {
        Py_DECREF(mat_rhs);
        Py_DECREF(mat_lhs);
        Py_DECREF(vec_rhs);
        Py_DECREF(vec_lhs);
        return NULL;
    }
    double *mat_rhs_double = (double *)PyArray_DATA((PyArrayObject *)mat_rhs_d);
    mat_rhs_double[0] = 0.5; mat_rhs_double[1] = 1.0; mat_rhs_double[2] = 1.5;
    mat_rhs_double[3] = 2.0; mat_rhs_double[4] = 2.5; mat_rhs_double[5] = 3.0;
    out = PyArray_MatrixProduct(mat_lhs, mat_rhs_d);
    if (out != NULL && PyArray_TYPE((PyArrayObject *)out) == NPY_DOUBLE) {
        double *prod_d = (double *)PyArray_DATA((PyArrayObject *)out);
        if (prod_d[0] > 10.9 && prod_d[0] < 11.1 && prod_d[3] > 31.9 && prod_d[3] < 32.1) score += 100;
    }
    Py_XDECREF(out);

    npy_intp bad_rhs_dims[2] = {2, 2};
    PyObject *bad_rhs = PyArray_SimpleNew(2, bad_rhs_dims, NPY_INT);
    out = bad_rhs != NULL ? PyArray_MatrixProduct(mat_lhs, bad_rhs) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(out);
    Py_XDECREF(bad_rhs);

    npy_intp dims3d[3] = {1, 1, 3};
    PyObject *tensor = PyArray_SimpleNew(3, dims3d, NPY_INT);
    out = tensor != NULL ? PyArray_MatrixProduct(tensor, vec_rhs) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    Py_XDECREF(tensor);

    PyObject *objarr = PyArray_SimpleNew(2, lhs_dims, NPY_OBJECT);
    out = objarr != NULL ? PyArray_MatrixProduct(objarr, mat_rhs) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_XDECREF(objarr);

    Py_DECREF(mat_rhs_d);
    Py_DECREF(mat_rhs);
    Py_DECREF(mat_lhs);
    Py_DECREF(vec_rhs);
    Py_DECREF(vec_lhs);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_matrix_product2_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims3[1] = {3};
    PyObject *vec_lhs = PyArray_SimpleNew(1, dims3, NPY_INT);
    PyObject *vec_rhs = PyArray_SimpleNew(1, dims3, NPY_INT);
    if (vec_lhs == NULL || vec_rhs == NULL) {
        Py_XDECREF(vec_lhs);
        Py_XDECREF(vec_rhs);
        return NULL;
    }
    int *vec_lhs_i = (int *)PyArray_DATA((PyArrayObject *)vec_lhs);
    int *vec_rhs_i = (int *)PyArray_DATA((PyArrayObject *)vec_rhs);
    vec_lhs_i[0] = 1; vec_lhs_i[1] = 2; vec_lhs_i[2] = 3;
    vec_rhs_i[0] = 4; vec_rhs_i[1] = 5; vec_rhs_i[2] = 6;
    PyObject *out = PyArray_MatrixProduct2(vec_lhs, vec_rhs, NULL);
    if (out != NULL && PyLong_AsLong(out) == 32) score += 1;
    Py_XDECREF(out);

    npy_intp lhs_dims[2] = {2, 3};
    npy_intp rhs_dims[2] = {3, 2};
    PyObject *mat_lhs = PyArray_SimpleNew(2, lhs_dims, NPY_INT);
    PyObject *mat_rhs = PyArray_SimpleNew(2, rhs_dims, NPY_INT);
    if (mat_lhs == NULL || mat_rhs == NULL) {
        Py_XDECREF(mat_lhs);
        Py_XDECREF(mat_rhs);
        Py_DECREF(vec_rhs);
        Py_DECREF(vec_lhs);
        return NULL;
    }
    int *mat_lhs_i = (int *)PyArray_DATA((PyArrayObject *)mat_lhs);
    int *mat_rhs_i = (int *)PyArray_DATA((PyArrayObject *)mat_rhs);
    for (int i = 0; i < 6; i++) { mat_lhs_i[i] = i + 1; mat_rhs_i[i] = i + 7; }
    out = PyArray_MatrixProduct2(mat_lhs, mat_rhs, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 2 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_DIMS((PyArrayObject *)out)[1] == 2 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *prod = (long *)PyArray_DATA((PyArrayObject *)out);
        if (prod[0] == 58 && prod[1] == 64 && prod[2] == 139 && prod[3] == 154) score += 10;
    }
    Py_XDECREF(out);

    out = PyArray_MatrixProduct2(vec_lhs, mat_rhs, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *prod = (long *)PyArray_DATA((PyArrayObject *)out);
        if (prod[0] == 58 && prod[1] == 64) score += 1000000;
    }
    Py_XDECREF(out);

    out = PyArray_MatrixProduct2(mat_lhs, vec_rhs, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *prod = (long *)PyArray_DATA((PyArrayObject *)out);
        if (prod[0] == 32 && prod[1] == 77) score += 10000000;
    }
    Py_XDECREF(out);

    PyObject *mat_rhs_d = PyArray_SimpleNew(2, rhs_dims, NPY_DOUBLE);
    if (mat_rhs_d == NULL) {
        Py_DECREF(mat_rhs);
        Py_DECREF(mat_lhs);
        Py_DECREF(vec_rhs);
        Py_DECREF(vec_lhs);
        return NULL;
    }
    double *mat_rhs_double = (double *)PyArray_DATA((PyArrayObject *)mat_rhs_d);
    mat_rhs_double[0] = 0.5; mat_rhs_double[1] = 1.0; mat_rhs_double[2] = 1.5;
    mat_rhs_double[3] = 2.0; mat_rhs_double[4] = 2.5; mat_rhs_double[5] = 3.0;
    out = PyArray_MatrixProduct2(mat_lhs, mat_rhs_d, NULL);
    if (out != NULL && PyArray_TYPE((PyArrayObject *)out) == NPY_DOUBLE) {
        double *prod_d = (double *)PyArray_DATA((PyArrayObject *)out);
        if (prod_d[0] > 10.9 && prod_d[0] < 11.1 && prod_d[3] > 31.9 && prod_d[3] < 32.1) score += 100;
    }
    Py_XDECREF(out);

    npy_intp out_dims[2] = {2, 2};
    PyObject *out_arr = PyArray_SimpleNew(2, out_dims, NPY_LONG);
    out = out_arr != NULL ? PyArray_MatrixProduct2(mat_lhs, mat_rhs, (PyArrayObject *)out_arr) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(out);
    Py_XDECREF(out_arr);

    npy_intp bad_rhs_dims[2] = {2, 2};
    PyObject *bad_rhs = PyArray_SimpleNew(2, bad_rhs_dims, NPY_INT);
    out = bad_rhs != NULL ? PyArray_MatrixProduct2(mat_lhs, bad_rhs, NULL) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    Py_XDECREF(bad_rhs);

    PyObject *objarr = PyArray_SimpleNew(2, lhs_dims, NPY_OBJECT);
    out = objarr != NULL ? PyArray_MatrixProduct2(objarr, mat_rhs, NULL) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_XDECREF(objarr);

    Py_DECREF(mat_rhs_d);
    Py_DECREF(mat_rhs);
    Py_DECREF(mat_lhs);
    Py_DECREF(vec_rhs);
    Py_DECREF(vec_lhs);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_einstein_sum_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims3[1] = {3};
    PyObject *vec_lhs = PyArray_SimpleNew(1, dims3, NPY_INT);
    PyObject *vec_rhs = PyArray_SimpleNew(1, dims3, NPY_INT);
    if (vec_lhs == NULL || vec_rhs == NULL) {
        Py_XDECREF(vec_lhs);
        Py_XDECREF(vec_rhs);
        return NULL;
    }
    int *vec_lhs_i = (int *)PyArray_DATA((PyArrayObject *)vec_lhs);
    int *vec_rhs_i = (int *)PyArray_DATA((PyArrayObject *)vec_rhs);
    vec_lhs_i[0] = 1; vec_lhs_i[1] = 2; vec_lhs_i[2] = 3;
    vec_rhs_i[0] = 10; vec_rhs_i[1] = 20; vec_rhs_i[2] = 30;
    PyArrayObject *ops[2] = {(PyArrayObject *)vec_lhs, (PyArrayObject *)vec_rhs};
    PyObject *out = (PyObject *)PyArray_EinsteinSum("i,i->", 2, ops, NULL, NPY_KEEPORDER, NPY_SAFE_CASTING, NULL);
    if (out != NULL && PyLong_AsLong(out) == 140) score += 1;
    Py_XDECREF(out);

    npy_intp lhs_dims[2] = {2, 2};
    npy_intp rhs_dims[2] = {2, 2};
    PyObject *mat_lhs = PyArray_SimpleNew(2, lhs_dims, NPY_INT);
    PyObject *mat_rhs = PyArray_SimpleNew(2, rhs_dims, NPY_INT);
    if (mat_lhs == NULL || mat_rhs == NULL) {
        Py_XDECREF(mat_lhs);
        Py_XDECREF(mat_rhs);
        Py_DECREF(vec_rhs);
        Py_DECREF(vec_lhs);
        return NULL;
    }
    int *mat_lhs_i = (int *)PyArray_DATA((PyArrayObject *)mat_lhs);
    int *mat_rhs_i = (int *)PyArray_DATA((PyArrayObject *)mat_rhs);
    mat_lhs_i[0] = 1; mat_lhs_i[1] = 2; mat_lhs_i[2] = 3; mat_lhs_i[3] = 4;
    mat_rhs_i[0] = 5; mat_rhs_i[1] = 6; mat_rhs_i[2] = 7; mat_rhs_i[3] = 8;
    ops[0] = (PyArrayObject *)mat_lhs;
    ops[1] = (PyArrayObject *)mat_rhs;
    out = (PyObject *)PyArray_EinsteinSum("ij,jk->ik", 2, ops, NULL, NPY_KEEPORDER, NPY_SAFE_CASTING, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 2 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_DIMS((PyArrayObject *)out)[1] == 2 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *prod = (long *)PyArray_DATA((PyArrayObject *)out);
        if (prod[0] == 19 && prod[1] == 22 && prod[2] == 43 && prod[3] == 50) score += 10;
    }
    Py_XDECREF(out);

    npy_intp dims2[1] = {2};
    PyObject *vec2 = PyArray_SimpleNew(1, dims2, NPY_INT);
    if (vec2 == NULL) {
        Py_DECREF(mat_rhs);
        Py_DECREF(mat_lhs);
        Py_DECREF(vec_rhs);
        Py_DECREF(vec_lhs);
        return NULL;
    }
    int *vec2_i = (int *)PyArray_DATA((PyArrayObject *)vec2);
    vec2_i[0] = 1; vec2_i[1] = 2;
    ops[0] = (PyArrayObject *)vec2;
    ops[1] = (PyArrayObject *)mat_rhs;
    out = (PyObject *)PyArray_EinsteinSum("i,ij->j", 2, ops, NULL, NPY_KEEPORDER, NPY_SAFE_CASTING, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *prod = (long *)PyArray_DATA((PyArrayObject *)out);
        if (prod[0] == 19 && prod[1] == 22) score += 100000;
    }
    Py_XDECREF(out);

    ops[0] = (PyArrayObject *)mat_lhs;
    ops[1] = (PyArrayObject *)vec2;
    out = (PyObject *)PyArray_EinsteinSum("ij,j->i", 2, ops, NULL, NPY_KEEPORDER, NPY_SAFE_CASTING, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *prod = (long *)PyArray_DATA((PyArrayObject *)out);
        if (prod[0] == 5 && prod[1] == 11) score += 1000000;
    }
    Py_XDECREF(out);

    ops[0] = (PyArrayObject *)vec_lhs;
    out = (PyObject *)PyArray_EinsteinSum("i->", 1, ops, NULL, NPY_KEEPORDER, NPY_SAFE_CASTING, NULL);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(out);

    PyObject *out_arr = PyArray_SimpleNew(2, lhs_dims, NPY_LONG);
    ops[0] = (PyArrayObject *)mat_lhs;
    ops[1] = (PyArrayObject *)mat_rhs;
    out = out_arr != NULL ? (PyObject *)PyArray_EinsteinSum("ij,jk->ik", 2, ops, NULL, NPY_KEEPORDER, NPY_SAFE_CASTING, (PyArrayObject *)out_arr) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(out);
    Py_XDECREF(out_arr);

    PyArray_Descr *descr = PyArray_DescrFromType(NPY_DOUBLE);
    ops[0] = (PyArrayObject *)vec_lhs;
    ops[1] = (PyArrayObject *)vec_rhs;
    out = descr != NULL ? (PyObject *)PyArray_EinsteinSum("i,i->", 2, ops, descr, NPY_KEEPORDER, NPY_SAFE_CASTING, NULL) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    Py_XDECREF(descr);

    Py_DECREF(vec2);
    Py_DECREF(mat_rhs);
    Py_DECREF(mat_lhs);
    Py_DECREF(vec_rhs);
    Py_DECREF(vec_lhs);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_correlate_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims3[1] = {3};
    npy_intp dims2[1] = {2};
    PyObject *a = PyArray_SimpleNew(1, dims3, NPY_INT);
    PyObject *b = PyArray_SimpleNew(1, dims2, NPY_INT);
    if (a == NULL || b == NULL) {
        Py_XDECREF(a);
        Py_XDECREF(b);
        return NULL;
    }
    int *ai = (int *)PyArray_DATA((PyArrayObject *)a);
    int *bi = (int *)PyArray_DATA((PyArrayObject *)b);
    ai[0] = 1; ai[1] = 2; ai[2] = 3;
    bi[0] = 4; bi[1] = 5;
    PyObject *out = PyArray_Correlate(a, b, NPY_VALID);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *vals = (long *)PyArray_DATA((PyArrayObject *)out);
        if (vals[0] == 14 && vals[1] == 23) score += 1;
    }
    Py_XDECREF(out);

    out = PyArray_Correlate(a, b, NPY_SAME);
    if (out != NULL && PyArray_DIMS((PyArrayObject *)out)[0] == 3) {
        long *vals = (long *)PyArray_DATA((PyArrayObject *)out);
        if (vals[0] == 5 && vals[1] == 14 && vals[2] == 23) score += 10;
    }
    Py_XDECREF(out);

    out = PyArray_Correlate(a, b, NPY_FULL);
    if (out != NULL && PyArray_DIMS((PyArrayObject *)out)[0] == 4) {
        long *vals = (long *)PyArray_DATA((PyArrayObject *)out);
        if (vals[0] == 5 && vals[1] == 14 && vals[2] == 23 && vals[3] == 12) score += 100;
    }
    Py_XDECREF(out);

    PyObject *short_a = PyArray_SimpleNew(1, dims2, NPY_INT);
    if (short_a == NULL) {
        Py_DECREF(b);
        Py_DECREF(a);
        return NULL;
    }
    int *short_i = (int *)PyArray_DATA((PyArrayObject *)short_a);
    short_i[0] = 1; short_i[1] = 2;
    out = PyArray_Correlate(short_a, a, NPY_VALID);
    if (out != NULL && PyArray_DIMS((PyArrayObject *)out)[0] == 2) {
        long *vals = (long *)PyArray_DATA((PyArrayObject *)out);
        if (vals[0] == 5 && vals[1] == 8) score += 1000;
    }
    Py_XDECREF(out);

    PyObject *da = PyArray_SimpleNew(1, dims2, NPY_DOUBLE);
    PyObject *db = PyArray_SimpleNew(1, dims2, NPY_DOUBLE);
    if (da == NULL || db == NULL) {
        Py_XDECREF(da);
        Py_XDECREF(db);
        Py_DECREF(short_a);
        Py_DECREF(b);
        Py_DECREF(a);
        return NULL;
    }
    double *dai = (double *)PyArray_DATA((PyArrayObject *)da);
    double *dbi = (double *)PyArray_DATA((PyArrayObject *)db);
    dai[0] = 1.5; dai[1] = -2.0;
    dbi[0] = 2.0; dbi[1] = 0.5;
    out = PyArray_Correlate(da, db, NPY_FULL);
    if (out != NULL && PyArray_TYPE((PyArrayObject *)out) == NPY_DOUBLE && PyArray_DIMS((PyArrayObject *)out)[0] == 3) {
        double *vals = (double *)PyArray_DATA((PyArrayObject *)out);
        if (vals[0] > 0.74 && vals[0] < 0.76 && vals[1] > 1.99 && vals[1] < 2.01 && vals[2] > -4.01 && vals[2] < -3.99) score += 10000;
    }
    Py_XDECREF(out);

    npy_intp dim0[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, dim0, NPY_INT);
    out = empty != NULL ? PyArray_Correlate(empty, b, NPY_VALID) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_XDECREF(empty);

    out = PyArray_Correlate(a, b, 99);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(out);

    npy_intp dims2d[2] = {1, 2};
    PyObject *matrix = PyArray_SimpleNew(2, dims2d, NPY_INT);
    out = matrix != NULL ? PyArray_Correlate(matrix, b, NPY_VALID) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(out);
    Py_XDECREF(matrix);

    PyObject *objarr = PyArray_SimpleNew(1, dims2, NPY_OBJECT);
    out = objarr != NULL ? PyArray_Correlate(objarr, b, NPY_VALID) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(out);
    Py_XDECREF(objarr);

    Py_DECREF(db);
    Py_DECREF(da);
    Py_DECREF(short_a);
    Py_DECREF(b);
    Py_DECREF(a);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_correlate2_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims3[1] = {3};
    npy_intp dims2[1] = {2};
    PyObject *a = PyArray_SimpleNew(1, dims3, NPY_INT);
    PyObject *b = PyArray_SimpleNew(1, dims2, NPY_INT);
    if (a == NULL || b == NULL) {
        Py_XDECREF(a);
        Py_XDECREF(b);
        return NULL;
    }
    int *ai = (int *)PyArray_DATA((PyArrayObject *)a);
    int *bi = (int *)PyArray_DATA((PyArrayObject *)b);
    ai[0] = 2; ai[1] = -1; ai[2] = 3;
    bi[0] = 4; bi[1] = 5;
    PyObject *out = PyArray_Correlate2(a, b, NPY_VALID);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_TYPE((PyArrayObject *)out) == NPY_LONG) {
        long *vals = (long *)PyArray_DATA((PyArrayObject *)out);
        if (vals[0] == 3 && vals[1] == 11) score += 1;
    }
    Py_XDECREF(out);

    out = PyArray_Correlate2(a, b, NPY_FULL);
    if (out != NULL && PyArray_DIMS((PyArrayObject *)out)[0] == 4) {
        long *vals = (long *)PyArray_DATA((PyArrayObject *)out);
        if (vals[0] == 10 && vals[1] == 3 && vals[2] == 11 && vals[3] == 12) score += 10;
    }
    Py_XDECREF(out);

    PyObject *da = PyArray_SimpleNew(1, dims2, NPY_DOUBLE);
    PyObject *db = PyArray_SimpleNew(1, dims2, NPY_DOUBLE);
    if (da == NULL || db == NULL) {
        Py_XDECREF(da);
        Py_XDECREF(db);
        Py_DECREF(b);
        Py_DECREF(a);
        return NULL;
    }
    double *dai = (double *)PyArray_DATA((PyArrayObject *)da);
    double *dbi = (double *)PyArray_DATA((PyArrayObject *)db);
    dai[0] = -1.0; dai[1] = 2.0;
    dbi[0] = 0.5; dbi[1] = 4.0;
    out = PyArray_Correlate2(da, db, NPY_SAME);
    if (out != NULL && PyArray_TYPE((PyArrayObject *)out) == NPY_DOUBLE && PyArray_DIMS((PyArrayObject *)out)[0] == 2) {
        double *vals = (double *)PyArray_DATA((PyArrayObject *)out);
        if (vals[0] > -4.01 && vals[0] < -3.99 && vals[1] > 7.49 && vals[1] < 7.51) score += 100;
    }
    Py_XDECREF(out);

    out = PyArray_Correlate2(a, b, 99);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(out);

    npy_intp dims2d[2] = {1, 2};
    PyObject *matrix = PyArray_SimpleNew(2, dims2d, NPY_INT);
    out = matrix != NULL ? PyArray_Correlate2(matrix, b, NPY_VALID) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    Py_XDECREF(matrix);

    PyObject *complex_arr = PyArray_SimpleNew(1, dims2, NPY_CDOUBLE);
    out = complex_arr != NULL ? PyArray_Correlate2(complex_arr, b, NPY_VALID) : NULL;
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_XDECREF(complex_arr);

    Py_DECREF(db);
    Py_DECREF(da);
    Py_DECREF(b);
    Py_DECREF(a);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_lexsort_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims4[1] = {4};
    PyObject *key0 = PyArray_SimpleNew(1, dims4, NPY_INT);
    PyObject *key1 = PyArray_SimpleNew(1, dims4, NPY_INT);
    if (key0 == NULL || key1 == NULL) {
        Py_XDECREF(key0);
        Py_XDECREF(key1);
        return NULL;
    }
    int *k0 = (int *)PyArray_DATA((PyArrayObject *)key0);
    int *k1 = (int *)PyArray_DATA((PyArrayObject *)key1);
    k0[0] = 2; k0[1] = 1; k0[2] = 2; k0[3] = 1;
    k1[0] = 0; k1[1] = 0; k1[2] = 1; k1[3] = 1;
    PyObject *keys = PyTuple_Pack(2, key0, key1);
    PyObject *idx = keys != NULL ? PyArray_LexSort(keys, 0) : NULL;
    if (idx != NULL && idx != key0 && idx != key1 && PyArray_BASE((PyArrayObject *)idx) == NULL &&
        PyArray_NDIM((PyArrayObject *)idx) == 1 && PyArray_DIMS((PyArrayObject *)idx)[0] == 4 &&
        PyArray_TYPE((PyArrayObject *)idx) == NPY_LONG &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 1 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 0 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[3] == 2) score += 1;
    Py_XDECREF(idx);
    Py_XDECREF(keys);

    npy_intp dims3[1] = {3};
    PyObject *single = PyArray_SimpleNew(1, dims3, NPY_LONG);
    if (single == NULL) {
        Py_DECREF(key1);
        Py_DECREF(key0);
        return NULL;
    }
    long *sk = (long *)PyArray_DATA((PyArrayObject *)single);
    sk[0] = 3; sk[1] = 1; sk[2] = 2;
    keys = PyTuple_Pack(1, single);
    idx = keys != NULL ? PyArray_LexSort(keys, -1) : NULL;
    if (idx != NULL && PyArray_NDIM((PyArrayObject *)idx) == 1 && PyArray_DIMS((PyArrayObject *)idx)[0] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 1 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 2 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 0) score += 10;
    Py_XDECREF(idx);
    Py_XDECREF(keys);

    PyObject *empty_keys = PyTuple_New(0);
    idx = empty_keys != NULL ? PyArray_LexSort(empty_keys, 0) : NULL;
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(idx);
    Py_XDECREF(empty_keys);

    keys = PyTuple_Pack(2, key0, single);
    idx = keys != NULL ? PyArray_LexSort(keys, 0) : NULL;
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(idx);
    Py_XDECREF(keys);

    npy_intp dims2d[2] = {1, 4};
    PyObject *matrix = PyArray_SimpleNew(2, dims2d, NPY_INT);
    keys = matrix != NULL ? PyTuple_Pack(1, matrix) : NULL;
    idx = keys != NULL ? PyArray_LexSort(keys, 0) : NULL;
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(idx);
    Py_XDECREF(keys);
    Py_XDECREF(matrix);

    PyObject *objarr = PyArray_SimpleNew(1, dims4, NPY_OBJECT);
    keys = objarr != NULL ? PyTuple_Pack(1, objarr) : NULL;
    idx = keys != NULL ? PyArray_LexSort(keys, 0) : NULL;
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(idx);
    Py_XDECREF(keys);
    Py_XDECREF(objarr);

    Py_DECREF(single);
    Py_DECREF(key1);
    Py_DECREF(key0);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_sort_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {5};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 5; data[1] = 1; data[2] = 3; data[3] = 1; data[4] = 4;
    if (PyArray_Sort((PyArrayObject *)arr, 0, NPY_QUICKSORT) == 0 &&
        data[0] == 1 && data[1] == 1 && data[2] == 3 && data[3] == 4 && data[4] == 5) score += 1;
    data[0] = 2; data[1] = -1; data[2] = 7; data[3] = 2; data[4] = 0;
    if (PyArray_Sort((PyArrayObject *)arr, -1, NPY_STABLESORT) == 0 &&
        data[0] == -1 && data[1] == 0 && data[2] == 2 && data[3] == 2 && data[4] == 7) score += 10;
    if (PyArray_Sort((PyArrayObject *)arr, 0, NPY_SORT_DESCENDING) == -1 && PyErr_Occurred() != NULL) {
        PyErr_Clear();
        score += 100;
    }
    npy_intp dims2[2] = {1, 2};
    PyObject *matrix = PyArray_SimpleNew(2, dims2, NPY_INT);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (matrix == NULL || objarr == NULL) {
        Py_XDECREF(matrix);
        Py_XDECREF(objarr);
        Py_DECREF(arr);
        return NULL;
    }
    if (PyArray_Sort((PyArrayObject *)matrix, 0, NPY_QUICKSORT) == -1 && PyErr_Occurred() != NULL) {
        PyErr_Clear();
        score += 1000;
    }
    if (PyArray_Sort((PyArrayObject *)objarr, 0, NPY_QUICKSORT) == -1 && PyErr_Occurred() != NULL) {
        PyErr_Clear();
        score += 10000;
    }
    Py_DECREF(objarr);
    Py_DECREF(matrix);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_argsort_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {5};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 5; data[1] = 1; data[2] = 3; data[3] = 1; data[4] = 4;
    PyObject *idx = PyArray_ArgSort((PyArrayObject *)arr, 0, NPY_QUICKSORT);
    if (idx != NULL && idx != arr && PyArray_BASE((PyArrayObject *)idx) == NULL &&
        PyArray_NDIM((PyArrayObject *)idx) == 1 && PyArray_DIMS((PyArrayObject *)idx)[0] == 5 &&
        PyArray_TYPE((PyArrayObject *)idx) == NPY_LONG &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 1 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 2 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[3] == 4 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[4] == 0) score += 1;
    if (data[0] == 5 && data[1] == 1 && data[2] == 3 && data[3] == 1 && data[4] == 4) score += 10;
    Py_XDECREF(idx);
    data[0] = 2; data[1] = -1; data[2] = 7; data[3] = 2; data[4] = 0;
    idx = PyArray_ArgSort((PyArrayObject *)arr, -1, NPY_STABLESORT);
    if (idx != NULL &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 1 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 4 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 0 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[3] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[4] == 2) score += 100;
    Py_XDECREF(idx);
    idx = PyArray_ArgSort((PyArrayObject *)arr, 0, NPY_SORT_DESCENDING);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(idx);
    npy_intp dims2[2] = {1, 2};
    PyObject *matrix = PyArray_SimpleNew(2, dims2, NPY_INT);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (matrix == NULL || objarr == NULL) {
        Py_XDECREF(matrix);
        Py_XDECREF(objarr);
        Py_DECREF(arr);
        return NULL;
    }
    idx = PyArray_ArgSort((PyArrayObject *)matrix, 0, NPY_QUICKSORT);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(idx);
    idx = PyArray_ArgSort((PyArrayObject *)objarr, 0, NPY_QUICKSORT);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(idx);
    Py_DECREF(objarr);
    Py_DECREF(matrix);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_partition_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {5};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    npy_intp kth_dims[1] = {1};
    PyObject *kth = PyArray_SimpleNew(1, kth_dims, NPY_LONG);
    PyObject *scalar_kth = PyArray_SimpleNew(0, NULL, NPY_LONG);
    if (arr == NULL || kth == NULL || scalar_kth == NULL) {
        Py_XDECREF(scalar_kth);
        Py_XDECREF(kth);
        Py_XDECREF(arr);
        return NULL;
    }
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    long *kth_data = (long *)PyArray_DATA((PyArrayObject *)kth);
    long *scalar_kth_data = (long *)PyArray_DATA((PyArrayObject *)scalar_kth);
    data[0] = 5; data[1] = 1; data[2] = 4; data[3] = 2; data[4] = 3;
    kth_data[0] = 2;
    if (PyArray_Partition((PyArrayObject *)arr, (PyArrayObject *)kth, 0, NPY_INTROSELECT) == 0 &&
        data[0] == 1 && data[1] == 2 && data[2] == 3 && data[3] == 4 && data[4] == 5) score += 1;
    data[0] = 9; data[1] = 7; data[2] = 8; data[3] = 6; data[4] = 5;
    kth_data[0] = -1;
    if (PyArray_Partition((PyArrayObject *)arr, (PyArrayObject *)kth, -1, NPY_INTROSELECT) == 0 &&
        data[0] == 5 && data[1] == 6 && data[2] == 7 && data[3] == 8 && data[4] == 9) score += 10;
    data[0] = 30; data[1] = 10; data[2] = 20; data[3] = 40; data[4] = 0;
    kth_data[0] = 1;
    PyObject *idx = PyArray_ArgPartition((PyArrayObject *)arr, (PyArrayObject *)kth, 0, NPY_INTROSELECT);
    if (idx != NULL && idx != arr && PyArray_BASE((PyArrayObject *)idx) == NULL &&
        PyArray_NDIM((PyArrayObject *)idx) == 1 && PyArray_DIMS((PyArrayObject *)idx)[0] == 5 &&
        PyArray_TYPE((PyArrayObject *)idx) == NPY_LONG &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 4 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 1 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 2 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[3] == 0 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[4] == 3) score += 100;
    Py_XDECREF(idx);
    idx = PyArray_ArgPartition((PyArrayObject *)arr, (PyArrayObject *)kth, 0, (NPY_SELECTKIND)99);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(idx);
    kth_data[0] = 5;
    if (PyArray_Partition((PyArrayObject *)arr, (PyArrayObject *)kth, 0, NPY_INTROSELECT) == -1 &&
        PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    data[0] = 4; data[1] = 3; data[2] = 2; data[3] = 1; data[4] = 0;
    scalar_kth_data[0] = 3;
    if (PyArray_Partition((PyArrayObject *)arr, (PyArrayObject *)scalar_kth, 0, NPY_INTROSELECT) == 0 &&
        data[0] == 0 && data[1] == 1 && data[2] == 2 && data[3] == 3 && data[4] == 4) score += 100000;
    data[0] = 5; data[1] = 4; data[2] = 3; data[3] = 2; data[4] = 1;
    scalar_kth_data[0] = -2;
    idx = PyArray_ArgPartition((PyArrayObject *)arr, (PyArrayObject *)scalar_kth, 0, NPY_INTROSELECT);
    if (idx != NULL &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 4 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 2 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[3] == 1 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[4] == 0) score += 1000000;
    Py_XDECREF(idx);
    scalar_kth_data[0] = 5;
    if (PyArray_ArgPartition((PyArrayObject *)arr, (PyArrayObject *)scalar_kth, 0, NPY_INTROSELECT) == NULL &&
        PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_DECREF(kth);
    Py_DECREF(scalar_kth);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_searchsorted_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp hay_dims[1] = {4};
    npy_intp key_dims[1] = {4};
    PyObject *hay = PyArray_SimpleNew(1, hay_dims, NPY_INT);
    PyObject *keys = PyArray_SimpleNew(1, key_dims, NPY_INT);
    if (hay == NULL || keys == NULL) {
        Py_XDECREF(hay);
        Py_XDECREF(keys);
        return NULL;
    }
    int *hay_data = (int *)PyArray_DATA((PyArrayObject *)hay);
    int *key_data = (int *)PyArray_DATA((PyArrayObject *)keys);
    hay_data[0] = 1; hay_data[1] = 3; hay_data[2] = 3; hay_data[3] = 5;
    key_data[0] = 0; key_data[1] = 3; key_data[2] = 4; key_data[3] = 8;
    PyObject *idx = PyArray_SearchSorted((PyArrayObject *)hay, keys, NPY_SEARCHLEFT, NULL);
    if (idx != NULL && idx != hay && idx != keys && PyArray_BASE((PyArrayObject *)idx) == NULL &&
        PyArray_NDIM((PyArrayObject *)idx) == 1 && PyArray_DIMS((PyArrayObject *)idx)[0] == 4 &&
        PyArray_TYPE((PyArrayObject *)idx) == NPY_LONG &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 0 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 1 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[3] == 4) score += 1;
    Py_XDECREF(idx);
    idx = PyArray_SearchSorted((PyArrayObject *)hay, keys, NPY_SEARCHRIGHT, NULL);
    if (idx != NULL &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 0 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[3] == 4) score += 10;
    Py_XDECREF(idx);
    if (hay_data[0] == 1 && hay_data[1] == 3 && hay_data[2] == 3 && hay_data[3] == 5) score += 100;
    idx = PyArray_SearchSorted((PyArrayObject *)hay, keys, (NPY_SEARCHSIDE)7, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(idx);
    PyObject *unsorted = PyArray_SimpleNew(1, hay_dims, NPY_INT);
    PyObject *sorter = PyArray_SimpleNew(1, hay_dims, NPY_LONG);
    if (unsorted == NULL || sorter == NULL) {
        Py_XDECREF(unsorted);
        Py_XDECREF(sorter);
        Py_DECREF(keys);
        Py_DECREF(hay);
        return NULL;
    }
    int *unsorted_data = (int *)PyArray_DATA((PyArrayObject *)unsorted);
    long *sorter_data = (long *)PyArray_DATA((PyArrayObject *)sorter);
    unsorted_data[0] = 30; unsorted_data[1] = 10; unsorted_data[2] = 20; unsorted_data[3] = 20;
    sorter_data[0] = 1; sorter_data[1] = 2; sorter_data[2] = 3; sorter_data[3] = 0;
    key_data[0] = 5; key_data[1] = 20; key_data[2] = 25; key_data[3] = 40;
    idx = PyArray_SearchSorted((PyArrayObject *)unsorted, keys, NPY_SEARCHLEFT, sorter);
    if (idx != NULL &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 0 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 1 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[3] == 4) score += 10000000;
    Py_XDECREF(idx);
    idx = PyArray_SearchSorted((PyArrayObject *)unsorted, keys, NPY_SEARCHRIGHT, sorter);
    if (idx != NULL &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 0 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 3 &&
        ((long *)PyArray_DATA((PyArrayObject *)idx))[3] == 4) score += 100000000;
    Py_XDECREF(idx);
    idx = PyArray_SearchSorted((PyArrayObject *)hay, keys, NPY_SEARCHLEFT, keys);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(idx);
    Py_DECREF(sorter);
    Py_DECREF(unsorted);
    npy_intp dims2[2] = {1, 2};
    PyObject *matrix = PyArray_SimpleNew(2, dims2, NPY_INT);
    PyObject *objarr = PyArray_SimpleNew(1, hay_dims, NPY_OBJECT);
    if (matrix == NULL || objarr == NULL) {
        Py_XDECREF(matrix);
        Py_XDECREF(objarr);
        Py_DECREF(keys);
        Py_DECREF(hay);
        return NULL;
    }
    idx = PyArray_SearchSorted((PyArrayObject *)matrix, keys, NPY_SEARCHLEFT, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(idx);
    idx = PyArray_SearchSorted((PyArrayObject *)objarr, keys, NPY_SEARCHLEFT, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(idx);
    Py_DECREF(objarr);
    Py_DECREF(matrix);
    Py_DECREF(keys);
    Py_DECREF(hay);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_nonzero_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {6};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 0; data[1] = 2; data[2] = 0; data[3] = -3; data[4] = 4; data[5] = 0;
    PyObject *tuple = PyArray_Nonzero((PyArrayObject *)arr);
    if (tuple != NULL && PyTuple_Check(tuple) && PyTuple_Size(tuple) == 1) {
        PyObject *idx = PyTuple_GetItem(tuple, 0);
        if (idx != NULL && PyArray_Check(idx) && PyArray_BASE((PyArrayObject *)idx) == NULL &&
            PyArray_NDIM((PyArrayObject *)idx) == 1 && PyArray_DIMS((PyArrayObject *)idx)[0] == 3 &&
            PyArray_TYPE((PyArrayObject *)idx) == NPY_LONG &&
            ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 1 &&
            ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 3 &&
            ((long *)PyArray_DATA((PyArrayObject *)idx))[2] == 4) score += 1;
    }
    if (data[0] == 0 && data[1] == 2 && data[2] == 0 && data[3] == -3 && data[4] == 4 && data[5] == 0) score += 10;
    Py_XDECREF(tuple);
    data[1] = 0; data[3] = 0; data[4] = 0;
    tuple = PyArray_Nonzero((PyArrayObject *)arr);
    if (tuple != NULL && PyTuple_Check(tuple) && PyTuple_Size(tuple) == 1) {
        PyObject *idx = PyTuple_GetItem(tuple, 0);
        if (idx != NULL && PyArray_Check(idx) &&
            PyArray_NDIM((PyArrayObject *)idx) == 1 && PyArray_DIMS((PyArrayObject *)idx)[0] == 0 &&
            PyArray_TYPE((PyArrayObject *)idx) == NPY_LONG) score += 100;
    }
    Py_XDECREF(tuple);
    npy_intp dims2[2] = {1, 2};
    PyObject *matrix = PyArray_SimpleNew(2, dims2, NPY_INT);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (matrix == NULL || objarr == NULL) {
        Py_XDECREF(matrix);
        Py_XDECREF(objarr);
        Py_DECREF(arr);
        return NULL;
    }
    int *matrix_data = (int *)PyArray_DATA((PyArrayObject *)matrix);
    matrix_data[0] = 0; matrix_data[1] = 7;
    tuple = PyArray_Nonzero((PyArrayObject *)matrix);
    if (tuple != NULL && PyTuple_Check(tuple) && PyTuple_Size(tuple) == 2) {
        PyObject *rows = PyTuple_GetItem(tuple, 0);
        PyObject *cols = PyTuple_GetItem(tuple, 1);
        if (rows != NULL && cols != NULL && PyArray_Check(rows) && PyArray_Check(cols) &&
            PyArray_NDIM((PyArrayObject *)rows) == 1 && PyArray_DIMS((PyArrayObject *)rows)[0] == 1 &&
            PyArray_NDIM((PyArrayObject *)cols) == 1 && PyArray_DIMS((PyArrayObject *)cols)[0] == 1 &&
            PyArray_TYPE((PyArrayObject *)rows) == NPY_LONG && PyArray_TYPE((PyArrayObject *)cols) == NPY_LONG &&
            ((long *)PyArray_DATA((PyArrayObject *)rows))[0] == 0 &&
            ((long *)PyArray_DATA((PyArrayObject *)cols))[0] == 1) score += 1000;
    } else if (PyErr_Occurred() != NULL) {
        PyErr_Clear();
    }
    Py_XDECREF(tuple);
    tuple = PyArray_Nonzero((PyArrayObject *)objarr);
    if (tuple == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(tuple);
    Py_DECREF(objarr);
    Py_DECREF(matrix);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_count_nonzero_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {5};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 0; data[1] = -2; data[2] = 0; data[3] = 7; data[4] = 9;
    if (PyArray_CountNonzero((PyArrayObject *)arr) == 3) score += 1;
    npy_intp ddims[2] = {2, 3};
    PyObject *darr = PyArray_SimpleNew(2, ddims, NPY_DOUBLE);
    if (darr == NULL) { Py_DECREF(arr); return NULL; }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = 0.0; ddata[1] = 1.5; ddata[2] = 0.0; ddata[3] = -2.0; ddata[4] = 3.25; ddata[5] = 0.0;
    if (PyArray_CountNonzero((PyArrayObject *)darr) == 3) score += 10;
    npy_intp bdims[1] = {4};
    PyObject *barr = PyArray_SimpleNew(1, bdims, NPY_BOOL);
    if (barr == NULL) { Py_DECREF(darr); Py_DECREF(arr); return NULL; }
    unsigned char *bdata = (unsigned char *)PyArray_DATA((PyArrayObject *)barr);
    bdata[0] = 1; bdata[1] = 0; bdata[2] = 1; bdata[3] = 1;
    if (PyArray_CountNonzero((PyArrayObject *)barr) == 3) score += 100;
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) { Py_DECREF(barr); Py_DECREF(darr); Py_DECREF(arr); return NULL; }
    if (PyArray_CountNonzero((PyArrayObject *)empty) == 0) score += 1000;
    if (PyArray_CountNonzero((PyArrayObject *)Py_None) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) { Py_DECREF(empty); Py_DECREF(barr); Py_DECREF(darr); Py_DECREF(arr); return NULL; }
    if (PyArray_CountNonzero((PyArrayObject *)objarr) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    PyObject *carr = PyArray_SimpleNew(1, dims, NPY_CDOUBLE);
    if (carr == NULL) { Py_DECREF(objarr); Py_DECREF(empty); Py_DECREF(barr); Py_DECREF(darr); Py_DECREF(arr); return NULL; }
    if (PyArray_CountNonzero((PyArrayObject *)carr) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    PyObject *strided = PyArray_SimpleNew(1, dims, NPY_INT);
    if (strided != NULL) {
        PyArray_STRIDES((PyArrayObject *)strided)[0] = 2 * (npy_intp)sizeof(int);
        if (PyArray_CountNonzero((PyArrayObject *)strided) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    }
    Py_XDECREF(strided);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(empty);
    Py_DECREF(barr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_min_scalar_type_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    PyArray_Descr *descr = PyArray_MinScalarType((PyArrayObject *)arr);
    if (descr != NULL && descr->type_num == NPY_INT) score += 1;
    Py_XDECREF(descr);
    PyObject *pos = PyArray_SimpleNew(0, NULL, NPY_LONG);
    PyObject *neg = PyArray_SimpleNew(0, NULL, NPY_LONG);
    PyObject *uns = PyArray_SimpleNew(0, NULL, NPY_ULONGLONG);
    PyObject *flt = PyArray_SimpleNew(0, NULL, NPY_DOUBLE);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    PyObject *carr = PyArray_SimpleNew(1, dims, NPY_CDOUBLE);
    if (pos == NULL || neg == NULL || uns == NULL || flt == NULL || objarr == NULL || carr == NULL) {
        Py_XDECREF(carr);
        Py_XDECREF(objarr);
        Py_XDECREF(flt);
        Py_XDECREF(uns);
        Py_XDECREF(neg);
        Py_XDECREF(pos);
        Py_DECREF(arr);
        return NULL;
    }
    *((long *)PyArray_DATA((PyArrayObject *)pos)) = 7;
    descr = PyArray_MinScalarType((PyArrayObject *)pos);
    if (descr != NULL && descr->type_num == NPY_UBYTE) score += 10;
    Py_XDECREF(descr);
    *((long *)PyArray_DATA((PyArrayObject *)neg)) = -2;
    descr = PyArray_MinScalarType((PyArrayObject *)neg);
    if (descr != NULL && descr->type_num == NPY_BYTE) score += 100;
    Py_XDECREF(descr);
    *((unsigned long long *)PyArray_DATA((PyArrayObject *)uns)) = 255ULL;
    descr = PyArray_MinScalarType((PyArrayObject *)uns);
    if (descr != NULL && descr->type_num == NPY_UBYTE) score += 1000;
    Py_XDECREF(descr);
    *((double *)PyArray_DATA((PyArrayObject *)flt)) = 1.5;
    descr = PyArray_MinScalarType((PyArrayObject *)flt);
    if (descr != NULL && descr->type_num == NPY_FLOAT) score += 10000;
    Py_XDECREF(descr);
    descr = PyArray_MinScalarType((PyArrayObject *)objarr);
    if (descr != NULL && descr->type_num == NPY_OBJECT) score += 100000;
    Py_XDECREF(descr);
    descr = PyArray_MinScalarType((PyArrayObject *)carr);
    if (descr != NULL && descr->type_num == NPY_CDOUBLE) score += 1000000;
    Py_XDECREF(descr);
    descr = PyArray_MinScalarType((PyArrayObject *)Py_None);
    if (descr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(descr);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(flt);
    Py_DECREF(uns);
    Py_DECREF(neg);
    Py_DECREF(pos);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_sorted_stride_perm_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp strides[3] = {4, -2, 12};
    npy_stride_sort_item items[3];
    PyArray_CreateSortedStridePerm(3, strides, items);
    if (items[0].perm == 2 && items[0].stride == 12 &&
        items[1].perm == 0 && items[1].stride == 4 &&
        items[2].perm == 1 && items[2].stride == -2) score += 1;
    npy_intp tied[4] = {8, -8, 4, 0};
    npy_stride_sort_item tied_items[4];
    PyArray_CreateSortedStridePerm(4, tied, tied_items);
    if (tied_items[0].perm == 0 && tied_items[0].stride == 8 &&
        tied_items[1].perm == 1 && tied_items[1].stride == -8 &&
        tied_items[2].perm == 2 && tied_items[2].stride == 4 &&
        tied_items[3].perm == 3 && tied_items[3].stride == 0) score += 10;
    npy_intp neg[3] = {-1, -9, 3};
    PyArray_CreateSortedStridePerm(3, neg, items);
    if (items[0].perm == 1 && items[0].stride == -9 &&
        items[1].perm == 2 && items[1].stride == 3 &&
        items[2].perm == 0 && items[2].stride == -1) score += 100;
    npy_stride_sort_item untouched[2];
    untouched[0].perm = 90; untouched[0].stride = 91;
    untouched[1].perm = 92; untouched[1].stride = 93;
    PyArray_CreateSortedStridePerm(0, NULL, untouched);
    if (untouched[0].perm == 90 && untouched[0].stride == 91 &&
        untouched[1].perm == 92 && untouched[1].stride == 93) score += 1000;
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_remove_axes_in_place_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[3] = {2, 1, 3};
    PyObject *arr_obj = PyArray_SimpleNew(3, dims, NPY_INT);
    if (arr_obj == NULL) return NULL;
    PyArrayObject *arr = (PyArrayObject *)arr_obj;
    void *data_before = PyArray_DATA(arr);
    npy_intp stride0 = PyArray_STRIDES(arr)[0];
    npy_intp stride2 = PyArray_STRIDES(arr)[2];
    npy_bool drop_middle[3] = {0, 1, 0};
    PyArray_RemoveAxesInPlace(arr, drop_middle);
    if (PyArray_NDIM(arr) == 2 && PyArray_DIMS(arr)[0] == 2 &&
        PyArray_DIMS(arr)[1] == 3 && PyArray_DATA(arr) == data_before &&
        PyArray_TYPE(arr) == NPY_INT) score += 1;
    if (PyArray_STRIDES(arr)[0] == stride0 && PyArray_STRIDES(arr)[1] == stride2) score += 10;
    npy_bool keep_all[2] = {0, 0};
    PyArray_RemoveAxesInPlace(arr, keep_all);
    if (PyArray_NDIM(arr) == 2 && PyArray_DIMS(arr)[0] == 2 &&
        PyArray_DIMS(arr)[1] == 3 && PyArray_DATA(arr) == data_before) score += 100;
    Py_DECREF(arr_obj);
    npy_intp scalar_dims[2] = {1, 1};
    PyObject *scalar_obj = PyArray_SimpleNew(2, scalar_dims, NPY_INT);
    if (scalar_obj == NULL) return NULL;
    void *scalar_data = PyArray_DATA((PyArrayObject *)scalar_obj);
    npy_bool drop_all[2] = {1, 1};
    PyArray_RemoveAxesInPlace((PyArrayObject *)scalar_obj, drop_all);
    if (PyArray_NDIM((PyArrayObject *)scalar_obj) == 0 &&
        PyArray_DATA((PyArrayObject *)scalar_obj) == scalar_data) score += 1000;
    Py_DECREF(scalar_obj);
    PyArray_RemoveAxesInPlace((PyArrayObject *)Py_None, drop_all);
    if (PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_debug_print_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *arr_obj = PyArray_SimpleNew(2, dims, NPY_INT);
    if (arr_obj == NULL) return NULL;
    PyArray_DebugPrint((PyArrayObject *)arr_obj);
    if (PyErr_Occurred() == NULL) score += 1;
    PyArray_DebugPrint(NULL);
    if (PyErr_Occurred() == NULL) score += 10;
    PyArray_DebugPrint((PyArrayObject *)Py_None);
    if (PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_DECREF(arr_obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_where_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {4};
    PyObject *cond = PyArray_SimpleNew(1, dims, NPY_BOOL);
    PyObject *left = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *right = PyArray_SimpleNew(1, dims, NPY_INT);
    if (cond == NULL || left == NULL || right == NULL) {
        Py_XDECREF(cond);
        Py_XDECREF(left);
        Py_XDECREF(right);
        return NULL;
    }
    unsigned char *cond_data = (unsigned char *)PyArray_DATA((PyArrayObject *)cond);
    int *left_data = (int *)PyArray_DATA((PyArrayObject *)left);
    int *right_data = (int *)PyArray_DATA((PyArrayObject *)right);
    cond_data[0] = 1; cond_data[1] = 0; cond_data[2] = 1; cond_data[3] = 0;
    left_data[0] = 10; left_data[1] = 20; left_data[2] = 30; left_data[3] = 40;
    right_data[0] = 100; right_data[1] = 200; right_data[2] = 300; right_data[3] = 400;
    PyObject *out = PyArray_Where(cond, left, right);
    if (out != NULL && out != left && out != right && PyArray_BASE((PyArrayObject *)out) == NULL &&
        PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 4 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 10 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 200 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 30 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 400) score += 1;
    right_data[1] = 999;
    if (out != NULL && ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 200) score += 10;
    Py_XDECREF(out);
    PyObject *nz = PyArray_Where(cond, NULL, NULL);
    if (nz != NULL && PyTuple_Check(nz) && PyTuple_Size(nz) == 1) {
        PyObject *idx = PyTuple_GetItem(nz, 0);
        if (idx != NULL && PyArray_Check(idx) && PyArray_TYPE((PyArrayObject *)idx) == NPY_LONG &&
            PyArray_DIMS((PyArrayObject *)idx)[0] == 2 &&
            ((long *)PyArray_DATA((PyArrayObject *)idx))[0] == 0 &&
            ((long *)PyArray_DATA((PyArrayObject *)idx))[1] == 2) score += 100;
    }
    Py_XDECREF(nz);
    out = PyArray_Where(cond, left, NULL);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(out);
    npy_intp short_dims[1] = {2};
    PyObject *short_arr = PyArray_SimpleNew(1, short_dims, NPY_INT);
    if (short_arr == NULL) {
        Py_DECREF(right);
        Py_DECREF(left);
        Py_DECREF(cond);
        return NULL;
    }
    out = PyArray_Where(cond, left, short_arr);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    npy_intp dims2[2] = {2, 2};
    PyObject *cond2 = PyArray_SimpleNew(2, dims2, NPY_BOOL);
    PyObject *left2 = PyArray_SimpleNew(2, dims2, NPY_INT);
    PyObject *right2 = PyArray_SimpleNew(2, dims2, NPY_INT);
    if (cond2 == NULL || left2 == NULL || right2 == NULL) {
        Py_XDECREF(cond2);
        Py_XDECREF(left2);
        Py_XDECREF(right2);
        Py_DECREF(short_arr);
        Py_DECREF(right);
        Py_DECREF(left);
        Py_DECREF(cond);
        return NULL;
    }
    unsigned char *cond2_data = (unsigned char *)PyArray_DATA((PyArrayObject *)cond2);
    int *left2_data = (int *)PyArray_DATA((PyArrayObject *)left2);
    int *right2_data = (int *)PyArray_DATA((PyArrayObject *)right2);
    cond2_data[0] = 1; cond2_data[1] = 0; cond2_data[2] = 0; cond2_data[3] = 1;
    left2_data[0] = 1; left2_data[1] = 2; left2_data[2] = 3; left2_data[3] = 4;
    right2_data[0] = 10; right2_data[1] = 20; right2_data[2] = 30; right2_data[3] = 40;
    out = PyArray_Where(cond2, left2, right2);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 2 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_DIMS((PyArrayObject *)out)[1] == 2 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 20 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 30 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 4) score += 1000000;
    Py_XDECREF(out);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(right2);
        Py_DECREF(left2);
        Py_DECREF(cond2);
        Py_DECREF(short_arr);
        Py_DECREF(right);
        Py_DECREF(left);
        Py_DECREF(cond);
        return NULL;
    }
    out = PyArray_Where(cond, objarr, right);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_DECREF(objarr);
    Py_DECREF(right2);
    Py_DECREF(left2);
    Py_DECREF(cond2);
    Py_DECREF(short_arr);
    Py_DECREF(right);
    Py_DECREF(left);
    Py_DECREF(cond);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_compress_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {5};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *cond = PyArray_SimpleNew(1, dims, NPY_BOOL);
    if (arr == NULL || cond == NULL) {
        Py_XDECREF(arr);
        Py_XDECREF(cond);
        return NULL;
    }
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    unsigned char *cond_data = (unsigned char *)PyArray_DATA((PyArrayObject *)cond);
    data[0] = 10; data[1] = 20; data[2] = 30; data[3] = 40; data[4] = 50;
    cond_data[0] = 1; cond_data[1] = 0; cond_data[2] = 1; cond_data[3] = 0; cond_data[4] = 1;
    PyObject *out = PyArray_Compress((PyArrayObject *)arr, cond, 0, NULL);
    if (out != NULL && out != arr && PyArray_BASE((PyArrayObject *)out) == NULL &&
        PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 3 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 10 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 30 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 50) score += 1;
    data[2] = 99;
    if (out != NULL && ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 30) score += 10;
    Py_XDECREF(out);
    cond_data[0] = 0; cond_data[2] = 0; cond_data[4] = 0;
    out = PyArray_Compress((PyArrayObject *)arr, cond, -1, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 0 && PyArray_TYPE((PyArrayObject *)out) == NPY_INT) score += 100;
    Py_XDECREF(out);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (out_arr == NULL) {
        Py_DECREF(cond);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Compress((PyArrayObject *)arr, cond, 0, (PyArrayObject *)out_arr);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(out);
    npy_intp short_dims[1] = {3};
    PyObject *short_cond = PyArray_SimpleNew(1, short_dims, NPY_BOOL);
    if (short_cond == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(cond);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Compress((PyArrayObject *)arr, short_cond, 0, NULL);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    npy_intp matrix_dims[2] = {2, 3};
    npy_intp cond0_dims[1] = {2};
    npy_intp cond1_dims[1] = {3};
    PyObject *matrix = PyArray_SimpleNew(2, matrix_dims, NPY_INT);
    PyObject *cond0 = PyArray_SimpleNew(1, cond0_dims, NPY_BOOL);
    PyObject *cond1 = PyArray_SimpleNew(1, cond1_dims, NPY_BOOL);
    if (matrix == NULL || cond0 == NULL || cond1 == NULL) {
        Py_XDECREF(cond1);
        Py_XDECREF(cond0);
        Py_XDECREF(matrix);
        Py_DECREF(short_cond);
        Py_DECREF(out_arr);
        Py_DECREF(cond);
        Py_DECREF(arr);
        return NULL;
    }
    int *matrix_data = (int *)PyArray_DATA((PyArrayObject *)matrix);
    unsigned char *cond0_data = (unsigned char *)PyArray_DATA((PyArrayObject *)cond0);
    unsigned char *cond1_data = (unsigned char *)PyArray_DATA((PyArrayObject *)cond1);
    matrix_data[0] = 1; matrix_data[1] = 2; matrix_data[2] = 3;
    matrix_data[3] = 4; matrix_data[4] = 5; matrix_data[5] = 6;
    cond0_data[0] = 1; cond0_data[1] = 0;
    cond1_data[0] = 0; cond1_data[1] = 1; cond1_data[2] = 1;
    out = PyArray_Compress((PyArrayObject *)matrix, cond0, 0, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 2 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 1 && PyArray_DIMS((PyArrayObject *)out)[1] == 3 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 2 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 3) score += 1000000;
    Py_XDECREF(out);
    out = PyArray_Compress((PyArrayObject *)matrix, cond1, 1, NULL);
    if (out != NULL && PyArray_NDIM((PyArrayObject *)out) == 2 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 2 && PyArray_DIMS((PyArrayObject *)out)[1] == 2 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 2 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 5 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 6) score += 10000000;
    Py_XDECREF(out);
    Py_DECREF(cond1);
    Py_DECREF(cond0);
    Py_DECREF(matrix);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(short_cond);
        Py_DECREF(out_arr);
        Py_DECREF(cond);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Compress((PyArrayObject *)objarr, cond, 0, NULL);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_DECREF(objarr);
    Py_DECREF(short_cond);
    Py_DECREF(out_arr);
    Py_DECREF(cond);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_diagonal_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *matrix = PyArray_SimpleNew(2, dims, NPY_INT);
    if (matrix == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)matrix);
    data[0] = 1; data[1] = 2; data[2] = 3; data[3] = 4; data[4] = 5; data[5] = 6;
    PyObject *diag = PyArray_Diagonal((PyArrayObject *)matrix, 0, 0, 1);
    if (diag != NULL && diag != matrix && PyArray_BASE((PyArrayObject *)diag) == NULL &&
        PyArray_NDIM((PyArrayObject *)diag) == 1 && PyArray_DIMS((PyArrayObject *)diag)[0] == 2 &&
        PyArray_TYPE((PyArrayObject *)diag) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)diag))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)diag))[1] == 5) score += 1;
    data[4] = 99;
    if (diag != NULL && ((int *)PyArray_DATA((PyArrayObject *)diag))[1] == 5) score += 10;
    Py_XDECREF(diag);
    diag = PyArray_Diagonal((PyArrayObject *)matrix, 1, 0, 1);
    if (diag != NULL && PyArray_NDIM((PyArrayObject *)diag) == 1 &&
        PyArray_DIMS((PyArrayObject *)diag)[0] == 2 && PyArray_TYPE((PyArrayObject *)diag) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)diag))[0] == 2 &&
        ((int *)PyArray_DATA((PyArrayObject *)diag))[1] == 6) score += 100;
    Py_XDECREF(diag);
    diag = PyArray_Diagonal((PyArrayObject *)matrix, -1, 0, 1);
    if (diag != NULL && PyArray_NDIM((PyArrayObject *)diag) == 1 &&
        PyArray_DIMS((PyArrayObject *)diag)[0] == 1 && PyArray_TYPE((PyArrayObject *)diag) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)diag))[0] == 4) score += 1000;
    Py_XDECREF(diag);
    diag = PyArray_Diagonal((PyArrayObject *)matrix, 0, 1, 0);
    if (diag != NULL && PyArray_NDIM((PyArrayObject *)diag) == 1 &&
        PyArray_DIMS((PyArrayObject *)diag)[0] == 2 && PyArray_TYPE((PyArrayObject *)diag) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)diag))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)diag))[1] == 99) score += 10000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(diag);
    diag = PyArray_Diagonal((PyArrayObject *)matrix, 0, -1, -2);
    if (diag != NULL && PyArray_NDIM((PyArrayObject *)diag) == 1 &&
        PyArray_DIMS((PyArrayObject *)diag)[0] == 2 && PyArray_TYPE((PyArrayObject *)diag) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)diag))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)diag))[1] == 99) score += 1000000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(diag);
    diag = PyArray_Diagonal((PyArrayObject *)matrix, 0, 1, 1);
    if (diag == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(diag);
    PyObject *objarr = PyArray_SimpleNew(2, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(matrix);
        return NULL;
    }
    diag = PyArray_Diagonal((PyArrayObject *)objarr, 0, 0, 1);
    if (diag == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(diag);
    Py_DECREF(objarr);
    Py_DECREF(matrix);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_trace_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *matrix = PyArray_SimpleNew(2, dims, NPY_INT);
    if (matrix == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)matrix);
    data[0] = 1; data[1] = 2; data[2] = 3; data[3] = 4; data[4] = 5; data[5] = 6;
    PyObject *trace = PyArray_Trace((PyArrayObject *)matrix, 0, 0, 1, NPY_NOTYPE, NULL);
    if (trace != NULL && PyLong_AsLong(trace) == 6) score += 1;
    Py_XDECREF(trace);
    trace = PyArray_Trace((PyArrayObject *)matrix, 1, 0, 1, NPY_NOTYPE, NULL);
    if (trace != NULL && PyLong_AsLong(trace) == 8) score += 10;
    Py_XDECREF(trace);
    trace = PyArray_Trace((PyArrayObject *)matrix, -1, 0, 1, NPY_NOTYPE, NULL);
    if (trace != NULL && PyLong_AsLong(trace) == 4) score += 100;
    Py_XDECREF(trace);
    trace = PyArray_Trace((PyArrayObject *)matrix, 0, 1, 0, NPY_NOTYPE, NULL);
    if (trace != NULL && PyLong_AsLong(trace) == 6) score += 1000000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(trace);
    trace = PyArray_Trace((PyArrayObject *)matrix, 0, -1, -2, NPY_NOTYPE, NULL);
    if (trace != NULL && PyLong_AsLong(trace) == 6) score += 10000000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(trace);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (out_arr == NULL) {
        Py_DECREF(matrix);
        return NULL;
    }
    trace = PyArray_Trace((PyArrayObject *)matrix, 0, 0, 1, NPY_NOTYPE, (PyArrayObject *)out_arr);
    if (trace == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(trace);
    trace = PyArray_Trace((PyArrayObject *)matrix, 0, 0, 1, NPY_DOUBLE, NULL);
    if (trace != NULL && PyFloat_Check(trace) && PyFloat_AsDouble(trace) == 6.0) score += 10000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(trace);
    PyObject *objarr = PyArray_SimpleNew(2, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(matrix);
        return NULL;
    }
    trace = PyArray_Trace((PyArrayObject *)objarr, 0, 0, 1, NPY_NOTYPE, NULL);
    if (trace == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(trace);
    Py_DECREF(objarr);
    Py_DECREF(out_arr);
    Py_DECREF(matrix);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_clip_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {5};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = -5; data[1] = 0; data[2] = 5; data[3] = 10; data[4] = 15;
    PyObject *lo = PyLong_FromLong(0);
    PyObject *hi = PyLong_FromLong(9);
    if (lo == NULL || hi == NULL) {
        Py_XDECREF(lo);
        Py_XDECREF(hi);
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *out = PyArray_Clip((PyArrayObject *)arr, lo, hi, NULL);
    if (out != NULL && out != arr && PyArray_BASE((PyArrayObject *)out) == NULL &&
        PyArray_NDIM((PyArrayObject *)out) == 1 && PyArray_DIMS((PyArrayObject *)out)[0] == 5 &&
        PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 0 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 0 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 5 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 9 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[4] == 9) score += 1;
    data[2] = 99;
    if (out != NULL && ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 5) score += 10;
    Py_XDECREF(out);
    Py_DECREF(hi);
    hi = NULL;
    Py_DECREF(lo);
    lo = PyLong_FromLong(7);
    if (lo == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Clip((PyArrayObject *)arr, lo, NULL, NULL);
    if (out != NULL && ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 7 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 7 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 99 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 10 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[4] == 15) score += 100;
    Py_XDECREF(out);
    Py_DECREF(lo);
    hi = PyLong_FromLong(3);
    if (hi == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Clip((PyArrayObject *)arr, NULL, hi, NULL);
    if (out != NULL && ((int *)PyArray_DATA((PyArrayObject *)out))[0] == -5 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == 0 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[3] == 3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[4] == 3) score += 1000;
    Py_XDECREF(out);
    Py_DECREF(hi);
    out = PyArray_Clip((PyArrayObject *)arr, NULL, NULL, NULL);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (out_arr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    lo = PyLong_FromLong(0);
    if (lo == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Clip((PyArrayObject *)arr, lo, NULL, (PyArrayObject *)out_arr);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    Py_DECREF(lo);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(arr);
        return NULL;
    }
    hi = PyLong_FromLong(1);
    if (hi == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Clip((PyArrayObject *)objarr, NULL, hi, NULL);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(out);
    Py_DECREF(hi);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = 3.5;
    lo = PyFloat_FromDouble(-1.0);
    hi = PyFloat_FromDouble(2.0);
    if (lo == NULL || hi == NULL) {
        Py_XDECREF(lo);
        Py_XDECREF(hi);
        Py_DECREF(darr);
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Clip((PyArrayObject *)darr, lo, hi, NULL);
    if (out != NULL && PyArray_TYPE((PyArrayObject *)out) == NPY_DOUBLE &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[0] == -1.0 &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[1] == 2.0) score += 10000000;
    Py_XDECREF(out);
    Py_DECREF(hi);
    Py_DECREF(lo);
    Py_DECREF(darr);
    Py_DECREF(objarr);
    Py_DECREF(out_arr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_conjugate_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 2; data[1] = -3; data[2] = 5;
    PyObject *out = PyArray_Conjugate((PyArrayObject *)arr, NULL);
    if (out == arr && PyArray_NDIM((PyArrayObject *)out) == 1 &&
        PyArray_DIMS((PyArrayObject *)out)[0] == 3 && PyArray_TYPE((PyArrayObject *)out) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[0] == 2 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[1] == -3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out))[2] == 5) score += 1;
    Py_XDECREF(out);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (out_arr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Conjugate((PyArrayObject *)arr, (PyArrayObject *)out_arr);
    if (out == out_arr && ((int *)PyArray_DATA((PyArrayObject *)out_arr))[0] == 2 &&
        ((int *)PyArray_DATA((PyArrayObject *)out_arr))[1] == -3 &&
        ((int *)PyArray_DATA((PyArrayObject *)out_arr))[2] == 5) score += 10;
    data[0] = 7;
    if (((int *)PyArray_DATA((PyArrayObject *)out_arr))[0] == 2) score += 100;
    Py_XDECREF(out);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = 2.5;
    out = PyArray_Conjugate((PyArrayObject *)darr, NULL);
    if (out == darr && PyArray_TYPE((PyArrayObject *)out) == NPY_DOUBLE &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[0] == -1.5 &&
        ((double *)PyArray_DATA((PyArrayObject *)out))[1] == 2.5) score += 1000;
    Py_XDECREF(out);
    PyObject *wrong_out = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    if (wrong_out == NULL) {
        Py_DECREF(darr);
        Py_DECREF(out_arr);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Conjugate((PyArrayObject *)arr, (PyArrayObject *)wrong_out);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(out);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(wrong_out);
        Py_DECREF(darr);
        Py_DECREF(out_arr);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Conjugate((PyArrayObject *)objarr, NULL);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(out);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(wrong_out);
        Py_DECREF(darr);
        Py_DECREF(out_arr);
        Py_DECREF(arr);
        return NULL;
    }
    out = PyArray_Conjugate((PyArrayObject *)carr, NULL);
    if (out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(out);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(wrong_out);
    Py_DECREF(darr);
    Py_DECREF(out_arr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_std_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {2};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 1; data[1] = 3;
    PyObject *stdv = PyArray_Std((PyArrayObject *)arr, 0, NPY_NOTYPE, NULL, 0);
    if (stdv != NULL && PyFloat_AsDouble(stdv) == 1.0) score += 1;
    Py_XDECREF(stdv);
    stdv = PyArray_Std((PyArrayObject *)arr, -1, NPY_NOTYPE, NULL, 0);
    if (stdv != NULL && PyFloat_AsDouble(stdv) == 1.0) score += 10;
    Py_XDECREF(stdv);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = 1.0; ddata[1] = 5.0;
    stdv = PyArray_Std((PyArrayObject *)darr, 0, NPY_NOTYPE, NULL, 0);
    if (stdv != NULL && PyFloat_AsDouble(stdv) == 2.0) score += 100;
    Py_XDECREF(stdv);
    stdv = PyArray_Std((PyArrayObject *)darr, 0, NPY_NOTYPE, NULL, 1);
    if (stdv != NULL && PyFloat_AsDouble(stdv) == 4.0) score += 1000;
    Py_XDECREF(stdv);
    PyObject *out_arr = PyArray_SimpleNew(0, NULL, NPY_DOUBLE);
    PyObject *bad_out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (out_arr == NULL || bad_out_arr == NULL) {
        Py_XDECREF(bad_out_arr);
        Py_XDECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    stdv = PyArray_Std((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)out_arr, 0);
    if (stdv == out_arr && ((double *)PyArray_DATA((PyArrayObject *)out_arr))[0] == 1.0) score += 10000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(stdv);
    stdv = PyArray_Std((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)bad_out_arr, 0);
    if (stdv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000000; }
    Py_XDECREF(stdv);
    stdv = PyArray_Std((PyArrayObject *)arr, 1, NPY_NOTYPE, NULL, 0);
    if (stdv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(stdv);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    stdv = PyArray_Std((PyArrayObject *)objarr, 0, NPY_NOTYPE, NULL, 0);
    if (stdv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(stdv);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    stdv = PyArray_Std((PyArrayObject *)carr, 0, NPY_NOTYPE, NULL, 0);
    if (stdv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(stdv);
    stdv = PyArray_Std((PyArrayObject *)arr, 0, NPY_DOUBLE, NULL, 0);
    if (stdv != NULL && PyFloat_AsDouble(stdv) == 1.0) score += 100000000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(stdv);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(carr);
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    stdv = PyArray_Std((PyArrayObject *)empty, 0, NPY_NOTYPE, NULL, 0);
    if (stdv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000000; }
    Py_XDECREF(stdv);
    Py_DECREF(empty);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(bad_out_arr);
    Py_DECREF(out_arr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_round_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 1; data[1] = -3; data[2] = 5;
    PyObject *rounded = PyArray_Round((PyArrayObject *)arr, 3, NULL);
    if (rounded != NULL && rounded != arr && PyArray_TYPE((PyArrayObject *)rounded) == NPY_INT &&
        ((int *)PyArray_DATA((PyArrayObject *)rounded))[0] == 1 &&
        ((int *)PyArray_DATA((PyArrayObject *)rounded))[1] == -3 &&
        ((int *)PyArray_DATA((PyArrayObject *)rounded))[2] == 5) score += 1;
    data[0] = 9;
    if (rounded != NULL && ((int *)PyArray_DATA((PyArrayObject *)rounded))[0] == 1) score += 10;
    Py_XDECREF(rounded);
    npy_intp ddims[1] = {3};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = 1.2; ddata[1] = -2.7; ddata[2] = 3.0;
    rounded = PyArray_Round((PyArrayObject *)darr, 0, NULL);
    if (rounded != NULL && PyArray_TYPE((PyArrayObject *)rounded) == NPY_DOUBLE) {
        double *out = (double *)PyArray_DATA((PyArrayObject *)rounded);
        if (out[0] == 1.0 && out[1] == -3.0 && out[2] == 3.0) score += 100;
    }
    Py_XDECREF(rounded);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    rounded = PyArray_Round((PyArrayObject *)empty, 0, NULL);
    if (rounded != NULL && PyArray_TYPE((PyArrayObject *)rounded) == NPY_INT && PyArray_SIZE((PyArrayObject *)rounded) == 0) score += 1000;
    Py_XDECREF(rounded);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (out_arr == NULL) {
        Py_DECREF(empty);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    rounded = PyArray_Round((PyArrayObject *)arr, 0, (PyArrayObject *)out_arr);
    if (rounded == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(rounded);
    rounded = PyArray_Round((PyArrayObject *)darr, 1, NULL);
    if (rounded == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(rounded);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(empty);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    rounded = PyArray_Round((PyArrayObject *)objarr, 0, NULL);
    if (rounded == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(rounded);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(empty);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    rounded = PyArray_Round((PyArrayObject *)carr, 0, NULL);
    if (rounded == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(rounded);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(out_arr);
    Py_DECREF(empty);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_sum_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 2; data[1] = -3; data[2] = 5;
    PyObject *sum = PyArray_Sum((PyArrayObject *)arr, 0, NPY_NOTYPE, NULL);
    if (sum != NULL && PyLong_AsLong(sum) == 4) score += 1;
    Py_XDECREF(sum);
    sum = PyArray_Sum((PyArrayObject *)arr, -1, NPY_NOTYPE, NULL);
    if (sum != NULL && PyLong_AsLong(sum) == 4) score += 10;
    Py_XDECREF(sum);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = 2.5;
    sum = PyArray_Sum((PyArrayObject *)darr, 0, NPY_NOTYPE, NULL);
    if (sum != NULL && PyFloat_AsDouble(sum) == 1.0) score += 100;
    Py_XDECREF(sum);
    sum = PyArray_Sum((PyArrayObject *)arr, 0, NPY_DOUBLE, NULL);
    if (sum != NULL && PyFloat_AsDouble(sum) == 4.0) score += 1000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(sum);
    PyObject *out_arr = PyArray_SimpleNew(0, NULL, NPY_DOUBLE);
    if (out_arr == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    sum = PyArray_Sum((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)out_arr);
    if (sum == out_arr && PyArray_SIZE((PyArrayObject *)out_arr) == 1) {
        double *out_data = (double *)PyArray_DATA((PyArrayObject *)out_arr);
        if (out_data != NULL && out_data[0] == 4.0) score += 10000;
    } else if (sum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(sum);
    PyObject *bad_out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (bad_out_arr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    sum = PyArray_Sum((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)bad_out_arr);
    if (sum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(sum);
    sum = PyArray_Sum((PyArrayObject *)arr, 1, NPY_NOTYPE, NULL);
    if (sum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(sum);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    sum = PyArray_Sum((PyArrayObject *)objarr, 0, NPY_NOTYPE, NULL);
    if (sum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(sum);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    sum = PyArray_Sum((PyArrayObject *)carr, 0, NPY_NOTYPE, NULL);
    if (sum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(sum);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(bad_out_arr);
    Py_DECREF(out_arr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_prod_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 2; data[1] = -3; data[2] = 5;
    PyObject *prod = PyArray_Prod((PyArrayObject *)arr, 0, NPY_NOTYPE, NULL);
    if (prod != NULL && PyLong_AsLong(prod) == -30) score += 1;
    Py_XDECREF(prod);
    prod = PyArray_Prod((PyArrayObject *)arr, -1, NPY_NOTYPE, NULL);
    if (prod != NULL && PyLong_AsLong(prod) == -30) score += 10;
    Py_XDECREF(prod);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = 2.0;
    prod = PyArray_Prod((PyArrayObject *)darr, 0, NPY_NOTYPE, NULL);
    if (prod != NULL && PyFloat_AsDouble(prod) == -3.0) score += 100;
    Py_XDECREF(prod);
    prod = PyArray_Prod((PyArrayObject *)arr, 0, NPY_DOUBLE, NULL);
    if (prod != NULL && PyFloat_AsDouble(prod) == -30.0) score += 1000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(prod);
    PyObject *out_arr = PyArray_SimpleNew(0, NULL, NPY_DOUBLE);
    PyObject *bad_out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (out_arr == NULL || bad_out_arr == NULL) {
        Py_XDECREF(bad_out_arr);
        Py_XDECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    prod = PyArray_Prod((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)out_arr);
    if (prod == out_arr && ((double *)PyArray_DATA((PyArrayObject *)out_arr))[0] == -30.0) score += 10000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(prod);
    prod = PyArray_Prod((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)bad_out_arr);
    if (prod == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(prod);
    prod = PyArray_Prod((PyArrayObject *)arr, 1, NPY_NOTYPE, NULL);
    if (prod == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(prod);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    prod = PyArray_Prod((PyArrayObject *)objarr, 0, NPY_NOTYPE, NULL);
    if (prod == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(prod);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    prod = PyArray_Prod((PyArrayObject *)carr, 0, NPY_NOTYPE, NULL);
    if (prod == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(prod);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(bad_out_arr);
    Py_DECREF(out_arr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_cumsum_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 1; data[1] = 2; data[2] = 3;
    PyObject *cum = PyArray_CumSum((PyArrayObject *)arr, 0, NPY_NOTYPE, NULL);
    if (cum != NULL && PyArray_TYPE((PyArrayObject *)cum) == NPY_INT && PyArray_SIZE((PyArrayObject *)cum) == 3) {
        int *out = (int *)PyArray_DATA((PyArrayObject *)cum);
        if (out[0] == 1 && out[1] == 3 && out[2] == 6) score += 1;
    }
    Py_XDECREF(cum);
    cum = PyArray_CumSum((PyArrayObject *)arr, -1, NPY_NOTYPE, NULL);
    if (cum != NULL) {
        int *out = (int *)PyArray_DATA((PyArrayObject *)cum);
        if (out[0] == 1 && out[1] == 3 && out[2] == 6) score += 10;
    }
    Py_XDECREF(cum);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = 0.5; ddata[1] = 1.5;
    cum = PyArray_CumSum((PyArrayObject *)darr, 0, NPY_NOTYPE, NULL);
    if (cum != NULL && PyArray_TYPE((PyArrayObject *)cum) == NPY_DOUBLE) {
        double *out = (double *)PyArray_DATA((PyArrayObject *)cum);
        if (out[0] == 0.5 && out[1] == 2.0) score += 100;
    }
    Py_XDECREF(cum);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    cum = PyArray_CumSum((PyArrayObject *)empty, 0, NPY_NOTYPE, NULL);
    if (cum != NULL && PyArray_TYPE((PyArrayObject *)cum) == NPY_INT && PyArray_SIZE((PyArrayObject *)cum) == 0) score += 1000;
    Py_XDECREF(cum);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *bad_out_arr = PyArray_SimpleNew(1, ddims, NPY_INT);
    if (out_arr == NULL || bad_out_arr == NULL) {
        Py_XDECREF(bad_out_arr);
        Py_XDECREF(out_arr);
        Py_DECREF(empty);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    cum = PyArray_CumSum((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)out_arr);
    if (cum == out_arr && PyArray_TYPE((PyArrayObject *)out_arr) == NPY_INT) {
        int *out = (int *)PyArray_DATA((PyArrayObject *)out_arr);
        if (out[0] == 1 && out[1] == 3 && out[2] == 6) score += 10000;
    } else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(cum);
    cum = PyArray_CumSum((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)bad_out_arr);
    if (cum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000000; }
    Py_XDECREF(cum);
    cum = PyArray_CumSum((PyArrayObject *)arr, 1, NPY_NOTYPE, NULL);
    if (cum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(cum);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(empty);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    cum = PyArray_CumSum((PyArrayObject *)objarr, 0, NPY_NOTYPE, NULL);
    if (cum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(cum);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(empty);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    cum = PyArray_CumSum((PyArrayObject *)carr, 0, NPY_NOTYPE, NULL);
    if (cum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(cum);
    cum = PyArray_CumSum((PyArrayObject *)arr, 0, NPY_DOUBLE, NULL);
    if (cum != NULL && PyArray_TYPE((PyArrayObject *)cum) == NPY_DOUBLE && PyArray_SIZE((PyArrayObject *)cum) == 3) {
        double *out = (double *)PyArray_DATA((PyArrayObject *)cum);
        if (out[0] == 1.0 && out[1] == 3.0 && out[2] == 6.0) score += 100000000;
    } else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(cum);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(bad_out_arr);
    Py_DECREF(out_arr);
    Py_DECREF(empty);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_cumprod_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 2; data[1] = 3; data[2] = 4;
    PyObject *cum = PyArray_CumProd((PyArrayObject *)arr, 0, NPY_NOTYPE, NULL);
    if (cum != NULL && PyArray_TYPE((PyArrayObject *)cum) == NPY_INT && PyArray_SIZE((PyArrayObject *)cum) == 3) {
        int *out = (int *)PyArray_DATA((PyArrayObject *)cum);
        if (out[0] == 2 && out[1] == 6 && out[2] == 24) score += 1;
    }
    Py_XDECREF(cum);
    cum = PyArray_CumProd((PyArrayObject *)arr, -1, NPY_NOTYPE, NULL);
    if (cum != NULL) {
        int *out = (int *)PyArray_DATA((PyArrayObject *)cum);
        if (out[0] == 2 && out[1] == 6 && out[2] == 24) score += 10;
    }
    Py_XDECREF(cum);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = 0.5; ddata[1] = 4.0;
    cum = PyArray_CumProd((PyArrayObject *)darr, 0, NPY_NOTYPE, NULL);
    if (cum != NULL && PyArray_TYPE((PyArrayObject *)cum) == NPY_DOUBLE) {
        double *out = (double *)PyArray_DATA((PyArrayObject *)cum);
        if (out[0] == 0.5 && out[1] == 2.0) score += 100;
    }
    Py_XDECREF(cum);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    cum = PyArray_CumProd((PyArrayObject *)empty, 0, NPY_NOTYPE, NULL);
    if (cum != NULL && PyArray_TYPE((PyArrayObject *)cum) == NPY_INT && PyArray_SIZE((PyArrayObject *)cum) == 0) score += 1000;
    Py_XDECREF(cum);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *bad_out_arr = PyArray_SimpleNew(1, ddims, NPY_INT);
    if (out_arr == NULL || bad_out_arr == NULL) {
        Py_XDECREF(bad_out_arr);
        Py_XDECREF(out_arr);
        Py_DECREF(empty);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    cum = PyArray_CumProd((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)out_arr);
    if (cum == out_arr && PyArray_TYPE((PyArrayObject *)out_arr) == NPY_INT) {
        int *out = (int *)PyArray_DATA((PyArrayObject *)out_arr);
        if (out[0] == 2 && out[1] == 6 && out[2] == 24) score += 10000;
    } else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(cum);
    cum = PyArray_CumProd((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)bad_out_arr);
    if (cum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000000; }
    Py_XDECREF(cum);
    cum = PyArray_CumProd((PyArrayObject *)arr, 1, NPY_NOTYPE, NULL);
    if (cum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(cum);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(empty);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    cum = PyArray_CumProd((PyArrayObject *)objarr, 0, NPY_NOTYPE, NULL);
    if (cum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(cum);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(empty);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    cum = PyArray_CumProd((PyArrayObject *)carr, 0, NPY_NOTYPE, NULL);
    if (cum == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(cum);
    cum = PyArray_CumProd((PyArrayObject *)arr, 0, NPY_DOUBLE, NULL);
    if (cum != NULL && PyArray_TYPE((PyArrayObject *)cum) == NPY_DOUBLE && PyArray_SIZE((PyArrayObject *)cum) == 3) {
        double *out = (double *)PyArray_DATA((PyArrayObject *)cum);
        if (out[0] == 2.0 && out[1] == 6.0 && out[2] == 24.0) score += 100000000;
    } else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(cum);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(bad_out_arr);
    Py_DECREF(out_arr);
    Py_DECREF(empty);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_max_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = -7; data[1] = 5; data[2] = 2;
    PyObject *maxv = PyArray_Max((PyArrayObject *)arr, 0, NULL);
    if (maxv != NULL && PyLong_AsLong(maxv) == 5) score += 1;
    Py_XDECREF(maxv);
    maxv = PyArray_Max((PyArrayObject *)arr, -1, NULL);
    if (maxv != NULL && PyLong_AsLong(maxv) == 5) score += 10;
    Py_XDECREF(maxv);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = 2.0;
    maxv = PyArray_Max((PyArrayObject *)darr, 0, NULL);
    if (maxv != NULL && PyFloat_AsDouble(maxv) == 2.0) score += 100;
    Py_XDECREF(maxv);
    PyObject *out_arr = PyArray_SimpleNew(0, NULL, NPY_INT);
    if (out_arr == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    maxv = PyArray_Max((PyArrayObject *)arr, 0, (PyArrayObject *)out_arr);
    if (maxv == out_arr && PyArray_SIZE((PyArrayObject *)out_arr) == 1) {
        int *out_data = (int *)PyArray_DATA((PyArrayObject *)out_arr);
        if (out_data != NULL && out_data[0] == 5) score += 1000;
    } else if (maxv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(maxv);
    PyObject *bad_out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (bad_out_arr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    maxv = PyArray_Max((PyArrayObject *)arr, 0, (PyArrayObject *)bad_out_arr);
    if (maxv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(maxv);
    maxv = PyArray_Max((PyArrayObject *)arr, 1, NULL);
    if (maxv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(maxv);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    maxv = PyArray_Max((PyArrayObject *)objarr, 0, NULL);
    if (maxv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(maxv);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    maxv = PyArray_Max((PyArrayObject *)carr, 0, NULL);
    if (maxv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(maxv);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(carr);
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    maxv = PyArray_Max((PyArrayObject *)empty, 0, NULL);
    if (maxv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(maxv);
    Py_DECREF(empty);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(bad_out_arr);
    Py_DECREF(out_arr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_min_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = -7; data[1] = 5; data[2] = 2;
    PyObject *minv = PyArray_Min((PyArrayObject *)arr, 0, NULL);
    if (minv != NULL && PyLong_AsLong(minv) == -7) score += 1;
    Py_XDECREF(minv);
    minv = PyArray_Min((PyArrayObject *)arr, -1, NULL);
    if (minv != NULL && PyLong_AsLong(minv) == -7) score += 10;
    Py_XDECREF(minv);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = 2.0;
    minv = PyArray_Min((PyArrayObject *)darr, 0, NULL);
    if (minv != NULL && PyFloat_AsDouble(minv) == -1.5) score += 100;
    Py_XDECREF(minv);
    PyObject *out_arr = PyArray_SimpleNew(0, NULL, NPY_INT);
    if (out_arr == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    minv = PyArray_Min((PyArrayObject *)arr, 0, (PyArrayObject *)out_arr);
    if (minv == out_arr && PyArray_SIZE((PyArrayObject *)out_arr) == 1) {
        int *out_data = (int *)PyArray_DATA((PyArrayObject *)out_arr);
        if (out_data != NULL && out_data[0] == -7) score += 1000;
    } else if (minv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(minv);
    PyObject *bad_out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (bad_out_arr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    minv = PyArray_Min((PyArrayObject *)arr, 0, (PyArrayObject *)bad_out_arr);
    if (minv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(minv);
    minv = PyArray_Min((PyArrayObject *)arr, 1, NULL);
    if (minv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(minv);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    minv = PyArray_Min((PyArrayObject *)objarr, 0, NULL);
    if (minv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(minv);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    minv = PyArray_Min((PyArrayObject *)carr, 0, NULL);
    if (minv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(minv);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(carr);
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    minv = PyArray_Min((PyArrayObject *)empty, 0, NULL);
    if (minv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(minv);
    Py_DECREF(empty);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(bad_out_arr);
    Py_DECREF(out_arr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_ptp_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {4};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = -3; data[1] = 7; data[2] = 2; data[3] = -1;
    PyObject *ptp = PyArray_Ptp((PyArrayObject *)arr, 0, NULL);
    if (ptp != NULL && PyLong_AsLong(ptp) == 10) score += 1;
    Py_XDECREF(ptp);
    ptp = PyArray_Ptp((PyArrayObject *)arr, -1, NULL);
    if (ptp != NULL && PyLong_AsLong(ptp) == 10) score += 10;
    Py_XDECREF(ptp);
    npy_intp ddims[1] = {3};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = 2.25; ddata[2] = 0.25;
    ptp = PyArray_Ptp((PyArrayObject *)darr, 0, NULL);
    if (ptp != NULL) {
        double got = PyFloat_AsDouble(ptp);
        if (got > 3.749 && got < 3.751) score += 100;
    }
    Py_XDECREF(ptp);
    PyObject *out_arr = PyArray_SimpleNew(0, NULL, NPY_INT);
    if (out_arr == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    ptp = PyArray_Ptp((PyArrayObject *)arr, 0, (PyArrayObject *)out_arr);
    if (ptp == out_arr && PyArray_SIZE((PyArrayObject *)out_arr) == 1) {
        int *out_data = (int *)PyArray_DATA((PyArrayObject *)out_arr);
        if (out_data != NULL && out_data[0] == 10) score += 1000;
    } else if (ptp == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(ptp);
    PyObject *bad_out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (bad_out_arr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    ptp = PyArray_Ptp((PyArrayObject *)arr, 0, (PyArrayObject *)bad_out_arr);
    if (ptp == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(ptp);
    ptp = PyArray_Ptp((PyArrayObject *)arr, 1, NULL);
    if (ptp == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(ptp);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    ptp = PyArray_Ptp((PyArrayObject *)objarr, 0, NULL);
    if (ptp == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(ptp);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    ptp = PyArray_Ptp((PyArrayObject *)carr, 0, NULL);
    if (ptp == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(ptp);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(carr);
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    ptp = PyArray_Ptp((PyArrayObject *)empty, 0, NULL);
    if (ptp == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(ptp);
    Py_DECREF(empty);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(bad_out_arr);
    Py_DECREF(out_arr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_mean_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {4};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = -3; data[1] = 7; data[2] = 2; data[3] = -2;
    PyObject *mean = PyArray_Mean((PyArrayObject *)arr, 0, NPY_NOTYPE, NULL);
    if (mean != NULL && PyFloat_AsDouble(mean) == 1.0) score += 1;
    Py_XDECREF(mean);
    mean = PyArray_Mean((PyArrayObject *)arr, -1, NPY_NOTYPE, NULL);
    if (mean != NULL && PyFloat_AsDouble(mean) == 1.0) score += 10;
    Py_XDECREF(mean);
    npy_intp ddims[1] = {3};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = 2.25; ddata[2] = 0.75;
    mean = PyArray_Mean((PyArrayObject *)darr, 0, NPY_NOTYPE, NULL);
    if (mean != NULL) {
        double got = PyFloat_AsDouble(mean);
        if (got > 0.499 && got < 0.501) score += 100;
    }
    Py_XDECREF(mean);
    PyObject *out_arr = PyArray_SimpleNew(0, NULL, NPY_DOUBLE);
    if (out_arr == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    mean = PyArray_Mean((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)out_arr);
    if (mean == out_arr && PyArray_SIZE((PyArrayObject *)out_arr) == 1) {
        double *out_data = (double *)PyArray_DATA((PyArrayObject *)out_arr);
        if (out_data != NULL && out_data[0] == 1.0) score += 1000;
    } else if (mean == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(mean);
    PyObject *bad_out_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (bad_out_arr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    mean = PyArray_Mean((PyArrayObject *)arr, 0, NPY_NOTYPE, (PyArrayObject *)bad_out_arr);
    if (mean == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000000; }
    Py_XDECREF(mean);
    mean = PyArray_Mean((PyArrayObject *)arr, 1, NPY_NOTYPE, NULL);
    if (mean == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(mean);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    mean = PyArray_Mean((PyArrayObject *)objarr, 0, NPY_NOTYPE, NULL);
    if (mean == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(mean);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    mean = PyArray_Mean((PyArrayObject *)carr, 0, NPY_NOTYPE, NULL);
    if (mean == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(mean);
    mean = PyArray_Mean((PyArrayObject *)arr, 0, NPY_DOUBLE, NULL);
    if (mean != NULL && PyFloat_AsDouble(mean) == 1.0) score += 10000000;
    else if (PyErr_Occurred() != NULL) { PyErr_Clear(); }
    Py_XDECREF(mean);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(carr);
        Py_DECREF(objarr);
        Py_DECREF(bad_out_arr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    mean = PyArray_Mean((PyArrayObject *)empty, 0, NPY_NOTYPE, NULL);
    if (mean == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(mean);
    Py_DECREF(empty);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(bad_out_arr);
    Py_DECREF(out_arr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_any_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 0; data[1] = 0; data[2] = 3;
    PyObject *anyv = PyArray_Any((PyArrayObject *)arr, 0, NULL);
    if (anyv != NULL && PyObject_IsTrue(anyv) == 1) score += 1;
    Py_XDECREF(anyv);
    anyv = PyArray_Any((PyArrayObject *)arr, -1, NULL);
    if (anyv != NULL && PyObject_IsTrue(anyv) == 1) score += 10;
    Py_XDECREF(anyv);
    data[2] = 0;
    anyv = PyArray_Any((PyArrayObject *)arr, 0, NULL);
    if (anyv != NULL && PyObject_IsTrue(anyv) == 0 && PyErr_Occurred() == NULL) score += 100;
    Py_XDECREF(anyv);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = 0.0; ddata[1] = 0.25;
    anyv = PyArray_Any((PyArrayObject *)darr, 0, NULL);
    if (anyv != NULL && PyObject_IsTrue(anyv) == 1) score += 1000;
    Py_XDECREF(anyv);
    npy_intp bdims[1] = {2};
    PyObject *barr = PyArray_SimpleNew(1, bdims, NPY_BOOL);
    if (barr == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    unsigned char *bdata = (unsigned char *)PyArray_DATA((PyArrayObject *)barr);
    bdata[0] = 0; bdata[1] = 0;
    anyv = PyArray_Any((PyArrayObject *)barr, 0, NULL);
    if (anyv != NULL && PyObject_IsTrue(anyv) == 0 && PyErr_Occurred() == NULL) score += 10000;
    Py_XDECREF(anyv);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_BOOL);
    if (out_arr == NULL) {
        Py_DECREF(barr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    anyv = PyArray_Any((PyArrayObject *)arr, 0, (PyArrayObject *)out_arr);
    if (anyv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(anyv);
    anyv = PyArray_Any((PyArrayObject *)arr, 1, NULL);
    if (anyv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(anyv);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(barr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    anyv = PyArray_Any((PyArrayObject *)objarr, 0, NULL);
    if (anyv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(anyv);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(barr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    anyv = PyArray_Any((PyArrayObject *)carr, 0, NULL);
    if (anyv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(anyv);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(carr);
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(barr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    anyv = PyArray_Any((PyArrayObject *)empty, 0, NULL);
    if (anyv != NULL && PyObject_IsTrue(anyv) == 0 && PyErr_Occurred() == NULL) score += 1000000000;
    Py_XDECREF(anyv);
    Py_DECREF(empty);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(out_arr);
    Py_DECREF(barr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_all_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 1; data[1] = 2; data[2] = 3;
    PyObject *allv = PyArray_All((PyArrayObject *)arr, 0, NULL);
    if (allv != NULL && PyObject_IsTrue(allv) == 1) score += 1;
    Py_XDECREF(allv);
    allv = PyArray_All((PyArrayObject *)arr, -1, NULL);
    if (allv != NULL && PyObject_IsTrue(allv) == 1) score += 10;
    Py_XDECREF(allv);
    data[1] = 0;
    allv = PyArray_All((PyArrayObject *)arr, 0, NULL);
    if (allv != NULL && PyObject_IsTrue(allv) == 0 && PyErr_Occurred() == NULL) score += 100;
    Py_XDECREF(allv);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = 1.0; ddata[1] = 0.25;
    allv = PyArray_All((PyArrayObject *)darr, 0, NULL);
    if (allv != NULL && PyObject_IsTrue(allv) == 1) score += 1000;
    Py_XDECREF(allv);
    npy_intp bdims[1] = {2};
    PyObject *barr = PyArray_SimpleNew(1, bdims, NPY_BOOL);
    if (barr == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    unsigned char *bdata = (unsigned char *)PyArray_DATA((PyArrayObject *)barr);
    bdata[0] = 1; bdata[1] = 0;
    allv = PyArray_All((PyArrayObject *)barr, 0, NULL);
    if (allv != NULL && PyObject_IsTrue(allv) == 0 && PyErr_Occurred() == NULL) score += 10000;
    Py_XDECREF(allv);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_BOOL);
    if (out_arr == NULL) {
        Py_DECREF(barr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    allv = PyArray_All((PyArrayObject *)arr, 0, (PyArrayObject *)out_arr);
    if (allv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(allv);
    allv = PyArray_All((PyArrayObject *)arr, 1, NULL);
    if (allv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(allv);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(barr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    allv = PyArray_All((PyArrayObject *)objarr, 0, NULL);
    if (allv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(allv);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(barr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    allv = PyArray_All((PyArrayObject *)carr, 0, NULL);
    if (allv == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(allv);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(carr);
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(barr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    allv = PyArray_All((PyArrayObject *)empty, 0, NULL);
    if (allv != NULL && PyObject_IsTrue(allv) == 1) score += 1000000000;
    Py_XDECREF(allv);
    Py_DECREF(empty);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(out_arr);
    Py_DECREF(barr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_argmax_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = -7; data[1] = 5; data[2] = 5;
    PyObject *idx = PyArray_ArgMax((PyArrayObject *)arr, 0, NULL);
    if (idx != NULL && PyArray_NDIM((PyArrayObject *)idx) == 0 && PyArray_TYPE((PyArrayObject *)idx) == NPY_LONG && *((long *)PyArray_DATA((PyArrayObject *)idx)) == 1) score += 1;
    Py_XDECREF(idx);
    idx = PyArray_ArgMax((PyArrayObject *)arr, -1, NULL);
    if (idx != NULL && *((long *)PyArray_DATA((PyArrayObject *)idx)) == 1) score += 10;
    Py_XDECREF(idx);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = 2.0;
    idx = PyArray_ArgMax((PyArrayObject *)darr, 0, NULL);
    if (idx != NULL && *((long *)PyArray_DATA((PyArrayObject *)idx)) == 1) score += 100;
    Py_XDECREF(idx);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (out_arr == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    idx = PyArray_ArgMax((PyArrayObject *)arr, 0, (PyArrayObject *)out_arr);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(idx);
    idx = PyArray_ArgMax((PyArrayObject *)arr, 1, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(idx);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    idx = PyArray_ArgMax((PyArrayObject *)objarr, 0, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(idx);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    idx = PyArray_ArgMax((PyArrayObject *)carr, 0, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(idx);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(carr);
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    idx = PyArray_ArgMax((PyArrayObject *)empty, 0, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(idx);
    Py_DECREF(empty);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(out_arr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_argmin_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = -7; data[1] = -7; data[2] = 2;
    PyObject *idx = PyArray_ArgMin((PyArrayObject *)arr, 0, NULL);
    if (idx != NULL && PyArray_NDIM((PyArrayObject *)idx) == 0 && PyArray_TYPE((PyArrayObject *)idx) == NPY_LONG && *((long *)PyArray_DATA((PyArrayObject *)idx)) == 0) score += 1;
    Py_XDECREF(idx);
    idx = PyArray_ArgMin((PyArrayObject *)arr, -1, NULL);
    if (idx != NULL && *((long *)PyArray_DATA((PyArrayObject *)idx)) == 0) score += 10;
    Py_XDECREF(idx);
    npy_intp ddims[1] = {2};
    PyObject *darr = PyArray_SimpleNew(1, ddims, NPY_DOUBLE);
    if (darr == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    double *ddata = (double *)PyArray_DATA((PyArrayObject *)darr);
    ddata[0] = -1.5; ddata[1] = -2.0;
    idx = PyArray_ArgMin((PyArrayObject *)darr, 0, NULL);
    if (idx != NULL && *((long *)PyArray_DATA((PyArrayObject *)idx)) == 1) score += 100;
    Py_XDECREF(idx);
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (out_arr == NULL) {
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    idx = PyArray_ArgMin((PyArrayObject *)arr, 0, (PyArrayObject *)out_arr);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(idx);
    idx = PyArray_ArgMin((PyArrayObject *)arr, 1, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(idx);
    PyObject *objarr = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    if (objarr == NULL) {
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    idx = PyArray_ArgMin((PyArrayObject *)objarr, 0, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(idx);
    PyObject *carr = PyArray_SimpleNew(1, ddims, NPY_CDOUBLE);
    if (carr == NULL) {
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    idx = PyArray_ArgMin((PyArrayObject *)carr, 0, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(idx);
    npy_intp edims[1] = {0};
    PyObject *empty = PyArray_SimpleNew(1, edims, NPY_INT);
    if (empty == NULL) {
        Py_DECREF(carr);
        Py_DECREF(objarr);
        Py_DECREF(out_arr);
        Py_DECREF(darr);
        Py_DECREF(arr);
        return NULL;
    }
    idx = PyArray_ArgMin((PyArrayObject *)empty, 0, NULL);
    if (idx == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(idx);
    Py_DECREF(empty);
    Py_DECREF(carr);
    Py_DECREF(objarr);
    Py_DECREF(out_arr);
    Py_DECREF(darr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_reshape_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    if (data == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    for (int i = 0; i < 6; i++) data[i] = i + 1;
    PyObject *shape0 = PyLong_FromLong(3);
    PyObject *shape1 = PyLong_FromLong(2);
    PyObject *shape = NULL;
    if (shape0 == NULL || shape1 == NULL) {
        Py_XDECREF(shape0);
        Py_XDECREF(shape1);
        Py_DECREF(arr);
        return NULL;
    }
    shape = PyTuple_Pack(2, shape0, shape1);
    Py_DECREF(shape0);
    Py_DECREF(shape1);
    if (shape == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    PyObject *reshaped = PyArray_Reshape((PyArrayObject *)arr, shape);
    if (reshaped != NULL) {
        PyArrayObject *reshaped_arr = (PyArrayObject *)reshaped;
        npy_intp *rdims = PyArray_DIMS(reshaped_arr);
        int *rdata = (int *)PyArray_DATA(reshaped_arr);
        if (PyArray_NDIM(reshaped_arr) == 2 && rdims != NULL && rdims[0] == 3 && rdims[1] == 2 && rdata == data && PyArray_BASE(reshaped_arr) == arr) score += 1;
        if (rdata != NULL) {
            rdata[3] = 44;
            if (data[3] == 44) score += 10;
        }
    }
    Py_XDECREF(reshaped);
    PyObject *flat_shape = PyLong_FromLong(6);
    if (flat_shape == NULL) {
        Py_DECREF(shape);
        Py_DECREF(arr);
        return NULL;
    }
    reshaped = PyArray_Reshape((PyArrayObject *)arr, flat_shape);
    if (reshaped != NULL) {
        npy_intp *rdims = PyArray_DIMS((PyArrayObject *)reshaped);
        if (PyArray_NDIM((PyArrayObject *)reshaped) == 1 && rdims != NULL && rdims[0] == 6 && PyArray_DATA((PyArrayObject *)reshaped) == data) score += 100;
    }
    Py_XDECREF(reshaped);
    PyObject *list_shape = PyList_New(2);
    if (list_shape == NULL || PyList_SetItem(list_shape, 0, PyLong_FromLong(1)) != 0 || PyList_SetItem(list_shape, 1, PyLong_FromLong(6)) != 0) {
        Py_XDECREF(list_shape);
        Py_DECREF(flat_shape);
        Py_DECREF(shape);
        Py_DECREF(arr);
        return NULL;
    }
    reshaped = PyArray_Reshape((PyArrayObject *)arr, list_shape);
    if (reshaped != NULL) {
        npy_intp *rdims = PyArray_DIMS((PyArrayObject *)reshaped);
        if (PyArray_NDIM((PyArrayObject *)reshaped) == 2 && rdims != NULL && rdims[0] == 1 && rdims[1] == 6 && PyArray_DATA((PyArrayObject *)reshaped) == data) score += 1000;
    }
    Py_XDECREF(reshaped);
    PyObject *bad0 = PyLong_FromLong(4);
    PyObject *bad1 = PyLong_FromLong(2);
    PyObject *bad_shape = bad0 == NULL || bad1 == NULL ? NULL : PyTuple_Pack(2, bad0, bad1);
    Py_XDECREF(bad0);
    Py_XDECREF(bad1);
    if (bad_shape == NULL) {
        Py_DECREF(list_shape);
        Py_DECREF(flat_shape);
        Py_DECREF(shape);
        Py_DECREF(arr);
        return NULL;
    }
    reshaped = PyArray_Reshape((PyArrayObject *)arr, bad_shape);
    if (reshaped == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(reshaped);
    PyObject *neg0 = PyLong_FromLong(-1);
    PyObject *neg1 = PyLong_FromLong(6);
    PyObject *neg_shape = neg0 == NULL || neg1 == NULL ? NULL : PyTuple_Pack(2, neg0, neg1);
    Py_XDECREF(neg0);
    Py_XDECREF(neg1);
    if (neg_shape == NULL) {
        Py_DECREF(bad_shape);
        Py_DECREF(list_shape);
        Py_DECREF(flat_shape);
        Py_DECREF(shape);
        Py_DECREF(arr);
        return NULL;
    }
    reshaped = PyArray_Reshape((PyArrayObject *)arr, neg_shape);
    if (reshaped == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(reshaped);
    reshaped = PyArray_Reshape((PyArrayObject *)arr, Py_None);
    if (reshaped == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(reshaped);
    npy_intp axes[2] = {1, 0};
    PyArray_Dims permute = {axes, 2};
    PyObject *transposed = PyArray_Transpose((PyArrayObject *)arr, &permute);
    if (transposed == NULL) {
        Py_DECREF(neg_shape);
        Py_DECREF(bad_shape);
        Py_DECREF(list_shape);
        Py_DECREF(flat_shape);
        Py_DECREF(shape);
        Py_DECREF(arr);
        return NULL;
    }
    reshaped = PyArray_Reshape((PyArrayObject *)transposed, flat_shape);
    if (reshaped == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(reshaped);
    reshaped = PyArray_Reshape((PyArrayObject *)Py_None, shape);
    if (reshaped == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(reshaped);
    Py_DECREF(transposed);
    Py_DECREF(neg_shape);
    Py_DECREF(bad_shape);
    Py_DECREF(list_shape);
    Py_DECREF(flat_shape);
    Py_DECREF(shape);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_newshape_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);
    if (arr == NULL) return NULL;
    int *data = (int *)PyArray_DATA((PyArrayObject *)arr);
    if (data == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    for (int i = 0; i < 6; i++) data[i] = i + 1;
    npy_intp view_dims[2] = {3, 2};
    PyArray_Dims newshape = {view_dims, 2};
    PyObject *view = PyArray_Newshape((PyArrayObject *)arr, &newshape, NPY_CORDER);
    if (view != NULL) {
        PyArrayObject *view_arr = (PyArrayObject *)view;
        npy_intp *rdims = PyArray_DIMS(view_arr);
        int *rdata = (int *)PyArray_DATA(view_arr);
        if (PyArray_NDIM(view_arr) == 2 && rdims != NULL && rdims[0] == 3 && rdims[1] == 2 && rdata == data && PyArray_BASE(view_arr) == arr) score += 1;
        if (rdata != NULL) {
            rdata[4] = 55;
            if (data[4] == 55) score += 10;
        }
    }
    Py_XDECREF(view);
    view = PyArray_Newshape((PyArrayObject *)arr, &newshape, NPY_ANYORDER);
    if (view != NULL && PyArray_DATA((PyArrayObject *)view) == data) score += 100;
    Py_XDECREF(view);
    view = PyArray_Newshape((PyArrayObject *)arr, &newshape, NPY_KEEPORDER);
    if (view != NULL && PyArray_DATA((PyArrayObject *)view) == data) score += 1000;
    Py_XDECREF(view);
    npy_intp flat_dims[1] = {6};
    PyArray_Dims flatshape = {flat_dims, 1};
    PyObject *flat = PyArray_SimpleNew(1, flat_dims, NPY_INT);
    if (flat == NULL) {
        Py_DECREF(arr);
        return NULL;
    }
    view = PyArray_Newshape((PyArrayObject *)flat, &flatshape, NPY_FORTRANORDER);
    if (view != NULL && PyArray_NDIM((PyArrayObject *)view) == 1 && PyArray_DATA((PyArrayObject *)view) == PyArray_DATA((PyArrayObject *)flat)) score += 10000;
    Py_XDECREF(view);
    view = PyArray_Newshape((PyArrayObject *)arr, &newshape, NPY_FORTRANORDER);
    if (view == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(view);
    npy_intp bad_dims[2] = {4, 2};
    PyArray_Dims badshape = {bad_dims, 2};
    view = PyArray_Newshape((PyArrayObject *)arr, &badshape, NPY_CORDER);
    if (view == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(view);
    npy_intp neg_dims[2] = {-1, 6};
    PyArray_Dims negshape = {neg_dims, 2};
    view = PyArray_Newshape((PyArrayObject *)arr, &negshape, NPY_CORDER);
    if (view == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_XDECREF(view);
    PyArray_Dims invalid_ptr = {NULL, 1};
    view = PyArray_Newshape((PyArrayObject *)arr, &invalid_ptr, NPY_CORDER);
    if (view == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000; }
    Py_XDECREF(view);
    view = PyArray_Newshape((PyArrayObject *)arr, NULL, NPY_CORDER);
    if (view == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000000; }
    Py_XDECREF(view);
    npy_intp axes[2] = {1, 0};
    PyArray_Dims permute = {axes, 2};
    PyObject *transposed = PyArray_Transpose((PyArrayObject *)arr, &permute);
    if (transposed == NULL) {
        Py_DECREF(flat);
        Py_DECREF(arr);
        return NULL;
    }
    view = PyArray_Newshape((PyArrayObject *)transposed, &newshape, NPY_CORDER);
    if (view == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000000; }
    Py_XDECREF(view);
    view = PyArray_Newshape((PyArrayObject *)Py_None, &newshape, NPY_CORDER);
    if (view == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000000; }
    Py_XDECREF(view);
    Py_DECREF(transposed);
    Py_DECREF(flat);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_ensure_array_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *src_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *list = PyList_New(2);
    if (src_obj == NULL || list == NULL) {
        Py_XDECREF(src_obj);
        Py_XDECREF(list);
        return NULL;
    }
    PyArrayObject *src = (PyArrayObject *)src_obj;
    int *src_data = (int *)PyArray_DATA(src);
    if (src_data == NULL) {
        Py_DECREF(src_obj);
        Py_DECREF(list);
        return NULL;
    }
    src_data[0] = 31;
    src_data[1] = 32;
    if (PyList_SetItem(list, 0, PyLong_FromLong(41)) != 0) {
        Py_DECREF(src_obj);
        Py_DECREF(list);
        return NULL;
    }
    if (PyList_SetItem(list, 1, PyLong_FromLong(42)) != 0) {
        Py_DECREF(src_obj);
        Py_DECREF(list);
        return NULL;
    }
    Py_INCREF(src_obj);
    PyObject *same_obj = PyArray_EnsureArray(src_obj);
    PyObject *from_list = PyArray_EnsureArray(list);
    Py_INCREF(src_obj);
    PyObject *any_obj = PyArray_EnsureAnyArray(src_obj);
    if (same_obj == NULL || from_list == NULL || any_obj == NULL) {
        Py_XDECREF(same_obj);
        Py_XDECREF(from_list);
        Py_XDECREF(any_obj);
        Py_DECREF(src_obj);
        return NULL;
    }
    PyArrayObject *list_arr = (PyArrayObject *)from_list;
    long score = 0;
    if (same_obj == src_obj && PyArray_CheckExact((PyArrayObject *)same_obj)) score += 1;
    if (any_obj == src_obj && PyArray_Check((PyArrayObject *)any_obj)) score += 10;
    if (from_list != list && PyArray_CheckExact(list_arr) && PyArray_SIZE(list_arr) == 2) score += 100;
    PyObject *item = PyArray_ToScalar(PyArray_DATA(list_arr), list_arr);
    if (item != NULL && PyLong_Check(item) && PyLong_AsLong(item) == 41) score += 1000;
    Py_XDECREF(item);
    Py_DECREF(same_obj);
    Py_DECREF(from_list);
    Py_DECREF(any_obj);
    Py_DECREF(src_obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_check_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {1};
    PyArray_Descr *descr = PyArray_DescrFromType(NPY_INT);
    PyObject *arr_obj = PyArray_SimpleNew(1, dims, NPY_INT);
    if (descr == NULL || arr_obj == NULL) {
        Py_XDECREF(arr_obj);
        return NULL;
    }
    PyArrayObject *arr = (PyArrayObject *)arr_obj;
    long score = 0;
    if (PyArray_DescrCheck((PyObject *)descr)) score += 1;
    if (PyArray_DescrCheck((PyObject *)PyArray_DESCR(arr))) score += 10;
    if (!PyArray_DescrCheck(arr_obj)) score += 100;
    if (!PyArray_DescrCheck(Py_None)) score += 1000;
    Py_DECREF(arr_obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_new_from_type_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyArray_Descr *descr = PyArray_DescrNewFromType(NPY_DOUBLE);
    long score = 0;
    if (descr != NULL && PyArray_DescrCheck((PyObject *)descr)) score += 1;
    if (descr != NULL && PyDataType_TYPE(descr) == NPY_DOUBLE) score += 10;
    if (descr != NULL && PyDataType_KIND(descr) == 'f') score += 100;
    if (descr != NULL && PyDataType_ELSIZE(descr) == (int)sizeof(double)) score += 1000;
    Py_XDECREF((PyObject *)descr);
    PyArray_Descr *bad = PyArray_DescrNewFromType(9999);
    if (bad == NULL && PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 10000;
    Py_XDECREF((PyObject *)bad);
    PyErr_Clear();
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_new_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyArray_Descr *base = PyArray_DescrFromType(NPY_DOUBLE);
    PyArray_Descr *copy = PyArray_DescrNew(base);
    long score = 0;
    if (copy != NULL && PyArray_DescrCheck((PyObject *)copy)) score += 1;
    if (copy != NULL && PyDataType_TYPE(copy) == NPY_DOUBLE) score += 10;
    if (copy != NULL && PyDataType_KIND(copy) == 'f') score += 100;
    if (copy != NULL && PyDataType_ELSIZE(copy) == (int)sizeof(double)) score += 1000;
    if (copy != NULL && copy != base) score += 10000;
    Py_XDECREF((PyObject *)copy);
    PyArray_Descr *bad = PyArray_DescrNew(NULL);
    if (bad == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 100000;
    Py_XDECREF((PyObject *)bad);
    PyErr_Clear();
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_new_byteorder_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyArray_Descr *base = PyArray_DescrFromType(NPY_INT);
    PyArray_Descr *big = PyArray_DescrNewByteorder(base, NPY_BIG);
    PyArray_Descr *little = PyArray_DescrNewByteorder(base, NPY_LITTLE);
    PyArray_Descr *native = PyArray_DescrNewByteorder(base, NPY_NATBYTE);
    long score = 0;
    if (big != NULL && PyArray_DescrCheck((PyObject *)big)) score += 1;
    if (big != NULL && PyDataType_TYPE(big) == NPY_INT) score += 10;
    if (big != NULL && PyDataType_KIND(big) == 'i') score += 100;
    if (big != NULL && PyDataType_ELSIZE(big) == (int)sizeof(int)) score += 1000;
    if (big != NULL && big != base && big->byteorder == NPY_BIG) score += 10000;
    if (little != NULL && little != base && little->byteorder == NPY_LITTLE) score += 100000;
    if (native != NULL && native != base && native->byteorder == NPY_NATBYTE) score += 1000000;
    Py_XDECREF((PyObject *)big);
    Py_XDECREF((PyObject *)little);
    Py_XDECREF((PyObject *)native);
    PyArray_Descr *bad = PyArray_DescrNewByteorder(NULL, NPY_NATBYTE);
    if (bad == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 10000000;
    Py_XDECREF((PyObject *)bad);
    PyErr_Clear();
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_can_cast_safely_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    if (PyArray_CanCastSafely(NPY_BOOL, NPY_INT)) score += 1;
    if (PyArray_CanCastSafely(NPY_SHORT, NPY_LONGLONG)) score += 10;
    if (PyArray_CanCastSafely(NPY_UBYTE, NPY_SHORT)) score += 100;
    if (PyArray_CanCastSafely(NPY_UINT, NPY_LONGLONG)) score += 1000;
    if (PyArray_CanCastSafely(NPY_INT, NPY_DOUBLE)) score += 10000;
    if (!PyArray_CanCastSafely(NPY_DOUBLE, NPY_INT)) score += 100000;
    if (!PyArray_CanCastSafely(NPY_DOUBLE, NPY_FLOAT)) score += 1000000;
    if (!PyArray_CanCastSafely(NPY_INT, NPY_UINT)) score += 10000000;
    if (!PyArray_CanCastSafely(9999, NPY_INT) && PyErr_Occurred() == NULL) score += 100000000;
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_can_cast_to_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyArray_Descr *bool_descr = PyArray_DescrFromType(NPY_BOOL);
    PyArray_Descr *int_descr = PyArray_DescrFromType(NPY_INT);
    PyArray_Descr *double_descr = PyArray_DescrFromType(NPY_DOUBLE);
    if (bool_descr == NULL || int_descr == NULL || double_descr == NULL) return NULL;
    long score = 0;
    if (PyArray_CanCastTo(bool_descr, int_descr)) score += 1;
    if (PyArray_CanCastTo(int_descr, double_descr)) score += 10;
    if (!PyArray_CanCastTo(double_descr, int_descr)) score += 100;
    if (!PyArray_CanCastTo(NULL, int_descr) && PyErr_Occurred() == NULL) score += 1000;
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_can_cast_type_array_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyArray_Descr *int_descr = PyArray_DescrFromType(NPY_INT);
    PyArray_Descr *double_descr = PyArray_DescrFromType(NPY_DOUBLE);
    npy_intp dims[1] = {2};
    PyObject *int_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *double_arr = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    if (int_descr == NULL || double_descr == NULL || int_arr == NULL || double_arr == NULL) return NULL;
    long score = 0;
    if (PyArray_CanCastTypeTo(int_descr, double_descr, NPY_SAFE_CASTING)) score += 1;
    if (!PyArray_CanCastTypeTo(double_descr, int_descr, NPY_SAFE_CASTING)) score += 10;
    if (PyArray_CanCastTypeTo(int_descr, int_descr, NPY_NO_CASTING)) score += 100;
    if (!PyArray_CanCastTypeTo(int_descr, double_descr, NPY_NO_CASTING)) score += 1000;
    if (PyArray_CanCastArrayTo((PyArrayObject *)int_arr, double_descr, NPY_SAFE_CASTING)) score += 10000;
    if (!PyArray_CanCastArrayTo((PyArrayObject *)double_arr, int_descr, NPY_SAFE_CASTING)) score += 100000;
    if (PyArray_CanCastArrayTo((PyArrayObject *)double_arr, int_descr, NPY_UNSAFE_CASTING)) score += 1000000;
    Py_DECREF(int_arr);
    Py_DECREF(double_arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_casting_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    NPY_CASTING casting = NPY_UNSAFE_CASTING;
    PyObject *no_obj = PyUnicode_FromString("no");
    PyObject *equiv_obj = PyUnicode_FromString("equiv");
    PyObject *safe_obj = PyUnicode_FromString("safe");
    PyObject *same_kind_obj = PyUnicode_FromString("same_kind");
    PyObject *unsafe_obj = PyUnicode_FromString("unsafe");
    PyObject *safe_bytes = PyBytes_FromString("safe");
    PyObject *same_value_obj = PyUnicode_FromString("same_value");
    PyObject *bad_obj = PyUnicode_FromString("wild");
    if (no_obj == NULL || equiv_obj == NULL || safe_obj == NULL || same_kind_obj == NULL ||
        unsafe_obj == NULL || safe_bytes == NULL || same_value_obj == NULL || bad_obj == NULL) {
        goto done;
    }
    if (PyArray_CastingConverter(no_obj, &casting) == NPY_SUCCEED && casting == NPY_NO_CASTING) score += 1;
    if (PyArray_CastingConverter(equiv_obj, &casting) == NPY_SUCCEED && casting == NPY_EQUIV_CASTING) score += 10;
    if (PyArray_CastingConverter(safe_obj, &casting) == NPY_SUCCEED && casting == NPY_SAFE_CASTING) score += 100;
    if (PyArray_CastingConverter(same_kind_obj, &casting) == NPY_SUCCEED && casting == NPY_SAME_KIND_CASTING) score += 1000;
    if (PyArray_CastingConverter(unsafe_obj, &casting) == NPY_SUCCEED && casting == NPY_UNSAFE_CASTING) score += 10000;
    if (PyArray_CastingConverter(safe_bytes, &casting) == NPY_SUCCEED && casting == NPY_SAFE_CASTING) score += 100000;
    if (PyArray_CastingConverter(same_value_obj, &casting) == NPY_FAIL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 1000000;
    PyErr_Clear();
    if (PyArray_CastingConverter(bad_obj, &casting) == NPY_FAIL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 10000000;
    PyErr_Clear();
    if (PyArray_CastingConverter(Py_None, &casting) == NPY_FAIL && PyErr_ExceptionMatches(PyExc_TypeError)) score += 100000000;
    PyErr_Clear();
done:
    Py_XDECREF(no_obj);
    Py_XDECREF(equiv_obj);
    Py_XDECREF(safe_obj);
    Py_XDECREF(same_kind_obj);
    Py_XDECREF(unsafe_obj);
    Py_XDECREF(safe_bytes);
    Py_XDECREF(same_value_obj);
    Py_XDECREF(bad_obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_zero_one_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {1};
    PyObject *int_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *double_arr = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    PyObject *bool_arr = PyArray_SimpleNew(1, dims, NPY_BOOL);
    if (int_arr == NULL || double_arr == NULL || bool_arr == NULL) return NULL;
    char *int_zero = PyArray_Zero((PyArrayObject *)int_arr);
    char *int_one = PyArray_One((PyArrayObject *)int_arr);
    char *double_zero = PyArray_Zero((PyArrayObject *)double_arr);
    char *double_one = PyArray_One((PyArrayObject *)double_arr);
    char *bool_one = PyArray_One((PyArrayObject *)bool_arr);
    long score = 0;
    if (int_zero != NULL && *(int *)int_zero == 0) score += 1;
    if (int_one != NULL && *(int *)int_one == 1) score += 10;
    if (double_zero != NULL && *(double *)double_zero == 0.0) score += 100;
    if (double_one != NULL && *(double *)double_one == 1.0) score += 1000;
    PyObject *scalar = int_one != NULL ? PyArray_ToScalar(int_one, (PyArrayObject *)int_arr) : NULL;
    if (scalar != NULL && PyLong_AsLong(scalar) == 1) score += 10000;
    Py_XDECREF(scalar);
    if (bool_one != NULL && *(unsigned char *)bool_one == 1) score += 100000;
    PyArray_free(int_zero);
    PyArray_free(int_one);
    PyArray_free(double_zero);
    PyArray_free(double_one);
    PyArray_free(bool_one);
    Py_DECREF(bool_arr);
    Py_DECREF(double_arr);
    Py_DECREF(int_arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_object_type_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *ival = PyLong_FromLong(7);
    PyObject *fval = PyFloat_FromDouble(1.5);
    PyObject *cval = PyComplex_FromDoubles(1.0, 2.0);
    PyObject *sval = PyUnicode_FromString("abc");
    PyObject *seq = PyList_New(2);
    PyObject *seq0 = PyLong_FromLong(1);
    PyObject *seq1 = PyFloat_FromDouble(2.5);
    if (seq != NULL && seq0 != NULL && seq1 != NULL) {
        if (PyList_SetItem(seq, 0, seq0) == 0) seq0 = NULL;
        if (PyList_SetItem(seq, 1, seq1) == 0) seq1 = NULL;
    }
    long score = 0;
    if (ival != NULL && PyArray_ObjectType(ival, NPY_NOTYPE) == NPY_LONG) score += 1;
    if (fval != NULL && PyArray_ObjectType(fval, NPY_NOTYPE) == NPY_DOUBLE) score += 10;
    if (cval != NULL && PyArray_ObjectType(cval, NPY_NOTYPE) == NPY_CDOUBLE) score += 100;
    if (sval != NULL && PyArray_ObjectType(sval, NPY_NOTYPE) == NPY_STRING) score += 1000;
    if (arr != NULL && PyArray_ObjectType(arr, NPY_NOTYPE) == NPY_INT) score += 10000;
    if (arr != NULL && PyArray_ObjectType(arr, NPY_DOUBLE) == NPY_DOUBLE) score += 100000;
    if (ival != NULL && PyArray_ObjectType(ival, NPY_DOUBLE) == NPY_DOUBLE) score += 1000000;
    if (seq != NULL && PyArray_ObjectType(seq, NPY_NOTYPE) == NPY_DOUBLE) score += 10000000;
    if (PyArray_ObjectType(Py_None, NPY_NOTYPE) == NPY_OBJECT) score += 100000000;
    Py_XDECREF(arr);
    Py_XDECREF(ival);
    Py_XDECREF(fval);
    Py_XDECREF(cval);
    Py_XDECREF(sval);
    Py_XDECREF(seq);
    Py_XDECREF(seq0);
    Py_XDECREF(seq1);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_from_object_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {1};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *fval = PyFloat_FromDouble(1.5);
    PyObject *ival = PyLong_FromLong(7);
    PyObject *seq = PyList_New(2);
    PyObject *seq0 = PyLong_FromLong(1);
    PyObject *seq1 = PyFloat_FromDouble(2.5);
    if (seq != NULL && seq0 != NULL && seq1 != NULL) {
        if (PyList_SetItem(seq, 0, seq0) == 0) seq0 = NULL;
        if (PyList_SetItem(seq, 1, seq1) == 0) seq1 = NULL;
    }
    PyArray_Descr *min_double = PyArray_DescrFromType(NPY_DOUBLE);
    PyArray_Descr *float_descr = fval != NULL ? PyArray_DescrFromObject(fval, NULL) : NULL;
    PyArray_Descr *int_min_descr = (ival != NULL && min_double != NULL) ? PyArray_DescrFromObject(ival, min_double) : NULL;
    PyArray_Descr *array_descr = arr != NULL ? PyArray_DescrFromObject(arr, NULL) : NULL;
    PyArray_Descr *seq_descr = seq != NULL ? PyArray_DescrFromObject(seq, NULL) : NULL;
    PyArray_Descr *none_descr = PyArray_DescrFromObject(Py_None, NULL);
    long score = 0;
    if (float_descr != NULL && PyArray_DescrCheck((PyObject *)float_descr) && PyDataType_TYPE(float_descr) == NPY_DOUBLE) score += 1;
    if (int_min_descr != NULL && PyDataType_TYPE(int_min_descr) == NPY_DOUBLE) score += 10;
    if (array_descr != NULL && PyDataType_TYPE(array_descr) == NPY_INT) score += 100;
    if (seq_descr != NULL && PyDataType_TYPE(seq_descr) == NPY_DOUBLE) score += 1000;
    if (none_descr != NULL && PyDataType_TYPE(none_descr) == NPY_OBJECT) score += 10000;
    PyArray_Descr *bad = PyArray_DescrFromObject(NULL, NULL);
    if (bad == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 100000;
    Py_XDECREF((PyObject *)bad);
    PyErr_Clear();
    Py_XDECREF((PyObject *)none_descr);
    Py_XDECREF((PyObject *)seq_descr);
    Py_XDECREF((PyObject *)array_descr);
    Py_XDECREF((PyObject *)int_min_descr);
    Py_XDECREF((PyObject *)float_descr);
    Py_XDECREF((PyObject *)min_double);
    Py_XDECREF(arr);
    Py_XDECREF(fval);
    Py_XDECREF(ival);
    Py_XDECREF(seq);
    Py_XDECREF(seq0);
    Py_XDECREF(seq1);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *fval = PyFloat_FromDouble(1.5);
    PyObject *ival = PyLong_FromLong(7);
    npy_intp dims[1] = {1};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (fval == NULL || ival == NULL || arr == NULL) return NULL;
    PyArray_Descr *descr = NULL;
    if (PyArray_DescrConverter(fval, &descr) == NPY_SUCCEED && descr != NULL && PyDataType_TYPE(descr) == NPY_DOUBLE) score += 1;
    Py_XDECREF((PyObject *)descr);
    descr = NULL;
    if (PyArray_DescrConverter2(Py_None, &descr) == NPY_SUCCEED && descr == NULL) score += 10;
    Py_XDECREF((PyObject *)descr);
    descr = NULL;
    if (PyArray_DescrConverter2(ival, &descr) == NPY_SUCCEED && descr != NULL && PyDataType_TYPE(descr) == NPY_LONG) score += 100;
    Py_XDECREF((PyObject *)descr);
    descr = NULL;
    if (PyArray_DescrConverter(arr, &descr) == NPY_SUCCEED && descr != NULL && PyDataType_TYPE(descr) == NPY_INT) score += 1000;
    Py_XDECREF((PyObject *)descr);
    descr = NULL;
    if (PyArray_DescrConverter(NULL, &descr) == NPY_FAIL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF((PyObject *)descr);
    if (PyArray_DescrConverter(fval, NULL) == NPY_FAIL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_DECREF(arr);
    Py_DECREF(ival);
    Py_DECREF(fval);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_align_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *fval = PyFloat_FromDouble(1.5);
    PyObject *ival = PyLong_FromLong(7);
    npy_intp dims[1] = {1};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_INT);
    if (fval == NULL || ival == NULL || arr == NULL) return NULL;
    PyArray_Descr *descr = NULL;
    if (PyArray_DescrAlignConverter(fval, &descr) == NPY_SUCCEED && descr != NULL &&
        PyDataType_TYPE(descr) == NPY_DOUBLE && PyDataType_ALIGNMENT(descr) >= (int)sizeof(double)) score += 1;
    Py_XDECREF((PyObject *)descr);
    descr = NULL;
    if (PyArray_DescrAlignConverter2(Py_None, &descr) == NPY_SUCCEED && descr == NULL) score += 10;
    Py_XDECREF((PyObject *)descr);
    descr = NULL;
    if (PyArray_DescrAlignConverter2(ival, &descr) == NPY_SUCCEED && descr != NULL &&
        PyDataType_TYPE(descr) == NPY_LONG && PyDataType_ALIGNMENT(descr) >= (int)sizeof(long)) score += 100;
    Py_XDECREF((PyObject *)descr);
    descr = NULL;
    if (PyArray_DescrAlignConverter(arr, &descr) == NPY_SUCCEED && descr != NULL &&
        PyDataType_TYPE(descr) == NPY_INT && PyDataType_ALIGNMENT(descr) >= (int)sizeof(int)) score += 1000;
    Py_XDECREF((PyObject *)descr);
    descr = NULL;
    if (PyArray_DescrAlignConverter(NULL, &descr) == NPY_FAIL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF((PyObject *)descr);
    Py_DECREF(arr);
    Py_DECREF(ival);
    Py_DECREF(fval);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_from_scalar_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *ival = PyLong_FromLong(7);
    PyObject *fval = PyFloat_FromDouble(1.5);
    PyObject *cval = PyComplex_FromDoubles(1.0, 2.0);
    PyObject *sval = PyUnicode_FromString("abc");
    if (ival == NULL || fval == NULL || cval == NULL || sval == NULL) return NULL;
    PyArray_Descr *int_descr = PyArray_DescrFromScalar(ival);
    PyArray_Descr *float_descr = PyArray_DescrFromScalar(fval);
    PyArray_Descr *complex_descr = PyArray_DescrFromScalar(cval);
    PyArray_Descr *string_descr = PyArray_DescrFromScalar(sval);
    PyArray_Descr *object_descr = PyArray_DescrFromScalar(Py_None);
    long score = 0;
    if (int_descr != NULL && PyArray_DescrCheck((PyObject *)int_descr) && PyDataType_TYPE(int_descr) == NPY_LONG) score += 1;
    if (float_descr != NULL && PyDataType_TYPE(float_descr) == NPY_DOUBLE) score += 10;
    if (complex_descr != NULL && PyDataType_TYPE(complex_descr) == NPY_CDOUBLE) score += 100;
    if (string_descr != NULL && PyDataType_TYPE(string_descr) == NPY_STRING) score += 1000;
    if (object_descr != NULL && PyDataType_TYPE(object_descr) == NPY_OBJECT) score += 10000;
    Py_XDECREF((PyObject *)object_descr);
    Py_XDECREF((PyObject *)string_descr);
    Py_XDECREF((PyObject *)complex_descr);
    Py_XDECREF((PyObject *)float_descr);
    Py_XDECREF((PyObject *)int_descr);
    Py_DECREF(sval);
    Py_DECREF(cval);
    Py_DECREF(fval);
    Py_DECREF(ival);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_descr_from_type_object_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyArray_Descr *bool_descr = PyArray_DescrFromTypeObject((PyObject *)&PyBool_Type);
    PyArray_Descr *int_descr = PyArray_DescrFromTypeObject((PyObject *)&PyLong_Type);
    PyArray_Descr *float_descr = PyArray_DescrFromTypeObject((PyObject *)&PyFloat_Type);
    PyArray_Descr *complex_descr = PyArray_DescrFromTypeObject((PyObject *)&PyComplex_Type);
    PyArray_Descr *string_descr = PyArray_DescrFromTypeObject((PyObject *)&PyBytes_Type);
    PyArray_Descr *object_descr = PyArray_DescrFromTypeObject((PyObject *)&PyBaseObject_Type);
    long score = 0;
    if (bool_descr != NULL && PyArray_DescrCheck((PyObject *)bool_descr) && PyDataType_TYPE(bool_descr) == NPY_BOOL) score += 1;
    if (int_descr != NULL && PyDataType_TYPE(int_descr) == NPY_LONG) score += 10;
    if (float_descr != NULL && PyDataType_TYPE(float_descr) == NPY_DOUBLE) score += 100;
    if (complex_descr != NULL && PyDataType_TYPE(complex_descr) == NPY_CDOUBLE) score += 1000;
    if (string_descr != NULL && PyDataType_TYPE(string_descr) == NPY_STRING) score += 10000;
    if (object_descr != NULL && PyDataType_TYPE(object_descr) == NPY_OBJECT) score += 100000;
    Py_XDECREF((PyObject *)object_descr);
    Py_XDECREF((PyObject *)string_descr);
    Py_XDECREF((PyObject *)complex_descr);
    Py_XDECREF((PyObject *)float_descr);
    Py_XDECREF((PyObject *)int_descr);
    Py_XDECREF((PyObject *)bool_descr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_scalar_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyArray_Descr *int_descr = PyArray_DescrFromType(NPY_INT);
    PyArray_Descr *double_descr = PyArray_DescrFromType(NPY_DOUBLE);
    PyArray_Descr *complex_descr = PyArray_DescrFromType(NPY_CDOUBLE);
    PyArray_Descr *object_descr = PyArray_DescrFromType(NPY_OBJECT);
    int iv = -7;
    double dv = 2.5;
    npy_cdouble cv;
    cv.real = 2.0;
    cv.imag = 3.0;
    PyObject *obj = PyLong_FromLong(42);
    if (obj == NULL) return NULL;
    PyObject *obj_slot = obj;
    PyObject *is = int_descr != NULL ? PyArray_Scalar(&iv, int_descr, NULL) : NULL;
    PyObject *ds = double_descr != NULL ? PyArray_Scalar(&dv, double_descr, NULL) : NULL;
    PyObject *cs = complex_descr != NULL ? PyArray_Scalar(&cv, complex_descr, NULL) : NULL;
    PyObject *os = object_descr != NULL ? PyArray_Scalar(&obj_slot, object_descr, NULL) : NULL;
    long score = 0;
    if (is != NULL && PyLong_AsLong(is) == -7) score += 1;
    if (ds != NULL && PyFloat_AsDouble(ds) > 2.49 && PyFloat_AsDouble(ds) < 2.51) score += 10;
    if (cs != NULL) {
        Py_complex got = PyComplex_AsCComplex(cs);
        if (got.real > 1.99 && got.real < 2.01 && got.imag > 2.99 && got.imag < 3.01) score += 100;
    }
    if (os != NULL && PyLong_AsLong(os) == 42) score += 1000;
    if (int_descr != NULL && PyArray_Scalar(NULL, int_descr, NULL) == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    if (PyArray_Scalar(&iv, NULL, NULL) == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(os);
    Py_XDECREF(cs);
    Py_XDECREF(ds);
    Py_XDECREF(is);
    Py_DECREF(obj);
    Py_XDECREF((PyObject *)object_descr);
    Py_XDECREF((PyObject *)complex_descr);
    Py_XDECREF((PyObject *)double_descr);
    Py_XDECREF((PyObject *)int_descr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_scalar_as_ctype_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *ival = PyLong_FromLong(-7);
    PyObject *fval = PyFloat_FromDouble(1.5);
    PyObject *cval = PyComplex_FromDoubles(2.0, 3.0);
    PyObject *sval = PyBytes_FromString("xy");
    if (ival == NULL || fval == NULL || cval == NULL || sval == NULL) return NULL;
    long out_i = 0;
    double out_d = 0.0;
    npy_cdouble out_c;
    char *out_s = NULL;
    out_c.real = 0.0;
    out_c.imag = 0.0;
    PyArray_ScalarAsCtype(ival, &out_i);
    PyArray_ScalarAsCtype(fval, &out_d);
    PyArray_ScalarAsCtype(cval, &out_c);
    PyArray_ScalarAsCtype(sval, &out_s);
    long score = 0;
    if (out_i == -7) score += 1;
    if (out_d > 1.49 && out_d < 1.51) score += 10;
    if (out_c.real > 1.99 && out_c.real < 2.01 && out_c.imag > 2.99 && out_c.imag < 3.01) score += 100;
    if (out_s != NULL && out_s[0] == 'x' && out_s[1] == 'y') score += 1000;
    Py_DECREF(sval);
    Py_DECREF(cval);
    Py_DECREF(fval);
    Py_DECREF(ival);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_from_scalar_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *ival = PyLong_FromLong(11);
    PyObject *fval = PyFloat_FromDouble(2.5);
    PyObject *int_arr_obj = NULL;
    PyObject *double_arr_obj = NULL;
    PyObject *int_item = NULL;
    PyObject *double_item = NULL;
    if (ival == NULL || fval == NULL) return NULL;
    int_arr_obj = PyArray_FromScalar(ival, NULL);
    double_arr_obj = PyArray_FromScalar(fval, PyArray_DescrFromType(NPY_DOUBLE));
    if (int_arr_obj != NULL) int_item = PyArray_GETITEM((PyArrayObject *)int_arr_obj, PyArray_DATA((PyArrayObject *)int_arr_obj));
    if (double_arr_obj != NULL) double_item = PyArray_GETITEM((PyArrayObject *)double_arr_obj, PyArray_DATA((PyArrayObject *)double_arr_obj));
    long score = 0;
    if (int_arr_obj != NULL && PyArray_NDIM((PyArrayObject *)int_arr_obj) == 0 && PyArray_SIZE((PyArrayObject *)int_arr_obj) == 1) score += 1;
    if (int_arr_obj != NULL && PyArray_TYPE((PyArrayObject *)int_arr_obj) == NPY_LONG) score += 10;
    if (int_item != NULL && PyLong_AsLong(int_item) == 11) score += 100;
    if (double_arr_obj != NULL && PyArray_NDIM((PyArrayObject *)double_arr_obj) == 0 && PyArray_TYPE((PyArrayObject *)double_arr_obj) == NPY_DOUBLE) score += 1000;
    if (double_item != NULL && PyFloat_AsDouble(double_item) > 2.49 && PyFloat_AsDouble(double_item) < 2.51) score += 10000;
    Py_XDECREF(double_item);
    Py_XDECREF(int_item);
    Py_XDECREF(double_arr_obj);
    Py_XDECREF(int_arr_obj);
    Py_DECREF(fval);
    Py_DECREF(ival);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_cast_scalar_to_ctype_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *ival = PyLong_FromLong(7);
    PyObject *fval = PyFloat_FromDouble(3.5);
    PyObject *cval = PyComplex_FromDoubles(4.0, 5.0);
    if (ival == NULL || fval == NULL || cval == NULL) return NULL;
    long out_l = 0;
    double out_d_from_int = 0.0;
    double out_d = 0.0;
    npy_cdouble out_c;
    out_c.real = 0.0;
    out_c.imag = 0.0;
    PyArray_Descr *long_descr = PyArray_DescrFromType(NPY_LONG);
    PyArray_Descr *double_descr_a = PyArray_DescrFromType(NPY_DOUBLE);
    PyArray_Descr *double_descr_b = PyArray_DescrFromType(NPY_DOUBLE);
    PyArray_Descr *complex_descr = PyArray_DescrFromType(NPY_CDOUBLE);
    int rc_l = long_descr != NULL ? PyArray_CastScalarToCtype(ival, &out_l, long_descr) : -1;
    int rc_di = double_descr_a != NULL ? PyArray_CastScalarToCtype(ival, &out_d_from_int, double_descr_a) : -1;
    int rc_d = double_descr_b != NULL ? PyArray_CastScalarToCtype(fval, &out_d, double_descr_b) : -1;
    int rc_c = complex_descr != NULL ? PyArray_CastScalarToCtype(cval, &out_c, complex_descr) : -1;
    long score = 0;
    if (rc_l == 0 && out_l == 7) score += 1;
    if (rc_di == 0 && out_d_from_int > 6.99 && out_d_from_int < 7.01) score += 10;
    if (rc_d == 0 && out_d > 3.49 && out_d < 3.51) score += 100;
    if (rc_c == 0 && out_c.real > 3.99 && out_c.real < 4.01 && out_c.imag > 4.99 && out_c.imag < 5.01) score += 1000;
    Py_XDECREF((PyObject *)complex_descr);
    Py_XDECREF((PyObject *)double_descr_b);
    Py_XDECREF((PyObject *)double_descr_a);
    Py_XDECREF((PyObject *)long_descr);
    Py_DECREF(cval);
    Py_DECREF(fval);
    Py_DECREF(ival);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_cast_scalar_direct_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *ival = PyLong_FromLong(13);
    PyObject *fval = PyFloat_FromDouble(6.25);
    PyObject *cval = PyComplex_FromDoubles(2.0, -3.0);
    if (ival == NULL || fval == NULL || cval == NULL) return NULL;
    long out_l = 0;
    double out_d_from_int = 0.0;
    double out_d = 0.0;
    npy_cdouble out_c;
    out_c.real = 0.0;
    out_c.imag = 0.0;
    PyArray_Descr *long_descr = PyArray_DescrFromType(NPY_LONG);
    PyArray_Descr *double_descr = PyArray_DescrFromType(NPY_DOUBLE);
    PyArray_Descr *complex_descr = PyArray_DescrFromType(NPY_CDOUBLE);
    int rc_l = long_descr != NULL ? PyArray_CastScalarDirect(ival, long_descr, &out_l, NPY_LONG) : -1;
    int rc_di = long_descr != NULL ? PyArray_CastScalarDirect(ival, long_descr, &out_d_from_int, NPY_DOUBLE) : -1;
    int rc_d = double_descr != NULL ? PyArray_CastScalarDirect(fval, double_descr, &out_d, NPY_DOUBLE) : -1;
    int rc_c = complex_descr != NULL ? PyArray_CastScalarDirect(cval, complex_descr, &out_c, NPY_CDOUBLE) : -1;
    long score = 0;
    if (rc_l == 0 && out_l == 13) score += 1;
    if (rc_di == 0 && out_d_from_int > 12.99 && out_d_from_int < 13.01) score += 10;
    if (rc_d == 0 && out_d > 6.24 && out_d < 6.26) score += 100;
    if (rc_c == 0 && out_c.real > 1.99 && out_c.real < 2.01 && out_c.imag < -2.99 && out_c.imag > -3.01) score += 1000;
    Py_XDECREF((PyObject *)complex_descr);
    Py_XDECREF((PyObject *)double_descr);
    Py_XDECREF((PyObject *)long_descr);
    Py_DECREF(cval);
    Py_DECREF(fval);
    Py_DECREF(ival);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_pack_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyArray_Descr *long_descr = PyArray_DescrFromType(NPY_LONG);
    PyArray_Descr *double_descr = PyArray_DescrFromType(NPY_DOUBLE);
    PyArray_Descr *object_descr = PyArray_DescrFromType(NPY_OBJECT);
    PyObject *ival = PyLong_FromLong(9);
    PyObject *jval = PyLong_FromLong(5);
    PyObject *text = PyUnicode_FromString("packed");
    PyObject *seed = PyLong_FromLong(23);
    if (long_descr == NULL || double_descr == NULL || object_descr == NULL || ival == NULL || jval == NULL || text == NULL || seed == NULL) return NULL;
    long out_l = 0;
    double out_d = 0.0;
    PyObject *slot = NULL;
    PyObject *scalar_arr = PyArray_FromScalar(seed, NULL);
    Py_DECREF(seed);
    long out_from_array = 0;
    int rc_l = PyArray_Pack(long_descr, &out_l, ival);
    int rc_d = PyArray_Pack(double_descr, &out_d, jval);
    int rc_obj = PyArray_Pack(object_descr, &slot, text);
    Py_DECREF(text);
    int rc_arr = scalar_arr != NULL ? PyArray_Pack(long_descr, &out_from_array, scalar_arr) : -1;
    long score = 0;
    if (rc_l == 0 && out_l == 9) score += 1;
    if (rc_d == 0 && out_d > 4.99 && out_d < 5.01) score += 10;
    if (rc_obj == 0 && slot != NULL && strcmp(PyUnicode_AsUTF8(slot), "packed") == 0) score += 100;
    if (rc_arr == 0 && out_from_array == 23) score += 1000;
    Py_XDECREF(scalar_arr);
    Py_XDECREF(slot);
    Py_DECREF(jval);
    Py_DECREF(ival);
    Py_XDECREF((PyObject *)object_descr);
    Py_XDECREF((PyObject *)double_descr);
    Py_XDECREF((PyObject *)long_descr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_from_array_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (arr == NULL) return NULL;
    long *data = (long *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 7; data[1] = 8; data[2] = 9;
    long score = 0;
    PyObject *same = PyArray_FromArray((PyArrayObject *)arr, NULL, 0);
    if (same == arr && PyArray_TYPE((PyArrayObject *)same) == NPY_LONG) score += 1;
    Py_XDECREF(same);
    PyObject *copy = PyArray_FromArray((PyArrayObject *)arr, NULL, NPY_ARRAY_ENSURECOPY);
    if (copy != NULL && copy != arr && PyArray_TYPE((PyArrayObject *)copy) == NPY_LONG && ((long *)PyArray_DATA((PyArrayObject *)copy))[1] == 8) score += 10;
    Py_XDECREF(copy);
    PyObject *cast = PyArray_FromArray((PyArrayObject *)arr, PyArray_DescrFromType(NPY_DOUBLE), 0);
    if (cast != NULL && PyArray_TYPE((PyArrayObject *)cast) == NPY_DOUBLE && ((double *)PyArray_DATA((PyArrayObject *)cast))[2] == 9.0) score += 100;
    Py_XDECREF(cast);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_cast_to_type_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (arr == NULL) return NULL;
    long *data = (long *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 2; data[1] = 4; data[2] = 6;
    long score = 0;
    PyObject *cast_macro = PyArray_Cast((PyArrayObject *)arr, NPY_DOUBLE);
    if (cast_macro != NULL && cast_macro != arr && PyArray_TYPE((PyArrayObject *)cast_macro) == NPY_DOUBLE && ((double *)PyArray_DATA((PyArrayObject *)cast_macro))[1] == 4.0) score += 1;
    Py_XDECREF(cast_macro);
    PyObject *cast_direct = PyArray_CastToType((PyArrayObject *)arr, PyArray_DescrFromType(NPY_DOUBLE), 0);
    if (cast_direct != NULL && cast_direct != arr && PyArray_TYPE((PyArrayObject *)cast_direct) == NPY_DOUBLE && ((double *)PyArray_DATA((PyArrayObject *)cast_direct))[2] == 6.0) score += 10;
    Py_XDECREF(cast_direct);
    PyObject *same_type = PyArray_CastToType((PyArrayObject *)arr, PyArray_DescrFromType(NPY_LONG), 0);
    if (same_type != NULL && same_type != arr && PyArray_TYPE((PyArrayObject *)same_type) == NPY_LONG && ((long *)PyArray_DATA((PyArrayObject *)same_type))[0] == 2) score += 100;
    Py_XDECREF(same_type);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_fill_with_scalar_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {4};
    PyObject *long_arr = PyArray_SimpleNew(1, dims, NPY_LONG);
    PyObject *double_arr = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    PyObject *seven = PyLong_FromLong(7);
    PyObject *two_half = PyFloat_FromDouble(2.5);
    if (long_arr == NULL || double_arr == NULL || seven == NULL || two_half == NULL) return NULL;
    long score = 0;
    if (PyArray_FillWithScalar((PyArrayObject *)long_arr, seven) == 0) {
        long *data = (long *)PyArray_DATA((PyArrayObject *)long_arr);
        if (data[0] == 7 && data[1] == 7 && data[2] == 7 && data[3] == 7) score += 1;
    }
    if (PyArray_FillWithScalar((PyArrayObject *)double_arr, two_half) == 0) {
        double *data = (double *)PyArray_DATA((PyArrayObject *)double_arr);
        if (data[0] == 2.5 && data[1] == 2.5 && data[2] == 2.5 && data[3] == 2.5) score += 10;
    }
    Py_DECREF(two_half);
    Py_DECREF(seven);
    Py_DECREF(double_arr);
    Py_DECREF(long_arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_to_list_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_LONG);
    PyObject *scalar_value = PyFloat_FromDouble(3.5);
    PyObject *scalar_arr = scalar_value != NULL ? PyArray_FromScalar(scalar_value, NULL) : NULL;
    Py_XDECREF(scalar_value);
    if (arr == NULL || scalar_arr == NULL) return NULL;
    long *data = (long *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 1; data[1] = 2; data[2] = 3; data[3] = 4; data[4] = 5; data[5] = 6;
    long score = 0;
    PyObject *list = PyArray_ToList((PyArrayObject *)arr);
    if (list != NULL && PyList_Check(list) && PyList_Size(list) == 2) {
        PyObject *row0 = PyList_GetItem(list, 0);
        PyObject *row1 = PyList_GetItem(list, 1);
        if (PyList_Check(row0) && PyList_Check(row1) && PyList_Size(row0) == 3 && PyList_Size(row1) == 3 &&
            PyLong_AsLong(PyList_GetItem(row0, 0)) == 1 && PyLong_AsLong(PyList_GetItem(row0, 2)) == 3 &&
            PyLong_AsLong(PyList_GetItem(row1, 0)) == 4 && PyLong_AsLong(PyList_GetItem(row1, 2)) == 6) score += 1;
    }
    Py_XDECREF(list);
    PyObject *scalar = PyArray_ToList((PyArrayObject *)scalar_arr);
    if (scalar != NULL && !PyList_Check(scalar) && PyFloat_AsDouble(scalar) == 3.5) score += 10;
    Py_XDECREF(scalar);
    Py_DECREF(scalar_arr);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_to_string_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {3};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (arr == NULL) return NULL;
    long *data = (long *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 11; data[1] = 22; data[2] = 33;
    long score = 0;
    PyObject *bytes = PyArray_ToString((PyArrayObject *)arr, NPY_CORDER);
    if (bytes != NULL && PyBytes_Check(bytes) && PyBytes_Size(bytes) == (Py_ssize_t)(3 * sizeof(long))) {
        char *raw = PyBytes_AsString(bytes);
        if (raw != NULL && ((long *)raw)[0] == 11 && ((long *)raw)[1] == 22 && ((long *)raw)[2] == 33) score += 1;
    }
    Py_XDECREF(bytes);
    bytes = PyArray_ToString((PyArrayObject *)arr, NPY_ANYORDER);
    if (bytes != NULL && PyBytes_Check(bytes) && PyBytes_Size(bytes) == (Py_ssize_t)(3 * sizeof(long))) {
        char *raw = PyBytes_AsString(bytes);
        if (raw != NULL && ((long *)raw)[0] == 11 && ((long *)raw)[1] == 22 && ((long *)raw)[2] == 33) score += 10;
    }
    Py_XDECREF(bytes);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_byteswap_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_UINT);
    if (arr == NULL) return NULL;
    uint32_t *data = (uint32_t *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 0x01020304u;
    data[1] = 0x0a0b0c0du;
    long score = 0;
    PyObject *copy = PyArray_Byteswap((PyArrayObject *)arr, 0);
    if (copy != NULL && copy != arr && PyArray_TYPE((PyArrayObject *)copy) == NPY_UINT) {
        uint32_t *copy_data = (uint32_t *)PyArray_DATA((PyArrayObject *)copy);
        if (copy_data[0] == 0x04030201u && copy_data[1] == 0x0d0c0b0au && data[0] == 0x01020304u) score += 1;
    }
    Py_XDECREF(copy);
    PyObject *same = PyArray_Byteswap((PyArrayObject *)arr, 1);
    if (same == arr && data[0] == 0x04030201u && data[1] == 0x0d0c0b0au) score += 10;
    Py_XDECREF(same);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_from_string_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    uint32_t raw[3] = {0x11223344u, 0x55667788u, 0x99aabbccu};
    long score = 0;
    PyArray_Descr *descr = PyArray_DescrNewFromType(NPY_UINT);
    if (descr == NULL) return NULL;
    PyObject *all = PyArray_FromString((char *)raw, (npy_intp)sizeof(raw), descr, -1, NULL);
    if (all != NULL && PyArray_NDIM((PyArrayObject *)all) == 1 && PyArray_SIZE((PyArrayObject *)all) == 3 && PyArray_TYPE((PyArrayObject *)all) == NPY_UINT) {
        uint32_t *data = (uint32_t *)PyArray_DATA((PyArrayObject *)all);
        if (data[0] == raw[0] && data[1] == raw[1] && data[2] == raw[2]) score += 1;
    }
    Py_XDECREF(all);
    descr = PyArray_DescrNewFromType(NPY_UINT);
    if (descr == NULL) return NULL;
    PyObject *two = PyArray_FromString((char *)raw, (npy_intp)sizeof(raw), descr, 2, "");
    if (two != NULL && PyArray_SIZE((PyArrayObject *)two) == 2) {
        uint32_t *data = (uint32_t *)PyArray_DATA((PyArrayObject *)two);
        if (data[0] == raw[0] && data[1] == raw[1]) score += 10;
    }
    Py_XDECREF(two);
    descr = PyArray_DescrNewFromType(NPY_OBJECT);
    if (descr == NULL) return NULL;
    PyObject *object_arr = PyArray_FromString((char *)raw, (npy_intp)sizeof(raw), descr, -1, NULL);
    if (object_arr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(object_arr);
    descr = PyArray_DescrNewFromType(NPY_UINT);
    if (descr == NULL) return NULL;
    PyObject *text_arr = PyArray_FromString((char *)raw, (npy_intp)sizeof(raw), descr, -1, ",");
    if (text_arr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(text_arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_from_buffer_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    uint16_t raw[4] = {10, 20, 30, 40};
    long score = 0;
    PyObject *mv = PyMemoryView_FromMemory((char *)raw, (Py_ssize_t)sizeof(raw), PyBUF_WRITE);
    if (mv == NULL) return NULL;
    PyArray_Descr *descr = PyArray_DescrNewFromType(NPY_USHORT);
    if (descr == NULL) { Py_DECREF(mv); return NULL; }
    PyObject *arr = PyArray_FromBuffer(mv, descr, -1, 0);
    if (arr != NULL && PyArray_NDIM((PyArrayObject *)arr) == 1 && PyArray_SIZE((PyArrayObject *)arr) == 4 &&
        PyArray_TYPE((PyArrayObject *)arr) == NPY_USHORT && PyArray_BASE((PyArrayObject *)arr) == mv &&
        !(PyArray_FLAGS((PyArrayObject *)arr) & NPY_ARRAY_OWNDATA) && PyArray_ISWRITEABLE((PyArrayObject *)arr)) {
        uint16_t *data = (uint16_t *)PyArray_DATA((PyArrayObject *)arr);
        if (data[0] == 10 && data[3] == 40) score += 1;
        data[1] = 222;
        Py_buffer after;
        if (PyObject_GetBuffer(mv, &after, PyBUF_SIMPLE) == 0) {
            uint16_t *view_data = (uint16_t *)after.buf;
            if (view_data[1] == 222) score += 10;
            PyBuffer_Release(&after);
        }
    }
    Py_XDECREF(arr);
    Py_DECREF(mv);
    uint16_t raw_ro[4] = {10, 20, 30, 40};
    PyObject *bytes = PyBytes_FromStringAndSize((const char *)raw_ro, (Py_ssize_t)sizeof(raw_ro));
    if (bytes == NULL) return NULL;
    descr = PyArray_DescrNewFromType(NPY_USHORT);
    if (descr == NULL) { Py_DECREF(bytes); return NULL; }
    arr = PyArray_FromBuffer(bytes, descr, 2, (npy_intp)sizeof(uint16_t));
    if (arr != NULL && PyArray_SIZE((PyArrayObject *)arr) == 2 && PyArray_BASE((PyArrayObject *)arr) == bytes && !PyArray_ISWRITEABLE((PyArrayObject *)arr)) {
        uint16_t *data = (uint16_t *)PyArray_DATA((PyArrayObject *)arr);
        if (data[0] == 20 && data[1] == 30) score += 100;
    }
    Py_XDECREF(arr);
    descr = PyArray_DescrNewFromType(NPY_OBJECT);
    if (descr == NULL) { Py_DECREF(bytes); return NULL; }
    arr = PyArray_FromBuffer(bytes, descr, -1, 0);
    if (arr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(arr);
    descr = PyArray_DescrNewFromType(NPY_USHORT);
    if (descr == NULL) { Py_DECREF(bytes); return NULL; }
    arr = PyArray_FromBuffer(bytes, descr, -1, 99);
    if (arr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(arr);
    descr = PyArray_DescrNewFromType(NPY_USHORT);
    if (descr == NULL) { Py_DECREF(bytes); return NULL; }
    arr = PyArray_FromBuffer(bytes, descr, -1, 1);
    if (arr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(arr);
    Py_DECREF(bytes);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_buffer_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyArray_Chunk chunk;
    memset(&chunk, 0, sizeof(chunk));
    if (PyArray_BufferConverter(Py_None, &chunk) == NPY_SUCCEED && chunk.ptr == NULL &&
        chunk.base == NULL && chunk.len == 0 && (chunk.flags & NPY_ARRAY_BEHAVED) == NPY_ARRAY_BEHAVED) score += 1;
    char raw[4] = {1, 2, 3, 4};
    PyObject *mv = PyMemoryView_FromMemory(raw, 4, PyBUF_WRITE);
    if (mv == NULL) return NULL;
    memset(&chunk, 0, sizeof(chunk));
    if (PyArray_BufferConverter(mv, &chunk) == NPY_SUCCEED && chunk.ptr != NULL &&
        chunk.len == 4 && chunk.base == mv && (chunk.flags & NPY_ARRAY_WRITEABLE)) score += 10;
    Py_DECREF(mv);
    PyObject *ro = PyMemoryView_FromMemory(raw, 4, PyBUF_READ);
    if (ro == NULL) return NULL;
    memset(&chunk, 0, sizeof(chunk));
    if (PyArray_BufferConverter(ro, &chunk) == NPY_SUCCEED && chunk.ptr != NULL &&
        chunk.len == 4 && chunk.base == ro && !(chunk.flags & NPY_ARRAY_WRITEABLE)) score += 100;
    Py_DECREF(ro);
    if (PyArray_BufferConverter(Py_None, NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    PyObject *bad = PyLong_FromLong(5);
    if (bad == NULL) return NULL;
    memset(&chunk, 0, sizeof(chunk));
    if (PyArray_BufferConverter(bad, &chunk) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_DECREF(bad);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_from_iter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *list = PyList_New(3);
    if (list == NULL) return NULL;
    if (PyList_SetItem(list, 0, PyLong_FromLong(3)) != 0 ||
        PyList_SetItem(list, 1, PyLong_FromLong(4)) != 0 ||
        PyList_SetItem(list, 2, PyLong_FromLong(5)) != 0) {
        Py_DECREF(list);
        return NULL;
    }
    PyArray_Descr *descr = PyArray_DescrNewFromType(NPY_LONG);
    if (descr == NULL) { Py_DECREF(list); return NULL; }
    PyObject *arr = PyArray_FromIter(list, descr, -1);
    if (arr != NULL && PyArray_NDIM((PyArrayObject *)arr) == 1 && PyArray_SIZE((PyArrayObject *)arr) == 3 && PyArray_TYPE((PyArrayObject *)arr) == NPY_LONG) {
        long *data = (long *)PyArray_DATA((PyArrayObject *)arr);
        if (data[0] == 3 && data[1] == 4 && data[2] == 5) score += 1;
    }
    Py_XDECREF(arr);
    PyObject *tuple = PyTuple_New(3);
    if (tuple == NULL) { Py_DECREF(list); return NULL; }
    if (PyTuple_SetItem(tuple, 0, PyLong_FromLong(7)) != 0 ||
        PyTuple_SetItem(tuple, 1, PyLong_FromLong(8)) != 0 ||
        PyTuple_SetItem(tuple, 2, PyLong_FromLong(9)) != 0) {
        Py_DECREF(tuple);
        Py_DECREF(list);
        return NULL;
    }
    descr = PyArray_DescrNewFromType(NPY_LONG);
    if (descr == NULL) { Py_DECREF(tuple); Py_DECREF(list); return NULL; }
    arr = PyArray_FromIter(tuple, descr, 2);
    if (arr != NULL && PyArray_SIZE((PyArrayObject *)arr) == 2) {
        long *data = (long *)PyArray_DATA((PyArrayObject *)arr);
        if (data[0] == 7 && data[1] == 8) score += 10;
    }
    Py_XDECREF(arr);
    descr = PyArray_DescrNewFromType(NPY_LONG);
    if (descr == NULL) { Py_DECREF(tuple); Py_DECREF(list); return NULL; }
    arr = PyArray_FromIter(tuple, descr, 5);
    if (arr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_XDECREF(arr);
    descr = PyArray_DescrNewFromType(NPY_STRING);
    if (descr == NULL) { Py_DECREF(tuple); Py_DECREF(list); return NULL; }
    arr = PyArray_FromIter(list, descr, -1);
    if (arr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(arr);
    Py_DECREF(tuple);
    Py_DECREF(list);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *list = PyList_New(2);
    if (list == NULL) return NULL;
    if (PyList_SetItem(list, 0, PyLong_FromLong(3)) != 0 ||
        PyList_SetItem(list, 1, PyLong_FromLong(4)) != 0) {
        Py_DECREF(list);
        return NULL;
    }
    PyObject *converted = NULL;
    if (PyArray_Converter(list, &converted) == NPY_SUCCEED && converted != NULL && PyArray_Check(converted) && PyArray_SIZE((PyArrayObject *)converted) == 2) {
        score += 1;
    }
    Py_XDECREF(converted);
    Py_DECREF(list);
    PyObject *a = PyLong_FromLong(7);
    PyObject *b = PyLong_FromLong(8);
    if (a == NULL || b == NULL) { Py_XDECREF(a); Py_XDECREF(b); return NULL; }
    PyObject *tuple = PyTuple_Pack(2, a, b);
    Py_DECREF(a);
    Py_DECREF(b);
    if (tuple == NULL) return NULL;
    PyObject *arr = PyArray_FromAny(tuple, NULL, 1, 1, 0, NULL);
    Py_DECREF(tuple);
    if (arr == NULL) return NULL;
    converted = NULL;
    if (PyArray_Converter(arr, &converted) == NPY_SUCCEED && converted == arr && PyArray_Check(converted)) score += 10;
    Py_XDECREF(converted);
    Py_DECREF(arr);
    converted = (PyObject *)1;
    if (PyArray_Converter(Py_None, &converted) != NPY_SUCCEED && converted == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    if (PyArray_Converter(Py_None, NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_pyint_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *small = PyLong_FromLong(42);
    if (small == NULL) return NULL;
    if (PyArray_PyIntAsInt(small) == 42 && PyArray_PyIntAsIntp(small) == (npy_intp)42) score += 1;
    Py_DECREF(small);
    PyObject *negative = PyLong_FromLong(-7);
    if (negative == NULL) return NULL;
    if (PyArray_PyIntAsInt(negative) == -7 && PyArray_PyIntAsIntp(negative) == (npy_intp)-7) score += 10;
    Py_DECREF(negative);
    if (PyArray_PyIntAsInt(Py_True) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    if (PyArray_PyIntAsIntp(Py_False) == (npy_intp)-1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    PyObject *flt = PyFloat_FromDouble(3.0);
    if (flt == NULL) return NULL;
    if (PyArray_PyIntAsInt(flt) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_DECREF(flt);
    PyObject *large = PyLong_FromLongLong(2147483648LL);
    if (large == NULL) return NULL;
    if (PyArray_PyIntAsInt(large) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    if (PyArray_PyIntAsIntp(large) == (npy_intp)2147483648LL && PyErr_Occurred() == NULL) score += 1000000;
    Py_DECREF(large);
    if (PyArray_PyIntAsInt(NULL) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    int parsed = 0;
    PyObject *arg_small = PyLong_FromLong(123);
    if (arg_small == NULL) return NULL;
    if (PyArray_PythonPyIntFromInt(arg_small, &parsed) && parsed == 123) score += 100000000L;
    Py_DECREF(arg_small);
    PyObject *arg_negative = PyLong_FromLong(-9);
    if (arg_negative == NULL) return NULL;
    parsed = 0;
    if (PyArray_PythonPyIntFromInt(arg_negative, &parsed) && parsed == -9) score += 1000000000L;
    Py_DECREF(arg_negative);
    parsed = 77;
    if (!PyArray_PythonPyIntFromInt(Py_True, &parsed) && parsed == 77 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000000L; }
    PyObject *arg_too_large = PyLong_FromLongLong(2147483648LL);
    if (arg_too_large == NULL) return NULL;
    parsed = 88;
    if (!PyArray_PythonPyIntFromInt(arg_too_large, &parsed) && parsed == 88 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000000000L; }
    Py_DECREF(arg_too_large);
    parsed = 99;
    if (!PyArray_PythonPyIntFromInt(NULL, &parsed) && parsed == 99 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000000000L; }
    if (!PyArray_PythonPyIntFromInt(Py_None, NULL) && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000000000L; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_intp_from_sequence_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp vals[4] = {-99, -99, -99, -99};
    PyObject *scalar = PyLong_FromLong(5);
    if (scalar == NULL) return NULL;
    int n = PyArray_IntpFromSequence(scalar, vals, 4);
    if (n == 1 && vals[0] == 5 && PyErr_Occurred() == NULL) score += 1;
    Py_DECREF(scalar);
    vals[0] = vals[1] = vals[2] = vals[3] = -99;
    PyObject *tuple = PyTuple_New(3);
    if (tuple == NULL) return NULL;
    if (PyTuple_SetItem(tuple, 0, PyLong_FromLong(7)) != 0 ||
        PyTuple_SetItem(tuple, 1, PyLong_FromLong(8)) != 0 ||
        PyTuple_SetItem(tuple, 2, PyLong_FromLong(9)) != 0) {
        Py_DECREF(tuple);
        return NULL;
    }
    n = PyArray_IntpFromSequence(tuple, vals, 4);
    if (n == 3 && vals[0] == 7 && vals[1] == 8 && vals[2] == 9 && PyErr_Occurred() == NULL) score += 10;
    Py_DECREF(tuple);
    vals[0] = vals[1] = vals[2] = vals[3] = -99;
    PyObject *list = PyList_New(3);
    if (list == NULL) return NULL;
    if (PyList_SetItem(list, 0, PyLong_FromLong(4)) != 0 ||
        PyList_SetItem(list, 1, PyLong_FromLong(5)) != 0 ||
        PyList_SetItem(list, 2, PyLong_FromLong(6)) != 0) {
        Py_DECREF(list);
        return NULL;
    }
    n = PyArray_IntpFromSequence(list, vals, 2);
    if (n == 3 && vals[0] == 4 && vals[1] == 5 && vals[2] == -99 && PyErr_Occurred() == NULL) score += 100;
    Py_DECREF(list);
    if (PyArray_IntpFromSequence(Py_True, vals, 4) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    PyObject *flt = PyFloat_FromDouble(3.5);
    if (flt == NULL) return NULL;
    if (PyArray_IntpFromSequence(flt, vals, 4) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_DECREF(flt);
    if (PyArray_IntpFromSequence(Py_None, NULL, 4) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_intp_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyArray_Dims dims = {NULL, 123};
    PyObject *scalar = PyLong_FromLong(5);
    if (scalar == NULL) return NULL;
    if (PyArray_IntpConverter(scalar, &dims) == NPY_SUCCEED && dims.len == 1 && dims.ptr != NULL && dims.ptr[0] == 5) score += 1;
    PyDimMem_FREE(dims.ptr);
    dims.ptr = NULL;
    dims.len = 0;
    Py_DECREF(scalar);
    PyObject *tuple = PyTuple_New(2);
    if (tuple == NULL) return NULL;
    if (PyTuple_SetItem(tuple, 0, PyLong_FromLong(7)) != 0 ||
        PyTuple_SetItem(tuple, 1, PyLong_FromLong(8)) != 0) {
        Py_DECREF(tuple);
        return NULL;
    }
    if (PyArray_IntpConverter(tuple, &dims) == NPY_SUCCEED && dims.len == 2 && dims.ptr != NULL && dims.ptr[0] == 7 && dims.ptr[1] == 8) score += 10;
    PyDimMem_FREE(dims.ptr);
    dims.ptr = NULL;
    dims.len = 0;
    Py_DECREF(tuple);
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) return NULL;
    if (PyArray_IntpConverter(empty, &dims) == NPY_SUCCEED && dims.len == 0 && dims.ptr == NULL) score += 100;
    PyDimMem_FREE(dims.ptr);
    dims.ptr = NULL;
    dims.len = 0;
    Py_DECREF(empty);
    dims.ptr = (npy_intp *)1;
    dims.len = 99;
    if (PyArray_IntpConverter(Py_None, &dims) != NPY_SUCCEED && dims.ptr == NULL && dims.len == 0 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    dims.ptr = (npy_intp *)1;
    dims.len = 99;
    if (PyArray_IntpConverter(Py_True, &dims) != NPY_SUCCEED && dims.ptr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_optional_intp_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyArray_Dims dims;
    npy_intp sentinel[1] = {77};
    dims.ptr = sentinel;
    dims.len = 77;
    if (PyArray_OptionalIntpConverter(Py_None, &dims) == NPY_SUCCEED && dims.ptr == sentinel && dims.len == 77) score += 1;
    dims.ptr = NULL;
    dims.len = 0;
    PyObject *tuple = PyTuple_New(2);
    if (tuple == NULL) return NULL;
    if (PyTuple_SetItem(tuple, 0, PyLong_FromLong(2)) != 0 ||
        PyTuple_SetItem(tuple, 1, PyLong_FromLong(4)) != 0) {
        Py_DECREF(tuple);
        return NULL;
    }
    if (PyArray_OptionalIntpConverter(tuple, &dims) == NPY_SUCCEED && dims.len == 2 && dims.ptr != NULL && dims.ptr[0] == 2 && dims.ptr[1] == 4) score += 10;
    PyDimMem_FREE(dims.ptr);
    dims.ptr = NULL;
    dims.len = 0;
    Py_DECREF(tuple);
    PyObject *scalar = PyLong_FromLong(9);
    if (scalar == NULL) return NULL;
    if (PyArray_OptionalIntpConverter(scalar, &dims) == NPY_SUCCEED && dims.len == 1 && dims.ptr != NULL && dims.ptr[0] == 9) score += 100;
    PyDimMem_FREE(dims.ptr);
    dims.ptr = NULL;
    dims.len = 0;
    Py_DECREF(scalar);
    dims.ptr = sentinel;
    dims.len = 77;
    if (PyArray_OptionalIntpConverter(Py_False, &dims) != NPY_SUCCEED && dims.ptr == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    if (PyArray_OptionalIntpConverter(Py_None, NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_promote_types_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyArray_Descr *bool_descr = PyArray_DescrFromType(NPY_BOOL);
    PyArray_Descr *long_descr = PyArray_DescrFromType(NPY_LONG);
    PyArray_Descr *double_descr = PyArray_DescrFromType(NPY_DOUBLE);
    PyArray_Descr *cdouble_descr = PyArray_DescrFromType(NPY_CDOUBLE);
    PyArray_Descr *object_descr = PyArray_DescrFromType(NPY_OBJECT);
    if (bool_descr == NULL || long_descr == NULL || double_descr == NULL || cdouble_descr == NULL || object_descr == NULL) return NULL;
    PyArray_Descr *res = PyArray_PromoteTypes(bool_descr, long_descr);
    if (res != NULL && PyDataType_TYPE(res) == NPY_LONG) score += 1;
    Py_XDECREF((PyObject *)res);
    res = PyArray_PromoteTypes(long_descr, double_descr);
    if (res != NULL && PyDataType_TYPE(res) == NPY_DOUBLE) score += 10;
    Py_XDECREF((PyObject *)res);
    res = PyArray_PromoteTypes(double_descr, cdouble_descr);
    if (res != NULL && PyDataType_TYPE(res) == NPY_CDOUBLE) score += 100;
    Py_XDECREF((PyObject *)res);
    res = PyArray_PromoteTypes(long_descr, object_descr);
    if (res != NULL && PyDataType_TYPE(res) == NPY_OBJECT) score += 1000;
    Py_XDECREF((PyObject *)res);
    if (PyArray_PromoteTypes(NULL, long_descr) == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_result_type_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims1[1] = {2};
    PyObject *arr_long_obj = PyArray_SimpleNew(1, dims1, NPY_LONG);
    PyObject *arr_double_obj = PyArray_SimpleNew(1, dims1, NPY_DOUBLE);
    PyObject *arr_bool_obj = PyArray_SimpleNew(1, dims1, NPY_BOOL);
    if (arr_long_obj == NULL || arr_double_obj == NULL || arr_bool_obj == NULL) {
        Py_XDECREF(arr_long_obj);
        Py_XDECREF(arr_double_obj);
        Py_XDECREF(arr_bool_obj);
        return NULL;
    }
    PyArrayObject *arrays2[2] = {(PyArrayObject *)arr_long_obj, (PyArrayObject *)arr_double_obj};
    PyArray_Descr *res = PyArray_ResultType(2, arrays2, 0, NULL);
    if (res != NULL && PyDataType_TYPE(res) == NPY_DOUBLE) score += 1;
    Py_XDECREF((PyObject *)res);
    PyArray_Descr *long_descr = PyArray_DescrFromType(NPY_LONG);
    PyArray_Descr *cdouble_descr = PyArray_DescrFromType(NPY_CDOUBLE);
    if (long_descr == NULL || cdouble_descr == NULL) {
        Py_DECREF(arr_long_obj);
        Py_DECREF(arr_double_obj);
        Py_DECREF(arr_bool_obj);
        return NULL;
    }
    PyArray_Descr *descrs2[2] = {long_descr, cdouble_descr};
    res = PyArray_ResultType(0, NULL, 2, descrs2);
    if (res != NULL && PyDataType_TYPE(res) == NPY_CDOUBLE) score += 10;
    Py_XDECREF((PyObject *)res);
    PyArrayObject *arrays1[1] = {(PyArrayObject *)arr_bool_obj};
    res = PyArray_ResultType(1, arrays1, 1, &long_descr);
    if (res != NULL && PyDataType_TYPE(res) == NPY_LONG) score += 100;
    Py_XDECREF((PyObject *)res);
    if (PyArray_ResultType(0, NULL, 0, NULL) == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    if (PyArray_ResultType(1, NULL, 0, NULL) == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_DECREF(arr_long_obj);
    Py_DECREF(arr_double_obj);
    Py_DECREF(arr_bool_obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_priority_score(PyObject *self, PyObject *args) {
    (void)args;
    long score = 0;
    double got = 0.0;
    PyObject *plain = PyList_New(0);
    if (plain == NULL) return NULL;
    got = PyArray_GetPriority(plain, 4.25);
    if (got > 4.24 && got < 4.26 && PyErr_Occurred() == NULL) score += 1;
    Py_DECREF(plain);
    PyObject *flt = PyFloat_FromDouble(7.5);
    if (flt == NULL) return NULL;
    if (PyObject_SetAttrString(self, "__array_priority__", flt) != 0) { Py_DECREF(flt); return NULL; }
    Py_DECREF(flt);
    got = PyArray_GetPriority(self, 1.0);
    if (got > 7.49 && got < 7.51 && PyErr_Occurred() == NULL) score += 10;
    PyObject *ival = PyLong_FromLong(9);
    if (ival == NULL) return NULL;
    if (PyObject_SetAttrString(self, "__array_priority__", ival) != 0) { Py_DECREF(ival); return NULL; }
    Py_DECREF(ival);
    got = PyArray_GetPriority(self, 1.0);
    if (got > 8.99 && got < 9.01 && PyErr_Occurred() == NULL) score += 100;
    PyObject *bad = PyUnicode_FromString("bad");
    if (bad == NULL) return NULL;
    if (PyObject_SetAttrString(self, "__array_priority__", bad) != 0) { Py_DECREF(bad); return NULL; }
    Py_DECREF(bad);
    got = PyArray_GetPriority(self, 3.25);
    if (got > 3.24 && got < 3.26 && PyErr_Occurred() == NULL) score += 1000;
    npy_intp dims[1] = {1};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (arr == NULL) return NULL;
    got = PyArray_GetPriority(arr, 5.5);
    if (got > -0.01 && got < 0.01 && PyErr_Occurred() == NULL) score += 10000;
    Py_DECREF(arr);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_check_strides_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2x3[2] = {2, 3};
    npy_intp cstrides[2] = {(npy_intp)(3 * sizeof(long)), (npy_intp)sizeof(long)};
    if (PyArray_CheckStrides((int)sizeof(long), 2, (npy_intp)(6 * sizeof(long)), 0, dims2x3, cstrides)) score += 1;
    npy_intp dims4[1] = {4};
    npy_intp reverse[1] = {-(npy_intp)sizeof(long)};
    if (PyArray_CheckStrides((int)sizeof(long), 1, (npy_intp)(4 * sizeof(long)), (npy_intp)(3 * sizeof(long)), dims4, reverse)) score += 10;
    npy_intp bad_forward[1] = {(npy_intp)sizeof(long)};
    if (!PyArray_CheckStrides((int)sizeof(long), 1, (npy_intp)(4 * sizeof(long)), (npy_intp)(2 * sizeof(long)), dims4, bad_forward)) score += 100;
    npy_intp zero_dim[1] = {0};
    if (PyArray_CheckStrides((int)sizeof(long), 1, 0, 0, zero_dim, bad_forward)) score += 1000;
    if (!PyArray_CheckStrides((int)sizeof(long), 1, 0, 99, zero_dim, bad_forward)) score += 10000;
    if (!PyArray_CheckStrides((int)sizeof(long), 2, (npy_intp)(6 * sizeof(long)), 0, dims2x3, NULL)) score += 100000;
    if (!PyArray_CheckStrides((int)sizeof(long), -1, (npy_intp)(6 * sizeof(long)), 0, dims2x3, cstrides)) score += 1000000;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_broadcast_to_shape_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2x1[2] = {2, 1};
    PyObject *arr = PyArray_SimpleNew(2, dims2x1, NPY_LONG);
    if (arr == NULL) return NULL;
    long *data = (long *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 11; data[1] = 22;
    PyObject *plain_iter_obj = PyArray_IterNew(arr);
    if (plain_iter_obj != NULL) {
        PyArrayIterObject *plain = (PyArrayIterObject *)plain_iter_obj;
        if (plain->size == 2 && plain->nd_m1 == 1 &&
            *(long *)PyArray_ITER_DATA(plain) == 11) {
            PyArray_ITER_NEXT(plain);
            if (plain->index == 1 && *(long *)PyArray_ITER_DATA(plain) == 22) score += 1;
        }
    }
    Py_XDECREF(plain_iter_obj);
    npy_intp target2x3[2] = {2, 3};
    PyObject *iter_obj = PyArray_BroadcastToShape(arr, target2x3, 2);
    if (iter_obj != NULL) {
        PyArrayIterObject *it = (PyArrayIterObject *)iter_obj;
        if (it->size == 6 && it->nd_m1 == 1 && it->dims_m1[0] == 1 &&
            it->dims_m1[1] == 2 && it->strides[0] == (npy_intp)sizeof(long) &&
            it->strides[1] == 0 && *(long *)PyArray_ITER_DATA(it) == 11) score += 10;
        PyArray_ITER_NEXT(it);
        if (it->index == 1 && it->coordinates[0] == 0 && it->coordinates[1] == 1 &&
            *(long *)PyArray_ITER_DATA(it) == 11) score += 100;
        PyArray_ITER_NEXT(it);
        PyArray_ITER_NEXT(it);
        if (it->index == 3 && it->coordinates[0] == 1 && it->coordinates[1] == 0 &&
            *(long *)PyArray_ITER_DATA(it) == 22) score += 1000;
    }
    Py_XDECREF(iter_obj);
    npy_intp dims3[1] = {3};
    PyObject *row = PyArray_SimpleNew(1, dims3, NPY_LONG);
    if (row == NULL) { Py_DECREF(arr); return NULL; }
    long *row_data = (long *)PyArray_DATA((PyArrayObject *)row);
    row_data[0] = 3; row_data[1] = 4; row_data[2] = 5;
    iter_obj = PyArray_BroadcastToShape(row, target2x3, 2);
    if (iter_obj != NULL) {
        PyArrayIterObject *it = (PyArrayIterObject *)iter_obj;
        if (it->size == 6 && it->strides[0] == 0 && it->strides[1] == (npy_intp)sizeof(long) &&
            *(long *)PyArray_ITER_DATA(it) == 3) {
            PyArray_ITER_NEXT(it);
            if (*(long *)PyArray_ITER_DATA(it) == 4) {
                PyArray_ITER_NEXT(it);
                if (*(long *)PyArray_ITER_DATA(it) == 5) {
                    PyArray_ITER_NEXT(it);
                    if (*(long *)PyArray_ITER_DATA(it) == 3) score += 10000;
                }
            }
        }
    }
    Py_XDECREF(iter_obj);
    npy_intp bad_target[2] = {3, 3};
    iter_obj = PyArray_BroadcastToShape(arr, bad_target, 2);
    if (iter_obj == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(iter_obj);
    Py_DECREF(row);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_broadcast_multi_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2x3[2] = {2, 3};
    npy_intp dims3[1] = {3};
    PyObject *grid = PyArray_SimpleNew(2, dims2x3, NPY_LONG);
    PyObject *row = PyArray_SimpleNew(1, dims3, NPY_LONG);
    if (grid == NULL || row == NULL) {
        Py_XDECREF(grid);
        Py_XDECREF(row);
        return NULL;
    }
    long *grid_data = (long *)PyArray_DATA((PyArrayObject *)grid);
    long *row_data = (long *)PyArray_DATA((PyArrayObject *)row);
    for (long i = 0; i < 6; i++) grid_data[i] = i + 1;
    row_data[0] = 10;
    row_data[1] = 20;
    row_data[2] = 30;
    PyObject *multi_obj = PyArray_MultiIterNew(2, grid, row);
    if (multi_obj == NULL) {
        Py_DECREF(row);
        Py_DECREF(grid);
        return NULL;
    }
    PyArrayMultiIterObject *multi = (PyArrayMultiIterObject *)multi_obj;
    PyArray_MultiIter_GOTO1D(multi, 4);
    if (PyArray_Broadcast(multi) == NPY_SUCCEED && PyArray_MultiIter_NDIM(multi) == 2 &&
        PyArray_MultiIter_SIZE(multi) == 6 && PyArray_MultiIter_DIMS(multi)[0] == 2 &&
        PyArray_MultiIter_DIMS(multi)[1] == 3 && PyArray_MultiIter_INDEX(multi) == 0) score += 1;
    if (*(long *)PyArray_MultiIter_DATA(multi, 0) == 1 &&
        *(long *)PyArray_MultiIter_DATA(multi, 1) == 10) score += 10;
    PyArray_MultiIter_GOTO1D(multi, 5);
    if (*(long *)PyArray_MultiIter_DATA(multi, 0) == 6 &&
        *(long *)PyArray_MultiIter_DATA(multi, 1) == 30) score += 100;
    if (PyArray_Broadcast(NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_DECREF(multi_obj);
    npy_intp bad_dims[1] = {4};
    PyObject *bad = PyArray_SimpleNew(1, bad_dims, NPY_LONG);
    if (bad == NULL) {
        Py_DECREF(row);
        Py_DECREF(grid);
        return NULL;
    }
    PyObject *bad_multi = PyArray_MultiIterNew(2, grid, bad);
    if (bad_multi == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(bad_multi);
    Py_DECREF(bad);
    Py_DECREF(row);
    Py_DECREF(grid);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_multi_iter_from_objects_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2x3[2] = {2, 3};
    npy_intp dims3[1] = {3};
    PyObject *grid = PyArray_SimpleNew(2, dims2x3, NPY_LONG);
    PyObject *row = PyArray_SimpleNew(1, dims3, NPY_LONG);
    if (grid == NULL || row == NULL) {
        Py_XDECREF(grid);
        Py_XDECREF(row);
        return NULL;
    }
    long *grid_data = (long *)PyArray_DATA((PyArrayObject *)grid);
    long *row_data = (long *)PyArray_DATA((PyArrayObject *)row);
    for (long i = 0; i < 6; i++) grid_data[i] = i + 1;
    row_data[0] = 10;
    row_data[1] = 20;
    row_data[2] = 30;
    PyObject *mps_one[1] = {grid};
    PyObject *multi_obj = PyArray_MultiIterFromObjects(mps_one, 1, 1, row);
    if (multi_obj != NULL) {
        PyArrayMultiIterObject *multi = (PyArrayMultiIterObject *)multi_obj;
        if (PyArray_MultiIter_NUMITER(multi) == 2 && PyArray_MultiIter_NDIM(multi) == 2 &&
            PyArray_MultiIter_SIZE(multi) == 6 && PyArray_MultiIter_DIMS(multi)[0] == 2 &&
            PyArray_MultiIter_DIMS(multi)[1] == 3) score += 1;
        if (*(long *)PyArray_MultiIter_DATA(multi, 0) == 1 &&
            *(long *)PyArray_MultiIter_DATA(multi, 1) == 10) score += 10;
        PyArray_MultiIter_GOTO1D(multi, 5);
        if (*(long *)PyArray_MultiIter_DATA(multi, 0) == 6 &&
            *(long *)PyArray_MultiIter_DATA(multi, 1) == 30) score += 100;
    }
    Py_XDECREF(multi_obj);
    PyObject *mps_two[2] = {grid, row};
    multi_obj = PyArray_MultiIterFromObjects(mps_two, 2, 0);
    if (multi_obj != NULL) {
        PyArrayMultiIterObject *multi = (PyArrayMultiIterObject *)multi_obj;
        if (PyArray_MultiIter_NUMITER(multi) == 2 && PyArray_MultiIter_SIZE(multi) == 6 &&
            *(long *)PyArray_MultiIter_DATA(multi, 0) == 1 &&
            *(long *)PyArray_MultiIter_DATA(multi, 1) == 10) score += 1000;
    }
    Py_XDECREF(multi_obj);
    npy_intp bad_dims[1] = {4};
    PyObject *bad = PyArray_SimpleNew(1, bad_dims, NPY_LONG);
    if (bad == NULL) {
        Py_DECREF(row);
        Py_DECREF(grid);
        return NULL;
    }
    PyObject *bad_multi = PyArray_MultiIterFromObjects(mps_one, 1, 1, bad);
    if (bad_multi == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(bad_multi);
    bad_multi = PyArray_MultiIterFromObjects(NULL, 1, 0);
    if (bad_multi == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_XDECREF(bad_multi);
    Py_DECREF(bad);
    Py_DECREF(row);
    Py_DECREF(grid);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_remove_smallest_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2x3[2] = {2, 3};
    npy_intp dims3[1] = {3};
    PyObject *grid = PyArray_SimpleNew(2, dims2x3, NPY_LONG);
    PyObject *row = PyArray_SimpleNew(1, dims3, NPY_LONG);
    if (grid == NULL || row == NULL) {
        Py_XDECREF(grid);
        Py_XDECREF(row);
        return NULL;
    }
    long *grid_data = (long *)PyArray_DATA((PyArrayObject *)grid);
    long *row_data = (long *)PyArray_DATA((PyArrayObject *)row);
    for (long i = 0; i < 6; i++) grid_data[i] = i + 1;
    row_data[0] = 10;
    row_data[1] = 20;
    row_data[2] = 30;
    PyObject *multi_obj = PyArray_MultiIterNew(2, grid, row);
    if (multi_obj == NULL) {
        Py_DECREF(row);
        Py_DECREF(grid);
        return NULL;
    }
    PyArrayMultiIterObject *multi = (PyArrayMultiIterObject *)multi_obj;
    int axis = PyArray_RemoveSmallest(multi);
    if (axis == 1 && PyArray_MultiIter_SIZE(multi) == 2 &&
        multi->iters[0]->dims_m1[1] == 0 && multi->iters[0]->backstrides[1] == 0 &&
        multi->iters[1]->dims_m1[1] == 0 && multi->iters[1]->backstrides[1] == 0) score += 1;
    if (*(long *)PyArray_MultiIter_DATA(multi, 0) == 1 &&
        *(long *)PyArray_MultiIter_DATA(multi, 1) == 10) score += 10;
    PyArray_MultiIter_NEXT(multi);
    if (PyArray_MultiIter_INDEX(multi) == 1 &&
        *(long *)PyArray_MultiIter_DATA(multi, 0) == 4 &&
        *(long *)PyArray_MultiIter_DATA(multi, 1) == 10) score += 100;
    Py_DECREF(multi_obj);
    npy_intp dims0[1] = {1};
    PyObject *scalar = PyArray_SimpleNew(0, dims0, NPY_LONG);
    if (scalar == NULL) {
        Py_DECREF(row);
        Py_DECREF(grid);
        return NULL;
    }
    multi_obj = PyArray_MultiIterNew(1, scalar);
    if (multi_obj != NULL) {
        if (PyArray_RemoveSmallest((PyArrayMultiIterObject *)multi_obj) == -1 && PyErr_Occurred() == NULL) score += 1000;
    }
    Py_XDECREF(multi_obj);
    if (PyArray_RemoveSmallest(NULL) == -1 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_DECREF(scalar);
    Py_DECREF(row);
    Py_DECREF(grid);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_multi_iter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2x3[2] = {2, 3};
    npy_intp dims3[1] = {3};
    PyObject *grid = PyArray_SimpleNew(2, dims2x3, NPY_LONG);
    PyObject *row = PyArray_SimpleNew(1, dims3, NPY_LONG);
    if (grid == NULL || row == NULL) {
        Py_XDECREF(grid);
        Py_XDECREF(row);
        return NULL;
    }
    long *grid_data = (long *)PyArray_DATA((PyArrayObject *)grid);
    long *row_data = (long *)PyArray_DATA((PyArrayObject *)row);
    for (long i = 0; i < 6; i++) grid_data[i] = i + 1;
    row_data[0] = 10;
    row_data[1] = 20;
    row_data[2] = 30;
    PyObject *multi_obj = PyArray_MultiIterNew(2, grid, row);
    if (multi_obj == NULL) {
        Py_DECREF(row);
        Py_DECREF(grid);
        return NULL;
    }
    PyArrayMultiIterObject *multi = (PyArrayMultiIterObject *)multi_obj;
    if (PyArray_MultiIter_NUMITER(multi) == 2 && PyArray_MultiIter_NDIM(multi) == 2 &&
        PyArray_MultiIter_SIZE(multi) == 6 && PyArray_MultiIter_DIMS(multi)[0] == 2 &&
        PyArray_MultiIter_DIMS(multi)[1] == 3) score += 1;
    if (PyArray_MultiIter_INDEX(multi) == 0 && PyArray_MultiIter_NOTDONE(multi) &&
        *(long *)PyArray_MultiIter_DATA(multi, 0) == 1 &&
        *(long *)PyArray_MultiIter_DATA(multi, 1) == 10) score += 10;
    PyArray_MultiIter_NEXT(multi);
    if (PyArray_MultiIter_INDEX(multi) == 1 &&
        *(long *)PyArray_MultiIter_DATA(multi, 0) == 2 &&
        *(long *)PyArray_MultiIter_DATA(multi, 1) == 20) score += 100;
    PyArray_MultiIter_GOTO1D(multi, 3);
    if (PyArray_MultiIter_INDEX(multi) == 3 &&
        *(long *)PyArray_MultiIter_DATA(multi, 0) == 4 &&
        *(long *)PyArray_MultiIter_DATA(multi, 1) == 10) score += 1000;
    PyArray_MultiIter_RESET(multi);
    if (PyArray_MultiIter_INDEX(multi) == 0 &&
        *(long *)PyArray_MultiIter_DATA(multi, 0) == 1 &&
        *(long *)PyArray_MultiIter_DATA(multi, 1) == 10) score += 10000;
    Py_DECREF(multi_obj);
    npy_intp bad_dims[1] = {4};
    PyObject *bad = PyArray_SimpleNew(1, bad_dims, NPY_LONG);
    if (bad == NULL) {
        Py_DECREF(row);
        Py_DECREF(grid);
        return NULL;
    }
    PyObject *bad_multi = PyArray_MultiIterNew(2, grid, bad);
    if (bad_multi == NULL && PyErr_Occurred() != NULL) {
        PyErr_Clear();
        score += 100000;
    }
    Py_XDECREF(bad_multi);
    Py_DECREF(bad);
    Py_DECREF(row);
    Py_DECREF(grid);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_iter_all_but_axis_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2x3[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims2x3, NPY_LONG);
    if (arr == NULL) return NULL;
    long *data = (long *)PyArray_DATA((PyArrayObject *)arr);
    for (long i = 0; i < 6; i++) data[i] = i + 1;
    int axis = 1;
    PyObject *iter_obj = PyArray_IterAllButAxis(arr, &axis);
    if (iter_obj != NULL) {
        PyArrayIterObject *it = (PyArrayIterObject *)iter_obj;
        if (axis == 1 && it->size == 2 && it->dims_m1[1] == 0 &&
            it->backstrides[1] == 0 && *(long *)PyArray_ITER_DATA(it) == 1) {
            PyArray_ITER_NEXT(it);
            if (it->index == 1 && *(long *)PyArray_ITER_DATA(it) == 4) score += 1;
        }
    }
    Py_XDECREF(iter_obj);
    axis = -1;
    iter_obj = PyArray_IterAllButAxis(arr, &axis);
    if (iter_obj != NULL) {
        PyArrayIterObject *it = (PyArrayIterObject *)iter_obj;
        if (axis == 1 && it->size == 2 && *(long *)PyArray_ITER_DATA(it) == 1) {
            PyArray_ITER_NEXT(it);
            if (*(long *)PyArray_ITER_DATA(it) == 4) score += 10;
        }
    }
    Py_XDECREF(iter_obj);
    axis = 0;
    iter_obj = PyArray_IterAllButAxis(arr, &axis);
    if (iter_obj != NULL) {
        PyArrayIterObject *it = (PyArrayIterObject *)iter_obj;
        if (axis == 0 && it->size == 3 && it->dims_m1[0] == 0 &&
            *(long *)PyArray_ITER_DATA(it) == 1) {
            PyArray_ITER_NEXT(it);
            if (*(long *)PyArray_ITER_DATA(it) == 2) score += 100;
        }
    }
    Py_XDECREF(iter_obj);
    axis = 2;
    iter_obj = PyArray_IterAllButAxis(arr, &axis);
    if (iter_obj == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(iter_obj);
    iter_obj = PyArray_IterAllButAxis(arr, NULL);
    if (iter_obj == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_XDECREF(iter_obj);
    PyObject *scalar = PyArray_SimpleNew(0, NULL, NPY_LONG);
    if (scalar == NULL) { Py_DECREF(arr); return NULL; }
    long *scalar_data = (long *)PyArray_DATA((PyArrayObject *)scalar);
    *scalar_data = 99;
    axis = -1;
    iter_obj = PyArray_IterAllButAxis(scalar, &axis);
    if (iter_obj != NULL) {
        PyArrayIterObject *it = (PyArrayIterObject *)iter_obj;
        if (it->size == 1 && it->nd_m1 == -1 && *(long *)PyArray_ITER_DATA(it) == 99) score += 100000;
    }
    Py_XDECREF(iter_obj);
    Py_DECREF(scalar);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_list_pointer_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims3[3] = {2, 3, 4};
    int idims3[3] = {2, 3, 4};
    if (PyArray_MultiplyList(dims3, 3) == 24) score += 1;
    if (PyArray_MultiplyIntList(idims3, 3) == 24) score += 10;
    PyObject *tuple = PyArray_IntTupleFromIntp(3, dims3);
    if (tuple != NULL && PyTuple_Size(tuple) == 3 &&
        PyLong_AsLong(PyTuple_GetItem(tuple, 0)) == 2 &&
        PyLong_AsLong(PyTuple_GetItem(tuple, 1)) == 3 &&
        PyLong_AsLong(PyTuple_GetItem(tuple, 2)) == 4) score += 1000;
    Py_XDECREF(tuple);
    PyObject *empty_tuple = PyArray_IntTupleFromIntp(0, dims3);
    if (empty_tuple != NULL && PyTuple_Size(empty_tuple) == 0) score += 10000;
    Py_XDECREF(empty_tuple);
    npy_intp dims2[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims2, NPY_LONG);
    if (arr == NULL) return NULL;
    long *data = (long *)PyArray_DATA((PyArrayObject *)arr);
    data[0] = 10; data[1] = 20; data[2] = 30; data[3] = 40; data[4] = 50; data[5] = 60;
    npy_intp ind[2] = {1, 2};
    long *ptr = (long *)PyArray_GetPtr((PyArrayObject *)arr, ind);
    if (ptr == &data[5] && *ptr == 60) score += 100;
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_overflow_multiply_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims3[3] = {2, 3, 4};
    if (PyArray_OverflowMultiplyList(dims3, 3) == 24) score += 1;
    npy_intp max_intp = (npy_intp)(((size_t)-1) >> 1);
    npy_intp zero_first[3] = {2, 0, max_intp};
    if (PyArray_OverflowMultiplyList(zero_first, 3) == 0) score += 10;
    npy_intp overflow[2] = {max_intp, 2};
    if (PyArray_OverflowMultiplyList(overflow, 2) == -1) score += 100;
    if (PyArray_OverflowMultiplyList(dims3, 0) == 1) score += 1000;
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_endianness_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    const union { uint32_t i; unsigned char c[4]; } marker = {0x01020304u};
    int expected = NPY_CPU_UNKNOWN_ENDIAN;
    if (marker.c[0] == 1) expected = NPY_CPU_BIG;
    else if (marker.c[0] == 4) expected = NPY_CPU_LITTLE;
    int got = PyArray_GetEndianness();
    if (got == expected) score += 1;
    if (got == NPY_CPU_LITTLE || got == NPY_CPU_BIG || got == NPY_CPU_UNKNOWN_ENDIAN) score += 10;
    if (expected != NPY_CPU_UNKNOWN_ENDIAN && got != NPY_CPU_UNKNOWN_ENDIAN) score += 100;
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_feature_version_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    unsigned int got = PyArray_GetNDArrayCFeatureVersion();
    if (got == (unsigned int)NPY_API_VERSION) score += 1;
    if (got >= (unsigned int)NPY_1_7_API_VERSION) score += 10;
    if (got <= (unsigned int)NPY_2_4_API_VERSION) score += 100;
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_check_axis_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims2, NPY_INT);
    if (arr == NULL) return NULL;
    int axis = 1;
    PyObject *checked = PyArray_CheckAxis((PyArrayObject *)arr, &axis, 0);
    if (checked == arr && axis == 1) score += 1;
    Py_XDECREF(checked);
    axis = -1;
    checked = PyArray_CheckAxis((PyArrayObject *)arr, &axis, 0);
    if (checked == arr && axis == 1) score += 10;
    Py_XDECREF(checked);
    axis = 0;
    checked = PyArray_CheckAxis((PyArrayObject *)arr, &axis, NPY_ARRAY_CARRAY);
    if (checked == arr && axis == 0) score += 100;
    Py_XDECREF(checked);
    axis = 2;
    checked = PyArray_CheckAxis((PyArrayObject *)arr, &axis, 0);
    if (checked == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_XDECREF(checked);
    axis = NPY_RAVEL_AXIS;
    checked = PyArray_CheckAxis((PyArrayObject *)arr, &axis, 0);
    if (checked != NULL && checked != arr && axis == 0 &&
        PyArray_NDIM((PyArrayObject *)checked) == 1 &&
        PyArray_SIZE((PyArrayObject *)checked) == 6 &&
        PyArray_BASE((PyArrayObject *)checked) == arr) score += 10000;
    Py_XDECREF(checked);
    PyObject *scalar = PyArray_SimpleNew(0, NULL, NPY_INT);
    if (scalar == NULL) { Py_DECREF(arr); return NULL; }
    axis = 0;
    checked = PyArray_CheckAxis((PyArrayObject *)scalar, &axis, 0);
    if (checked != NULL && axis == 0 && PyArray_NDIM((PyArrayObject *)checked) == 1 &&
        PyArray_SIZE((PyArrayObject *)checked) == 1) score += 100000;
    Py_XDECREF(checked);
    checked = PyArray_CheckAxis((PyArrayObject *)arr, NULL, 0);
    if (checked == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_XDECREF(checked);
    Py_DECREF(scalar);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_clipmode_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    NPY_CLIPMODE mode = (NPY_CLIPMODE)-1;
    if (PyArray_ClipmodeConverter(Py_None, &mode) == 0 && mode == NPY_RAISE) score += 1;
    PyObject *clip = PyUnicode_FromString("clip");
    if (clip == NULL) return NULL;
    mode = (NPY_CLIPMODE)-1;
    if (PyArray_ClipmodeConverter(clip, &mode) == 0 && mode == NPY_CLIP) score += 10;
    Py_DECREF(clip);
    PyObject *wrap = PyBytes_FromString("wrap");
    if (wrap == NULL) return NULL;
    mode = (NPY_CLIPMODE)-1;
    if (PyArray_ClipmodeConverter(wrap, &mode) == 0 && mode == NPY_WRAP) score += 100;
    Py_DECREF(wrap);
    PyObject *raise_int = PyLong_FromLong((long)NPY_RAISE);
    if (raise_int == NULL) return NULL;
    mode = (NPY_CLIPMODE)-1;
    if (PyArray_ClipmodeConverter(raise_int, &mode) == 0 && mode == NPY_RAISE) score += 1000;
    Py_DECREF(raise_int);
    PyObject *clip_int = PyLong_FromLong((long)NPY_CLIP);
    if (clip_int == NULL) return NULL;
    mode = (NPY_CLIPMODE)-1;
    if (PyArray_ClipmodeConverter(clip_int, &mode) == 0 && mode == NPY_CLIP) score += 10000;
    Py_DECREF(clip_int);
    PyObject *bad_case = PyUnicode_FromString("Clip");
    if (bad_case == NULL) return NULL;
    if (PyArray_ClipmodeConverter(bad_case, &mode) != 0 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_DECREF(bad_case);
    PyObject *bad_int = PyLong_FromLong(99);
    if (bad_int == NULL) return NULL;
    if (PyArray_ClipmodeConverter(bad_int, &mode) != 0 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_DECREF(bad_int);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_clipmode_sequence_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    NPY_CLIPMODE modes[3] = {(NPY_CLIPMODE)-1, (NPY_CLIPMODE)-1, (NPY_CLIPMODE)-1};
    PyObject *wrap = PyUnicode_FromString("wrap");
    if (wrap == NULL) return NULL;
    if (PyArray_ConvertClipmodeSequence(wrap, modes, 3) == NPY_SUCCEED &&
        modes[0] == NPY_WRAP && modes[1] == NPY_WRAP && modes[2] == NPY_WRAP) score += 1;
    Py_DECREF(wrap);
    PyObject *seq = PyTuple_New(3);
    if (seq == NULL) return NULL;
    PyObject *clip = PyUnicode_FromString("clip");
    PyObject *raise_s = PyBytes_FromString("raise");
    PyObject *wrap_i = PyLong_FromLong((long)NPY_WRAP);
    if (clip == NULL || raise_s == NULL || wrap_i == NULL) { Py_XDECREF(clip); Py_XDECREF(raise_s); Py_XDECREF(wrap_i); Py_DECREF(seq); return NULL; }
    PyTuple_SET_ITEM(seq, 0, clip);
    PyTuple_SET_ITEM(seq, 1, raise_s);
    PyTuple_SET_ITEM(seq, 2, wrap_i);
    modes[0] = modes[1] = modes[2] = (NPY_CLIPMODE)-1;
    if (PyArray_ConvertClipmodeSequence(seq, modes, 3) == NPY_SUCCEED &&
        modes[0] == NPY_CLIP && modes[1] == NPY_RAISE && modes[2] == NPY_WRAP) score += 10;
    Py_DECREF(seq);
    modes[0] = modes[1] = (NPY_CLIPMODE)-1;
    if (PyArray_ConvertClipmodeSequence(NULL, modes, 2) == NPY_SUCCEED &&
        modes[0] == NPY_RAISE && modes[1] == NPY_RAISE) score += 100;
    PyObject *bad_len = PyTuple_New(1);
    if (bad_len == NULL) return NULL;
    PyObject *clip2 = PyUnicode_FromString("clip");
    if (clip2 == NULL) { Py_DECREF(bad_len); return NULL; }
    PyTuple_SET_ITEM(bad_len, 0, clip2);
    if (PyArray_ConvertClipmodeSequence(bad_len, modes, 2) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_DECREF(bad_len);
    PyObject *bad_item = PyTuple_New(1);
    if (bad_item == NULL) return NULL;
    PyObject *case_bad = PyUnicode_FromString("Clip");
    if (case_bad == NULL) { Py_DECREF(bad_item); return NULL; }
    PyTuple_SET_ITEM(bad_item, 0, case_bad);
    if (PyArray_ConvertClipmodeSequence(bad_item, modes, 1) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_DECREF(bad_item);
    if (PyArray_ConvertClipmodeSequence(Py_None, NULL, 2) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_output_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyArrayObject *out = (PyArrayObject *)0x1;
    if (PyArray_OutputConverter(Py_None, &out) == 0 && out == NULL) score += 1;
    npy_intp dims[1] = {2};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (arr == NULL) return NULL;
    out = NULL;
    if (PyArray_OutputConverter(arr, &out) == 0 && out == (PyArrayObject *)arr) score += 10;
    PyObject *bad = PyLong_FromLong(7);
    if (bad == NULL) { Py_DECREF(arr); return NULL; }
    out = (PyArrayObject *)0x1;
    if (PyArray_OutputConverter(bad, &out) != 0 && out == NULL && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_DECREF(bad);
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_searchside_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    NPY_SEARCHSIDE side = (NPY_SEARCHSIDE)-1;
    PyObject *left = PyUnicode_FromString("left");
    if (left == NULL) return NULL;
    if (PyArray_SearchsideConverter(left, &side) == 0 && side == NPY_SEARCHLEFT) score += 1;
    Py_DECREF(left);
    side = (NPY_SEARCHSIDE)-1;
    PyObject *right = PyBytes_FromString("right");
    if (right == NULL) return NULL;
    if (PyArray_SearchsideConverter(right, &side) == 0 && side == NPY_SEARCHRIGHT) score += 10;
    Py_DECREF(right);
    PyObject *bad_case = PyUnicode_FromString("Left");
    if (bad_case == NULL) return NULL;
    if (PyArray_SearchsideConverter(bad_case, &side) != 0 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_DECREF(bad_case);
    PyObject *bad = PyUnicode_FromString("middle");
    if (bad == NULL) return NULL;
    if (PyArray_SearchsideConverter(bad, &side) != 0 && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_DECREF(bad);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_version_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    unsigned int abi = PyArray_GetNDArrayCVersion();
    unsigned int feature = PyArray_GetNDArrayCFeatureVersion();
    long score = 0;
    if (abi == (unsigned int)NPY_VERSION) score += 1;
    if (abi == (unsigned int)NPY_ABI_VERSION) score += 10;
    if (feature == (unsigned int)NPY_API_VERSION) score += 100;
    if (feature >= (unsigned int)NPY_FEATURE_VERSION) score += 1000;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_byteorder_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    char endian = 0;
    PyObject *big = PyUnicode_FromString("big");
    if (big == NULL) return NULL;
    if (PyArray_ByteorderConverter(big, &endian) == NPY_SUCCEED && endian == NPY_BIG) score += 1;
    Py_DECREF(big);
    PyObject *little = PyBytes_FromString("<");
    if (little == NULL) return NULL;
    endian = 0;
    if (PyArray_ByteorderConverter(little, &endian) == NPY_SUCCEED && endian == NPY_LITTLE) score += 10;
    Py_DECREF(little);
    PyObject *native = PyUnicode_FromString("=");
    if (native == NULL) return NULL;
    endian = 0;
    if (PyArray_ByteorderConverter(native, &endian) == NPY_SUCCEED && endian == NPY_NATIVE) score += 100;
    Py_DECREF(native);
    PyObject *ignore = PyUnicode_FromString("ignore");
    if (ignore == NULL) return NULL;
    endian = 0;
    if (PyArray_ByteorderConverter(ignore, &endian) == NPY_SUCCEED && endian == NPY_IGNORE) score += 1000;
    Py_DECREF(ignore);
    PyObject *pipe = PyBytes_FromString("|");
    if (pipe == NULL) return NULL;
    endian = 0;
    if (PyArray_ByteorderConverter(pipe, &endian) == NPY_SUCCEED && endian == NPY_IGNORE) score += 10000;
    Py_DECREF(pipe);
    PyObject *swap = PyUnicode_FromString("swap");
    if (swap == NULL) return NULL;
    endian = 0;
    if (PyArray_ByteorderConverter(swap, &endian) == NPY_SUCCEED && endian == NPY_SWAP) score += 100000;
    Py_DECREF(swap);
    PyObject *bad = PyUnicode_FromString("middle");
    if (bad == NULL) return NULL;
    if (PyArray_ByteorderConverter(bad, &endian) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_DECREF(bad);
    if (PyArray_ByteorderConverter(Py_None, NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_sortkind_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    NPY_SORTKIND kind = NPY_HEAPSORT;
    if (PyArray_SortkindConverter(Py_None, &kind) == NPY_SUCCEED && kind == NPY_HEAPSORT) score += 1;
    PyObject *quick = PyUnicode_FromString("quicksort");
    if (quick == NULL) return NULL;
    kind = (NPY_SORTKIND)-99;
    if (PyArray_SortkindConverter(quick, &kind) == NPY_SUCCEED && kind == NPY_QUICKSORT) score += 10;
    Py_DECREF(quick);
    PyObject *heap = PyBytes_FromString("heapsort");
    if (heap == NULL) return NULL;
    kind = (NPY_SORTKIND)-99;
    if (PyArray_SortkindConverter(heap, &kind) == NPY_SUCCEED && kind == NPY_HEAPSORT) score += 100;
    Py_DECREF(heap);
    PyObject *merge = PyUnicode_FromString("mergesort");
    if (merge == NULL) return NULL;
    kind = (NPY_SORTKIND)-99;
    if (PyArray_SortkindConverter(merge, &kind) == NPY_SUCCEED && kind == NPY_MERGESORT) score += 1000;
    Py_DECREF(merge);
    PyObject *stable = PyBytes_FromString("stable");
    if (stable == NULL) return NULL;
    kind = (NPY_SORTKIND)-99;
    if (PyArray_SortkindConverter(stable, &kind) == NPY_SUCCEED && kind == NPY_STABLESORT) score += 10000;
    Py_DECREF(stable);
    PyObject *bad = PyUnicode_FromString("radix");
    if (bad == NULL) return NULL;
    if (PyArray_SortkindConverter(bad, &kind) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_DECREF(bad);
    PyObject *bad_type = PyLong_FromLong(1);
    if (bad_type == NULL) return NULL;
    if (PyArray_SortkindConverter(bad_type, &kind) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_DECREF(bad_type);
    if (PyArray_SortkindConverter(Py_None, NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_selectkind_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    NPY_SELECTKIND kind = (NPY_SELECTKIND)-99;
    PyObject *intro = PyUnicode_FromString("introselect");
    if (intro == NULL) return NULL;
    if (PyArray_SelectkindConverter(intro, &kind) == NPY_SUCCEED && kind == NPY_INTROSELECT) score += 1;
    Py_DECREF(intro);
    PyObject *intro_bytes = PyBytes_FromString("introselect");
    if (intro_bytes == NULL) return NULL;
    kind = (NPY_SELECTKIND)-99;
    if (PyArray_SelectkindConverter(intro_bytes, &kind) == NPY_SUCCEED && kind == NPY_INTROSELECT) score += 10;
    Py_DECREF(intro_bytes);
    PyObject *bad_case = PyUnicode_FromString("Introselect");
    if (bad_case == NULL) return NULL;
    if (PyArray_SelectkindConverter(bad_case, &kind) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100; }
    Py_DECREF(bad_case);
    PyObject *bad_value = PyUnicode_FromString("intro");
    if (bad_value == NULL) return NULL;
    if (PyArray_SelectkindConverter(bad_value, &kind) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_DECREF(bad_value);
    PyObject *bad_type = PyLong_FromLong(1);
    if (bad_type == NULL) return NULL;
    if (PyArray_SelectkindConverter(bad_type, &kind) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    Py_DECREF(bad_type);
    if (PyArray_SelectkindConverter(Py_None, NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_order_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    NPY_ORDER order = NPY_KEEPORDER;
    if (PyArray_OrderConverter(Py_None, &order) == NPY_SUCCEED && order == NPY_KEEPORDER) score += 1;
    PyObject *c_order = PyUnicode_FromString("C");
    if (c_order == NULL) return NULL;
    order = (NPY_ORDER)-99;
    if (PyArray_OrderConverter(c_order, &order) == NPY_SUCCEED && order == NPY_CORDER) score += 10;
    Py_DECREF(c_order);
    PyObject *f_order = PyBytes_FromString("f");
    if (f_order == NULL) return NULL;
    order = (NPY_ORDER)-99;
    if (PyArray_OrderConverter(f_order, &order) == NPY_SUCCEED && order == NPY_FORTRANORDER) score += 100;
    Py_DECREF(f_order);
    PyObject *a_order = PyUnicode_FromString("A");
    if (a_order == NULL) return NULL;
    order = (NPY_ORDER)-99;
    if (PyArray_OrderConverter(a_order, &order) == NPY_SUCCEED && order == NPY_ANYORDER) score += 1000;
    Py_DECREF(a_order);
    PyObject *k_order = PyBytes_FromString("k");
    if (k_order == NULL) return NULL;
    order = (NPY_ORDER)-99;
    if (PyArray_OrderConverter(k_order, &order) == NPY_SUCCEED && order == NPY_KEEPORDER) score += 10000;
    Py_DECREF(k_order);
    PyObject *bad_len = PyUnicode_FromString("CF");
    if (bad_len == NULL) return NULL;
    if (PyArray_OrderConverter(bad_len, &order) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    Py_DECREF(bad_len);
    PyObject *bad_value = PyUnicode_FromString("Z");
    if (bad_value == NULL) return NULL;
    if (PyArray_OrderConverter(bad_value, &order) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000000; }
    Py_DECREF(bad_value);
    PyObject *bad_type = PyLong_FromLong(1);
    if (bad_type == NULL) return NULL;
    if (PyArray_OrderConverter(bad_type, &order) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000000; }
    Py_DECREF(bad_type);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_bool_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_bool value = 0;
    if (PyArray_BoolConverter(Py_True, &value) == NPY_SUCCEED && value == 1) score += 1;
    value = 1;
    if (PyArray_BoolConverter(Py_False, &value) == NPY_SUCCEED && value == 0) score += 10;
    value = 1;
    if (PyArray_BoolConverter(Py_None, &value) == NPY_SUCCEED && value == 0) score += 100;
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) return NULL;
    value = 1;
    if (PyArray_BoolConverter(empty, &value) == NPY_SUCCEED && value == 0) score += 1000;
    Py_DECREF(empty);
    PyObject *full = PyTuple_New(1);
    if (full == NULL) return NULL;
    if (PyTuple_SetItem(full, 0, PyLong_FromLong(7)) != 0) { Py_DECREF(full); return NULL; }
    value = 0;
    if (PyArray_BoolConverter(full, &value) == NPY_SUCCEED && value == 1) score += 10000;
    Py_DECREF(full);
    if (PyArray_BoolConverter(Py_True, NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_optional_bool_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    int value = 7;
    if (PyArray_OptionalBoolConverter(Py_None, &value) == NPY_SUCCEED && value == 7) score += 1;
    value = 0;
    if (PyArray_OptionalBoolConverter(Py_True, &value) == NPY_SUCCEED && value == 1) score += 10;
    value = 1;
    if (PyArray_OptionalBoolConverter(Py_False, &value) == NPY_SUCCEED && value == 0) score += 100;
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) return NULL;
    value = 1;
    if (PyArray_OptionalBoolConverter(empty, &value) == NPY_SUCCEED && value == 0) score += 1000;
    Py_DECREF(empty);
    PyObject *full = PyTuple_New(1);
    if (full == NULL) return NULL;
    PyObject *item = PyLong_FromLong(1);
    if (item == NULL) { Py_DECREF(full); return NULL; }
    PyTuple_SET_ITEM(full, 0, item);
    value = 0;
    if (PyArray_OptionalBoolConverter(full, &value) == NPY_SUCCEED && value == 1) score += 10000;
    Py_DECREF(full);
    if (PyArray_OptionalBoolConverter(Py_True, NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 100000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_axis_converter_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    int axis = 99;
    if (PyArray_AxisConverter(Py_None, &axis) == NPY_SUCCEED && axis == NPY_RAVEL_AXIS) score += 1;
    PyObject *two = PyLong_FromLong(2);
    if (two == NULL) return NULL;
    axis = 99;
    if (PyArray_AxisConverter(two, &axis) == NPY_SUCCEED && axis == 2) score += 10;
    Py_DECREF(two);
    PyObject *neg = PyLong_FromLong(-1);
    if (neg == NULL) return NULL;
    axis = 99;
    if (PyArray_AxisConverter(neg, &axis) == NPY_SUCCEED && axis == -1) score += 100;
    Py_DECREF(neg);
    PyObject *bad = PyUnicode_FromString("axis");
    if (bad == NULL) return NULL;
    if (PyArray_AxisConverter(bad, &axis) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 1000; }
    Py_DECREF(bad);
    if (PyArray_AxisConverter(Py_None, NULL) != NPY_SUCCEED && PyErr_Occurred() != NULL) { PyErr_Clear(); score += 10000; }
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_element_strides_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[2] = {2, 3};
    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_LONG);
    if (arr == NULL) return NULL;
    long score = 0;
    if (PyArray_ElementStrides(arr)) score += 1;
    npy_intp *strides = PyArray_STRIDES((PyArrayObject *)arr);
    strides[0] = (npy_intp)PyArray_ITEMSIZE((PyArrayObject *)arr) + 1;
    if (!PyArray_ElementStrides(arr)) score += 10;
    if (!PyArray_ElementStrides(Py_None)) score += 100;
    Py_DECREF(arr);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_valid_type_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    if (PyArray_ValidType(NPY_INT)) score += 1;
    if (PyArray_ValidType(NPY_DOUBLE)) score += 10;
    if (!PyArray_ValidType(9999) && PyErr_Occurred() == NULL) score += 100;
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_item_refcount_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyArray_Descr *obj_descr = PyArray_DescrFromType(NPY_OBJECT);
    PyArray_Descr *int_descr = PyArray_DescrFromType(NPY_INT);
    PyObject *value = PyUnicode_FromString("held");
    if (obj_descr == NULL || int_descr == NULL || value == NULL) return NULL;
    Py_ssize_t before = Py_REFCNT(value);
    PyObject *slot = value;
    long score = 0;
    PyArray_Item_INCREF((char *)&slot, obj_descr);
    if (Py_REFCNT(value) == before + 1) score += 1;
    Py_DECREF(value);
    const char *held = PyUnicode_AsUTF8(slot);
    if (held != NULL && strcmp(held, "held") == 0) score += 10;
    PyArray_Item_XDECREF((char *)&slot, obj_descr);
    slot = NULL;
    PyArray_Item_INCREF((char *)&slot, obj_descr);
    PyArray_Item_XDECREF((char *)&slot, obj_descr);
    if (PyErr_Occurred() == NULL) score += 100;
    int raw = 7;
    PyArray_Item_INCREF((char *)&raw, int_descr);
    PyArray_Item_XDECREF((char *)&raw, int_descr);
    if (raw == 7 && PyErr_Occurred() == NULL) score += 1000;
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_array_refcount_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    npy_intp dims[1] = {2};
    PyObject *arr_obj = PyArray_SimpleNew(1, dims, NPY_OBJECT);
    PyObject *int_arr = PyArray_SimpleNew(1, dims, NPY_INT);
    PyObject *left = PyUnicode_FromString("array-inc-left");
    PyObject *right = PyUnicode_FromString("array-inc-right");
    if (arr_obj == NULL || int_arr == NULL || left == NULL || right == NULL) return NULL;
    PyObject **items = (PyObject **)PyArray_DATA((PyArrayObject *)arr_obj);
    if (items == NULL) return NULL;
    items[0] = left;
    items[1] = right;
    Py_ssize_t left_before = Py_REFCNT(left);
    Py_ssize_t right_before = Py_REFCNT(right);
    long score = 0;
    if (PyArray_INCREF((PyArrayObject *)arr_obj) == 0 && Py_REFCNT(left) == left_before + 1 && Py_REFCNT(right) == right_before + 1) score += 1;
    Py_DECREF(left);
    Py_DECREF(right);
    const char *left_text = PyUnicode_AsUTF8(items[0]);
    const char *right_text = PyUnicode_AsUTF8(items[1]);
    if (left_text != NULL && right_text != NULL && strcmp(left_text, "array-inc-left") == 0 && strcmp(right_text, "array-inc-right") == 0) score += 10;
    if (PyArray_XDECREF((PyArrayObject *)arr_obj) == 0 && PyErr_Occurred() == NULL) score += 100;
    items[0] = NULL;
    items[1] = NULL;
    if (PyArray_INCREF((PyArrayObject *)int_arr) == 0 && PyArray_XDECREF((PyArrayObject *)int_arr) == 0 && PyErr_Occurred() == NULL) score += 1000;
    Py_DECREF(int_arr);
    Py_DECREF(arr_obj);
    if (PyErr_Occurred() != NULL) return NULL;
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_allocator_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    char *buf = (char *)PyArray_malloc(3);
    if (buf == NULL) return PyErr_NoMemory();
    buf[0] = 'a'; buf[1] = 'b'; buf[2] = '\0';
    char *grown = (char *)PyArray_realloc(buf, 5);
    if (grown == NULL) {
        PyArray_free(buf);
        return PyErr_NoMemory();
    }
    buf = grown;
    buf[2] = 'c'; buf[3] = 'd'; buf[4] = '\0';
    if (buf[0] == 'a' && buf[1] == 'b' && buf[3] == 'd') score += 1;
    PyArray_free(buf);
    npy_intp *dims = PyDimMem_NEW(2);
    if (dims == NULL) return PyErr_NoMemory();
    dims[0] = 3; dims[1] = 4;
    npy_intp *grown_dims = PyDimMem_RENEW(dims, 3);
    if (grown_dims == NULL) {
        PyDimMem_FREE(dims);
        return PyErr_NoMemory();
    }
    dims = grown_dims;
    dims[2] = 5;
    if (dims[0] == 3 && dims[1] == 4 && dims[2] == 5) score += 10;
    PyDimMem_FREE(dims);
    char *from_null = (char *)PyArray_realloc(NULL, 2);
    if (from_null == NULL) return PyErr_NoMemory();
    from_null[0] = 'z'; from_null[1] = '\0';
    if (from_null[0] == 'z') score += 100;
    PyArray_free(from_null);
    PyArray_free(NULL);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_datamem_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    char *buf = (char *)PyDataMem_NEW(3);
    if (buf == NULL) return PyErr_NoMemory();
    buf[0] = 'x'; buf[1] = 'y'; buf[2] = '\0';
    if (buf[0] == 'x' && buf[1] == 'y') score += 1;
    char *grown = (char *)PyDataMem_RENEW(buf, 5);
    if (grown == NULL) {
        PyDataMem_FREE(buf);
        return PyErr_NoMemory();
    }
    buf = grown;
    if (buf[0] == 'x' && buf[1] == 'y') score += 10;
    buf[3] = 'z';
    if (buf[3] == 'z') score += 100;
    PyDataMem_FREE(buf);
    PyDataMem_FREE(NULL);
    unsigned char *zeroed = (unsigned char *)PyDataMem_NEW_ZEROED(4, sizeof(unsigned char));
    if (zeroed == NULL) return PyErr_NoMemory();
    if (zeroed[0] == 0 && zeroed[1] == 0 && zeroed[2] == 0 && zeroed[3] == 0) score += 1000;
    PyDataMem_FREE(zeroed);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_datamem_user_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *handler = PyDataMem_GetHandler();
    if (handler != NULL) score += 1;
    char *buf = (char *)PyDataMem_UserNEW(3, handler);
    if (buf == NULL) return PyErr_NoMemory();
    buf[0] = 'u'; buf[1] = 'v'; buf[2] = '\0';
    if (buf[0] == 'u' && buf[1] == 'v') score += 10;
    char *grown = (char *)PyDataMem_UserRENEW(buf, 5, handler);
    if (grown == NULL) {
        PyDataMem_UserFREE(buf, 3, handler);
        return PyErr_NoMemory();
    }
    buf = grown;
    if (buf[0] == 'u' && buf[1] == 'v') score += 100;
    buf[3] = 'w';
    if (buf[3] == 'w') score += 1000;
    PyDataMem_UserFREE(buf, 5, handler);
    PyDataMem_UserFREE(NULL, 0, handler);
    unsigned char *zeroed = (unsigned char *)PyDataMem_UserNEW_ZEROED(4, sizeof(unsigned char), handler);
    if (zeroed == NULL) return PyErr_NoMemory();
    if (zeroed[0] == 0 && zeroed[1] == 0 && zeroed[2] == 0 && zeroed[3] == 0) score += 10000;
    PyDataMem_UserFREE(zeroed, 4, handler);
    Py_XDECREF(handler);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_array_free_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims2[2] = {2, 2};
    PyObject *arr2 = PyArray_SimpleNew(2, dims2, NPY_LONG);
    char **ptr2 = (char **)PyArray_malloc(2 * sizeof(char *));
    if (arr2 == NULL || ptr2 == NULL) { Py_XDECREF(arr2); PyArray_free(ptr2); return PyErr_NoMemory(); }
    if (PyArray_Free(arr2, ptr2) == 0 && PyErr_Occurred() == NULL) score += 1;
    npy_intp dims1[1] = {1};
    PyObject *arr1 = PyArray_SimpleNew(1, dims1, NPY_LONG);
    if (arr1 == NULL) return NULL;
    if (PyArray_Free(arr1, NULL) == 0 && PyErr_Occurred() == NULL) score += 10;
    PyObject *arr0 = PyArray_SimpleNew(0, NULL, NPY_LONG);
    if (arr0 == NULL) return NULL;
    if (PyArray_Free(arr0, NULL) != 0 && PyErr_Occurred() == NULL) score += 100;
    Py_DECREF(arr0);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_as_carray_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    PyObject *seq1 = PyTuple_New(3);
    if (seq1 == NULL) return NULL;
    PyTuple_SetItem(seq1, 0, PyLong_FromLong(4));
    PyTuple_SetItem(seq1, 1, PyLong_FromLong(5));
    PyTuple_SetItem(seq1, 2, PyLong_FromLong(6));
    void *ptr1 = NULL;
    npy_intp out_dims1[1] = {-1};
    PyArray_Descr *descr1 = PyArray_DescrFromType(NPY_LONG);
    if (descr1 == NULL) { Py_DECREF(seq1); return NULL; }
    if (PyArray_AsCArray(&seq1, &ptr1, out_dims1, 1, descr1) == 0) {
        long *data1 = (long *)ptr1;
        if (out_dims1[0] == 3 && data1 != NULL && data1[0] == 4 && data1[2] == 6) score += 1;
        PyArray_Free(seq1, ptr1);
    } else {
        Py_DECREF(seq1);
        return NULL;
    }
    npy_intp dims2[2] = {2, 2};
    PyObject *arr2 = PyArray_SimpleNew(2, dims2, NPY_LONG);
    if (arr2 == NULL) return NULL;
    long *data2 = (long *)PyArray_DATA((PyArrayObject *)arr2);
    data2[0] = 10; data2[1] = 11; data2[2] = 12; data2[3] = 13;
    PyObject *op2 = arr2;
    void *ptr2 = NULL;
    npy_intp out_dims2[2] = {-1, -1};
    PyArray_Descr *descr2 = PyArray_DescrFromType(NPY_LONG);
    if (descr2 == NULL) { Py_DECREF(arr2); return NULL; }
    if (PyArray_AsCArray(&op2, &ptr2, out_dims2, 2, descr2) == 0) {
        char **rows = (char **)ptr2;
        if (out_dims2[0] == 2 && out_dims2[1] == 2 && rows != NULL
            && ((long *)rows[0])[0] == 10 && ((long *)rows[1])[1] == 13) score += 10;
        PyArray_Free(op2, ptr2);
        Py_DECREF(arr2);
    } else {
        Py_DECREF(arr2);
        return NULL;
    }
    PyObject *bad = PyTuple_New(0);
    if (bad == NULL) return NULL;
    void *bad_ptr = NULL;
    npy_intp bad_dims[1] = {-1};
    PyArray_Descr *bad_descr = PyArray_DescrFromType(NPY_LONG);
    if (bad_descr == NULL) { Py_DECREF(bad); return NULL; }
    if (PyArray_AsCArray(&bad, &bad_ptr, bad_dims, 0, bad_descr) != 0 && PyErr_Occurred() != NULL) {
        PyErr_Clear();
        score += 100;
    }
    Py_DECREF(bad);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_fail_writeable_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {2};
    PyObject *obj = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (obj == NULL) return NULL;
    PyArrayObject *arr = (PyArrayObject *)obj;
    if (PyArray_FailUnlessWriteable(arr, "write target") == 0 && PyErr_Occurred() == NULL) score += 1;
    PyArray_CLEARFLAGS(arr, NPY_ARRAY_WRITEABLE);
    if (PyArray_FailUnlessWriteable(arr, "write target") != 0 && PyErr_Occurred() != NULL) {
        PyErr_Clear();
        score += 10;
    }
    PyArray_ENABLEFLAGS(arr, NPY_ARRAY_WRITEABLE);
    if (PyArray_FailUnlessWriteable(arr, NULL) == 0 && PyErr_Occurred() == NULL) score += 100;
    Py_DECREF(obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_writeback_if_copy_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *base_obj = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (base_obj == NULL) return NULL;
    long *base_data = (long *)PyArray_DATA((PyArrayObject *)base_obj);
    base_data[0] = 1;
    base_data[1] = 2;
    base_data[2] = 3;
    PyObject *copy_obj = PyArray_NewCopy((PyArrayObject *)base_obj, NPY_CORDER);
    if (copy_obj == NULL) { Py_DECREF(base_obj); return NULL; }
    Py_INCREF(base_obj);
    if (PyArray_SetWritebackIfCopyBase((PyArrayObject *)copy_obj, (PyArrayObject *)base_obj) == 0) score += 1;
    if (PyArray_BASE((PyArrayObject *)copy_obj) == base_obj) score += 10;
    if (PyArray_CHKFLAGS((PyArrayObject *)copy_obj, NPY_ARRAY_WRITEBACKIFCOPY)) score += 100;
    if (!PyArray_ISWRITEABLE((PyArrayObject *)base_obj)) score += 1000;
    ((long *)PyArray_DATA((PyArrayObject *)copy_obj))[1] = 42;
    if (PyArray_ResolveWritebackIfCopy((PyArrayObject *)copy_obj) == 1 && base_data[1] == 42 &&
        PyArray_ISWRITEABLE((PyArrayObject *)base_obj) && PyArray_BASE((PyArrayObject *)copy_obj) == NULL &&
        !PyArray_CHKFLAGS((PyArrayObject *)copy_obj, NPY_ARRAY_WRITEBACKIFCOPY)) score += 10000;
    if (PyArray_ResolveWritebackIfCopy((PyArrayObject *)copy_obj) == 0) score += 100000;
    if (PyArray_SetWritebackIfCopyBase((PyArrayObject *)copy_obj, NULL) != 0 && PyErr_Occurred() != NULL) {
        PyErr_Clear();
        score += 1000000;
    }
    Py_INCREF(base_obj);
    if (PyArray_SetBaseObject((PyArrayObject *)copy_obj, base_obj) == 0) {
        Py_INCREF(base_obj);
        if (PyArray_SetWritebackIfCopyBase((PyArrayObject *)copy_obj, (PyArrayObject *)base_obj) != 0 && PyErr_Occurred() != NULL) {
            PyErr_Clear();
            score += 10000000;
        }
    }
    if (PyArray_SetUpdateIfCopyBase((PyArrayObject *)copy_obj, (PyArrayObject *)base_obj) != 0 && PyErr_Occurred() != NULL) {
        PyErr_Clear();
        score += 100000000;
    }
    Py_DECREF(copy_obj);
    Py_DECREF(base_obj);
    return PyLong_FromLong(score);
}

static PyObject *pccnpcons_discard_writeback_if_copy_score(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    long score = 0;
    npy_intp dims[1] = {3};
    PyObject *base_obj = PyArray_SimpleNew(1, dims, NPY_LONG);
    if (base_obj == NULL) return NULL;
    long *base_data = (long *)PyArray_DATA((PyArrayObject *)base_obj);
    base_data[0] = 1;
    base_data[1] = 2;
    base_data[2] = 3;
    PyObject *copy_obj = PyArray_NewCopy((PyArrayObject *)base_obj, NPY_CORDER);
    if (copy_obj == NULL) { Py_DECREF(base_obj); return NULL; }
    Py_INCREF(base_obj);
    if (PyArray_SetWritebackIfCopyBase((PyArrayObject *)copy_obj, (PyArrayObject *)base_obj) == 0) score += 1;
    ((long *)PyArray_DATA((PyArrayObject *)copy_obj))[1] = 99;
    PyArray_DiscardWritebackIfCopy((PyArrayObject *)copy_obj);
    if (base_data[1] == 2) score += 10;
    if (PyArray_ISWRITEABLE((PyArrayObject *)base_obj)) score += 100;
    if (PyArray_BASE((PyArrayObject *)copy_obj) == NULL) score += 1000;
    if (!PyArray_CHKFLAGS((PyArrayObject *)copy_obj, NPY_ARRAY_WRITEBACKIFCOPY)) score += 10000;
    PyArray_DiscardWritebackIfCopy((PyArrayObject *)copy_obj);
    if (base_data[1] == 2 && PyArray_ISWRITEABLE((PyArrayObject *)base_obj) &&
        PyArray_BASE((PyArrayObject *)copy_obj) == NULL &&
        !PyArray_CHKFLAGS((PyArrayObject *)copy_obj, NPY_ARRAY_WRITEBACKIFCOPY)) score += 100000;
    PyArray_DiscardWritebackIfCopy((PyArrayObject *)base_obj);
    if (PyErr_Occurred() == NULL && base_data[1] == 2) score += 1000000;
    PyArray_DiscardWritebackIfCopy(NULL);
    if (PyErr_Occurred() == NULL) score += 10000000;
    Py_DECREF(copy_obj);
    Py_DECREF(base_obj);
    return PyLong_FromLong(score);
}

static PyMethodDef ConsumerMethods[] = {
    {"table_shape_score", pccnpcons_table_shape_score, METH_VARARGS, "import pcc NumPy C API stub capsules"},
    {"type_object_score", pccnpcons_type_object_score, METH_VARARGS, "minimal pcc NumPy C API type-object tokens"},
    {"type_object_from_type_score", pccnpcons_type_object_from_type_score, METH_VARARGS, "minimal pcc NumPy scalar type-object lookup"},
    {"unsupported_score", pccnpcons_unsupported_score, METH_VARARGS, "unsupported stubs fail visibly"},
    {"ufunc_registration_score", pccnpcons_ufunc_registration_score, METH_VARARGS, "minimal pcc NumPy ufunc registration"},
    {"inferred_dtype_score", pccnpcons_inferred_dtype_score, METH_VARARGS, "minimal pcc NumPy descriptor-less FromAny inference"},
    {"descr_score", pccnpcons_descr_score, METH_VARARGS, "minimal pcc NumPy descriptor metadata"},
    {"fromany_score", pccnpcons_fromany_score, METH_VARARGS, "minimal pcc NumPy FromAny coercion"},
    {"array_metadata_score", pccnpcons_array_metadata_score, METH_VARARGS, "minimal pcc NumPy array metadata"},
    {"pointer_metadata_score", pccnpcons_pointer_metadata_score, METH_VARARGS, "minimal pcc NumPy pointer metadata"},
    {"dtype_field_score", pccnpcons_dtype_field_score, METH_VARARGS, "minimal pcc NumPy dtype field macros"},
    {"dtype_classification_score", pccnpcons_dtype_classification_score, METH_VARARGS, "minimal pcc NumPy dtype classification macros"},
    {"scalar_kind_score", pccnpcons_scalar_kind_score, METH_VARARGS, "minimal pcc NumPy scalar kind helper"},
    {"check_any_scalar_exact_score", pccnpcons_check_any_scalar_exact_score, METH_VARARGS, "minimal pcc NumPy exact scalar checker"},
    {"can_coerce_scalar_score", pccnpcons_can_coerce_scalar_score, METH_VARARGS, "minimal pcc NumPy scalar coercion helper"},
    {"can_cast_scalar_score", pccnpcons_can_cast_scalar_score, METH_VARARGS, "minimal pcc NumPy scalar type cast query helper"},
    {"convert_common_type_score", pccnpcons_convert_common_type_score, METH_VARARGS, "minimal pcc NumPy common dtype conversion helper"},
    {"state_byteorder_score", pccnpcons_state_byteorder_score, METH_VARARGS, "minimal pcc NumPy state and byteorder macros"},
    {"from_macro_score", pccnpcons_from_macro_score, METH_VARARGS, "minimal pcc NumPy FromAny convenience macros"},
    {"fill_byteorder_score", pccnpcons_fill_byteorder_score, METH_VARARGS, "minimal pcc NumPy fill and byteorder macros"},
    {"shape_compare_score", pccnpcons_shape_compare_score, METH_VARARGS, "minimal pcc NumPy shape comparison helpers"},
    {"empty_zeros_score", pccnpcons_empty_zeros_score, METH_VARARGS, "minimal pcc NumPy C-order allocation helpers"},
    {"equiv_types_score", pccnpcons_equiv_types_score, METH_VARARGS, "minimal pcc NumPy dtype equivalence helpers"},
    {"new_from_descr_score", pccnpcons_new_from_descr_score, METH_VARARGS, "minimal pcc NumPy descriptor allocation helpers"},
    {"new_score", pccnpcons_new_score, METH_VARARGS, "minimal pcc NumPy generic array constructor helper"},
    {"array_check_score", pccnpcons_array_check_score, METH_VARARGS, "minimal pcc NumPy array check helpers"},
    {"size_itemsize_score", pccnpcons_size_itemsize_score, METH_VARARGS, "minimal pcc NumPy size and itemsize helpers"},
    {"array_size_score", pccnpcons_array_size_score, METH_VARARGS, "minimal pcc NumPy object-safe array size helper"},
    {"accessor_score", pccnpcons_accessor_score, METH_VARARGS, "minimal pcc NumPy accessor helpers"},
    {"core_provider_score", pccnpcons_core_provider_score, METH_VARARGS, "minimal pcc NumPy core provider helpers"},
    {"base_object_score", pccnpcons_base_object_score, METH_VARARGS, "minimal pcc NumPy base-object ownership helpers"},
    {"return_score", pccnpcons_return_score, METH_VARARGS, "minimal pcc NumPy array return helper"},
    {"flags_mutation_score", pccnpcons_flags_mutation_score, METH_VARARGS, "minimal pcc NumPy flag mutation helpers"},
    {"copy_into_score", pccnpcons_copy_into_score, METH_VARARGS, "minimal pcc NumPy copy helpers"},
    {"to_scalar_score", pccnpcons_to_scalar_score, METH_VARARGS, "minimal pcc NumPy scalar extraction helper"},
    {"copy_score", pccnpcons_copy_score, METH_VARARGS, "minimal pcc NumPy array copy helper"},
    {"new_copy_score", pccnpcons_new_copy_score, METH_VARARGS, "minimal pcc NumPy ordered array copy helper"},
    {"copy_object_score", pccnpcons_copy_object_score, METH_VARARGS, "minimal pcc NumPy object-to-array assignment helper"},
    {"resize_score", pccnpcons_resize_score, METH_VARARGS, "minimal pcc NumPy resize helper"},
    {"new_like_score", pccnpcons_new_like_score, METH_VARARGS, "minimal pcc NumPy new-like-array helper"},
    {"view_score", pccnpcons_view_score, METH_VARARGS, "minimal pcc NumPy same-dtype view helper"},
    {"squeeze_score", pccnpcons_squeeze_score, METH_VARARGS, "minimal pcc NumPy squeeze view helper"},
    {"transpose_score", pccnpcons_transpose_score, METH_VARARGS, "minimal pcc NumPy transpose view helper"},
    {"swap_axes_score", pccnpcons_swap_axes_score, METH_VARARGS, "minimal pcc NumPy swap-axes view helper"},
    {"ravel_score", pccnpcons_ravel_score, METH_VARARGS, "minimal pcc NumPy ravel view helper"},
    {"flatten_score", pccnpcons_flatten_score, METH_VARARGS, "minimal pcc NumPy flatten copy helper"},
    {"take_from_score", pccnpcons_take_from_score, METH_VARARGS, "minimal pcc NumPy take-from copy helper"},
    {"put_to_score", pccnpcons_put_to_score, METH_VARARGS, "minimal pcc NumPy indexed put helper"},
    {"put_mask_score", pccnpcons_put_mask_score, METH_VARARGS, "minimal pcc NumPy masked put helper"},
    {"repeat_score", pccnpcons_repeat_score, METH_VARARGS, "minimal pcc NumPy repeat helper"},
    {"choose_score", pccnpcons_choose_score, METH_VARARGS, "minimal pcc NumPy choose helper"},
    {"concatenate_score", pccnpcons_concatenate_score, METH_VARARGS, "minimal pcc NumPy concatenate helper"},
    {"arange_score", pccnpcons_arange_score, METH_VARARGS, "minimal pcc NumPy arange helper"},
    {"arange_obj_score", pccnpcons_arange_obj_score, METH_VARARGS, "minimal pcc NumPy object-scalar arange helper"},
    {"inner_product_score", pccnpcons_inner_product_score, METH_VARARGS, "minimal pcc NumPy inner-product helper"},
    {"matrix_product_score", pccnpcons_matrix_product_score, METH_VARARGS, "minimal pcc NumPy matrix-product helper"},
    {"matrix_product2_score", pccnpcons_matrix_product2_score, METH_VARARGS, "minimal pcc NumPy matrix-product2 helper"},
    {"einstein_sum_score", pccnpcons_einstein_sum_score, METH_VARARGS, "minimal pcc NumPy Einstein-sum helper"},
    {"correlate_score", pccnpcons_correlate_score, METH_VARARGS, "minimal pcc NumPy correlate helper"},
    {"correlate2_score", pccnpcons_correlate2_score, METH_VARARGS, "minimal pcc NumPy correlate2 helper"},
    {"lexsort_score", pccnpcons_lexsort_score, METH_VARARGS, "minimal pcc NumPy lexsort helper"},
    {"sort_score", pccnpcons_sort_score, METH_VARARGS, "minimal pcc NumPy sort helper"},
    {"argsort_score", pccnpcons_argsort_score, METH_VARARGS, "minimal pcc NumPy argsort helper"},
    {"partition_score", pccnpcons_partition_score, METH_VARARGS, "minimal pcc NumPy partition helper"},
    {"searchsorted_score", pccnpcons_searchsorted_score, METH_VARARGS, "minimal pcc NumPy searchsorted helper"},
    {"nonzero_score", pccnpcons_nonzero_score, METH_VARARGS, "minimal pcc NumPy nonzero helper"},
    {"count_nonzero_score", pccnpcons_count_nonzero_score, METH_VARARGS, "minimal pcc NumPy count-nonzero helper"},
    {"min_scalar_type_score", pccnpcons_min_scalar_type_score, METH_VARARGS, "minimal pcc NumPy min-scalar-type helper"},
    {"sorted_stride_perm_score", pccnpcons_sorted_stride_perm_score, METH_VARARGS, "minimal pcc NumPy sorted-stride-permutation helper"},
    {"remove_axes_in_place_score", pccnpcons_remove_axes_in_place_score, METH_VARARGS, "minimal pcc NumPy in-place axis-removal helper"},
    {"debug_print_score", pccnpcons_debug_print_score, METH_VARARGS, "minimal pcc NumPy debug printer helper"},
    {"where_score", pccnpcons_where_score, METH_VARARGS, "minimal pcc NumPy where helper"},
    {"compress_score", pccnpcons_compress_score, METH_VARARGS, "minimal pcc NumPy compress helper"},
    {"diagonal_score", pccnpcons_diagonal_score, METH_VARARGS, "minimal pcc NumPy diagonal helper"},
    {"trace_score", pccnpcons_trace_score, METH_VARARGS, "minimal pcc NumPy trace helper"},
    {"clip_score", pccnpcons_clip_score, METH_VARARGS, "minimal pcc NumPy clip helper"},
    {"conjugate_score", pccnpcons_conjugate_score, METH_VARARGS, "minimal pcc NumPy conjugate helper"},
    {"std_score", pccnpcons_std_score, METH_VARARGS, "minimal pcc NumPy standard-deviation helper"},
    {"round_score", pccnpcons_round_score, METH_VARARGS, "minimal pcc NumPy round helper"},
    {"sum_score", pccnpcons_sum_score, METH_VARARGS, "minimal pcc NumPy sum helper"},
    {"cumsum_score", pccnpcons_cumsum_score, METH_VARARGS, "minimal pcc NumPy cumulative-sum helper"},
    {"prod_score", pccnpcons_prod_score, METH_VARARGS, "minimal pcc NumPy product helper"},
    {"cumprod_score", pccnpcons_cumprod_score, METH_VARARGS, "minimal pcc NumPy cumulative-product helper"},
    {"max_score", pccnpcons_max_score, METH_VARARGS, "minimal pcc NumPy max helper"},
    {"min_score", pccnpcons_min_score, METH_VARARGS, "minimal pcc NumPy min helper"},
    {"ptp_score", pccnpcons_ptp_score, METH_VARARGS, "minimal pcc NumPy peak-to-peak helper"},
    {"mean_score", pccnpcons_mean_score, METH_VARARGS, "minimal pcc NumPy mean helper"},
    {"any_score", pccnpcons_any_score, METH_VARARGS, "minimal pcc NumPy any helper"},
    {"all_score", pccnpcons_all_score, METH_VARARGS, "minimal pcc NumPy all helper"},
    {"argmax_score", pccnpcons_argmax_score, METH_VARARGS, "minimal pcc NumPy argmax helper"},
    {"argmin_score", pccnpcons_argmin_score, METH_VARARGS, "minimal pcc NumPy argmin helper"},
    {"reshape_score", pccnpcons_reshape_score, METH_VARARGS, "minimal pcc NumPy reshape helper"},
    {"newshape_score", pccnpcons_newshape_score, METH_VARARGS, "minimal pcc NumPy newshape helper"},
    {"ensure_array_score", pccnpcons_ensure_array_score, METH_VARARGS, "minimal pcc NumPy ensure-array helpers"},
    {"descr_check_score", pccnpcons_descr_check_score, METH_VARARGS, "minimal pcc NumPy descriptor type check helper"},
    {"descr_new_from_type_score", pccnpcons_descr_new_from_type_score, METH_VARARGS, "minimal pcc NumPy descriptor copy helper"},
    {"descr_new_score", pccnpcons_descr_new_score, METH_VARARGS, "minimal pcc NumPy descriptor copy-from-descriptor helper"},
    {"descr_new_byteorder_score", pccnpcons_descr_new_byteorder_score, METH_VARARGS, "minimal pcc NumPy descriptor byteorder-copy helper"},
    {"can_cast_safely_score", pccnpcons_can_cast_safely_score, METH_VARARGS, "minimal pcc NumPy safe-cast query helper"},
    {"can_cast_to_score", pccnpcons_can_cast_to_score, METH_VARARGS, "minimal pcc NumPy descriptor safe-cast query helper"},
    {"can_cast_type_array_score", pccnpcons_can_cast_type_array_score, METH_VARARGS, "minimal pcc NumPy casting-policy query helpers"},
    {"casting_converter_score", pccnpcons_casting_converter_score, METH_VARARGS, "minimal pcc NumPy casting string converter helper"},
    {"zero_one_score", pccnpcons_zero_one_score, METH_VARARGS, "minimal pcc NumPy zero/one scalar-buffer helpers"},
    {"object_type_score", pccnpcons_object_type_score, METH_VARARGS, "minimal pcc NumPy object-to-dtype inference helper"},
    {"descr_from_object_score", pccnpcons_descr_from_object_score, METH_VARARGS, "minimal pcc NumPy object-to-descriptor inference helper"},
    {"descr_converter_score", pccnpcons_descr_converter_score, METH_VARARGS, "minimal pcc NumPy object-to-descriptor converter helpers"},
    {"descr_align_converter_score", pccnpcons_descr_align_converter_score, METH_VARARGS, "minimal pcc NumPy aligned descriptor converter helpers"},
    {"descr_from_scalar_score", pccnpcons_descr_from_scalar_score, METH_VARARGS, "minimal pcc NumPy scalar-to-descriptor helper"},
    {"descr_from_type_object_score", pccnpcons_descr_from_type_object_score, METH_VARARGS, "minimal pcc NumPy type-object-to-descriptor helper"},
    {"scalar_score", pccnpcons_scalar_score, METH_VARARGS, "minimal pcc NumPy raw item-to-scalar helper"},
    {"scalar_as_ctype_score", pccnpcons_scalar_as_ctype_score, METH_VARARGS, "minimal pcc NumPy scalar-to-C-buffer helper"},
    {"from_scalar_score", pccnpcons_from_scalar_score, METH_VARARGS, "minimal pcc NumPy scalar-to-0d-array helper"},
    {"cast_scalar_to_ctype_score", pccnpcons_cast_scalar_to_ctype_score, METH_VARARGS, "minimal pcc NumPy scalar-to-typed-C-buffer helper"},
    {"cast_scalar_direct_score", pccnpcons_cast_scalar_direct_score, METH_VARARGS, "minimal pcc NumPy direct scalar cast helper"},
    {"pack_score", pccnpcons_pack_score, METH_VARARGS, "minimal pcc NumPy scalar/0d-array-to-C-buffer pack helper"},
    {"from_array_score", pccnpcons_from_array_score, METH_VARARGS, "minimal pcc NumPy FromArray helper"},
    {"cast_to_type_score", pccnpcons_cast_to_type_score, METH_VARARGS, "minimal pcc NumPy array cast-to-type helper"},
    {"fill_with_scalar_score", pccnpcons_fill_with_scalar_score, METH_VARARGS, "minimal pcc NumPy fill-with-scalar helper"},
    {"to_list_score", pccnpcons_to_list_score, METH_VARARGS, "minimal pcc NumPy array to-list helper"},
    {"to_string_score", pccnpcons_to_string_score, METH_VARARGS, "minimal pcc NumPy array to-string helper"},
    {"byteswap_score", pccnpcons_byteswap_score, METH_VARARGS, "minimal pcc NumPy byteswap helper"},
    {"from_string_score", pccnpcons_from_string_score, METH_VARARGS, "minimal pcc NumPy from-string helper"},
    {"from_buffer_score", pccnpcons_from_buffer_score, METH_VARARGS, "minimal pcc NumPy from-buffer helper"},
    {"buffer_converter_score", pccnpcons_buffer_converter_score, METH_VARARGS, "minimal pcc NumPy buffer converter helper"},
    {"from_iter_score", pccnpcons_from_iter_score, METH_VARARGS, "minimal pcc NumPy from-iter helper"},
    {"converter_score", pccnpcons_converter_score, METH_VARARGS, "minimal pcc NumPy generic array converter helper"},
    {"pyint_converter_score", pccnpcons_pyint_converter_score, METH_VARARGS, "minimal pcc NumPy PyInt converters"},
    {"intp_from_sequence_score", pccnpcons_intp_from_sequence_score, METH_VARARGS, "minimal pcc NumPy intp-from-sequence helper"},
    {"intp_converter_score", pccnpcons_intp_converter_score, METH_VARARGS, "minimal pcc NumPy intp converter helper"},
    {"optional_intp_converter_score", pccnpcons_optional_intp_converter_score, METH_VARARGS, "minimal pcc NumPy optional intp converter helper"},
    {"promote_types_score", pccnpcons_promote_types_score, METH_VARARGS, "minimal pcc NumPy dtype promotion helper"},
    {"result_type_score", pccnpcons_result_type_score, METH_VARARGS, "minimal pcc NumPy result-type helper"},
    {"priority_score", pccnpcons_priority_score, METH_VARARGS, "minimal pcc NumPy array-priority helper"},
    {"check_strides_score", pccnpcons_check_strides_score, METH_VARARGS, "minimal pcc NumPy stride bounds helper"},
    {"broadcast_to_shape_score", pccnpcons_broadcast_to_shape_score, METH_VARARGS, "minimal pcc NumPy broadcast iterator helper"},
    {"broadcast_multi_score", pccnpcons_broadcast_multi_score, METH_VARARGS, "minimal pcc NumPy multi-iterator broadcast recompute helper"},
    {"multi_iter_from_objects_score", pccnpcons_multi_iter_from_objects_score, METH_VARARGS, "minimal pcc NumPy multi-iterator pointer-vector helper"},
    {"remove_smallest_score", pccnpcons_remove_smallest_score, METH_VARARGS, "minimal pcc NumPy multi-iterator remove-smallest helper"},
    {"multi_iter_score", pccnpcons_multi_iter_score, METH_VARARGS, "minimal pcc NumPy multi-iterator broadcast helper"},
    {"iter_all_but_axis_score", pccnpcons_iter_all_but_axis_score, METH_VARARGS, "minimal pcc NumPy all-but-axis iterator helper"},
    {"list_pointer_score", pccnpcons_list_pointer_score, METH_VARARGS, "minimal pcc NumPy list/pointer helpers"},
    {"overflow_multiply_score", pccnpcons_overflow_multiply_score, METH_VARARGS, "minimal pcc NumPy overflow multiply helper"},
    {"endianness_score", pccnpcons_endianness_score, METH_VARARGS, "minimal pcc NumPy endianness helper"},
    {"feature_version_score", pccnpcons_feature_version_score, METH_VARARGS, "minimal pcc NumPy feature-version helper"},
    {"version_score", pccnpcons_version_score, METH_VARARGS, "minimal pcc NumPy ABI/API version helpers"},
    {"check_axis_score", pccnpcons_check_axis_score, METH_VARARGS, "minimal pcc NumPy CheckAxis helper"},
    {"clipmode_converter_score", pccnpcons_clipmode_converter_score, METH_VARARGS, "minimal pcc NumPy clipmode converter helper"},
    {"clipmode_sequence_score", pccnpcons_clipmode_sequence_score, METH_VARARGS, "minimal pcc NumPy clipmode sequence helper"},
    {"output_converter_score", pccnpcons_output_converter_score, METH_VARARGS, "minimal pcc NumPy output converter helper"},
    {"searchside_converter_score", pccnpcons_searchside_converter_score, METH_VARARGS, "minimal pcc NumPy searchside converter helper"},
    {"byteorder_converter_score", pccnpcons_byteorder_converter_score, METH_VARARGS, "minimal pcc NumPy byteorder converter helper"},
    {"sortkind_converter_score", pccnpcons_sortkind_converter_score, METH_VARARGS, "minimal pcc NumPy sortkind converter helper"},
    {"selectkind_converter_score", pccnpcons_selectkind_converter_score, METH_VARARGS, "minimal pcc NumPy selectkind converter helper"},
    {"order_converter_score", pccnpcons_order_converter_score, METH_VARARGS, "minimal pcc NumPy order converter helper"},
    {"bool_converter_score", pccnpcons_bool_converter_score, METH_VARARGS, "minimal pcc NumPy bool converter helper"},
    {"optional_bool_converter_score", pccnpcons_optional_bool_converter_score, METH_VARARGS, "minimal pcc NumPy optional bool converter helper"},
    {"axis_converter_score", pccnpcons_axis_converter_score, METH_VARARGS, "minimal pcc NumPy axis converter helper"},
    {"element_strides_score", pccnpcons_element_strides_score, METH_VARARGS, "minimal pcc NumPy element-strides helper"},
    {"valid_type_score", pccnpcons_valid_type_score, METH_VARARGS, "minimal pcc NumPy dtype validity helper"},
    {"item_refcount_score", pccnpcons_item_refcount_score, METH_VARARGS, "minimal pcc NumPy object item refcount helpers"},
    {"array_refcount_score", pccnpcons_array_refcount_score, METH_VARARGS, "minimal pcc NumPy object array refcount helpers"},
    {"allocator_score", pccnpcons_allocator_score, METH_VARARGS, "minimal pcc NumPy allocator macros"},
    {"datamem_score", pccnpcons_datamem_score, METH_VARARGS, "minimal pcc NumPy data-memory helpers"},
    {"datamem_user_score", pccnpcons_datamem_user_score, METH_VARARGS, "minimal pcc NumPy handler data-memory helpers"},
    {"array_free_score", pccnpcons_array_free_score, METH_VARARGS, "minimal pcc NumPy public array free helper"},
    {"as_carray_score", pccnpcons_as_carray_score, METH_VARARGS, "minimal pcc NumPy public C-array adapter"},
    {"fail_writeable_score", pccnpcons_fail_writeable_score, METH_VARARGS, "minimal pcc NumPy writeable guard"},
    {"writeback_if_copy_score", pccnpcons_writeback_if_copy_score, METH_VARARGS, "minimal pcc NumPy writeback-if-copy lifecycle"},
    {"discard_writeback_if_copy_score", pccnpcons_discard_writeback_if_copy_score, METH_VARARGS, "minimal pcc NumPy discard writeback-if-copy lifecycle"},
    {NULL, NULL, 0, NULL},
};

static PyModuleDef consumermodule = {
    PyModuleDef_HEAD_INIT, "pccnpcons", NULL, -1, ConsumerMethods,
};

PyMODINIT_FUNC PyInit_pccnpcons(void) {
    import_array();
    import_umath();
    return PyModule_Create(&consumermodule);
}
