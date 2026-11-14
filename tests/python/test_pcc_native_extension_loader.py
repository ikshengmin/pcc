"""pcc-native CPython extension loading under strict no-libpython.

Every build here goes through the platform linker (``PCC_SELF_LINK=cc``).
Loading a CPython extension needs C-API export anchors in the executable, and
pcc's own Mach-O link mode states it does not synthesize them
(``pipeline_self_link.validate_pcc_self_link_surface``). On Darwin AArch64
``pcc`` is the default mode, so without declaring ``cc`` every test in this
file fails on that capability boundary rather than on anything the extension
loader does.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pcc1_gate import repo_root

REPO = repo_root()
if not (REPO / "utils" / "fake_libc_include" / "Python.h").exists():
    REPO = REPO / "pcc"


def _compile_extension(tmp_path: Path, module_name: str, source: str) -> Path:
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    src = tmp_path / f"{module_name}.c"
    src.write_text(source, encoding="utf-8")
    return _compile_extension_source(tmp_path, module_name, src)


def _compile_extension_source(tmp_path: Path, module_name: str, source: Path) -> Path:
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    out = site / f"{module_name}.so"
    cmd = [
        os.environ.get("CC", "cc"),
        "-shared",
        "-fPIC",
        "-I",
        str(REPO / "utils" / "fake_libc_include"),
        "-I",
        str(REPO / "pcc" / "runtime" / "include"),
        str(source),
        "-o",
        str(out),
    ]
    if sys.platform == "darwin":
        cmd.extend(["-undefined", "dynamic_lookup"])
    subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=30)
    return site


def _compile_demo_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static PyObject *demo_add(PyObject *self, PyObject *args) {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "ll", &a, &b)) return NULL;\n'
        "    return PyLong_FromLong(a + b);\n"
        "}\n"
        "\n"
        "static PyMethodDef DemoMethods[] = {\n"
        '    {"add", demo_add, METH_VARARGS, "add two ints"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef demomodule = {\n"
        '    PyModuleDef_HEAD_INIT, "demo", NULL, -1, DemoMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_demo(void) {\n"
        "    return PyModule_Create(&demomodule);\n"
        "}\n",
    )


def _compile_methodflag_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "flagdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static PyObject *flag_sum(PyObject *self, PyObject *args) {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "ll", &a, &b)) return NULL;\n'
        "    return PyLong_FromLong(a + b);\n"
        "}\n"
        "\n"
        "static PyObject *flag_noargs(PyObject *self, PyObject *ignored) {\n"
        "    (void)ignored;\n"
        "    return PyLong_FromLong(self != NULL ? 41 : -41);\n"
        "}\n"
        "\n"
        "static PyObject *flag_one(PyObject *self, PyObject *arg) {\n"
        "    long value = PyLong_AsLong(arg);\n"
        "    if (PyErr_Occurred() != NULL) return NULL;\n"
        "    return PyLong_FromLong(value + (self != NULL ? 100 : -100));\n"
        "}\n"
        "\n"
        "static PyMethodDef FlagMethods[] = {\n"
        '    {"sum", flag_sum, METH_VARARGS, "add two ints"},\n'
        '    {"noargs", flag_noargs, METH_NOARGS, "return a constant"},\n'
        '    {"one", flag_one, METH_O, "add to one int"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef flagmodule = {\n"
        '    PyModuleDef_HEAD_INIT, "flagdemo", NULL, -1, FlagMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_flagdemo(void) {\n"
        "    return PyModule_Create(&flagmodule);\n"
        "}\n",
    )


def _compile_fastcall_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "fastdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static PyObject *fast_sum(PyObject *self, PyObject *const *args, Py_ssize_t nargs) {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    (void)self;\n"
        "    if (nargs != 2) {\n"
        '        PyErr_SetString(PyExc_TypeError, "fast_sum takes two arguments");\n'
        "        return NULL;\n"
        "    }\n"
        "    a = PyLong_AsLong(args[0]);\n"
        "    if (PyErr_Occurred() != NULL) return NULL;\n"
        "    b = PyLong_AsLong(args[1]);\n"
        "    if (PyErr_Occurred() != NULL) return NULL;\n"
        "    return PyLong_FromLong(a + b);\n"
        "}\n"
        "\n"
        "static PyObject *fast_kw_score(PyObject *self, PyObject *const *args,\n"
        "                               Py_ssize_t nargs, PyObject *kwnames) {\n"
        "    long positional = 0;\n"
        "    long keyword = 0;\n"
        "    Py_ssize_t nkwargs = kwnames == NULL ? 0 : PyTuple_Size(kwnames);\n"
        "    (void)self;\n"
        "    if (nargs != 1 || nkwargs != 1) {\n"
        '        PyErr_SetString(PyExc_TypeError, "expected one positional and one keyword");\n'
        "        return NULL;\n"
        "    }\n"
        "    positional = PyLong_AsLong(args[0]);\n"
        "    if (PyErr_Occurred() != NULL) return NULL;\n"
        "    keyword = PyLong_AsLong(args[1]);\n"
        "    if (PyErr_Occurred() != NULL) return NULL;\n"
        "    return PyLong_FromLong(positional * 100 + keyword * 10 + nkwargs);\n"
        "}\n"
        "\n"
        "static PyObject *method_name(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    PyCFunctionObject *fn = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    if (Py_TYPE(obj) != &PyCFunction_Type) {\n"
        '        return PyUnicode_FromString("not-cfunc");\n'
        "    }\n"
        "    fn = (PyCFunctionObject *)obj;\n"
        "    if (fn->m_ml == NULL || fn->m_ml->ml_name == NULL) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "missing C method definition");\n'
        "        return NULL;\n"
        "    }\n"
        "    return PyUnicode_FromString(fn->m_ml->ml_name);\n"
        "}\n"
        "\n"
        "static PyMethodDef FastMethods[] = {\n"
        '    {"fast_sum", (PyCFunction)fast_sum, METH_FASTCALL, "add two ints"},\n'
        '    {"fast_kw_score", (PyCFunction)fast_kw_score, METH_FASTCALL | METH_KEYWORDS, "score vector layout"},\n'
        '    {"method_name", method_name, METH_VARARGS, "read C function ABI"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef fastmodule = {\n"
        '    PyModuleDef_HEAD_INIT, "fastdemo", NULL, -1, FastMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_fastdemo(void) {\n"
        "    return PyModule_Create(&fastmodule);\n"
        "}\n",
    )


def _compile_keywordflag_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "kwdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static PyObject *kwdemo_mix(PyObject *self, PyObject *args, PyObject *kwargs) {\n"
        "    long value = 0;\n"
        '    const char *name = "";\n'
        "    long name_len = 0;\n"
        '    static char *kwlist[] = {"value", "name", NULL};\n'
        "    (void)self;\n"
        '    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "l|s", kwlist, &value, &name)) return NULL;\n'
        "    while (name[name_len] != '\\0') name_len++;\n"
        "    return PyLong_FromLong(value + name_len);\n"
        "}\n"
        "\n"
        "static int kwdemo_keep_object(PyObject *obj, void *out) {\n"
        "    *(PyObject **)out = obj;\n"
        "    return 1;\n"
        "}\n"
        "\n"
        "static PyObject *kwdemo_options(PyObject *self, PyObject *args, PyObject *kwargs) {\n"
        "    int coerce = 1;\n"
        "    PyObject *converted = NULL;\n"
        '    static char *kwlist[] = {"coerce", "na_object", NULL};\n'
        "    (void)self;\n"
        '    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$pO&:options", kwlist,\n'
        "                                     &coerce, kwdemo_keep_object, &converted)) return NULL;\n"
        "    long score = coerce ? 100 : 0;\n"
        "    if (converted != NULL) score += PyLong_AsLong(converted);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *kwdemo_typed_long(PyObject *self, PyObject *args) {\n"
        "    PyObject *value = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O!:typed_long", &PyLong_Type, &value)) return NULL;\n'
        "    return PyLong_FromLong(PyLong_AsLong(value));\n"
        "}\n"
        "\n"
        "static PyMethodDef KwMethods[] = {\n"
        '    {"mix", (PyCFunction)kwdemo_mix, METH_VARARGS | METH_KEYWORDS, "mix args and kwargs"},\n'
        '    {"options", (PyCFunction)kwdemo_options, METH_VARARGS | METH_KEYWORDS, "parse optional p and O&"},\n'
        '    {"typed_long", kwdemo_typed_long, METH_VARARGS, "parse O!"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef kwmodule = {\n"
        '    PyModuleDef_HEAD_INIT, "kwdemo", NULL, -1, KwMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_kwdemo(void) {\n"
        "    return PyModule_Create(&kwmodule);\n"
        "}\n",
    )


def _compile_typedemo_extension(tmp_path: Path) -> Path:
    # A minimal custom-type extension exercising the PyType_Ready bridge: the
    # static PyTypeObject is registered via PyType_Ready, exposed on the module,
    # and instantiated through tp_new=PyType_GenericNew. This is the reduced
    # numpy type-registration pattern; it must import + instantiate under strict
    # no-libpython (--backend self).
    return _compile_extension(
        tmp_path,
        "typedemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "#include <structmember.h>\n"
        "\n"
        "typedef struct { PyObject_HEAD long val; PyObject *dict; } FooObject;\n"
        "\n"
        "static PyObject *foo_get_base(PyObject *self, void *closure) {\n"
        "    (void)self;\n"
        "    (void)closure;\n"
        "    return PyLong_FromLong(7);\n"
        "}\n"
        "\n"
        "static PyObject *foo_richcompare(PyObject *lhs, PyObject *rhs, int op) {\n"
        "    (void)lhs;\n"
        "    (void)rhs;\n"
        "    return PyBool_FromLong(op == Py_GE);\n"
        "}\n"
        "\n"
        "static PyGetSetDef FooGetSet[] = {\n"
        '    {"base", foo_get_base, NULL, NULL, NULL},\n'
        "    {NULL, NULL, NULL, NULL, NULL},\n"
        "};\n"
        "\n"
        "static PyTypeObject BaseType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "typedemo.Base",\n'
        "    .tp_basicsize = sizeof(FooObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "\n"
        "static PyMemberDef FooMembers[] = {\n"
        '    {"itemsize", T_LONG, __builtin_offsetof(FooObject, val), READONLY, NULL},\n'
        "    {NULL, 0, 0, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyTypeObject FooType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "typedemo.Foo",\n'
        "    .tp_basicsize = sizeof(FooObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_getattro = PyObject_GenericGetAttr,\n"
        "    .tp_setattro = PyObject_GenericSetAttr,\n"
        "    .tp_members = FooMembers,\n"
        "    .tp_getset = FooGetSet,\n"
        "    .tp_richcompare = foo_richcompare,\n"
        "    .tp_dictoffset = __builtin_offsetof(FooObject, dict),\n"
        "    .tp_base = &BaseType,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "\n"
        "static PyModuleDef typemodule = {\n"
        '    PyModuleDef_HEAD_INIT, "typedemo", NULL, -1, NULL,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_typedemo(void) {\n"
        "    if (PyType_Ready(&BaseType) < 0) return NULL;\n"
        "    if (PyType_Ready(&FooType) < 0) return NULL;\n"
        "    PyObject *m = PyModule_Create(&typemodule);\n"
        "    if (m == NULL) return NULL;\n"
        "    Py_INCREF((PyObject *)&BaseType);\n"
        '    PyModule_AddObject(m, "Base", (PyObject *)&BaseType);\n'
        "    Py_INCREF((PyObject *)&FooType);\n"
        '    PyModule_AddObject(m, "Foo", (PyObject *)&FooType);\n'
        "    return m;\n"
        "}\n",
    )


def _compile_typespec_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "specdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "typedef struct { PyObject_HEAD long value; } SpecObject;\n"
        "\n"
        "static PyObject *spec_marker(PyObject *self, PyObject *arg) {\n"
        "    if (self == NULL || arg == NULL) return NULL;\n"
        "    Py_INCREF(arg);\n"
        "    return arg;\n"
        "}\n"
        "\n"
        "static PyMethodDef SpecTypeMethods[] = {\n"
        '    {"marker", spec_marker, METH_O, "return the marker argument"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyType_Slot SpecSlots[] = {\n"
        "    {Py_tp_new, PyType_GenericNew},\n"
        '    {Py_tp_doc, "spec-created type"},\n'
        "    {Py_tp_methods, SpecTypeMethods},\n"
        "    {0, NULL},\n"
        "};\n"
        "\n"
        "static PyType_Spec Spec = {\n"
        '    "specdemo.Spec",\n'
        "    sizeof(SpecObject),\n"
        "    0,\n"
        "    Py_TPFLAGS_DEFAULT,\n"
        "    SpecSlots,\n"
        "};\n"
        "\n"
        "static PyTypeObject MetaType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "specdemo.Meta",\n'
        "    .tp_basicsize = sizeof(PyTypeObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "\n"
        "static PyTypeObject MetaInstanceType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "specdemo.MetaInstance",\n'
        "    .tp_basicsize = sizeof(PyObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "\n"
        "static PyObject *specdemo_check(PyObject *self, PyObject *args) {\n"
        "    long score = 0;\n"
        "    (void)self; (void)args;\n"
        "    PyObject *type = PyType_FromSpec(&Spec);\n"
        "    if (type == NULL) return NULL;\n"
        "    PyObject *obj = PyType_GenericNew((PyTypeObject *)type, NULL, NULL);\n"
        "    if (type != NULL) score += 1;\n"
        "    if (Py_TYPE(type) == &PyType_Type) score += 2;\n"
        "    if (obj != NULL) score += 4;\n"
        "    if (obj != NULL && Py_TYPE(obj) == (PyTypeObject *)type) score += 8;\n"
        "    if (obj != NULL && PyObject_TypeCheck(obj, (PyTypeObject *)type)) score += 16;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *specdemo_slots(PyObject *self, PyObject *args) {\n"
        "    long score = 0;\n"
        "    (void)self; (void)args;\n"
        "    PyObject *type = PyType_FromSpec(&Spec);\n"
        "    if (type == NULL) return NULL;\n"
        "    if (PyType_GetSlot((PyTypeObject *)type, Py_tp_new) == PyType_GenericNew) score += 1;\n"
        "    const char *doc = (const char *)PyType_GetSlot((PyTypeObject *)type, Py_tp_doc);\n"
        "    if (doc != NULL && doc[0] == 's') score += 2;\n"
        "    if (PyType_GetSlot((PyTypeObject *)type, 9999) == NULL) score += 4;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *specdemo_metatype_check(PyObject *self, PyObject *args) {\n"
        "    long score = 0;\n"
        "    (void)self; (void)args;\n"
        "    if (Py_TYPE(&MetaInstanceType) == &MetaType) score += 1;\n"
        "    if (PyObject_TypeCheck((PyObject *)&MetaInstanceType, &MetaType)) score += 2;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyMethodDef SpecMethods[] = {\n"
        '    {"check", specdemo_check, METH_VARARGS, "check PyType_FromSpec"},\n'
        '    {"slots", specdemo_slots, METH_VARARGS, "check PyType_GetSlot"},\n'
        '    {"metatype_check", specdemo_metatype_check, METH_VARARGS, "check custom metaclass"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef specmodule = {\n"
        '    PyModuleDef_HEAD_INIT, "specdemo", NULL, -1, SpecMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_specdemo(void) {\n"
        "    MetaType.tp_base = &PyType_Type;\n"
        "    if (PyType_Ready(&MetaType) < 0) return NULL;\n"
        "    Py_SET_TYPE(&MetaInstanceType, &MetaType);\n"
        "    if (PyType_Ready(&MetaInstanceType) < 0) return NULL;\n"
        "    PyObject *m = PyModule_Create(&specmodule);\n"
        "    if (m == NULL) return NULL;\n"
        "    PyObject *type = PyType_FromSpec(&Spec);\n"
        "    if (type == NULL) return NULL;\n"
        '    if (PyModule_AddObject(m, "Spec", type) < 0) return NULL;\n'
        "    return m;\n"
        "}\n",
    )


def _compile_typecheck_extension(tmp_path: Path) -> Path:
    # Exercises the builtin-tag Py_TYPE mapping: Py_TYPE of pcc builtin objects
    # must resolve to the matching &PyXxx_Type token the shim provides (numpy
    # does this constantly). Returns a bitset so a partial mapping is visible.
    return _compile_extension(
        tmp_path,
        "tcdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *tc_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    PyObject *i = PyLong_FromLong(5);\n"
        '    PyObject *s = PyUnicode_FromString("x");\n'
        "    long ok = (Py_TYPE(i) == &PyLong_Type) ? 1 : 0;\n"
        "    ok += (Py_TYPE(s) == &PyUnicode_Type) ? 2 : 0;\n"
        "    return PyLong_FromLong(ok);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", tc_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "tcdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_tcdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_subtype_extension(tmp_path: Path) -> Path:
    # Exercises PyObject_TypeCheck across a tp_base inheritance chain (numpy's
    # scalar/array type hierarchy relies on subtype checks). check() returns 7
    # when: derived is-a base, derived is-a derived, base is-NOT-a derived.
    return _compile_extension(
        tmp_path,
        "stdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "typedef struct { PyObject_HEAD long v; } BaseObj;\n"
        "typedef struct { BaseObj base; long w; } DerivedObj;\n"
        "static PyTypeObject BaseType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "stdemo.Base", .tp_basicsize = sizeof(BaseObj),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyTypeObject DerivedType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "stdemo.Derived", .tp_basicsize = sizeof(DerivedObj),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_base = &BaseType,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyObject *st_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    PyObject *d = PyType_GenericNew(&DerivedType, NULL, NULL);\n"
        "    PyObject *b = PyType_GenericNew(&BaseType, NULL, NULL);\n"
        "    long r = 0;\n"
        "    if (PyObject_TypeCheck(d, &BaseType)) r += 1;\n"
        "    if (PyObject_TypeCheck(d, &DerivedType)) r += 2;\n"
        "    if (!PyObject_TypeCheck(b, &DerivedType)) r += 4;\n"
        "    if (PyType_IsSubtype(&DerivedType, &BaseType)) r += 8;\n"
        "    if (!PyType_IsSubtype(&BaseType, &DerivedType)) r += 16;\n"
        "    BaseObj *n = PyObject_New(BaseObj, &BaseType);\n"
        "    if (n != NULL) r += 32;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyObject *st_alloc_slots(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    if (BaseType.tp_alloc != NULL) r += 1;\n"
        "    if (DerivedType.tp_alloc != NULL) r += 2;\n"
        "    if (DerivedType.tp_alloc == BaseType.tp_alloc) r += 4;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", st_check, METH_VARARGS, ""},\n'
        '    {"alloc_slots", st_alloc_slots, METH_VARARGS, ""},\n'
        "    {NULL, NULL, 0, NULL}\n"
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "stdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_stdemo(void) {\n"
        "    if (PyType_Ready(&BaseType) < 0) return NULL;\n"
        "    if (PyType_Ready(&DerivedType) < 0) return NULL;\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )


def _compile_number_slot_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "numslotdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "typedef struct { PyObject_HEAD long value; } NumObject;\n"
        "static PyObject *num_int(PyObject *self) {\n"
        "    return PyLong_FromLong(((NumObject *)self)->value);\n"
        "}\n"
        "static PyObject *num_index(PyObject *self) {\n"
        "    return PyLong_FromLong(((NumObject *)self)->value);\n"
        "}\n"
        "static PyNumberMethods IntMethods = { .nb_int = num_int };\n"
        "static PyNumberMethods IndexMethods = { .nb_index = num_index };\n"
        "static PyTypeObject IntType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "numslotdemo.IntLike",\n'
        "    .tp_basicsize = sizeof(NumObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_as_number = &IntMethods,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyTypeObject IndexType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "numslotdemo.IndexLike",\n'
        "    .tp_basicsize = sizeof(NumObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_as_number = &IndexMethods,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyObject *check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    NumObject *int_obj = (NumObject *)PyType_GenericNew(&IntType, NULL, NULL);\n"
        "    NumObject *index_obj = (NumObject *)PyType_GenericNew(&IndexType, NULL, NULL);\n"
        "    if (int_obj == NULL || index_obj == NULL) return NULL;\n"
        "    int_obj->value = 40;\n"
        "    index_obj->value = 41;\n"
        "    PyObject *from_int = PyNumber_Long((PyObject *)int_obj);\n"
        "    if (from_int == NULL) return NULL;\n"
        "    PyObject *from_index_long = PyNumber_Long((PyObject *)index_obj);\n"
        "    if (from_index_long == NULL) return NULL;\n"
        "    PyObject *from_index = PyNumber_Index((PyObject *)index_obj);\n"
        "    if (from_index == NULL) return NULL;\n"
        "    long score = 0;\n"
        "    if (PyLong_AsLong(from_int) == 40) score += 1;\n"
        "    if (PyLong_AsLong(from_index_long) == 41) score += 2;\n"
        "    if (PyLong_AsLong(from_index) == 41) score += 4;\n"
        "    if (PyIndex_Check((PyObject *)index_obj)) score += 8;\n"
        "    if (PyNumber_Check((PyObject *)int_obj)) score += 16;\n"
        "    if (PyNumber_Check((PyObject *)index_obj)) score += 32;\n"
        "    Py_DECREF(from_index);\n"
        "    Py_DECREF(from_index_long);\n"
        "    Py_DECREF(from_int);\n"
        "    Py_DECREF(index_obj);\n"
        "    Py_DECREF(int_obj);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "numslotdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_numslotdemo(void) {\n"
        "    if (PyType_Ready(&IntType) < 0) return NULL;\n"
        "    if (PyType_Ready(&IndexType) < 0) return NULL;\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )


def _compile_managed_dealloc_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "mdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "typedef struct { PyObject_HEAD long value; } DemoObj;\n"
        "static long dealloc_hits = 0;\n"
        "static void managed_dealloc(PyObject *self) { (void)self; dealloc_hits += 1; }\n"
        "static void plain_dealloc(PyObject *self) { (void)self; dealloc_hits += 100; }\n"
        "static PyTypeObject ManagedType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "mdemo.Managed", .tp_basicsize = sizeof(DemoObj),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT | PCC_TPFLAGS_MANAGED_DEALLOC,\n"
        "    .tp_dealloc = managed_dealloc, .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyTypeObject PlainType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "mdemo.Plain", .tp_basicsize = sizeof(DemoObj),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_dealloc = plain_dealloc, .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyObject *md_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    dealloc_hits = 0;\n"
        "    PyObject *managed = PyType_GenericNew(&ManagedType, NULL, NULL);\n"
        "    PyObject *plain = PyType_GenericNew(&PlainType, NULL, NULL);\n"
        "    if (managed == NULL || plain == NULL) {\n"
        "        Py_XDECREF(managed); Py_XDECREF(plain); return NULL;\n"
        "    }\n"
        "    Py_DECREF(managed);\n"
        "    Py_DECREF(plain);\n"
        "    return PyLong_FromLong(dealloc_hits);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", md_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "mdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_mdemo(void) {\n"
        "    if (PyType_Ready(&ManagedType) < 0) return NULL;\n"
        "    if (PyType_Ready(&PlainType) < 0) return NULL;\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )


def _compile_linksym_extension(tmp_path: Path) -> Path:
    # Behavioral regression for the batch 7-9 link symbols added to
    # py_capi_shim.c for numpy's no-libpython link surface. These were
    # link-validated (the shim compiles + ext-abi gate) but not exercised at
    # runtime; this locks their BEHAVIOR under strict no-libpython. check()
    # returns a bitset; 31 = all five correct:
    #   1  PyTuple_GetSlice((10,20,30,40)[1:3]) -> (20,30)
    #   2  PyObject_AsFileDescriptor(PyLong(7)) -> 7
    #   4  Py_GenericAlias(origin=PyLong(99), NULL) -> origin (value 99 preserved)
    #   8  PyUnicode_AsLatin1String("Ab") -> b"Ab"
    #   16 PyErr_NormalizeException(NULL triple) -> no-op, error state stays clear
    return _compile_extension(
        tmp_path,
        "lsdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *ls_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *t = PyTuple_New(4);\n"
        "    PyTuple_SetItem(t, 0, PyLong_FromLong(10));\n"
        "    PyTuple_SetItem(t, 1, PyLong_FromLong(20));\n"
        "    PyTuple_SetItem(t, 2, PyLong_FromLong(30));\n"
        "    PyTuple_SetItem(t, 3, PyLong_FromLong(40));\n"
        "    PyObject *sl = PyTuple_GetSlice(t, 1, 3);\n"
        "    if (sl != NULL && PyTuple_Size(sl) == 2 &&\n"
        "        PyLong_AsLong(PyTuple_GetItem(sl, 0)) == 20 &&\n"
        "        PyLong_AsLong(PyTuple_GetItem(sl, 1)) == 30) r += 1;\n"
        "    PyObject *fd = PyLong_FromLong(7);\n"
        "    if (PyObject_AsFileDescriptor(fd) == 7) r += 2;\n"
        "    PyObject *origin = PyLong_FromLong(99);\n"
        "    PyObject *ga = Py_GenericAlias(origin, Py_None);\n"
        "    if (ga != NULL && PyLong_AsLong(ga) == 99) r += 4;\n"
        '    PyObject *u = PyUnicode_FromString("Ab");\n'
        "    PyObject *b = PyUnicode_AsLatin1String(u);\n"
        "    if (b != NULL && PyBytes_Size(b) == 2) {\n"
        "        const char *bs = PyBytes_AsString(b);\n"
        "        if (bs != NULL && bs[0] == 'A' && bs[1] == 'b') r += 8;\n"
        "    }\n"
        "    PyObject *e = NULL, *v = NULL, *tb = NULL;\n"
        "    PyErr_NormalizeException(&e, &v, &tb);\n"
        "    if (!PyErr_Occurred()) r += 16;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", ls_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "lsdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_lsdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_contextvar_extension(tmp_path: Path) -> Path:
    # Behavioral regression for the batch 10 single-context contextvar
    # (PyContextVar_New + PyContextVar_Get) added to py_capi_shim.c. numpy.errstate
    # creates a ContextVar AT import, so this is a real (not stub) object; check()
    # verifies correct CPython single-context Get semantics:
    #   1  Get(cv, NULL, &v) on an unset var -> the var's own default (5)
    #   2  Get(cv, PyLong(9), &v) on an unset var -> the explicit default arg (9)
    #   4  after Set(cv, 7) (batch 14): Get(cv, NULL, &v) -> the set value (7),
    #      and Set returns a non-NULL token (numpy set-and-discards it).
    # 7 = all correct.
    return _compile_extension(
        tmp_path,
        "cvdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *cv_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *cv = PyContextVar_New("x", PyLong_FromLong(5));\n'
        "    if (cv == NULL) return PyLong_FromLong(-1);\n"
        "    PyObject *v = NULL;\n"
        "    if (PyContextVar_Get(cv, NULL, &v) == 0 && v != NULL &&\n"
        "        PyLong_AsLong(v) == 5) r += 1;\n"
        "    PyObject *v2 = NULL;\n"
        "    if (PyContextVar_Get(cv, PyLong_FromLong(9), &v2) == 0 && v2 != NULL &&\n"
        "        PyLong_AsLong(v2) == 9) r += 2;\n"
        "    PyObject *tok = PyContextVar_Set(cv, PyLong_FromLong(7));\n"
        "    PyObject *v3 = NULL;\n"
        "    if (tok != NULL && PyContextVar_Get(cv, NULL, &v3) == 0 && v3 != NULL &&\n"
        "        PyLong_AsLong(v3) == 7) r += 4;\n"
        "    if (tok != NULL) Py_DECREF(tok);\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyObject *cv_make(PyObject *self, PyObject *unused) {\n"
        "    (void)self; (void)unused;\n"
        '    return PyContextVar_New("python-visible", PyLong_FromLong(5));\n'
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", cv_check, METH_VARARGS, ""},\n'
        '    {"make", cv_make, METH_NOARGS, ""},\n'
        "    {NULL, NULL, 0, NULL}\n"
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "cvdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_cvdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_buildvalue_dict_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "buildvaluedict",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        '    PyObject *key = PyUnicode_FromString("__signature__");\n'
        "    PyObject *value = PyLong_FromLong(42);\n"
        '    PyObject *dict = Py_BuildValue("{ON}", key, value);\n'
        "    Py_DECREF(key);\n"
        "    if (dict == NULL) return NULL;\n"
        '    PyObject *got = PyDict_GetItemString(dict, "__signature__");\n'
        "    long result = got == NULL ? -1 : PyLong_AsLong(got);\n"
        "    Py_DECREF(dict);\n"
        "    return PyLong_FromLong(result);\n"
        "}\n"
        "static PyObject *nested(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        '    PyObject *built = Py_BuildValue("{s, [(i), (i, i)]}",\n'
        '                                    "axes", -1, -2, -1);\n'
        "    if (built == NULL) return NULL;\n"
        '    PyObject *axes = PyDict_GetItemString(built, "axes");\n'
        "    if (axes == NULL) return NULL;\n"
        "    PyObject *first = PyList_GetItem(axes, 0);\n"
        "    PyObject *second = PyList_GetItem(axes, 1);\n"
        "    long score = 0;\n"
        "    if (PyList_Check(axes)) score += 1;\n"
        "    if (PyList_Size(axes) == 2) score += 2;\n"
        "    if (first != NULL && PyTuple_Check(first)) score += 4;\n"
        "    if (first != NULL && PyLong_AsLong(PyTuple_GetItem(first, 0)) == -1) score += 8;\n"
        "    if (second != NULL && PyTuple_Size(second) == 2) score += 16;\n"
        "    if (second != NULL && PyLong_AsLong(PyTuple_GetItem(second, 0)) == -2) score += 32;\n"
        "    if (second != NULL && PyLong_AsLong(PyTuple_GetItem(second, 1)) == -1) score += 64;\n"
        "    Py_DECREF(built);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", check, METH_VARARGS, ""},\n'
        '    {"nested", nested, METH_VARARGS, ""},\n'
        "    {NULL, NULL, 0, NULL}\n"
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "buildvaluedict", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_buildvaluedict(void) {\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )


def _compile_vaparse_extension(tmp_path: Path) -> Path:
    # Behavioral regression for the PyArg_ParseTupleAndKeywords -> (new)
    # PyArg_VaParseTupleAndKeywords va_list-core delegation (the canonical CPython
    # refactor; PyArg_VaParseTupleAndKeywords is the symbol numpy's C core
    # references). The extension does NOT call va_start itself (the curated
    # fake-libc dir's stdarg shim is build-only); instead it calls the `...`
    # wrapper, which now routes through the va_list core. kwargs=NULL because the
    # no-libpython loader dispatches METH_VARARGS (positional). check(10, 20)
    # parses "ll" via the core and returns a*100+b = 1020.
    return _compile_extension(
        tmp_path,
        "vademo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *va_check(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    long a = 0, b = 0;\n"
        '    char *kwlist[] = {"a", "b", NULL};\n'
        '    if (!PyArg_ParseTupleAndKeywords(args, NULL, "ll", kwlist, &a, &b))\n'
        "        return PyLong_FromLong(-1);\n"
        "    return PyLong_FromLong(a * 100 + b);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", va_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "vademo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_vademo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_getbuiltins_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyEval_GetBuiltins (numpy's sole consumer is
    # npy_PyFile_OpenFile in npy_3kcompat.h: PyDict_GetItemString(
    # PyEval_GetBuiltins(), "open"), which returns NULL gracefully if absent).
    # check() returns 7:
    #   1  PyEval_GetBuiltins() is non-NULL (a real dict, not NULL/fake)
    #   2  numpy's pattern: an absent key ("open") -> NULL, no crash (import-safe)
    #   4  it is a real mutable dict AND a persistent singleton (a second call
    #      returns the same pointer; set/get roundtrips) — i.e. borrowed-ref
    #      semantics, not a throwaway.
    return _compile_extension(
        tmp_path,
        "ebdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *eb_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *b = PyEval_GetBuiltins();\n"
        "    if (b != NULL) r += 1;\n"
        '    if (PyDict_GetItemString(b, "open") == NULL) r += 2;\n'
        '    PyDict_SetItemString(b, "answer", PyLong_FromLong(42));\n'
        "    PyObject *b2 = PyEval_GetBuiltins();\n"
        '    PyObject *v = PyDict_GetItemString(b2, "answer");\n'
        "    if (b2 == b && v != NULL && PyLong_AsLong(v) == 42) r += 4;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", eb_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "ebdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_ebdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_uniqueref_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyUnstable_Object_IsUniquelyReferenced (referenced
    # by the full numpy _core for safe in-place mutation). check() returns 3:
    #   1  a freshly-created object (refcount 1) is uniquely referenced
    #   2  after Py_INCREF (refcount 2) it is NOT uniquely referenced
    return _compile_extension(
        tmp_path,
        "urdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *ur_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *o = PyTuple_New(1);\n"
        "    PyTuple_SetItem(o, 0, PyLong_FromLong(0));\n"
        "    if (PyUnstable_Object_IsUniquelyReferenced(o)) r += 1;\n"
        "    Py_INCREF(o);\n"
        "    if (!PyUnstable_Object_IsUniquelyReferenced(o)) r += 2;\n"
        "    Py_DECREF(o);\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", ur_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "urdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_urdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_import_vectorcall_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyImport_Import (-> PyImport_ImportModule) and
    # PyVectorcall_Call (-> PyObject_Call), both batch 14. The caller imports the
    # sibling `demo` extension by name and calls demo.add(2, 3) via the vectorcall
    # entry. Compiled into the SAME site as demo (so it is importable). check()
    # returns 7:
    #   1  PyImport_Import("demo") is non-NULL
    #   2  demo.add is reachable
    #   4  PyVectorcall_Call(add, (2,3), NULL) == 5
    return _compile_extension(
        tmp_path,
        "imdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *im_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *m = PyImport_Import(PyUnicode_FromString("demo"));\n'
        "    if (m == NULL) return PyLong_FromLong(r);\n"
        "    r += 1;\n"
        '    PyObject *add = PyObject_GetAttrString(m, "add");\n'
        "    if (add == NULL) return PyLong_FromLong(r);\n"
        "    r += 2;\n"
        "    PyObject *t = PyTuple_New(2);\n"
        "    PyTuple_SetItem(t, 0, PyLong_FromLong(2));\n"
        "    PyTuple_SetItem(t, 1, PyLong_FromLong(3));\n"
        "    PyObject *res = PyVectorcall_Call(add, t, NULL);\n"
        "    if (res != NULL && PyLong_AsLong(res) == 5) r += 4;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", im_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "imdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_imdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_type_vectorcall_extension(tmp_path: Path) -> Path:
    # Reduced numpy ufunc call layout: tp_call points at PyVectorcall_Call and
    # tp_vectorcall_offset points at a per-instance vectorcallfunc slot.  The
    # shim must dispatch that slot directly instead of recursing through
    # PyObject_Call -> tp_call -> PyVectorcall_Call.
    return _compile_extension(
        tmp_path,
        "typevcdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "#include <stddef.h>\n"
        "\n"
        "typedef struct {\n"
        "    PyObject_HEAD\n"
        "    vectorcallfunc vectorcall;\n"
        "} VectorObject;\n"
        "\n"
        "static PyObject *vector_impl(PyObject *self, PyObject *const *args,\n"
        "                             size_t nargsf, PyObject *kwnames) {\n"
        "    Py_ssize_t nargs = (Py_ssize_t)PyVectorcall_NARGS(nargsf);\n"
        "    Py_ssize_t nkwargs = kwnames == NULL ? 0 : PyTuple_Size(kwnames);\n"
        "    long offset_bonus = (nargsf & PY_VECTORCALL_ARGUMENTS_OFFSET) ? 1000 : 0;\n"
        "    long first = 0;\n"
        "    long second = 0;\n"
        "    (void)self;\n"
        "    if (nargs < 1 || args == NULL || (nargs + nkwargs) != 2) {\n"
        '        PyErr_SetString(PyExc_TypeError, "expected two values");\n'
        "        return NULL;\n"
        "    }\n"
        "    first = PyLong_AsLong(args[0]);\n"
        "    if (PyErr_Occurred() != NULL) return NULL;\n"
        "    second = PyLong_AsLong(args[1]);\n"
        "    if (PyErr_Occurred() != NULL) return NULL;\n"
        "    if (nkwargs == 0) return PyLong_FromLong(first * 10 + second + offset_bonus);\n"
        "    return PyLong_FromLong(first * 100 + second * 10 + nkwargs + offset_bonus);\n"
        "}\n"
        "\n"
        "static PyObject *vector_new(PyTypeObject *type, PyObject *args,\n"
        "                            PyObject *kwargs) {\n"
        "    VectorObject *self = (VectorObject *)PyType_GenericNew(type, args, kwargs);\n"
        "    if (self != NULL) self->vectorcall = vector_impl;\n"
        "    return (PyObject *)self;\n"
        "}\n"
        "\n"
        "static PyObject *vector_iter(PyObject *self) {\n"
        "    PyObject *items = PyTuple_New(2);\n"
        "    PyObject *iter = NULL;\n"
        "    (void)self;\n"
        "    if (items == NULL) return NULL;\n"
        "    PyTuple_SetItem(items, 0, PyLong_FromLong(5));\n"
        "    PyTuple_SetItem(items, 1, PyLong_FromLong(6));\n"
        "    iter = PySeqIter_New(items);\n"
        "    Py_DECREF(items);\n"
        "    return iter;\n"
        "}\n"
        "\n"
        "static PyObject *vector_subscript(PyObject *self, PyObject *key) {\n"
        "    PyObject *first = NULL;\n"
        "    PyObject *second = NULL;\n"
        "    Py_ssize_t start = 0, stop = 0, step = 0, length = 0;\n"
        "    (void)self;\n"
        "    if (!PyTuple_Check(key) || PyTuple_Size(key) != 3) {\n"
        '        PyErr_SetString(PyExc_TypeError, "expected a 3-item index");\n'
        "        return NULL;\n"
        "    }\n"
        "    first = PyTuple_GetItem(key, 0);\n"
        "    second = PyTuple_GetItem(key, 1);\n"
        "    if (!PySlice_Check(first) || !PySlice_Check(second)\n"
        "        || PyTuple_GetItem(key, 2) != Py_None) {\n"
        '        PyErr_SetString(PyExc_TypeError, "expected slice, slice, None");\n'
        "        return NULL;\n"
        "    }\n"
        "    if (PySlice_GetIndicesEx(first, 10, &start, &stop, &step, &length) < 0\n"
        "        || start != 0 || stop != 10 || step != 1 || length != 10) {\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PySlice_GetIndicesEx(second, 10, &start, &stop, &step, &length) < 0\n"
        "        || start != 1 || stop != 5 || step != 2 || length != 2) {\n"
        "        return NULL;\n"
        "    }\n"
        "    return PyLong_FromLong(73);\n"
        "}\n"
        "\n"
        "static PyMappingMethods VectorMapping = {\n"
        "    .mp_subscript = vector_subscript,\n"
        "};\n"
        "\n"
        "static PyTypeObject VectorType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "typevcdemo.Vector",\n'
        "    .tp_basicsize = sizeof(VectorObject),\n"
        "    .tp_vectorcall_offset = __builtin_offsetof(VectorObject, vectorcall),\n"
        "    .tp_call = PyVectorcall_Call,\n"
        "    .tp_iter = vector_iter,\n"
        "    .tp_as_mapping = &VectorMapping,\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VECTORCALL,\n"
        "    .tp_new = vector_new,\n"
        "};\n"
        "\n"
        "static PyObject *make_vector(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    return vector_new(&VectorType, args, NULL);\n"
        "}\n"
        "\n"
        "static PyObject *call_keyword(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    PyObject *values[2] = {NULL, NULL};\n"
        "    PyObject *kwnames = NULL;\n"
        "    PyObject *result = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    if (PyLong_AsLong(Py_True) != 1 || PyLong_AsLong(Py_False) != 0) {\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyLong_AsLongLong(Py_True) != 1 || "
        "PyLong_AsLongLong(Py_False) != 0) {\n"
        "        return NULL;\n"
        "    }\n"
        "    values[0] = PyLong_FromLong(2);\n"
        "    values[1] = PyLong_FromLong(4);\n"
        "    kwnames = PyTuple_New(1);\n"
        "    if (values[0] == NULL || values[1] == NULL || kwnames == NULL) {\n"
        "        Py_XDECREF(values[0]);\n"
        "        Py_XDECREF(values[1]);\n"
        "        Py_XDECREF(kwnames);\n"
        "        return NULL;\n"
        "    }\n"
        '    PyTuple_SetItem(kwnames, 0, PyUnicode_FromString("scale"));\n'
        "    result = PyObject_Vectorcall(callable, values, 1, kwnames);\n"
        "    Py_DECREF(values[0]);\n"
        "    Py_DECREF(values[1]);\n"
        "    Py_DECREF(kwnames);\n"
        "    return result;\n"
        "}\n"
        "\n"
        "static PyObject *call_method_offset(PyObject *self, PyObject *args) {\n"
        "    PyObject *storage[4] = {NULL, NULL, NULL, NULL};\n"
        "    PyObject **values = &storage[1];\n"
        '    PyObject *name = PyUnicode_FromString("offset_target");\n'
        "    PyObject *result = NULL;\n"
        "    (void)args;\n"
        "    values[0] = self;\n"
        "    values[1] = PyLong_FromLong(2);\n"
        "    values[2] = PyLong_FromLong(4);\n"
        "    if (name == NULL || values[1] == NULL || values[2] == NULL) {\n"
        "        Py_XDECREF(name);\n"
        "        Py_XDECREF(values[1]);\n"
        "        Py_XDECREF(values[2]);\n"
        "        return NULL;\n"
        "    }\n"
        "    result = PyObject_VectorcallMethod(\n"
        "        name, values, ((size_t)3) | PY_VECTORCALL_ARGUMENTS_OFFSET, NULL\n"
        "    );\n"
        "    Py_DECREF(values[1]);\n"
        "    Py_DECREF(values[2]);\n"
        "    Py_DECREF(name);\n"
        "    return result;\n"
        "}\n"
        "\n"
        "static PyObject *check_builtin_type(PyObject *self, PyObject *arg) {\n"
        "    (void)self;\n"
        "    return PyLong_FromLong(\n"
        "        PyType_Check(arg) && arg == (PyObject *)&PyLong_Type\n"
        "    );\n"
        "}\n"
        "\n"
        "static PyMethodDef Methods[] = {\n"
        '    {"make", make_vector, METH_VARARGS, "make a vectorcall object"},\n'
        '    {"call_keyword", call_keyword, METH_VARARGS, "call with keyword"},\n'
        '    {"call_method_offset", call_method_offset, METH_VARARGS, "preserve vectorcall offset bit"},\n'
        '    {"check_builtin_type", check_builtin_type, METH_O, "check int type token"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef module = {\n"
        '    PyModuleDef_HEAD_INIT, "typevcdemo", NULL, -1, Methods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_typevcdemo(void) {\n"
        "    PyObject *module_obj = NULL;\n"
        "    PyObject *empty = NULL;\n"
        "    PyObject *target = NULL;\n"
        "    if (PyType_Ready(&VectorType) < 0) return NULL;\n"
        "    module_obj = PyModule_Create(&module);\n"
        "    if (module_obj == NULL) return NULL;\n"
        "    empty = PyTuple_New(0);\n"
        "    if (empty == NULL) return NULL;\n"
        "    target = vector_new(&VectorType, empty, NULL);\n"
        "    Py_DECREF(empty);\n"
        "    if (target == NULL) return NULL;\n"
        '    if (PyObject_SetAttrString(module_obj, "offset_target", target) != 0) return NULL;\n'
        "    Py_DECREF(target);\n"
        "    return module_obj;\n"
        "}\n",
    )


def _compile_type_number_slot_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "typenumdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "typedef struct { PyObject_HEAD long value; } NumberObject;\n"
        "static PyTypeObject NumberType;\n"
        "\n"
        "static PyObject *number_from_long(long value) {\n"
        "    NumberObject *number = (NumberObject *)PyType_GenericNew(\n"
        "        &NumberType, NULL, NULL\n"
        "    );\n"
        "    if (number != NULL) number->value = value;\n"
        "    return (PyObject *)number;\n"
        "}\n"
        "\n"
        "static int number_as_long(PyObject *value, long *out) {\n"
        "    if (PyObject_TypeCheck(value, &NumberType)) {\n"
        "        *out = ((NumberObject *)value)->value;\n"
        "        return 1;\n"
        "    }\n"
        "    if (PyLong_Check(value)) {\n"
        "        *out = PyLong_AsLong(value);\n"
        "        return PyErr_Occurred() == NULL ? 1 : -1;\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
        "\n"
        "static PyObject *number_add(PyObject *left, PyObject *right) {\n"
        "    if (PyObject_TypeCheck(left, &NumberType) && PyFloat_Check(right)) {\n"
        "        return PyFloat_FromDouble(\n"
        "            (double)((NumberObject *)left)->value + PyFloat_AsDouble(right)\n"
        "        );\n"
        "    }\n"
        "    if (PyFloat_Check(left) && PyObject_TypeCheck(right, &NumberType)) {\n"
        "        return PyFloat_FromDouble(\n"
        "            PyFloat_AsDouble(left) + (double)((NumberObject *)right)->value\n"
        "        );\n"
        "    }\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    int a_ok = number_as_long(left, &a);\n"
        "    int b_ok = number_as_long(right, &b);\n"
        "    if (a_ok < 0 || b_ok < 0) return NULL;\n"
        "    if (!a_ok || !b_ok) Py_RETURN_NOTIMPLEMENTED;\n"
        "    return PyLong_FromLong(a + b);\n"
        "}\n"
        "\n"
        "static PyObject *number_subtract(PyObject *left, PyObject *right) {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    int a_ok = number_as_long(left, &a);\n"
        "    int b_ok = number_as_long(right, &b);\n"
        "    if (a_ok < 0 || b_ok < 0) return NULL;\n"
        "    if (!a_ok || !b_ok) Py_RETURN_NOTIMPLEMENTED;\n"
        "    return PyLong_FromLong(a - b);\n"
        "}\n"
        "\n"
        "static PyObject *number_multiply(PyObject *left, PyObject *right) {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    int a_ok = number_as_long(left, &a);\n"
        "    int b_ok = number_as_long(right, &b);\n"
        "    if (a_ok < 0 || b_ok < 0) return NULL;\n"
        "    if (!a_ok || !b_ok) Py_RETURN_NOTIMPLEMENTED;\n"
        "    return PyLong_FromLong(a * b);\n"
        "}\n"
        "\n"
        "static PyObject *number_true_divide(PyObject *left, PyObject *right) {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    int a_ok = number_as_long(left, &a);\n"
        "    int b_ok = number_as_long(right, &b);\n"
        "    if (a_ok < 0 || b_ok < 0) return NULL;\n"
        "    if (!a_ok || !b_ok) Py_RETURN_NOTIMPLEMENTED;\n"
        "    if (b == 0) {\n"
        '        PyErr_SetString(PyExc_ZeroDivisionError, "division by zero");\n'
        "        return NULL;\n"
        "    }\n"
        "    return PyFloat_FromDouble((double)a / (double)b);\n"
        "}\n"
        "\n"
        "static PyObject *number_absolute(PyObject *value) {\n"
        "    long raw = 0;\n"
        "    if (!PyObject_TypeCheck(value, &NumberType)) {\n"
        "        Py_RETURN_NOTIMPLEMENTED;\n"
        "    }\n"
        "    raw = ((NumberObject *)value)->value;\n"
        "    return PyLong_FromLong(raw < 0 ? -raw : raw);\n"
        "}\n"
        "\n"
        "static int number_bool(PyObject *value) {\n"
        "    return ((NumberObject *)value)->value != 0;\n"
        "}\n"
        "\n"
        "static PyObject *number_richcompare(\n"
        "    PyObject *left, PyObject *right, int op\n"
        ") {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    int result = 0;\n"
        "    if (!PyObject_TypeCheck(left, &NumberType)\n"
        "        || !PyObject_TypeCheck(right, &NumberType)) {\n"
        "        Py_RETURN_NOTIMPLEMENTED;\n"
        "    }\n"
        "    a = ((NumberObject *)left)->value;\n"
        "    b = ((NumberObject *)right)->value;\n"
        "    if (op == Py_LT) result = a < b;\n"
        "    else if (op == Py_LE) result = a <= b;\n"
        "    else if (op == Py_EQ) result = a == b;\n"
        "    else if (op == Py_NE) result = a != b;\n"
        "    else if (op == Py_GT) result = a > b;\n"
        "    else if (op == Py_GE) result = a >= b;\n"
        "    return number_from_long(result);\n"
        "}\n"
        "\n"
        "static PyNumberMethods NumberMethods = {\n"
        "    .nb_add = number_add,\n"
        "    .nb_subtract = number_subtract,\n"
        "    .nb_multiply = number_multiply,\n"
        "    .nb_absolute = number_absolute,\n"
        "    .nb_bool = number_bool,\n"
        "    .nb_true_divide = number_true_divide,\n"
        "};\n"
        "\n"
        "static PyTypeObject NumberType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "typenumdemo.Number",\n'
        "    .tp_basicsize = sizeof(NumberObject),\n"
        "    .tp_as_number = &NumberMethods,\n"
        "    .tp_richcompare = number_richcompare,\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "\n"
        "static PyObject *make_number(PyObject *self, PyObject *args) {\n"
        "    long value = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "l", &value)) return NULL;\n'
        "    return number_from_long(value);\n"
        "}\n"
        "\n"
        "static PyMethodDef Methods[] = {\n"
        '    {"make", make_number, METH_VARARGS, "make a number"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "static PyModuleDef module = {\n"
        '    PyModuleDef_HEAD_INIT, "typenumdemo", NULL, -1, Methods,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_typenumdemo(void) {\n"
        "    if (PyType_Ready(&NumberType) < 0) return NULL;\n"
        "    return PyModule_Create(&module);\n"
        "}\n",
    )


def _compile_sysflags_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PySys_GetObject (batch 15). Mirrors numpy's exact
    # import-time usage (npy_static_data.c:222): PySys_GetObject("flags") then
    # PyObject_GetAttrString(flags, "optimize"). check() returns 7:
    #   1  PySys_GetObject("flags") is non-NULL (a real namespace, not NULL)
    #   2  flags.optimize == 0 (accurate for pcc's no-O compile)
    #   4  an unprovided sys attr returns NULL (honest, not a fake object)
    return _compile_extension(
        tmp_path,
        "sfdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *sf_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *flags = PySys_GetObject("flags");\n'
        "    if (flags == NULL) return PyLong_FromLong(r);\n"
        "    r += 1;\n"
        '    PyObject *opt = PyObject_GetAttrString(flags, "optimize");\n'
        "    if (opt != NULL && PyLong_AsLong(opt) == 0) r += 2;\n"
        '    if (PySys_GetObject("nonexistent_attr_xyz") == NULL) r += 4;\n'
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", sf_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "sfdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_sfdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_genericgetdict_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyObject_GenericGetDict (batch 16). A module-level
    # METH_VARARGS function receives the module as `self`; its __dict__ is the
    # module namespace, which holds the function "check". check() returns 3:
    #   1  PyObject_GenericGetDict(self, NULL) is a non-NULL dict
    #   2  that dict contains this module's own "check" entry
    return _compile_extension(
        tmp_path,
        "gddemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *gd_check(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *d = PyObject_GenericGetDict(self, NULL);\n"
        "    if (d == NULL) return PyLong_FromLong(r);\n"
        "    r += 1;\n"
        '    if (PyDict_GetItemString(d, "check") != NULL) r += 2;\n'
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", gd_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "gddemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_gddemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_seqiter_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PySeqIter_New (batch 17). Build a real sequence
    # iterator over a tuple and drive it through the C-API PyIter_Next path.
    # check() returns 1 when iterating (10,20,30) yields exactly those 3 values
    # (sum 60), then NULL.
    return _compile_extension(
        tmp_path,
        "sidemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *si_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    PyObject *t = PyTuple_New(3);\n"
        "    PyTuple_SetItem(t, 0, PyLong_FromLong(10));\n"
        "    PyTuple_SetItem(t, 1, PyLong_FromLong(20));\n"
        "    PyTuple_SetItem(t, 2, PyLong_FromLong(30));\n"
        "    PyObject *it = PySeqIter_New(t);\n"
        "    if (it == NULL) return PyLong_FromLong(-1);\n"
        "    long sum = 0; int count = 0; PyObject *item;\n"
        "    while ((item = PyIter_Next(it)) != NULL) {\n"
        "        sum += PyLong_AsLong(item);\n"
        "        if (++count > 10) break;\n"
        "    }\n"
        "    return PyLong_FromLong((sum == 60 && count == 3) ? 1 : 0);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", si_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "sidemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_sidemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_pymethod_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyMethod_New (batch 17). A module-level function
    # gets the module as self; PyMethod_New binds the module's own "helper"
    # function to self and the result must be a real callable. check() returns 7:
    #   1  the func object is reachable
    #   2  PyMethod_New(func, self) is non-NULL
    #   4  the bound method is callable
    return _compile_extension(
        tmp_path,
        "mndemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *mn_helper(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args; return PyLong_FromLong(42);\n"
        "}\n"
        "static PyObject *mn_check(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *h = PyObject_GetAttrString(self, "helper");\n'
        "    if (h == NULL) return PyLong_FromLong(r);\n"
        "    r += 1;\n"
        "    PyObject *bm = PyMethod_New(h, self);\n"
        "    if (bm != NULL) r += 2;\n"
        "    if (bm != NULL && PyCallable_Check(bm)) r += 4;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"helper", mn_helper, METH_VARARGS, ""},\n'
        '    {"check", mn_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "mndemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_mndemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_batch18_extension(tmp_path: Path) -> Path:
    # Behavioral regression for batch 18 host symbols (PyDict_Copy, PyDict_Merge,
    # PyUnicode_Format, PyObject_GenericGetAttr/SetAttr). check() returns 15:
    #   1  PyDict_Copy is a distinct dict carrying the original's key
    #   2  PyDict_Merge(override=1) adds the other dict's key
    #   4  PyUnicode_Format("%d", (42,)) == "42"
    #   8  PyObject_GenericSetAttr then GenericGetAttr round-trips on self
    return _compile_extension(
        tmp_path,
        "b18demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *b18_check(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *d = PyDict_New();\n"
        '    PyDict_SetItemString(d, "a", PyLong_FromLong(1));\n'
        "    PyObject *c = PyDict_Copy(d);\n"
        '    if (c != NULL && c != d && PyDict_GetItemString(c, "a") != NULL) r += 1;\n'
        "    PyObject *e = PyDict_New();\n"
        '    PyDict_SetItemString(e, "b", PyLong_FromLong(2));\n'
        '    if (PyDict_Merge(d, e, 1) == 0 && PyDict_GetItemString(d, "b") != NULL) r += 2;\n'
        '    PyObject *fmt = PyUnicode_FromString("%d");\n'
        "    PyObject *ta = PyTuple_New(1);\n"
        "    PyTuple_SetItem(ta, 0, PyLong_FromLong(42));\n"
        "    PyObject *s = PyUnicode_Format(fmt, ta);\n"
        "    if (s != NULL) {\n"
        "        const char *cs = PyUnicode_AsUTF8(s);\n"
        "        if (cs != NULL && cs[0]=='4' && cs[1]=='2' && cs[2]=='\\0') r += 4;\n"
        "    }\n"
        '    PyObject *nm = PyUnicode_FromString("zz");\n'
        "    if (PyObject_GenericSetAttr(self, nm, PyLong_FromLong(9)) == 0) {\n"
        "        PyObject *got = PyObject_GenericGetAttr(self, nm);\n"
        "        if (got != NULL && PyLong_AsLong(got) == 9) r += 8;\n"
        "    }\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", b18_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "b18demo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_b18demo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_batch19_extension(tmp_path: Path) -> Path:
    # Behavioral regression for batch 19 host symbols (PySlice_New,
    # PySlice_GetIndicesEx, PyArg_UnpackTuple, PyDictProxy_New, PyObject_Init).
    # check() returns 31:
    #   1  slice(1,8,2) over len 10 -> start1 stop8 step2 slicelen4
    #   2  slice(None,None,None) over len 5 -> 0,5,1,5
    #   4  PyArg_UnpackTuple((10,20)) -> 10,20
    #   8  PyDictProxy_New(d) is a readable mapping
    #  16  PyObject_Init returns its op (passthrough on a fresh alloc)
    return _compile_extension(
        tmp_path,
        "b19demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *b19_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    Py_ssize_t st, sp, stp, sln;\n"
        "    PyObject *sl = PySlice_New(PyLong_FromLong(1), PyLong_FromLong(8), PyLong_FromLong(2));\n"
        "    if (sl != NULL && PySlice_GetIndicesEx(sl, 10, &st, &sp, &stp, &sln) == 0\n"
        "        && st==1 && sp==8 && stp==2 && sln==4) r += 1;\n"
        "    PyObject *sl2 = PySlice_New(NULL, NULL, NULL);\n"
        "    if (sl2 != NULL && PySlice_GetIndicesEx(sl2, 5, &st, &sp, &stp, &sln) == 0\n"
        "        && st==0 && sp==5 && stp==1 && sln==5) r += 2;\n"
        "    PyObject *t = PyTuple_New(2);\n"
        "    PyTuple_SetItem(t, 0, PyLong_FromLong(10));\n"
        "    PyTuple_SetItem(t, 1, PyLong_FromLong(20));\n"
        "    PyObject *a = NULL, *b = NULL;\n"
        '    if (PyArg_UnpackTuple(t, "x", 2, 2, &a, &b) && a && b\n'
        "        && PyLong_AsLong(a)==10 && PyLong_AsLong(b)==20) r += 4;\n"
        "    PyObject *d = PyDict_New();\n"
        '    PyDict_SetItemString(d, "k", PyLong_FromLong(99));\n'
        "    PyObject *proxy = PyDictProxy_New(d);\n"
        '    if (proxy != NULL && PyDict_GetItemString(proxy, "k") != NULL) r += 8;\n'
        "    PyObject *fresh = _PyObject_New(&PyType_Type);\n"
        "    if (fresh != NULL && PyObject_Init(fresh, &PyType_Type) == fresh) r += 16;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", b19_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "b19demo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_b19demo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_batch2021_extension(tmp_path: Path) -> Path:
    # Behavioral regression for batch 20/21 host surface (PyUnicode_KIND,
    # PyUnicode_READ_CHAR, PyUnicode_1BYTE_DATA, PyNumber_Divmod, _Py_HashDouble) —
    # the symbols that let all 98 numpy _core files compile + link. check()==15:
    #   1  KIND==1 and READ_CHAR reads the right ASCII codepoints
    #   2  1BYTE_DATA is the UTF-8 byte buffer
    #   4  PyNumber_Divmod(17,5) == (3,2)
    #   8  _Py_HashDouble(NULL,2.0)==2 (CPython congruence: hash(2.0)==hash(2))
    return _compile_extension(
        tmp_path,
        "b21demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *b21_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *s = PyUnicode_FromString("Abc");\n'
        "    if (PyUnicode_KIND(s)==1 && PyUnicode_READ_CHAR(s,0)=='A'\n"
        "        && PyUnicode_READ_CHAR(s,2)=='c') r += 1;\n"
        "    const char *data = (const char *)PyUnicode_1BYTE_DATA(s);\n"
        "    if (data != NULL && data[0]=='A' && data[1]=='b') r += 2;\n"
        "    PyObject *dm = PyNumber_Divmod(PyLong_FromLong(17), PyLong_FromLong(5));\n"
        "    if (dm != NULL && PyLong_AsLong(PyTuple_GetItem(dm,0))==3\n"
        "        && PyLong_AsLong(PyTuple_GetItem(dm,1))==2) r += 4;\n"
        "    if (_Py_HashDouble(NULL, 2.0) == 2) r += 8;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", b21_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "b21demo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_b21demo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_unicode_writer_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "writerdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "#include <string.h>\n"
        "static PyObject *writer_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long score = 0;\n"
        "    PyUnicodeWriter *w = PyUnicodeWriter_Create(2);\n"
        '    PyObject *emoji = PyUnicode_FromString("\\xf0\\x9f\\x99\\x82z");\n'
        "    if (w != NULL\n"
        "        && PyUnicodeWriter_WriteChar(w, '\"') == 0\n"
        '        && PyUnicodeWriter_WriteUTF8(w, "h\\xc3\\xa9", 3) == 0\n'
        "        && PyUnicodeWriter_WriteSubstring(w, emoji, 0, 1) == 0\n"
        "        && PyUnicodeWriter_WriteStr(w, PyLong_FromLong(42)) == 0\n"
        "        && PyUnicodeWriter_WriteChar(w, '\"') == 0) {\n"
        "        PyObject *text = PyUnicodeWriter_Finish(w);\n"
        "        Py_ssize_t n = 0;\n"
        "        const char *raw = PyUnicode_AsUTF8AndSize(text, &n);\n"
        '        const char expected[] = "\\"h\\xc3\\xa9\\xf0\\x9f\\x99\\x82" "42\\"";\n'
        "        if (raw != NULL && n == 11 && strcmp(raw, expected) == 0) score += 1;\n"
        "    } else {\n"
        "        PyUnicodeWriter_Discard(w);\n"
        "    }\n"
        "    PyUnicodeWriter *stable = PyUnicodeWriter_Create(0);\n"
        '    if (stable != NULL && PyUnicodeWriter_WriteUTF8(stable, "ok", 2) == 0\n'
        '        && PyUnicodeWriter_WriteUTF8(stable, "\\xff", 1) == -1) {\n'
        "        PyErr_Clear();\n"
        "        PyObject *text = PyUnicodeWriter_Finish(stable);\n"
        "        const char *raw = PyUnicode_AsUTF8(text);\n"
        '        if (raw != NULL && strcmp(raw, "ok") == 0) score += 2;\n'
        "    } else {\n"
        "        PyUnicodeWriter_Discard(stable);\n"
        "    }\n"
        "    const char embedded[] = {'A', '\\0', (char)0xc3, (char)0xa9};\n"
        "    PyObject *mixed = PyUnicode_FromStringAndSize(embedded, 4);\n"
        "    int kind = PyUnicode_KIND(mixed);\n"
        "    void *data = PyUnicode_DATA(mixed);\n"
        "    if (PyUnicode_GET_LENGTH(mixed) == 3\n"
        "        && PyUnicode_READ(kind, data, 0) == 'A'\n"
        "        && PyUnicode_READ(kind, data, 1) == 0\n"
        "        && PyUnicode_READ(kind, data, 2) == 0xe9) score += 4;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", writer_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "writerdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_writerdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_decode_heaptype_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "decodeheapdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "#include <string.h>\n"
        'PyDoc_STRVAR(heap_doc, "module-associated heap type");\n'
        "static PyModuleDef module;\n"
        "static PyObject *HeapType = NULL;\n"
        "static int heap_traverse(PyObject *self, visitproc visit, void *arg) {\n"
        "    Py_VISIT(Py_TYPE(self));\n"
        "    return 0;\n"
        "}\n"
        "static PyType_Slot heap_slots[] = {\n"
        "    {Py_tp_doc, (void *)heap_doc},\n"
        "    {Py_tp_traverse, (void *)heap_traverse},\n"
        "    {Py_tp_new, (void *)PyType_GenericNew},\n"
        "    {0, NULL}\n"
        "};\n"
        "static PyType_Spec heap_spec = {\n"
        '    "decodeheapdemo.Heap", sizeof(PyObject), 0,\n'
        "    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC, heap_slots\n"
        "};\n"
        "static PyObject *decode_check(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        '    PyObject *utf8 = PyUnicode_DecodeUTF8("h\\xc3\\xa9", 3, NULL);\n'
        "    if (utf8 != NULL && PyUnicode_GetLength(utf8) == 2\n"
        '        && strcmp(PyUnicode_AsUTF8(utf8), "h\\xc3\\xa9") == 0) score += 1;\n'
        "    const char latin1[] = {(char)0xe9};\n"
        '    PyObject *latin = PyUnicode_Decode(latin1, 1, "latin-1", NULL);\n'
        '    if (latin != NULL && strcmp(PyUnicode_AsUTF8(latin), "\\xc3\\xa9") == 0) score += 2;\n'
        '    if (PyUnicode_DecodeUTF8("\\xff", 1, NULL) == NULL\n'
        "        && PyErr_ExceptionMatches(PyExc_UnicodeDecodeError)) {\n"
        "        score += 4;\n"
        "        PyErr_Clear();\n"
        "    }\n"
        "    PyObject *dict = PyDict_New();\n"
        '    PyDict_SetItemString(dict, "x", PyLong_FromLong(1));\n'
        "    PyDict_Clear(dict);\n"
        "    if (PyDict_Size(dict) == 0\n"
        "        && PyType_GetModuleByDef((PyTypeObject *)HeapType, &module) == self) score += 8;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", decode_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static int module_exec(PyObject *m) {\n"
        "    HeapType = PyType_FromModuleAndSpec(m, &heap_spec, NULL);\n"
        "    if (HeapType == NULL) return -1;\n"
        '    return PyModule_AddObjectRef(m, "Heap", HeapType);\n'
        "}\n"
        "static PyModuleDef_Slot module_slots[] = {\n"
        "    {Py_mod_exec, module_exec}, {0, NULL}\n"
        "};\n"
        "static PyModuleDef module = {\n"
        '    PyModuleDef_HEAD_INIT, "decodeheapdemo", NULL, 1, M, module_slots\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_decodeheapdemo(void) {\n"
        "    return PyModuleDef_Init(&module);\n"
        "}\n",
    )


def _compile_simplejson_final_api_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "sjfinaldemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *final_check(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    PyObject *target = NULL;\n"
        '    if (!PyArg_ParseTuple(args, "O", &target)) return NULL;\n'
        "    long score = 0;\n"
        '    PyObject *name = PyUnicode_FromString("combine");\n'
        "    PyObject *a = PyLong_FromLong(2);\n"
        "    PyObject *b = PyLong_FromLong(3);\n"
        "    PyObject *called = PyObject_CallMethodObjArgs(target, name, a, b, NULL);\n"
        "    if (called != NULL && PyLong_AsLong(called) == 23) score += 1;\n"
        "    Py_XDECREF(called); Py_DECREF(b); Py_DECREF(a); Py_DECREF(name);\n"
        "    PyObject *empty = PyUnicode_New(0, 127);\n"
        "    if (empty != NULL && PyUnicode_GetLength(empty) == 0) score += 2;\n"
        "    Py_XDECREF(empty);\n"
        "    if (PyUnicode_New(1, 127) == NULL\n"
        "        && PyErr_ExceptionMatches(PyExc_NotImplementedError)) {\n"
        "        score += 4;\n"
        "        PyErr_Clear();\n"
        "    }\n"
        "    if (Py_IS_FINITE(1.0) && !Py_IS_FINITE(INFINITY)) score += 8;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", final_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "sjfinaldemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_sjfinaldemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_compiled_module_import_extension(tmp_path: Path) -> Path:
    site = _compile_extension(
        tmp_path,
        "compiledimportdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *lookup(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        '    PyObject *module = PyImport_ImportModule("depmod");\n'
        "    if (module == NULL) return NULL;\n"
        '    PyObject *again = PyImport_ImportModule("depmod");\n'
        "    if (again == NULL || again != module) {\n"
        "        Py_XDECREF(again); Py_DECREF(module);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "module identity changed");\n'
        "        return NULL;\n"
        "    }\n"
        "    Py_DECREF(again);\n"
        '    PyObject *function = PyObject_GetAttrString(module, "value");\n'
        "    Py_DECREF(module);\n"
        "    if (function == NULL) return NULL;\n"
        "    PyObject *value = PyObject_CallNoArgs(function);\n"
        "    Py_DECREF(function);\n"
        "    return value;\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"lookup", lookup, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "compiledimportdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_compiledimportdemo(void) {\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )
    (site / "depmod.py").write_text(
        "VALUE = 41\n\n" "def value():\n" "    return VALUE\n",
        encoding="utf-8",
    )
    return site


def _compile_builtin_module_import_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "builtinimportdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *lookup(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        '    PyObject *module = PyImport_ImportModule("math");\n'
        "    if (module == NULL) return NULL;\n"
        '    PyObject *function = PyObject_GetAttrString(module, "floor");\n'
        "    Py_DECREF(module);\n"
        "    if (function == NULL) return NULL;\n"
        "    PyObject *argument = PyFloat_FromDouble(7.75);\n"
        "    if (argument == NULL) { Py_DECREF(function); return NULL; }\n"
        "    PyObject *value = PyObject_CallOneArg(function, argument);\n"
        "    Py_DECREF(argument); Py_DECREF(function);\n"
        "    return value;\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"lookup", lookup, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "builtinimportdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_builtinimportdemo(void) {\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )


def _compile_builtin_module_graph_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "builtinmodulegraphdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        '    const char *modules[] = {"math", "sys", "time", "gc", "copy"};\n'
        '    const char *attrs[] = {"floor", "getdefaultencoding", "perf_counter", "collect", "copy"};\n'
        "    long found = 0;\n"
        "    for (long i = 0; i < 5; i++) {\n"
        "        PyObject *module = PyImport_ImportModule(modules[i]);\n"
        "        if (module == NULL) return NULL;\n"
        "        PyObject *attr = PyObject_GetAttrString(module, attrs[i]);\n"
        "        Py_DECREF(module);\n"
        "        if (attr == NULL) return NULL;\n"
        "        found += 1;\n"
        "        Py_DECREF(attr);\n"
        "    }\n"
        "    return PyLong_FromLong(found);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "builtinmodulegraphdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_builtinmodulegraphdemo(void) {\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )


def _compile_batch22_extension(tmp_path: Path) -> Path:
    # Behavioral regression for batch 22 host symbols (PyLong_FromUnicodeObject,
    # PyFloat_FromString, PyLong_AsLongLongAndOverflow, PySlice_AdjustIndices) —
    # introduced by numpy's now-compiling C++ umath layer. check()==15.
    return _compile_extension(
        tmp_path,
        "b22demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *b22_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *li = PyLong_FromUnicodeObject(PyUnicode_FromString("42"), 10);\n'
        "    if (li != NULL && PyLong_AsLong(li) == 42) r += 1;\n"
        '    PyObject *fl = PyFloat_FromString(PyUnicode_FromString("3.5"));\n'
        "    if (fl != NULL && PyFloat_AsDouble(fl) == 3.5) r += 2;\n"
        "    int ov = 5;\n"
        "    long long v = PyLong_AsLongLongAndOverflow(PyLong_FromLong(7), &ov);\n"
        "    if (v == 7 && ov == 0) r += 4;\n"
        "    Py_ssize_t st = 2, sp = 8;\n"
        "    Py_ssize_t sln = PySlice_AdjustIndices(10, &st, &sp, 2);\n"
        "    if (sln == 3 && st == 2 && sp == 8) r += 8;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", b22_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "b22demo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_b22demo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_settype_extension(tmp_path: Path) -> Path:
    # Behavioral regression for Py_SET_TYPE -> pcc_capi_set_type (batch 23) — the
    # sole symbol that was undefined when link-testing numpy's full _core.
    # check() returns 3: a fresh Foo instance reports type Foo, then after
    # Py_SET_TYPE(o, &BarType) reports type Bar.
    return _compile_extension(
        tmp_path,
        "stdemo2",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "typedef struct { PyObject_HEAD long v; } FooObject;\n"
        "static PyTypeObject FooType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "stdemo2.Foo", .tp_basicsize = sizeof(FooObject),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyTypeObject BarType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "stdemo2.Bar", .tp_basicsize = sizeof(FooObject),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyObject *st2_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *o = PyType_GenericNew(&FooType, NULL, NULL);\n"
        "    if (o == NULL) return PyLong_FromLong(-1);\n"
        "    if (Py_TYPE(o) == &FooType) r += 1;\n"
        "    Py_SET_TYPE(o, &BarType);\n"
        "    if (Py_TYPE(o) == &BarType) r += 2;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", st2_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "stdemo2", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_stdemo2(void) {\n"
        "    if (PyType_Ready(&FooType) < 0) return NULL;\n"
        "    if (PyType_Ready(&BarType) < 0) return NULL;\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )


def _compile_multiphase_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PEP 489 multi-phase init: PyInit returns
    # PyModuleDef_Init(&def) with a Py_mod_exec slot; the loader must build the
    # module + run the exec slot (where numpy's _multiarray_umath registers its
    # types). The exec slot here registers `answer` = 42. main asserts
    # mpdemo.answer == 42, proving the slot ran.
    return _compile_extension(
        tmp_path,
        "mpdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static int mp_exec(PyObject *m) {\n"
        '    PyModule_AddObject(m, "answer", PyLong_FromLong(42));\n'
        "    return 0;\n"
        "}\n"
        "static PyModuleDef_Slot mp_slots[] = {\n"
        "    {Py_mod_exec, (void *)mp_exec}, {0, NULL}\n"
        "};\n"
        "static PyModuleDef mpmod = {\n"
        '    PyModuleDef_HEAD_INIT, "mpdemo", NULL, 0, NULL, mp_slots, NULL, NULL, NULL\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_mpdemo(void) { return PyModuleDef_Init(&mpmod); }\n",
    )


def _compile_capsule_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "capsdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static long secret = 37;\n"
        "static long context_secret = 11;\n"
        "static long destructor_secret = 13;\n"
        "static long destructor_hits = 0;\n"
        "\n"
        "static void capsdemo_destructor(PyObject *capsule) {\n"
        '    long *ptr = (long *)PyCapsule_GetPointer(capsule, "capsdemo.destruct");\n'
        "    if (ptr != NULL) destructor_hits += *ptr;\n"
        "}\n"
        "\n"
        "static PyObject *capsdemo_value(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *capsule = PyCapsule_New(&secret, "capsdemo.secret", NULL);\n'
        "    if (capsule == NULL) return NULL;\n"
        '    if (!PyCapsule_IsValid(capsule, "capsdemo.secret")) {\n'
        "        Py_DECREF(capsule);\n"
        "        return NULL;\n"
        "    }\n"
        '    long *ptr = (long *)PyCapsule_GetPointer(capsule, "capsdemo.secret");\n'
        "    Py_DECREF(capsule);\n"
        "    if (ptr == NULL) return NULL;\n"
        "    return PyLong_FromLong(*ptr);\n"
        "}\n"
        "\n"
        "static PyObject *capsdemo_context_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *capsule = PyCapsule_New(&secret, "capsdemo.secret", NULL);\n'
        "    if (capsule == NULL) return NULL;\n"
        "    if (!PyCapsule_CheckExact(capsule)) {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "capsule check failed");\n'
        "        return NULL;\n"
        "    }\n"
        "    if (PyCapsule_GetContext(capsule) != NULL || PyErr_Occurred()) {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "unexpected initial capsule context");\n'
        "        return NULL;\n"
        "    }\n"
        "    if (PyCapsule_SetContext(capsule, &context_secret) != 0) {\n"
        "        Py_DECREF(capsule);\n"
        "        return NULL;\n"
        "    }\n"
        "    long *ctx = (long *)PyCapsule_GetContext(capsule);\n"
        "    if (ctx == NULL || *ctx != 11) {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "capsule context mismatch");\n'
        "        return NULL;\n"
        "    }\n"
        '    if (PyCapsule_SetName(capsule, "capsdemo.renamed") != 0) {\n'
        "        Py_DECREF(capsule);\n"
        "        return NULL;\n"
        "    }\n"
        '    if (PyCapsule_IsValid(capsule, "capsdemo.secret")) {\n'
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "old capsule name still valid");\n'
        "        return NULL;\n"
        "    }\n"
        '    long *ptr = (long *)PyCapsule_GetPointer(capsule, "capsdemo.renamed");\n'
        "    if (ptr == NULL || *ptr != 37) {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "renamed capsule pointer mismatch");\n'
        "        return NULL;\n"
        "    }\n"
        "    const char *name = PyCapsule_GetName(capsule);\n"
        "    if (name == NULL || name[9] != 'r') {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "renamed capsule name mismatch");\n'
        "        return NULL;\n"
        "    }\n"
        "    Py_DECREF(capsule);\n"
        "    return PyLong_FromLong(*ptr + *ctx);\n"
        "}\n"
        "\n"
        "static PyObject *capsdemo_destructor_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    destructor_hits = 0;\n"
        '    PyObject *capsule = PyCapsule_New(&destructor_secret, "capsdemo.destruct", capsdemo_destructor);\n'
        "    if (capsule == NULL) return NULL;\n"
        "    Py_DECREF(capsule);\n"
        "    return PyLong_FromLong(destructor_hits);\n"
        "}\n"
        "\n"
        "static PyObject *capsdemo_pointer_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        '    PyObject *capsule = PyCapsule_New(&secret, "capsdemo.ptr", capsdemo_destructor);\n'
        "    if (capsule == NULL) return NULL;\n"
        "    if (PyCapsule_GetDestructor(capsule) == capsdemo_destructor) score += 1;\n"
        "    if (PyCapsule_SetPointer(capsule, &context_secret) == 0) score += 2;\n"
        '    long *ptr = (long *)PyCapsule_GetPointer(capsule, "capsdemo.ptr");\n'
        "    if (ptr != NULL && *ptr == 11) score += 4;\n"
        "    if (PyCapsule_SetDestructor(capsule, NULL) == 0) score += 8;\n"
        "    if (PyCapsule_GetDestructor(capsule) == NULL && PyErr_Occurred() == NULL) score += 16;\n"
        "    Py_DECREF(capsule);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyMethodDef CapsDemoMethods[] = {\n"
        '    {"value", capsdemo_value, METH_VARARGS, "read through a capsule"},\n'
        '    {"context_score", capsdemo_context_score, METH_VARARGS, "read capsule context"},\n'
        '    {"destructor_score", capsdemo_destructor_score, METH_VARARGS, "run capsule destructor"},\n'
        '    {"pointer_score", capsdemo_pointer_score, METH_VARARGS, "set/get capsule pointer and destructor"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef capsmodule = {\n"
        '    PyModuleDef_HEAD_INIT, "capsdemo", NULL, -1, CapsDemoMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_capsdemo(void) {\n"
        "    return PyModule_Create(&capsmodule);\n"
        "}\n",
    )


def _compile_capsule_import_extensions(tmp_path: Path) -> Path:
    site = _compile_extension(
        tmp_path,
        "capiprov",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static long secret = 73;\n"
        "static long capiprov_add_bias(long value) { return value + 78; }\n"
        "static void *api_table[] = {(void *)capiprov_add_bias, &secret};\n"
        "\n"
        "static PyMethodDef ProviderMethods[] = {\n"
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef providermodule = {\n"
        '    PyModuleDef_HEAD_INIT, "capiprov", NULL, -1, ProviderMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_capiprov(void) {\n"
        "    PyObject *module = PyModule_Create(&providermodule);\n"
        "    if (module == NULL) return NULL;\n"
        '    PyObject *capsule = PyCapsule_New(&secret, "capiprov._API", NULL);\n'
        "    if (capsule == NULL) return NULL;\n"
        '    if (PyModule_AddObject(module, "_API", capsule) != 0) return NULL;\n'
        '    capsule = PyCapsule_New(api_table, "capiprov._TABLE", NULL);\n'
        "    if (capsule == NULL) return NULL;\n"
        '    if (PyModule_AddObject(module, "_TABLE", capsule) != 0) return NULL;\n'
        "    return module;\n"
        "}\n",
    )
    _compile_extension(
        tmp_path,
        "capicons",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static PyObject *capicons_value(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    long *ptr = (long *)PyCapsule_Import("capiprov._API", 0);\n'
        "    if (ptr == NULL) return NULL;\n"
        "    return PyLong_FromLong(*ptr);\n"
        "}\n"
        "\n"
        "static PyObject *capicons_table_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    void **api = (void **)PyCapsule_Import("capiprov._TABLE", 0);\n'
        "    if (api == NULL) return NULL;\n"
        "    long (*add_bias)(long) = (long (*)(long))api[0];\n"
        "    long *ptr = (long *)api[1];\n"
        "    if (add_bias == NULL || ptr == NULL) return NULL;\n"
        "    return PyLong_FromLong(add_bias(*ptr));\n"
        "}\n"
        "\n"
        "static PyMethodDef ConsumerMethods[] = {\n"
        '    {"value", capicons_value, METH_VARARGS, "read provider API capsule"},\n'
        '    {"table_score", capicons_table_score, METH_VARARGS, "read provider API table"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef consumermodule = {\n"
        '    PyModuleDef_HEAD_INIT, "capicons", NULL, -1, ConsumerMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_capicons(void) {\n"
        "    return PyModule_Create(&consumermodule);\n"
        "}\n",
    )
    return site


def _compile_numpy_capi_provider_extensions(tmp_path: Path) -> Path:
    site = _compile_extension_source(
        tmp_path,
        "pccnpapi",
        REPO / "utils" / "pcc_numpy_capi_provider" / "pccnpapi.c",
    )
    _compile_extension(
        tmp_path,
        "pccnpcons",
        (
            Path(__file__).resolve().parent
            / "fixtures"
            / "native_extensions"
            / "pccnpcons.c"
        ).read_text(encoding="utf-8"),
    )
    return site


def _compile_feature_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "featuredemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static long init_count = 0;\n"
        "\n"
        "static PyObject *feature_count(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    return PyLong_FromLong(init_count);\n"
        "}\n"
        "\n"
        "static PyObject *feature_echo(PyObject *self, PyObject *args) {\n"
        "    const char *value = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "s", &value)) return NULL;\n'
        "    return PyUnicode_FromString(value);\n"
        "}\n"
        "\n"
        "static PyObject *feature_add(PyObject *self, PyObject *args) {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "ll", &a, &b)) return NULL;\n'
        "    return PyLong_FromLong(a + b);\n"
        "}\n"
        "\n"
        "static PyObject *feature_return_none(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    Py_RETURN_NONE;\n"
        "}\n"
        "\n"
        "static PyObject *feature_return_true(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    Py_RETURN_TRUE;\n"
        "}\n"
        "\n"
        "static PyObject *feature_return_false(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    Py_RETURN_FALSE;\n"
        "}\n"
        "\n"
        "static PyObject *feature_return_notimplemented(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    Py_RETURN_NOTIMPLEMENTED;\n"
        "}\n"
        "\n"
        "static PyObject *feature_identity_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    score += Py_IsNone(Py_None) ? 1 : 0;\n"
        "    score += Py_IsTrue(Py_True) ? 10 : 0;\n"
        "    score += Py_IsFalse(Py_False) ? 100 : 0;\n"
        "    score += Py_Is(Py_NotImplemented, Py_NotImplemented) ? 1000 : 0;\n"
        "    score += !Py_Is(Py_None, Py_False) ? 10000 : 0;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *feature_fail(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyErr_SetString(PyExc_RuntimeError, "feature method failed");\n'
        "    return NULL;\n"
        "}\n"
        "\n"
        "static PyMethodDef FeatureMethods[] = {\n"
        '    {"count", feature_count, METH_VARARGS, "return init count"},\n'
        '    {"echo", feature_echo, METH_VARARGS, "echo a string"},\n'
        '    {"add", feature_add, METH_VARARGS, "add two ints"},\n'
        '    {"return_none", feature_return_none, METH_VARARGS, "return None"},\n'
        '    {"return_true", feature_return_true, METH_VARARGS, "return True"},\n'
        '    {"return_false", feature_return_false, METH_VARARGS, "return False"},\n'
        '    {"return_notimplemented", feature_return_notimplemented, METH_VARARGS, "return NotImplemented"},\n'
        '    {"identity_score", feature_identity_score, METH_VARARGS, "Py_Is macro smoke"},\n'
        '    {"fail", feature_fail, METH_VARARGS, "raise a RuntimeError"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef featuremodule = {\n"
        '    PyModuleDef_HEAD_INIT, "featuredemo", NULL, -1, FeatureMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_featuredemo(void) {\n"
        "    init_count += 1;\n"
        "    return PyModule_Create(&featuremodule);\n"
        "}\n",
    )


def _compile_buffer_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "bufdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static PyObject *buffer_sum(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    Py_buffer view;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    if (PyObject_GetBuffer(obj, &view, PyBUF_SIMPLE) != 0) return NULL;\n"
        "    unsigned char *data = (unsigned char *)view.buf;\n"
        "    for (Py_ssize_t i = 0; i < view.len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    PyBuffer_Release(&view);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_writable(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    Py_buffer view;\n"
        "    int ok = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    if (PyObject_GetBuffer(obj, &view, PyBUF_WRITABLE) != 0) {\n"
        "        PyErr_Clear();\n"
        "        return PyLong_FromLong(0);\n"
        "    }\n"
        "    ok = view.readonly == 0;\n"
        "    PyBuffer_Release(&view);\n"
        "    return PyLong_FromLong(ok);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_metadata_score(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    Py_buffer view;\n"
        "    long score = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    if (PyObject_GetBuffer(obj, &view, PyBUF_STRIDES | PyBUF_FORMAT) != 0) return NULL;\n"
        "    score += view.ndim == 1 ? 1 : 0;\n"
        "    score += view.itemsize == 1 ? 10 : 0;\n"
        "    score += view.shape != NULL ? view.shape[0] * 100 : 0;\n"
        "    score += view.strides != NULL ? view.strides[0] * 1000 : 0;\n"
        "    score += view.format != NULL && view.format[0] == 'B' ? 10000 : 0;\n"
        "    score += view.readonly ? 100000 : 200000;\n"
        "    score += view.len * 1000000;\n"
        "    PyBuffer_Release(&view);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_view_sum(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    PyObject *mv = NULL;\n"
        "    Py_buffer view;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    mv = PyMemoryView_FromObject(obj);\n"
        "    if (mv == NULL) return NULL;\n"
        "    if (PyObject_GetBuffer(mv, &view, PyBUF_SIMPLE) != 0) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    unsigned char *data = (unsigned char *)view.buf;\n"
        "    for (Py_ssize_t i = 0; i < view.len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    PyBuffer_Release(&view);\n"
        "    Py_DECREF(mv);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_from_memory_sum(PyObject *self, PyObject *args) {\n"
        "    static char raw[] = {7, 8, 9};\n"
        "    PyObject *mv = NULL;\n"
        "    Py_buffer view;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    mv = PyMemoryView_FromMemory(raw, 3, PyBUF_READ);\n"
        "    if (mv == NULL) return NULL;\n"
        "    if (PyObject_GetBuffer(mv, &view, PyBUF_SIMPLE) != 0) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    unsigned char *data = (unsigned char *)view.buf;\n"
        "    for (Py_ssize_t i = 0; i < view.len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    PyBuffer_Release(&view);\n"
        "    Py_DECREF(mv);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_from_memory_write_sum(PyObject *self, PyObject *args) {\n"
        "    static char raw[] = {1, 2, 3};\n"
        "    PyObject *mv = NULL;\n"
        "    Py_buffer view;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    mv = PyMemoryView_FromMemory(raw, 3, PyBUF_WRITE);\n"
        "    if (mv == NULL) return NULL;\n"
        "    if (PyObject_GetBuffer(mv, &view, PyBUF_WRITABLE) != 0) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    unsigned char *data = (unsigned char *)view.buf;\n"
        "    data[0] = 10;\n"
        "    for (Py_ssize_t i = 0; i < view.len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    PyBuffer_Release(&view);\n"
        "    Py_DECREF(mv);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_memoryview_inspect_score(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    PyObject *mv = NULL;\n"
        "    PyObject *base = NULL;\n"
        "    Py_buffer *view = NULL;\n"
        "    long score = 0;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    mv = PyMemoryView_FromObject(obj);\n"
        "    if (mv == NULL) return NULL;\n"
        "    score += PyMemoryView_Check(mv) ? 1 : 0;\n"
        "    score += !PyMemoryView_Check(obj) ? 10 : 0;\n"
        "    base = PyMemoryView_GET_BASE(mv);\n"
        "    if (base == NULL) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    score += base == obj ? 100 : 0;\n"
        "    view = PyMemoryView_GET_BUFFER(mv);\n"
        "    if (view == NULL) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    unsigned char *data = (unsigned char *)view->buf;\n"
        "    for (Py_ssize_t i = 0; i < view->len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    score += view->len * 1000;\n"
        "    score += view->ndim == 1 ? 10000 : 0;\n"
        "    score += view->shape != NULL && view->shape[0] == view->len ? 100000 : 0;\n"
        "    score += view->strides != NULL && view->strides[0] == 1 ? 1000000 : 0;\n"
        "    score += view->format != NULL && view->format[0] == 'B' ? 10000000 : 0;\n"
        "    score += view->readonly == 0 ? 100000000 : 0;\n"
        "    score += total * 100000000;\n"
        "    Py_DECREF(mv);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_two_live_views_score(PyObject *self, PyObject *args) {\n"
        "    PyObject *left = NULL;\n"
        "    PyObject *right = NULL;\n"
        "    PyObject *left_mv = NULL;\n"
        "    PyObject *right_mv = NULL;\n"
        "    Py_buffer *left_view = NULL;\n"
        "    Py_buffer *right_view = NULL;\n"
        "    long score = 0;\n"
        "    (void)self;\n"
        "    if (!PyArg_ParseTuple(args, \"OO\", &left, &right)) return NULL;\n"
        "    left_mv = PyMemoryView_FromObject(left);\n"
        "    if (left_mv == NULL) return NULL;\n"
        "    right_mv = PyMemoryView_FromObject(right);\n"
        "    if (right_mv == NULL) {\n"
        "        Py_DECREF(left_mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    left_view = PyMemoryView_GET_BUFFER(left_mv);\n"
        "    right_view = PyMemoryView_GET_BUFFER(right_mv);\n"
        "    if (left_view != NULL && right_view != NULL) score += 1;\n"
        "    if (left_view != right_view) score += 2;\n"
        "    if (left_view != NULL && left_view->obj == left) score += 4;\n"
        "    if (right_view != NULL && right_view->obj == right) score += 8;\n"
        "    if (left_view != NULL && left_view->len == 3\n"
        "            && ((unsigned char *)left_view->buf)[0] == 'A'\n"
        "            && ((unsigned char *)left_view->buf)[2] == 'C') score += 16;\n"
        "    if (right_view != NULL && right_view->len == 3\n"
        "            && ((unsigned char *)right_view->buf)[0] == 'x'\n"
        "            && ((unsigned char *)right_view->buf)[2] == 'z') score += 32;\n"
        "    if (PyMemoryView_GET_BASE(left_mv) == left\n"
        "            && PyMemoryView_GET_BASE(right_mv) == right) score += 64;\n"
        "    Py_DECREF(right_mv);\n"
        "    Py_DECREF(left_mv);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_check_score(PyObject *self, PyObject *args) {\n"
        "    PyObject *bytes_obj = NULL;\n"
        "    PyObject *bytearray_obj = NULL;\n"
        "    PyObject *memoryview_obj = NULL;\n"
        "    PyObject *other = NULL;\n"
        "    long score = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "OOOO", &bytes_obj, &bytearray_obj, &memoryview_obj, &other)) return NULL;\n'
        "    score += PyObject_CheckBuffer(bytes_obj) ? 1 : 0;\n"
        "    score += PyObject_CheckBuffer(bytearray_obj) ? 10 : 0;\n"
        "    score += PyObject_CheckBuffer(memoryview_obj) ? 100 : 0;\n"
        "    score += !PyObject_CheckBuffer(other) ? 1000 : 0;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyMethodDef BufferMethods[] = {\n"
        '    {"sum", buffer_sum, METH_VARARGS, "sum bytes through the buffer API"},\n'
        '    {"writable", buffer_writable, METH_VARARGS, "request a writable buffer"},\n'
        '    {"metadata_score", buffer_metadata_score, METH_VARARGS, "buffer metadata smoke"},\n'
        '    {"view_sum", buffer_view_sum, METH_VARARGS, "sum through PyMemoryView_FromObject"},\n'
        '    {"from_memory_sum", buffer_from_memory_sum, METH_VARARGS, "sum PyMemoryView_FromMemory"},\n'
        '    {"from_memory_write_sum", buffer_from_memory_write_sum, METH_VARARGS, "writable PyMemoryView_FromMemory"},\n'
        '    {"memoryview_inspect_score", buffer_memoryview_inspect_score, METH_VARARGS, "memoryview inspect macro smoke"},\n'
        '    {"two_live_views_score", buffer_two_live_views_score, METH_VARARGS, "two live memoryviews keep distinct buffers"},\n'
        '    {"check_score", buffer_check_score, METH_VARARGS, "PyObject_CheckBuffer smoke"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef buffermodule = {\n"
        '    PyModuleDef_HEAD_INIT, "bufdemo", NULL, -1, BufferMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_bufdemo(void) {\n"
        "    return PyModule_Create(&buffermodule);\n"
        "}\n",
    )


def _compile_call_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "calldemo",
        "#include <Python.h>\n"
        "\n"
        "static PyObject *call_noargs(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    return PyObject_Call(callable, NULL, NULL);\n"
        "}\n"
        "\n"
        "static PyObject *call_object_noargs(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    return PyObject_CallObject(callable, NULL);\n"
        "}\n"
        "\n"
        "static PyObject *call_one_arg(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    PyObject *arg = NULL;\n"
        "    PyObject *result = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    arg = PyLong_FromLong(8);\n"
        "    if (arg == NULL) return NULL;\n"
        "    result = PyObject_CallFunctionObjArgs(callable, arg, NULL);\n"
        "    Py_DECREF(arg);\n"
        "    return result;\n"
        "}\n"
        "\n"
        "static PyObject *call_bad_args(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    PyObject *bad_args = NULL;\n"
        "    PyObject *result = NULL;\n"
        "    int ok = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    bad_args = PyLong_FromLong(1);\n"
        "    result = PyObject_Call(callable, bad_args, NULL);\n"
        "    if (result != NULL) {\n"
        "        Py_DECREF(result);\n"
        "        Py_DECREF(bad_args);\n"
        "        return PyLong_FromLong(0);\n"
        "    }\n"
        "    ok = PyErr_Occurred() != NULL;\n"
        "    PyErr_Clear();\n"
        "    Py_DECREF(bad_args);\n"
        "    return PyLong_FromLong(ok);\n"
        "}\n"
        "\n"
        "static PyObject *call_is_none(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    PyObject *result = NULL;\n"
        "    int is_none = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    result = PyObject_Call(callable, NULL, NULL);\n"
        "    if (result == NULL) return NULL;\n"
        "    is_none = result == Py_None;\n"
        "    Py_DECREF(result);\n"
        "    return PyLong_FromLong(is_none);\n"
        "}\n"
        "\n"
        "static PyMethodDef CallMethods[] = {\n"
        '    {"call0", call_noargs, METH_VARARGS, "call with no args"},\n'
        '    {"call_object0", call_object_noargs, METH_VARARGS, "call object with no args"},\n'
        '    {"call_one", call_one_arg, METH_VARARGS, "call with one object arg"},\n'
        '    {"bad_args", call_bad_args, METH_VARARGS, "call with invalid args"},\n'
        '    {"call_is_none", call_is_none, METH_VARARGS, "call and check for None"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef callmodule = {\n"
        '    PyModuleDef_HEAD_INIT, "calldemo", NULL, -1, CallMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_calldemo(void) {\n"
        "    return PyModule_Create(&callmodule);\n"
        "}\n",
    )


def _compile_generic_vectorcall_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "genericvcdemo",
        "#include <Python.h>\n"
        "static PyObject *target(PyObject *self, PyObject *args) {\n"
        "    PyObject *arg = NULL;\n"
        '    if (!PyArg_ParseTuple(args, "O", &arg)) return NULL;\n'
        "    return PyLong_FromLong(arg == self);\n"
        "}\n"
        "static PyObject *call_one(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = PyObject_GetAttrString(self, \"target\");\n"
        "    PyObject *result = NULL;\n"
        "    (void)args;\n"
        "    if (callable == NULL) return NULL;\n"
        "    result = PyObject_CallOneArg(callable, self);\n"
        "    Py_DECREF(callable);\n"
        "    return result;\n"
        "}\n"
        "static PyObject *vectorcall_one(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = PyObject_GetAttrString(self, \"target\");\n"
        "    PyObject *values[1] = {self};\n"
        "    PyObject *result = NULL;\n"
        "    (void)args;\n"
        "    if (callable == NULL) return NULL;\n"
        "    result = PyObject_Vectorcall(callable, values, 1, NULL);\n"
        "    Py_DECREF(callable);\n"
        "    return result;\n"
        "}\n"
        "static PyObject *vectorcall_method_one(PyObject *self, PyObject *args) {\n"
        "    PyObject *name = PyUnicode_FromString(\"target\");\n"
        "    PyObject *values[2] = {self, self};\n"
        "    PyObject *result = NULL;\n"
        "    (void)args;\n"
        "    if (name == NULL) return NULL;\n"
        "    result = PyObject_VectorcallMethod(name, values, 2, NULL);\n"
        "    Py_DECREF(name);\n"
        "    return result;\n"
        "}\n"
        "static PyMethodDef Methods[] = {\n"
        '    {"target", target, METH_VARARGS, "one-argument target"},\n'
        '    {"call_one", call_one, METH_NOARGS, "PyObject_CallOneArg reference"},\n'
        '    {"vectorcall_one", vectorcall_one, METH_NOARGS, "generic vectorcall"},\n'
        '    {"vectorcall_method_one", vectorcall_method_one, METH_NOARGS, "generic method vectorcall"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "static PyModuleDef module = {\n"
        '    PyModuleDef_HEAD_INIT, "genericvcdemo", NULL, -1, Methods,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_genericvcdemo(void) { return PyModule_Create(&module); }\n",
    )


def _compile_object_api_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "objectapi",
        (
            Path(__file__).resolve().parent
            / "fixtures"
            / "native_extensions"
            / "objectapi.c"
        ).read_text(encoding="utf-8"),
    )


def _compile_errno_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "errnoapi",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "#include <errno.h>\n"
        "extern int *__error(void);\n"
        "static PyObject *fetch_message(PyObject *filename, int code) {\n"
        "  PyObject *type = NULL, *value = NULL, *traceback = NULL;\n"
        "  *__error() = code;\n"
        "  if (filename == NULL) PyErr_SetFromErrno(PyExc_OSError);\n"
        "  else PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, filename);\n"
        "  PyErr_Fetch(&type, &value, &traceback);\n"
        "  if (value == NULL) { Py_XDECREF(type); Py_XDECREF(traceback); return NULL; }\n"
        "  PyObject *text = PyObject_Str(value);\n"
        "  Py_XDECREF(type); Py_DECREF(value); Py_XDECREF(traceback);\n"
        "  return text;\n"
        "}\n"
        "static PyObject *message(PyObject *self, PyObject *args) {\n"
        "  (void)self; (void)args; return fetch_message(NULL, ENOENT);\n"
        "}\n"
        "static PyObject *filename_message(PyObject *self, PyObject *args) {\n"
        "  (void)self; (void)args;\n"
        "  PyObject *name = PyUnicode_FromString(\"missing.dat\");\n"
        "  if (name == NULL) return NULL;\n"
        "  PyObject *result = fetch_message(name, ENOENT);\n"
        "  Py_DECREF(name); return result;\n"
        "}\n"
        "static PyObject *unknown_message(PyObject *self, PyObject *args) {\n"
        "  (void)self; (void)args; return fetch_message(NULL, 9999);\n"
        "}\n"
        "static PyMethodDef Methods[] = {\n"
        "  {\"message\", message, METH_NOARGS, NULL},\n"
        "  {\"filename_message\", filename_message, METH_NOARGS, NULL},\n"
        "  {\"unknown_message\", unknown_message, METH_NOARGS, NULL},\n"
        "  {NULL, NULL, 0, NULL}\n"
        "};\n"
        "static PyModuleDef module = { PyModuleDef_HEAD_INIT, \"errnoapi\", NULL, -1, Methods };\n"
        "PyMODINIT_FUNC PyInit_errnoapi(void) { return PyModule_Create(&module); }\n",
    )


def _compile_missing_init_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "badinit",
        "#include <Python.h>\n" "int not_the_init_symbol(void) { return 0; }\n",
    )


def _compile_null_init_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "nullinit",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "PyMODINIT_FUNC PyInit_nullinit(void) {\n"
        '    PyErr_SetString(PyExc_RuntimeError, "init failed intentionally");\n'
        "    return NULL;\n"
        "}\n",
    )


def _compile_retry_init_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "retryinit",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "#include <stdlib.h>\n"
        "\n"
        "#ifdef _WIN32\n"
        "extern int _putenv(const char *);\n"
        "#else\n"
        "extern int unsetenv(const char *);\n"
        "#endif\n"
        "\n"
        "static long successful_inits = 0;\n"
        "\n"
        "static PyObject *retryinit_count(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    return PyLong_FromLong(successful_inits);\n"
        "}\n"
        "\n"
        "static PyMethodDef RetryMethods[] = {\n"
        '    {"count", retryinit_count, METH_VARARGS, "return successful init count"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef retrymodule = {\n"
        '    PyModuleDef_HEAD_INIT, "retryinit", NULL, -1, RetryMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_retryinit(void) {\n"
        '    const char *fail_once = getenv("PCC_RETRYINIT_FAIL_ONCE");\n'
        "    if (fail_once != NULL && fail_once[0] != '\\0') {\n"
        "#ifdef _WIN32\n"
        '        _putenv("PCC_RETRYINIT_FAIL_ONCE=");\n'
        "#else\n"
        '        unsetenv("PCC_RETRYINIT_FAIL_ONCE");\n'
        "#endif\n"
        '        PyErr_SetString(PyExc_RuntimeError, "retry init failed once");\n'
        "        return NULL;\n"
        "    }\n"
        "    successful_inits += 1;\n"
        "    return PyModule_Create(&retrymodule);\n"
        "}\n",
    )


def _compile_silent_null_init_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "silentnullinit",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "PyMODINIT_FUNC PyInit_silentnullinit(void) {\n"
        "    return NULL;\n"
        "}\n",
    )


def _compile_numpy_25_capi_batch_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "capi25",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyTypeObject SequenceType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "capi25.Sequence",\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_SEQUENCE,\n"
        "};\n"
        "static PyObject *probe(PyObject *self, PyObject *args) {\n"
        "    long score = 0;\n"
        "    PyObject *zero = PyLong_FromLong(0);\n"
        "    PyObject *one = PyLong_FromLong(1);\n"
        '    PyObject *text = PyUnicode_FromString("ok");\n'
        "    PyObject *copy = NULL;\n"
        "    (void)self; (void)args;\n"
        "    if (PYMEM_DOMAIN_RAW == 0) score += 1;\n"
        "    if (PyLong_IsZero(zero) == 1 && PyLong_IsZero(one) == 0) score += 2;\n"
        "    copy = PyUnicode_FromObject(text);\n"
        "    if (copy == text) score += 4;\n"
        "    PyType_Modified(&SequenceType);\n"
        "    score += 8;\n"
        "    if (SequenceType.tp_flags & Py_TPFLAGS_SEQUENCE) score += 16;\n"
        "    if (PyLong_IsZero(text) == -1 && PyErr_ExceptionMatches(PyExc_TypeError)) score += 32;\n"
        "    PyErr_Clear();\n"
        "    Py_XDECREF(copy); Py_DECREF(text); Py_DECREF(one); Py_DECREF(zero);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "static PyMethodDef Methods[] = {\n"
        '    {"probe", probe, METH_VARARGS, "probe NumPy 2.5 C-API batch"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "static PyModuleDef module = {\n"
        '    PyModuleDef_HEAD_INIT, "capi25", NULL, -1, Methods,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_capi25(void) { return PyModule_Create(&module); }\n",
    )


def _compile_main(
    site: Path, main: Path, exe: Path, *, runtime_cc: str = "cc"
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    env["PCC_RUNTIME_CC"] = runtime_cc
    return subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )


def _run_main(
    site: Path, exe: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    if extra_env is not None:
        env.update(extra_env)
    return subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )


def test_pcc_native_extension_import_runs_under_self_backend_no_libpython(tmp_path):
    site = _compile_demo_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import demo\n" "print(demo.add(2, 3))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "5"


def test_relative_extension_import_publishes_wrapper_module_binding(tmp_path):
    site = _compile_extension(
        tmp_path,
        "_native",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static struct PyModuleDef module = {\n"
        "    PyModuleDef_HEAD_INIT, \"_native\", NULL, -1, NULL,\n"
        "};\n"
        "PyMODINIT_FUNC PyInit__native(void) { return PyModule_Create(&module); }\n",
    )
    package = site / "pkg"
    package.mkdir()
    (site / "_native.so").replace(package / "_native.so")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "wrapper.py").write_text(
        "from . import _native\n"
        + "".join(f"module_value_{index} = {index}\n" for index in range(200)),
        encoding="utf-8",
    )
    main = tmp_path / "main.py"
    main.write_text(
        "import pkg.wrapper as wrapper\n"
        "print(hasattr(wrapper, '_native'))\n"
        "print(wrapper._native.__name__)\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"

    compiled = _compile_main(site, main, exe)
    assert compiled.returncode == 0, compiled.stderr
    run = _run_main(site, exe, {"PCC_PACKAGE_SITE": ""})

    assert run.returncode == 0, run.stderr
    assert run.stdout == "True\n_native\n"


def test_pcc_native_extension_numpy_25_capi_batch_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_numpy_25_capi_batch_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import capi25\nprint(capi25.probe())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"

    compiled = _compile_main(site, main, exe)
    assert compiled.returncode == 0, compiled.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "63"


def test_pcc_native_extension_star_import_publishes_compiled_module_names(tmp_path):
    site = _compile_demo_extension(tmp_path)
    bridge = tmp_path / "bridge.py"
    bridge.write_text(
        "from demo import *\n" "probe = add(2, 3)\n",
        encoding="utf-8",
    )
    main = tmp_path / "main.py"
    main.write_text(
        "import bridge\n" "print(bridge.probe)\n" "print(bridge.add(4, 5))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["5", "9"]


def test_pcc_native_extension_fastcall_method_is_published_and_callable(tmp_path):
    site = _compile_fastcall_extension(tmp_path)
    bridge = tmp_path / "bridge.py"
    bridge.write_text(
        "from fastdemo import *\n" "probe = fast_sum(2, 3)\n",
        encoding="utf-8",
    )
    main = tmp_path / "main.py"
    main.write_text(
        "import bridge\n"
        "print(bridge.probe)\n"
        "print(bridge.fast_sum(4, 5))\n"
        "print(bridge.fast_kw_score(7, value=9))\n"
        "print(bridge.method_name(bridge.fast_sum))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["5", "9", "791", "fast_sum"]


def test_pcc_native_extension_function_allows_module_metadata_assignment(tmp_path):
    site = _compile_fastcall_extension(tmp_path)
    bridge = tmp_path / "bridge.py"
    bridge.write_text(
        "from fastdemo import *\n" "fast_sum.__module__ = 'bridge'\n",
        encoding="utf-8",
    )
    main = tmp_path / "main.py"
    main.write_text(
        "import bridge\n" "print(bridge.fast_sum.__module__)\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "bridge"


def test_pcc_native_extension_import_is_visible_inside_lambda(tmp_path):
    site = _compile_demo_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "from demo import add\n"
        "values = [3, 1, 2]\n"
        "values.sort(key=lambda value: add(value, 0))\n"
        "print(values)\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "[1, 2, 3]"


def test_pcc_native_custom_type_pytype_ready_under_self_backend_no_libpython(tmp_path):
    """A C extension that registers a custom type via PyType_Ready and
    instantiates it must import + instantiate under strict no-libpython. This
    exercises the type bridge in py_capi_shim.c (PyType_Ready + the dynamic
    type_tag registry + PyType_GenericNew/Alloc). Reduced numpy
    type-registration pattern.
"""
    site = _compile_typedemo_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import typedemo\n"
        'print("has", hasattr(typedemo, "Foo"))\n'
        'print("notnone", typedemo.Foo is not None)\n'
        'print("type-meta", typedemo.Foo.__module__, typedemo.Foo.__name__)\n'
        "x = typedemo.Foo()\n"
        'print("inst", x is not None)\n'
        "x.__module__ = 'demo'\n"
        "x.__qualname__ = 'Foo'\n"
        "print(x.__module__, x.__qualname__, x.itemsize)\n"
        "base_descriptor = typedemo.Foo.base\n"
        "setattr(base_descriptor, '__doc__', 'base doc')\n"
        "print(base_descriptor.__doc__, x.base)\n"
        "member_descriptor = typedemo.Foo.itemsize\n"
        "setattr(member_descriptor, '__doc__', 'member doc')\n"
        "print(member_descriptor.__doc__)\n"
        "ge_descriptor = typedemo.Foo.__ge__\n"
        "setattr(ge_descriptor, '__doc__', 'ge doc')\n"
        "print(ge_descriptor.__doc__, ge_descriptor(x, x))\n"
        "print(issubclass(typedemo.Foo, typedemo.Base))\n"
        "print(issubclass(typedemo.Base, typedemo.Foo))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    env["PCC_RUNTIME_CC"] = "pcc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert "has True" in run.stdout, run.stdout
    assert "notnone True" in run.stdout, run.stdout
    assert "type-meta typedemo Foo" in run.stdout, run.stdout
    assert "inst True" in run.stdout, run.stdout
    assert "demo Foo 0" in run.stdout, run.stdout
    assert "base doc 7" in run.stdout, run.stdout
    assert "member doc" in run.stdout, run.stdout
    assert "ge doc True" in run.stdout, run.stdout
    assert run.stdout.splitlines()[-2:] == ["True", "False"], run.stdout


def test_pcc_native_custom_type_from_spec_under_self_backend_no_libpython(tmp_path):
    site = _compile_typespec_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import specdemo\n" "print(specdemo.check())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "31"


def test_pcc_native_custom_type_getslot_under_self_backend_no_libpython(tmp_path):
    site = _compile_typespec_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import specdemo\n" "print(specdemo.slots())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "7"


def test_pcc_native_custom_type_name_under_self_backend_no_libpython(tmp_path):
    site = _compile_typespec_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import specdemo\n" "print(specdemo.Spec.__name__)\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "Spec"


def test_pcc_native_custom_type_method_descriptor_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_typespec_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import specdemo\n"
        "obj = specdemo.Spec()\n"
        "first = specdemo.Spec.marker\n"
        "second = specdemo.Spec.marker\n"
        "print(first is second, first(obj, 41))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "True 41"


def test_pcc_native_custom_metaclass_typecheck_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_typespec_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import specdemo\nprint(specdemo.metatype_check())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "3"


def test_pcc_native_builtin_py_type_mapping_under_self_backend_no_libpython(tmp_path):
    """Py_TYPE on pcc builtin objects must resolve to the shim's &PyXxx_Type
    tokens (numpy compares Py_TYPE(o) against builtin type objects). Validates
    the builtin-tag mapping in py_capi_shim.c, including immediate (tagged) ints.
    `check()` returns 3 when both int and str map correctly."""
    site = _compile_typecheck_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import tcdemo\nprint('tc', tcdemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "tc 3" in run.stdout, run.stdout


def test_pcc_native_subtype_check_under_self_backend_no_libpython(tmp_path):
    """Type-bridge inheritance + alloc surface under strict no-libpython.
    check() returns 63 = PyObject_TypeCheck(derived,&Base) + (derived,&Derived) +
    !(base,&Derived) + PyType_IsSubtype(&Derived,&Base) + !(&Base,&Derived) +
    PyObject_New(&Base) non-NULL — i.e. subtype walk (both PyObject_TypeCheck and
    PyType_IsSubtype) and _PyObject_New all work."""
    site = _compile_subtype_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import stdemo\nprint('st', stdemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "st 63" in run.stdout, run.stdout


def test_pcc_native_type_ready_inherits_alloc_slot(tmp_path):
    site = _compile_subtype_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import stdemo\nprint(stdemo.alloc_slots())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "7"


def test_pcc_native_extension_numeric_conversion_slots(tmp_path):
    site = _compile_number_slot_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import numslotdemo\nprint(numslotdemo.check())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe, runtime_cc="pcc")
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "63"


def test_pcc_native_extension_managed_dealloc_gate_under_self_backend_no_libpython(
    tmp_path,
):
    """pcc-private managed dealloc hooks may clean extension payloads, but
    ordinary C-extension tp_dealloc slots must not be called blindly because
    most CPython slots free the object body themselves."""
    site = _compile_managed_dealloc_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import mdemo\nprint('managed', mdemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert "managed 1" in run.stdout, run.stdout


def test_pcc_native_link_symbols_behave_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for the batch 7-9 link symbols added to
    py_capi_shim.c for numpy's no-libpython link surface (PyTuple_GetSlice,
    PyObject_AsFileDescriptor, Py_GenericAlias, PyUnicode_AsLatin1String,
    PyErr_NormalizeException). They were link-validated only; this exercises
    them at runtime under strict --backend self.
    check() returns 31 when all five behave correctly."""
    site = _compile_linksym_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import lsdemo\nprint('ls', lsdemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "ls 31" in run.stdout, run.stdout


def test_pcc_native_contextvar_get_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for the single-context contextvar (PyContextVar_New
    + PyContextVar_Get from batch 10, PyContextVar_Set from batch 14) in
    py_capi_shim.c — a real object, not a stub, since numpy.errstate creates a
    ContextVar at import and numpy alloc.c calls PyContextVar_Set. check() returns
    7: Get returns the var's own default (5) for a NULL default arg, the explicit
    default arg (9) when unset, and the set value (7) after Set — correct CPython
    single-context semantics under the native pipeline."""
    site = _compile_contextvar_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import cvdemo\n"
        "cv = cvdemo.make()\n"
        "token = cv.set(7)\n"
        "before = cv.get()\n"
        "cv.reset(token)\n"
        "after = cv.get()\n"
        "print('cv', cvdemo.check(), before * 10 + after)\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "cv 7 75" in run.stdout, run.stdout


def test_pcc_native_buildvalue_dict_under_self_backend_no_libpython(tmp_path):
    site = _compile_buildvalue_dict_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import buildvaluedict\nprint(buildvaluedict.check())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["42"]


def test_pcc_native_buildvalue_nested_list_under_self_backend_no_libpython(tmp_path):
    site = _compile_buildvalue_dict_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import buildvaluedict\nprint(buildvaluedict.nested())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["127"]


def test_pcc_native_vaparse_tuple_and_keywords_under_self_backend_no_libpython(
    tmp_path,
):
    """Behavioral regression for the PyArg_ParseTupleAndKeywords -> (new)
    PyArg_VaParseTupleAndKeywords va_list-core delegation (canonical CPython
    refactor; PyArg_VaParseTupleAndKeywords is the symbol numpy's C core
    references). check(10, 20) parses "ll" through the core and returns 1020
    under strict --backend self."""
    site = _compile_vaparse_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import vademo\nprint('va', vademo.check(10, 20))\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "va 1020" in run.stdout, run.stdout


def test_pcc_native_eval_getbuiltins_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyEval_GetBuiltins — numpy's sole consumer
    (npy_PyFile_OpenFile) does PyDict_GetItemString(PyEval_GetBuiltins(), "open")
    and returns NULL gracefully if absent, so an empty real dict is import-safe.
    check() returns 7: GetBuiltins is a non-NULL real dict, an absent key yields
    NULL without crashing, and it is a mutable persistent singleton (borrowed-ref
    semantics) under strict --backend self."""
    site = _compile_getbuiltins_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import ebdemo\nprint('eb', ebdemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    env["PCC_RUNTIME_CC"] = "pcc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "eb 7" in run.stdout, run.stdout


def test_pcc_native_uniquely_referenced_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyUnstable_Object_IsUniquelyReferenced (full
    numpy _core references it for safe in-place mutation). check() returns 3 when
    a freshly-created object (refcount 1) is uniquely referenced and, after
    Py_INCREF (refcount 2), it is not — under the native pipeline."""
    site = _compile_uniqueref_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import urdemo\nprint('ur', urdemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "ur 3" in run.stdout, run.stdout


def test_pcc_native_import_and_vectorcall_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyImport_Import (-> PyImport_ImportModule) and
    PyVectorcall_Call (-> PyObject_Call), batch 14, both referenced by the full
    numpy _core. The caller extension imports the sibling `demo` extension by name
    and invokes demo.add(2, 3) through the vectorcall entry. check() returns 7
    (import non-NULL, attr reachable, call == 5) under strict
    --backend self."""
    site = _compile_demo_extension(tmp_path)
    _compile_import_vectorcall_extension(tmp_path)  # same site as demo
    main = tmp_path / "main.py"
    main.write_text("import imdemo\nprint('im', imdemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    env["PCC_RUNTIME_CC"] = "pcc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "im 7" in run.stdout, run.stdout


def test_pcc_native_type_vectorcall_dispatch_under_self_backend_no_libpython(tmp_path):
    """A C-extension type using NumPy's vectorcall layout dispatches its
    instance vectorcall slot for positional and keyword calls without recursing
    through ``tp_call = PyVectorcall_Call``."""
    site = _compile_type_vectorcall_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import typevcdemo\n"
        "def target(value, scale=1):\n"
        "    return value * scale\n"
        "vector = typevcdemo.make()\n"
        "def slice_vector(value):\n"
        "    return value[:, 1:5:2, None]\n"
        "items = []\n"
        "for item in vector:\n"
        "    items.append(item)\n"
        "print('typevc', vector(2, 3), vector(2, scale=4), "
        "typevcdemo.call_keyword(target), items, slice_vector(vector), "
        "typevcdemo.check_builtin_type(int), typevcdemo.call_method_offset())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe, runtime_cc="pcc")
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert "typevc 23 241 8 [5, 6] 73 1 1024" in run.stdout, run.stdout


def test_pcc_native_type_number_slot_subtract_under_self_backend_no_libpython(
    tmp_path,
):
    """Dynamic C-extension values use ``tp_as_number->nb_subtract``."""
    site = _compile_type_number_slot_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import typenumdemo\n"
        "left = typenumdemo.make(9)\n"
        "right = typenumdemo.make(4)\n"
        "negative = typenumdemo.make(-9)\n"
        "zero = typenumdemo.make(0)\n"
        "larger = typenumdemo.make(10)\n"
        "print('typenum', left - right, abs(negative), bool(zero), bool(left), left < larger, left + right, 2 + left, left + 0.5, 0.5 + left, left * right, 2 * left, left / 2, 18 / left)\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert (
        "typenum 5 9 False True True 13 11 9.5 9.5 36 18 4.5 2.0" in run.stdout
    ), run.stdout


def test_pcc_native_sys_getobject_flags_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PySys_GetObject (batch 15) — numpy's
    npy_static_data init reads sys.flags.optimize at IMPORT and fails on NULL.
    check() returns 7: PySys_GetObject("flags") is a non-NULL real namespace,
    flags.optimize == 0 (accurate for pcc's no-O compile, matching numpy's exact
    PyObject_GetAttrString pattern), and an unprovided sys attr returns NULL —
    under strict --backend self."""
    site = _compile_sysflags_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import sfdemo\nprint('sf', sfdemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "sf 7" in run.stdout, run.stdout


def test_pcc_native_generic_getdict_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyObject_GenericGetDict (batch 16), which numpy
    installs as a `__dict__` getset. A module-level function gets the module as
    self; check() returns 3 when PyObject_GenericGetDict(self, NULL) is a non-NULL
    dict that contains the module's own "check" entry — under strict
    --backend self."""
    site = _compile_genericgetdict_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import gddemo\nprint('gd', gddemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "gd 3" in run.stdout, run.stdout


def test_pcc_native_seqiter_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PySeqIter_New (batch 17) — a real sequence
    iterator driven through the C-API PyIter_Next path. check() returns 1 when
    iterating the tuple (10,20,30) yields exactly those 3 values (sum 60) then
    NULL, under strict --backend self."""
    site = _compile_seqiter_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import sidemo\nprint('si', sidemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    env["PCC_RUNTIME_CC"] = "pcc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "si 1" in run.stdout, run.stdout


def test_pcc_native_pymethod_new_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyMethod_New (batch 17) — binds a function to a
    self via the runtime's instance-method machinery. check() returns 7 when the
    func is reachable, PyMethod_New(func, self) is non-NULL, and the result is a
    real callable, under strict --backend self."""
    site = _compile_pymethod_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import mndemo\nprint('mn', mndemo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "mn 7" in run.stdout, run.stdout


def test_pcc_native_batch18_host_symbols_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for batch 18 full-module host symbols (PyDict_Copy,
    PyDict_Merge, PyUnicode_Format, PyObject_GenericGetAttr/SetAttr), all routed
    to existing pcc primitives. check() returns 15 when copy/merge/format and the
    generic getattr/setattr round-trip all behave, under strict
    --backend self."""
    site = _compile_batch18_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import b18demo\nprint('b18', b18demo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "b18 15" in run.stdout, run.stdout


def test_pcc_native_batch19_host_symbols_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for batch 19 full-module host symbols (PySlice_New +
    PySlice_GetIndicesEx, PyArg_UnpackTuple, PyDictProxy_New, PyObject_Init).
    check() returns 31 when slice index computation (both an explicit and an
    all-None slice), tuple unpacking, the dict proxy read, and PyObject_Init
    passthrough all behave, under strict --backend self."""
    site = _compile_batch19_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import b19demo\nprint('b19', b19demo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "b19 31" in run.stdout, run.stdout


def test_pcc_native_batch2021_host_symbols_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for batch 20/21 host surface (PyUnicode_KIND,
    PyUnicode_READ_CHAR, PyUnicode_1BYTE_DATA, PyNumber_Divmod, _Py_HashDouble) —
    the symbols that let all 98 numpy _core files compile and link. check()
    returns 15 when KIND/READ_CHAR/1BYTE_DATA read the UTF-8 buffer correctly,
    PyNumber_Divmod(17,5)==(3,2), and _Py_HashDouble(NULL,2.0)==2, under strict
    --backend self."""
    site = _compile_batch2021_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import b21demo\nprint('b21', b21demo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "b21 15" in run.stdout, run.stdout


def test_pcc_native_unicode_writer_under_self_backend_no_libpython(tmp_path):
    site = _compile_unicode_writer_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import writerdemo\nprint('writer', writerdemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "writer 7" in run.stdout, run.stdout


def test_pcc_native_decode_heaptype_batch_under_self_backend_no_libpython(tmp_path):
    site = _compile_decode_heaptype_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import decodeheapdemo\nprint('decodeheap', decodeheapdemo.check())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "decodeheap 15" in run.stdout, run.stdout


def test_pcc_native_simplejson_final_api_batch_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_simplejson_final_api_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "class Target:\n"
        "    def combine(self, a, b):\n"
        "        return a * 10 + b\n"
        "import sjfinaldemo\n"
        "print('sjfinal', sjfinaldemo.check(Target()))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "sjfinal 15" in run.stdout, run.stdout


def test_capi_import_sees_compiled_python_module_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_compiled_module_import_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import depmod\n"
        "import compiledimportdemo\n"
        "print('compiledimport', compiledimportdemo.lookup())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "compiledimport 41" in run.stdout, run.stdout


def test_capi_import_sees_native_math_module_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_builtin_module_import_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import math\n"
        "import builtinimportdemo\n"
        "print('builtinimport', builtinimportdemo.lookup())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "builtinimport 7" in run.stdout, run.stdout


def test_capi_import_sees_explicit_pcc_python_math_port_under_self_backend(
    tmp_path,
):
    site = _compile_builtin_module_import_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import math\n"
        "import builtinimportdemo\n"
        "print('builtinimport', builtinimportdemo.lookup())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(REPO / "scripts" / "pcc_multi.py"),
            "--entry",
            "main",
            "--out",
            str(exe),
            "--backend",
            "self",
            "--ir-scaffold",
            "on",
            f"{main}=main",
            f"{REPO / 'pcc' / 'ir' / 'support' / 'math.py'}=math",
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "builtinimport 7" in run.stdout, run.stdout


def test_capi_import_sees_pcc_python_builtin_module_graph_under_self_backend(
    tmp_path,
):
    site = _compile_builtin_module_graph_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import math\n"
        "import sys\n"
        "import time\n"
        "import gc\n"
        "import copy\n"
        "import builtinmodulegraphdemo\n"
        "print('builtinmodulegraph', builtinmodulegraphdemo.check())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "builtinmodulegraph 5" in run.stdout, run.stdout


def test_pcc_native_batch22_host_symbols_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for batch 22 host symbols (PyLong_FromUnicodeObject,
    PyFloat_FromString, PyLong_AsLongLongAndOverflow, PySlice_AdjustIndices) from
    numpy's C++ umath layer. check() returns 15 when str->int, str->float,
    long-long-with-overflow, and slice-index adjustment all behave, under strict
    --backend self."""
    site = _compile_batch22_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import b22demo\nprint('b22', b22demo.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "b22 15" in run.stdout, run.stdout


def test_pcc_native_set_type_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for Py_SET_TYPE -> pcc_capi_set_type (batch 23), the
    sole symbol undefined when link-testing numpy's full _core. check() returns 3
    when a fresh Foo instance reports type Foo and, after Py_SET_TYPE(o, &BarType),
    reports type Bar, under strict --backend self."""
    site = _compile_settype_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import stdemo2\nprint('st2', stdemo2.check())\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "st2 3" in run.stdout, run.stdout


def test_pcc_native_multiphase_init_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PEP 489 multi-phase C-extension init (numpy's
    _multiarray_umath uses it): PyInit returns PyModuleDef_Init(&def) with a
    Py_mod_exec slot, and the no-libpython loader must build the module + run the
    exec slot. The slot registers answer=42; this asserts mpdemo.answer == 42,
    proving the loader executes Py_mod_exec in the native pipeline."""
    site = _compile_multiphase_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import mpdemo\nprint('mp', mpdemo.answer)\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "mp 42" in run.stdout, run.stdout


def test_pcc_native_extension_capsule_roundtrip_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_capsule_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import capsdemo\n"
        "print(capsdemo.value())\n"
        "print(capsdemo.context_score())\n"
        "print(capsdemo.destructor_score())\n"
        "print(capsdemo.pointer_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["37", "48", "13", "31"]


def test_pcc_native_extension_capsule_import_loads_provider_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_capsule_import_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import capicons\n"
        "print(capicons.value())\n"
        "print(capicons.table_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["73", "151"]


def test_pcc_native_extension_numpy_capi_provider_minimal_array_metadata(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n"
        "print(pccnpcons.table_shape_score())\n"
        "print(pccnpcons.type_object_score())\n"
        "print(pccnpcons.type_object_from_type_score())\n"
        "print(pccnpcons.unsupported_score())\n"
        "print(pccnpcons.ufunc_registration_score())\n"
        "print(pccnpcons.inferred_dtype_score())\n"
        "print(pccnpcons.descr_score())\n"
        "print(pccnpcons.fromany_score())\n"
        "print(pccnpcons.array_metadata_score())\n"
        "print(pccnpcons.pointer_metadata_score())\n"
        "print(pccnpcons.dtype_field_score())\n"
        "print(pccnpcons.dtype_classification_score())\n"
        "print(pccnpcons.scalar_kind_score())\n"
        "print(pccnpcons.can_coerce_scalar_score())\n"
        "print(pccnpcons.can_cast_scalar_score())\n"
        "print(pccnpcons.convert_common_type_score())\n"
        "print(pccnpcons.state_byteorder_score())\n"
        "print(pccnpcons.from_macro_score())\n"
        "print(pccnpcons.fill_byteorder_score())\n"
        "print(pccnpcons.shape_compare_score())\n"
        "print(pccnpcons.empty_zeros_score())\n"
        "print(pccnpcons.equiv_types_score())\n"
        "print(pccnpcons.new_from_descr_score())\n"
        "print(pccnpcons.new_score())\n"
        "print(pccnpcons.array_check_score())\n"
        "print(pccnpcons.size_itemsize_score())\n"
        "print(pccnpcons.array_size_score())\n"
        "print(pccnpcons.accessor_score())\n"
        "print(pccnpcons.core_provider_score())\n"
        "print(pccnpcons.base_object_score())\n"
        "print(pccnpcons.return_score())\n"
        "print(pccnpcons.flags_mutation_score())\n"
        "print(pccnpcons.copy_into_score())\n"
        "print(pccnpcons.to_scalar_score())\n"
        "print(pccnpcons.copy_score())\n"
        "print(pccnpcons.new_copy_score())\n"
        "print(pccnpcons.copy_object_score())\n"
        "print(pccnpcons.resize_score())\n"
        "print(pccnpcons.new_like_score())\n"
        "print(pccnpcons.view_score())\n"
        "print(pccnpcons.squeeze_score())\n"
        "print(pccnpcons.transpose_score())\n"
        "print(pccnpcons.swap_axes_score())\n"
        "print(pccnpcons.ravel_score())\n"
        "print(pccnpcons.flatten_score())\n"
        "print(pccnpcons.take_from_score())\n"
        "print(pccnpcons.put_to_score())\n"
        "print(pccnpcons.put_mask_score())\n"
        "print(pccnpcons.repeat_score())\n"
        "print(pccnpcons.choose_score())\n"
        "print(pccnpcons.concatenate_score())\n"
        "print(pccnpcons.arange_score())\n"
        "print(pccnpcons.arange_obj_score())\n"
        "print(pccnpcons.inner_product_score())\n"
        "print(pccnpcons.matrix_product_score())\n"
        "print(pccnpcons.matrix_product2_score())\n"
        "print(pccnpcons.correlate_score())\n"
        "print(pccnpcons.correlate2_score())\n"
        "print(pccnpcons.lexsort_score())\n"
        "print(pccnpcons.sort_score())\n"
        "print(pccnpcons.argsort_score())\n"
        "print(pccnpcons.searchsorted_score())\n"
        "print(pccnpcons.nonzero_score())\n"
        "print(pccnpcons.count_nonzero_score())\n"
        "print(pccnpcons.min_scalar_type_score())\n"
        "print(pccnpcons.sorted_stride_perm_score())\n"
        "print(pccnpcons.remove_axes_in_place_score())\n"
        "print(pccnpcons.where_score())\n"
        "print(pccnpcons.compress_score())\n"
        "print(pccnpcons.diagonal_score())\n"
        "print(pccnpcons.trace_score())\n"
        "print(pccnpcons.clip_score())\n"
        "print(pccnpcons.conjugate_score())\n"
        "print(pccnpcons.std_score())\n"
        "print(pccnpcons.round_score())\n"
        "print(pccnpcons.sum_score())\n"
        "print(pccnpcons.cumsum_score())\n"
        "print(pccnpcons.prod_score())\n"
        "print(pccnpcons.cumprod_score())\n"
        "print(pccnpcons.max_score())\n"
        "print(pccnpcons.min_score())\n"
        "print(pccnpcons.ptp_score())\n"
        "print(pccnpcons.mean_score())\n"
        "print(pccnpcons.any_score())\n"
        "print(pccnpcons.all_score())\n"
        "print(pccnpcons.argmax_score())\n"
        "print(pccnpcons.argmin_score())\n"
        "print(pccnpcons.reshape_score())\n"
        "print(pccnpcons.newshape_score())\n"
        "print(pccnpcons.ensure_array_score())\n"
        "print(pccnpcons.descr_check_score())\n"
        "print(pccnpcons.descr_new_from_type_score())\n"
        "print(pccnpcons.descr_new_score())\n"
        "print(pccnpcons.descr_new_byteorder_score())\n"
        "print(pccnpcons.can_cast_safely_score())\n"
        "print(pccnpcons.can_cast_to_score())\n"
        "print(pccnpcons.can_cast_type_array_score())\n"
        "print(pccnpcons.casting_converter_score())\n"
        "print(pccnpcons.zero_one_score())\n"
        "print(pccnpcons.object_type_score())\n"
        "print(pccnpcons.descr_from_object_score())\n"
        "print(pccnpcons.descr_converter_score())\n"
        "print(pccnpcons.descr_align_converter_score())\n"
        "print(pccnpcons.descr_from_scalar_score())\n"
        "print(pccnpcons.descr_from_type_object_score())\n"
        "print(pccnpcons.scalar_score())\n"
        "print(pccnpcons.scalar_as_ctype_score())\n"
        "print(pccnpcons.from_scalar_score())\n"
        "print(pccnpcons.cast_scalar_to_ctype_score())\n"
        "print(pccnpcons.cast_scalar_direct_score())\n"
        "print(pccnpcons.pack_score())\n"
        "print(pccnpcons.from_array_score())\n"
        "print(pccnpcons.cast_to_type_score())\n"
        "print(pccnpcons.fill_with_scalar_score())\n"
        "print(pccnpcons.to_list_score())\n"
        "print(pccnpcons.to_string_score())\n"
        "print(pccnpcons.byteswap_score())\n"
        "print(pccnpcons.from_string_score())\n"
        "print(pccnpcons.from_buffer_score())\n"
        "print(pccnpcons.buffer_converter_score())\n"
        "print(pccnpcons.from_iter_score())\n"
        "print(pccnpcons.converter_score())\n"
        "print(pccnpcons.pyint_converter_score())\n"
        "print(pccnpcons.intp_from_sequence_score())\n"
        "print(pccnpcons.intp_converter_score())\n"
        "print(pccnpcons.optional_intp_converter_score())\n"
        "print(pccnpcons.promote_types_score())\n"
        "print(pccnpcons.result_type_score())\n"
        "print(pccnpcons.priority_score())\n"
        "print(pccnpcons.check_strides_score())\n"
        "print(pccnpcons.broadcast_to_shape_score())\n"
        "print(pccnpcons.broadcast_multi_score())\n"
        "print(pccnpcons.multi_iter_from_objects_score())\n"
        "print(pccnpcons.remove_smallest_score())\n"
        "print(pccnpcons.multi_iter_score())\n"
        "print(pccnpcons.iter_all_but_axis_score())\n"
        "print(pccnpcons.list_pointer_score())\n"
        "print(pccnpcons.overflow_multiply_score())\n"
        "print(pccnpcons.endianness_score())\n"
        "print(pccnpcons.feature_version_score())\n"
        "print(pccnpcons.version_score())\n"
        "print(pccnpcons.check_axis_score())\n"
        "print(pccnpcons.clipmode_converter_score())\n"
        "print(pccnpcons.clipmode_sequence_score())\n"
        "print(pccnpcons.output_converter_score())\n"
        "print(pccnpcons.searchside_converter_score())\n"
        "print(pccnpcons.byteorder_converter_score())\n"
        "print(pccnpcons.sortkind_converter_score())\n"
        "print(pccnpcons.selectkind_converter_score())\n"
        "print(pccnpcons.order_converter_score())\n"
        "print(pccnpcons.bool_converter_score())\n"
        "print(pccnpcons.optional_bool_converter_score())\n"
        "print(pccnpcons.axis_converter_score())\n"
        "print(pccnpcons.element_strides_score())\n"
        "print(pccnpcons.valid_type_score())\n"
        "print(pccnpcons.item_refcount_score())\n"
        "print(pccnpcons.array_refcount_score())\n"
        "print(pccnpcons.allocator_score())\n"
        "print(pccnpcons.datamem_score())\n"
        "print(pccnpcons.datamem_user_score())\n"
        "print(pccnpcons.array_free_score())\n"
        "print(pccnpcons.as_carray_score())\n"
        "print(pccnpcons.fail_writeable_score())\n"
        "print(pccnpcons.writeback_if_copy_score())\n"
        "print(pccnpcons.discard_writeback_if_copy_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "123",
        "1111111111",
        "1111111",
        "101",
        "111111111111111",
        "11111111111111111",
        "11111111111111",
        "1111111111111111111",
        "1111111111111111111",
        "11111",
        "11111",
        "111111111",
        "11111111",
        "111111",
        "111111",
        "111111",
        "1111111111",
        "1111111111",
        "11111",
        "111111",
        "111111",
        "11111111",
        "11111",
        "111111",
        "1111",
        "1111",
        "1111",
        "1111111",
        "111111",
        "11111",
        "1111",
        "1111111",
        "11111",
        "111",
        "1111",
        "11111",
        "1111",
        "1111",
        "1111",
        "11",
        "111",
        "111",
        "1111111",
        "111",
        "1111",
        "1111",
        "1111",
        "111",
        "1111",
        "1111",
        "11111111",
        "111111",
        "111111",
        "111111",
        "11111111",
        "11111111",
        "111111111",
        "111111",
        "111111",
        "11111",
        "111111",
        "111111111",
        "11111",
        "11111111",
        "11111111",
        "1111",
        "11111",
        "1111111",
        "11111111",
        "11111111",
        "11111111",
        "11111111",
        "1111111",
        "11111111111",
        "11111111",
        "111111111",
        "1111111111",
        "111111111",
        "1111111111",
        "111111111",
        "111111111",
        "111111111",
        "1111111111",
        "1111111111",
        "1111111111",
        "11111111",
        "11111111",
        "111111111",
        "111111111111",
        "1111",
        "1111",
        "11111",
        "111111",
        "11111111",
        "111111111",
        "1111",
        "1111111",
        "111111111",
        "111111",
        "111111111",
        "111111",
        "111111",
        "11111",
        "11111",
        "111111",
        "111111",
        "1111",
        "11111",
        "1111",
        "1111",
        "1111",
        "111",
        "111",
        "11",
        "11",
        "11",
        "11",
        "1111",
        "111111",
        "11111",
        "1111",
        "1111",
        "11111111111111",
        "111111",
        "11111",
        "11111",
        "11111",
        "11111",
        "11111",
        "1111111",
        "111111",
        "11111",
        "111111",
        "11111",
        "111111",
        "111111",
        "11111",
        "1111",
        "111",
        "111",
        "1111",
        "1111111",
        "1111111",
        "111111",
        "111",
        "1111",
        "11111111",
        "11111111",
        "111111",
        "11111111",
        "111111",
        "111111",
        "11111",
        "111",
        "111",
        "1111",
        "1111",
        "111",
        "1111",
        "11111",
        "111",
        "111",
        "111",
        "111111111",
        "11111111",
    ]


def test_pcc_native_extension_numpy_capi_provider_debug_print(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n"
        "print('debug-start')\n"
        "print(pccnpcons.debug_print_score())\n"
        "print('debug-end')\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert lines[0] == "debug-start"
    assert "111" in lines
    assert lines[-1] == "debug-end"
    assert " Dump of pcc NumPy ndarray at address" in run.stdout
    assert " ndim   : 2" in run.stdout
    assert " shape  : 2 3" in run.stdout
    assert " dtype  : NPY_INT" in run.stdout
    assert " It's NULL!" in run.stdout
    assert " not a pcc NumPy array" in run.stdout


def test_pcc_native_extension_numpy_capi_provider_einstein_sum(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.einstein_sum_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1111111"]


def test_pcc_native_extension_numpy_capi_provider_partition_argpartition(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.partition_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["11111111"]


def test_pcc_native_extension_numpy_capi_provider_searchsorted_sorter(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.searchsorted_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["111111111"]


def test_pcc_native_extension_numpy_capi_provider_cumsum_out(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.cumsum_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1111111111"]


def test_pcc_native_extension_numpy_capi_provider_nonzero_2d(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.nonzero_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["11111"]


def test_pcc_native_extension_numpy_capi_provider_where_2d(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.where_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1111111"]


def test_pcc_native_extension_numpy_capi_provider_compress_2d(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.compress_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["11111111"]


def test_pcc_native_extension_numpy_capi_provider_diagonal_axes(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.diagonal_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["11111111"]


def test_pcc_native_extension_numpy_capi_provider_trace_rtype_axes(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.trace_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["11111111"]


def test_pcc_native_extension_numpy_capi_provider_std_rtype(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.std_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["11111111111"]


def test_pcc_native_extension_numpy_capi_provider_std_scalar_out(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.std_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["11111111111"]


def test_pcc_native_extension_numpy_capi_provider_sum_rtype(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.sum_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["111111111"]


def test_pcc_native_extension_numpy_capi_provider_sum_scalar_out(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.sum_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["111111111"]


def test_pcc_native_extension_numpy_capi_provider_prod_rtype(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.prod_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["111111111"]


def test_pcc_native_extension_numpy_capi_provider_prod_scalar_out(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.prod_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["111111111"]


def test_pcc_native_extension_numpy_capi_provider_cumsum_rtype(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.cumsum_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1111111111"]


def test_pcc_native_extension_numpy_capi_provider_cumprod_rtype(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.cumprod_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1111111111"]


def test_pcc_native_extension_numpy_capi_provider_cumprod_out(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.cumprod_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1111111111"]


def test_pcc_native_extension_numpy_capi_provider_max_scalar_out(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.max_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["111111111"]


def test_pcc_native_extension_numpy_capi_provider_min_scalar_out(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.min_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["111111111"]


def test_pcc_native_extension_numpy_capi_provider_ptp_scalar_out(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.ptp_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["111111111"]


def test_pcc_native_extension_numpy_capi_provider_mean_rtype(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.mean_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1111111111"]


def test_pcc_native_extension_numpy_capi_provider_mean_scalar_out(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.mean_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1111111111"]


def test_pcc_native_extension_numpy_capi_provider_check_any_scalar_exact(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n" "print(pccnpcons.check_any_scalar_exact_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["11111"]


def test_pcc_native_extension_cache_string_args_and_multiple_methods(tmp_path):
    site = _compile_feature_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import featuredemo\n"
        "import featuredemo as again\n"
        "print(featuredemo.count())\n"
        "print(again.count())\n"
        "print(featuredemo.echo('hello'))\n"
        "print(featuredemo.add(4, 5))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1", "1", "hello", "9"]


def test_pcc_native_extension_method_flags_noargs_and_o(tmp_path):
    site = _compile_methodflag_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import flagdemo\n"
        "print(flagdemo.sum(2, 3))\n"
        "print(flagdemo.noargs())\n"
        "print(flagdemo.one(7))\n"
        "try:\n"
        "    flagdemo.noargs(1)\n"
        "    print('unexpected-noargs')\n"
        "except Exception as exc:\n"
        "    print(str(exc))\n"
        "try:\n"
        "    flagdemo.one()\n"
        "    print('unexpected-one-missing')\n"
        "except Exception as exc:\n"
        "    print(str(exc))\n"
        "try:\n"
        "    flagdemo.one(1, 2)\n"
        "    print('unexpected-one-extra')\n"
        "except Exception as exc:\n"
        "    print(str(exc))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "5",
        "41",
        "107",
        "method takes no arguments",
        "method takes exactly one argument",
        "method takes exactly one argument",
    ]


def test_pcc_native_extension_method_flags_keywords(tmp_path):
    site = _compile_keywordflag_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import kwdemo\n"
        "print(kwdemo.mix(5))\n"
        "print(kwdemo.mix(5, name='abc'))\n"
        "print(kwdemo.mix(value=7, name='zz'))\n"
        "try:\n"
        "    kwdemo.mix(name='abc')\n"
        "    print('unexpected-missing')\n"
        "except Exception as exc:\n"
        "    print(str(exc))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "5",
        "8",
        "9",
        "argument type mismatch",
    ]


def test_pcc_native_extension_optional_truth_and_converter_parse_units(tmp_path):
    site = _compile_keywordflag_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import kwdemo\n"
        "print(kwdemo.options())\n"
        "print(kwdemo.options(coerce=False))\n"
        "print(kwdemo.options(na_object=7))\n"
        "print(kwdemo.options(coerce=False, na_object=7))\n"
        "print(kwdemo.typed_long(9))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["100", "0", "107", "7", "9"]


def test_pcc_native_extension_singleton_return_macros(tmp_path):
    site = _compile_feature_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import featuredemo\n"
        "print(1 if featuredemo.return_none() is None else 0)\n"
        "print(1 if featuredemo.return_true() else 0)\n"
        "print(1 if not featuredemo.return_false() else 0)\n"
        "print(1 if featuredemo.return_notimplemented() is NotImplemented else 0)\n"
        "print(featuredemo.identity_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1", "1", "1", "1", "11111"]


def test_pcc_native_extension_method_exception_propagates_and_module_still_works(
    tmp_path,
):
    site = _compile_feature_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import featuredemo\n"
        "try:\n"
        "    featuredemo.fail()\n"
        "    print('unexpected-success')\n"
        "except Exception as exc:\n"
        "    print(str(exc))\n"
        "print(featuredemo.add(6, 7))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["feature method failed", "13"]


def test_pcc_native_extension_imported_callable_exception_is_catchable(tmp_path):
    site = _compile_feature_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "from featuredemo import fail\n"
        "try:\n"
        "    fail()\n"
        "except Exception:\n"
        "    pass\n"
        "print('caught')\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "caught\n"


def test_pcc_native_extension_buffer_protocol_for_bytes_bytearray_memoryview(tmp_path):
    site = _compile_buffer_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import bufdemo\n"
        "b = b'ABC'\n"
        "ba = bytearray(b'\\x01\\x02\\x03')\n"
        "mv = memoryview(b'\\x04\\x05\\x06')\n"
        "print(bufdemo.sum(b))\n"
        "print(bufdemo.sum(ba))\n"
        "print(bufdemo.sum(mv))\n"
        "print(bufdemo.writable(b))\n"
        "print(bufdemo.metadata_score(b))\n"
        "print(bufdemo.metadata_score(ba))\n"
        "print(bufdemo.view_sum(ba))\n"
        "print(bufdemo.writable(ba))\n"
        "print(bufdemo.from_memory_sum())\n"
        "print(bufdemo.from_memory_write_sum())\n"
        "print(bufdemo.memoryview_inspect_score(ba))\n"
        "print(bufdemo.check_score(b, ba, mv, 123))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe, runtime_cc="pcc")
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "198",
        "6",
        "15",
        "0",
        "3111311",
        "3211311",
        "6",
        "1",
        "24",
        "15",
        "711113111",
        "1111",
    ]


def test_pcc_native_extension_memoryviews_keep_distinct_owned_buffers(tmp_path):
    site = _compile_buffer_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import bufdemo\n"
        "print(bufdemo.two_live_views_score(b'ABC', b'xyz'))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe, runtime_cc="pcc")
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "127\n"


def test_pcc_native_extension_pyobject_call_class_constructor(tmp_path):
    site = _compile_call_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import calldemo\n"
        "class Box:\n"
        "    x: int\n"
        "obj = calldemo.call0(Box)\n"
        "print(isinstance(obj, Box))\n"
        "print(calldemo.bad_args(Box))\n"
        "def answer():\n"
        "    return 42\n"
        "fn = [answer][0]\n"
        "print(calldemo.call0(fn))\n"
        "print(calldemo.call_object0(fn))\n"
        "print(isinstance(calldemo.call_one(Box), Box))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe, runtime_cc="pcc")
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["True", "1", "42", "42", "True"]


def test_pcc_native_extension_generic_vectorcall_preserves_one_argument(tmp_path):
    site = _compile_generic_vectorcall_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import genericvcdemo\n"
        "print(genericvcdemo.call_one())\n"
        "print(genericvcdemo.vectorcall_one())\n"
        "print(genericvcdemo.vectorcall_method_one())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe, runtime_cc="pcc")
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1", "1", "1"]


def test_pcc_native_extension_pyobject_call_receives_none_return(tmp_path):
    site = _compile_call_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import calldemo\n"
        "def implicit_none():\n"
        "    value = 1\n"
        "def bare_none():\n"
        "    return\n"
        "implicit_fn = [implicit_none][0]\n"
        "bare_fn = [bare_none][0]\n"
        "print(calldemo.call_is_none(implicit_fn))\n"
        "print(calldemo.call_is_none(bare_fn))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1", "1"]


def test_pcc_native_extension_tuple_dict_attr_helpers(tmp_path):
    site = _compile_object_api_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import objectapi\n"
        "class Box:\n"
        "    answer: int\n"
        "box = Box()\n"
        "print(objectapi.tuple_sum())\n"
        "print(objectapi.pack_sum())\n"
        "print(objectapi.dict_answer())\n"
        "print(objectapi.list_sum())\n"
        "print(objectapi.bytes_sum())\n"
        "print(objectapi.bytes_size_sum())\n"
        "print(objectapi.unicode_prefix())\n"
        "print(objectapi.unicode_utf8_size_score())\n"
        "print(objectapi.unicode_decode_length_score())\n"
        "print(objectapi.unicode_encode_concat_score())\n"
        "print(objectapi.unicode_macro_score())\n"
        "print(objectapi.unicode_kind_score())\n"
        "print(objectapi.float_bool_score())\n"
        "print(objectapi.scalar_complex_score())\n"
        "print(objectapi.set_attr(box))\n"
        "print(objectapi.has_attr(box, 'answer'))\n"
        "print(objectapi.has_attr(box, 'missing'))\n"
        "print(objectapi.format_error())\n"
        "print(objectapi.warning_score())\n"
        "print(objectapi.gil_state_score())\n"
        "print(objectapi.error_helper_score())\n"
        "print(objectapi.exception_global_score())\n"
        "print(objectapi.module_dict_magic())\n"
        "print(objectapi.bump_module_global())\n"
        "print(objectapi.SEEN)\n"
        "print(objectapi.bump_module_global())\n"
        "print(objectapi.SEEN)\n"
        "print(objectapi.delete_module_global())\n"
        "print(objectapi.has_attr(objectapi, 'SEEN'))\n"
        "print(objectapi.str_repr_compare())\n"
        "print(objectapi.print_score())\n"
        "print(objectapi.unsigned_sum())\n"
        "print(objectapi.import_self_magic())\n"
        "print(objectapi.sequence_score())\n"
        "print(objectapi.iter_contains_score())\n"
        "print(objectapi.collection_views_score())\n"
        "print(objectapi.set_score())\n"
        "print(objectapi.attr_hash_score())\n"
        "print(objectapi.build_mem_kw_score())\n"
        "print(objectapi.refcnt_macro_score())\n"
        "print(objectapi.memory_os_score())\n"
        "print(objectapi.pythoncapi_compat_score())\n"
        "print(objectapi.mapping_long_exc_score())\n"
        "print(objectapi.call_type_format_score())\n"
        "print(objectapi.call_helper_score())\n"
        "print(objectapi.number_protocol_score())\n"
        "print(objectapi.number_bitwise_score())\n"
        "print(objectapi.has_attr(objectapi, 'CustomError'))\n"
        "print(objectapi.ADDED_REF)\n"
        "print(objectapi.MAGIC)\n"
        "print(objectapi.LABEL)\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe, runtime_cc="pcc")
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "42",
        "14",
        "42",
        "12",
        "198",
        "3210",
        "abc",
        "107",
        "1111",
        "511111111",
        "1111111111",
        "11111111111111",
        "17",
        "111111111",
        "64",
        "1",
        "0",
        "1",
        "11111",
        "111",
        "111111111",
        "8191",
        "13",
        "1",
        "1",
        "2",
        "2",
        "1",
        "0",
        "111111",
        "111",
        "11",
        "12",
        "71126",
        "1531111111",
        "1111112033",
        "1111111111",
        "6611123",
        "1468",
        "11",
        "11111111",
        "111111111",
        "841243",
        "172562",
        "711082",
        "1046015888",
        "8191",
        "1",
        "77",
        "12",
        "ok",
    ]


# Darwin host-extension errno is a named libSystem boundary
@pytest.mark.pcc_gate(probe=lambda: not (sys.platform != "darwin"))
def test_pcc_native_extension_errno_messages_use_saved_thread_errno(tmp_path):
    site = _compile_errno_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import errnoapi\n"
        "print(errnoapi.message())\n"
        "print(errnoapi.filename_message())\n"
        "print(errnoapi.unknown_message())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe, runtime_cc="pcc")
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert lines[:2] == [
        "No such file or directory",
        "No such file or directory: missing.dat",
    ]
    assert len(lines) == 3
    assert "9999" in lines[2]


def test_pcc_native_extension_missing_pyinit_symbol_fails_at_runtime(tmp_path):
    site = _compile_missing_init_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import badinit\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode != 0
    assert "dlsym failed" in run.stderr
    assert "PyInit_badinit" in run.stderr


def test_pcc_native_extension_init_returning_null_fails_at_runtime(tmp_path):
    site = _compile_null_init_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import nullinit\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode != 0
    assert "init failed intentionally" in run.stderr


def test_pcc_native_extension_failed_import_is_not_cached(tmp_path):
    site = _compile_retry_init_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "try:\n"
        "    import retryinit\n"
        "    print('unexpected-success')\n"
        "except Exception as exc:\n"
        "    print(str(exc))\n"
        "import retryinit\n"
        "print(retryinit.count())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe, {"PCC_RETRYINIT_FAIL_ONCE": "1"})
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["retry init failed once", "1"]


def test_pcc_native_extension_init_returning_null_without_error_reports_import_failure(
    tmp_path,
):
    site = _compile_silent_null_init_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import silentnullinit\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode != 0
    assert "native extension init failed" in run.stderr
    assert "silentnullinit" in run.stderr


def _compile_phasedemo_extension(tmp_path: Path) -> Path:
    # numpy-shaped multi-phase (PEP 489) extension covering the C-API shim
    # semantics the C->pcc-Python migration dropped (2026-08-08, see
    # docs/investigations/pcc1-pip-numpy-runtime-import-capi-regressions.md):
    # m_slots@64 (not m_doc@40), Py_mod_exec == 2, PyDict_SetItemString into
    # the live module dict, Py_BuildValue "{..}" pairs, sys.flags.optimize,
    # PyObject_CallFunction full formats, METH_FASTCALL|METH_KEYWORDS nargs,
    # PyArg_ParseTupleAndKeywords, PyMemberDef T_CHAR/T_PYSSIZET codes,
    # tp_richcompare both-operand protocol, and nb_* right-operand dispatch.
    return _compile_extension(
        tmp_path,
        "phasedemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "#include <structmember.h>\n"
        "\n"
        "typedef struct { PyObject_HEAD Py_ssize_t psize; char letter; } PhObject;\n"
        "\n"
        "static PyObject *disp_richcompare(PyObject *l, PyObject *r, int op) {\n"
        "    (void)l;\n"
        "    (void)r;\n"
        "    if (op == Py_EQ) return PyBool_FromLong(1);\n"
        "    Py_RETURN_NOTIMPLEMENTED;\n"
        "}\n"
        "\n"
        "static PyMemberDef DispMembers[] = {\n"
        '    {"psize", T_PYSSIZET, __builtin_offsetof(PhObject, psize), READONLY, NULL},\n'
        '    {"letter", T_CHAR, __builtin_offsetof(PhObject, letter), READONLY, NULL},\n'
        "    {NULL, 0, 0, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyTypeObject DispType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "phasedemo.Disp",\n'
        "    .tp_basicsize = sizeof(PhObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_members = DispMembers,\n"
        "    .tp_richcompare = disp_richcompare,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "\n"
        "static PyTypeObject NoCmpType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "phasedemo.NoCmp",\n'
        "    .tp_basicsize = sizeof(PhObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "\n"
        "static PyObject *ph_mul(PyObject *a, PyObject *b) {\n"
        "    long v = PyLong_AsLong(a);\n"
        "    if (PyErr_Occurred()) {\n"
        "        PyErr_Clear();\n"
        "        v = PyLong_AsLong(b);\n"
        "    }\n"
        "    if (PyErr_Occurred()) {\n"
        "        PyErr_Clear();\n"
        "        Py_RETURN_NOTIMPLEMENTED;\n"
        "    }\n"
        "    return PyLong_FromLong(v * 7);\n"
        "}\n"
        "\n"
        "static PyNumberMethods NumAsNumber = {\n"
        "    .nb_multiply = ph_mul,\n"
        "};\n"
        "\n"
        "static PyTypeObject NumType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "phasedemo.Num",\n'
        "    .tp_basicsize = sizeof(PhObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_as_number = &NumAsNumber,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "\n"
        "static PyObject *ph_callback(PyObject *self, PyObject *fn) {\n"
        "    (void)self;\n"
        '    return PyObject_CallFunction(fn, "Os", Py_None, "alias");\n'
        "}\n"
        "\n"
        "static PyObject *ph_fastkw(PyObject *self, PyObject *const *args,\n"
        "                           Py_ssize_t nargs, PyObject *kwnames) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    Py_ssize_t kwn = kwnames == NULL ? 0 : PyTuple_Size(kwnames);\n"
        '    return Py_BuildValue("nn", nargs, kwn);\n'
        "}\n"
        "\n"
        "static PyObject *ph_kwparse(PyObject *self, PyObject *args, PyObject *kwargs) {\n"
        "    (void)self;\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        '    static char *kwlist[] = {"a", "b", NULL};\n'
        '    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "l|l:kwparse", kwlist, &a, &b)) {\n'
        "        return NULL;\n"
        "    }\n"
        "    return PyLong_FromLong(a + b);\n"
        "}\n"
        "\n"
        "static PyMethodDef PhaseMethods[] = {\n"
        '    {"callback", ph_callback, METH_O, "call fn via CallFunction Os"},\n'
        '    {"fastkw", (PyCFunction)ph_fastkw, METH_FASTCALL | METH_KEYWORDS, "report nargs/kw"},\n'
        '    {"kwparse", (PyCFunction)ph_kwparse, METH_VARARGS | METH_KEYWORDS, "a+b"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static int phase_exec(PyObject *m) {\n"
        "    PyObject *d = PyModule_GetDict(m);\n"
        "    if (d == NULL) return -1;\n"
        "    if (PyType_Ready(&DispType) < 0) return -1;\n"
        "    if (PyType_Ready(&NoCmpType) < 0) return -1;\n"
        "    if (PyType_Ready(&NumType) < 0) return -1;\n"
        "    Py_INCREF((PyObject *)&DispType);\n"
        '    if (PyDict_SetItemString(d, "Disp", (PyObject *)&DispType) < 0) return -1;\n'
        "    Py_INCREF((PyObject *)&NoCmpType);\n"
        '    if (PyDict_SetItemString(d, "NoCmp", (PyObject *)&NoCmpType) < 0) return -1;\n'
        "    Py_INCREF((PyObject *)&NumType);\n"
        '    if (PyDict_SetItemString(d, "Num", (PyObject *)&NumType) < 0) return -1;\n'
        '    PyObject *bv = Py_BuildValue("{ss}", "k", "v");\n'
        "    if (bv == NULL) return -1;\n"
        '    if (PyDict_SetItemString(d, "bv", bv) < 0) { Py_DECREF(bv); return -1; }\n'
        "    Py_DECREF(bv);\n"
        '    PyObject *flags = PySys_GetObject("flags");\n'
        "    if (flags == NULL) return -1;\n"
        '    PyObject *opt = PyObject_GetAttrString(flags, "optimize");\n'
        "    if (opt == NULL) return -1;\n"
        '    if (PyDict_SetItemString(d, "optimize", opt) < 0) { Py_DECREF(opt); return -1; }\n'
        "    Py_DECREF(opt);\n"
        "    return 0;\n"
        "}\n"
        "\n"
        "static PyModuleDef_Slot phase_slots[] = {\n"
        "    {Py_mod_exec, phase_exec},\n"
        "    {0, NULL},\n"
        "};\n"
        "\n"
        "static struct PyModuleDef phasemodule = {\n"
        "    PyModuleDef_HEAD_INIT,\n"
        '    .m_name = "phasedemo",\n'
        '    .m_doc = "m_doc canary: reading m_doc as m_slots must not happen",\n'
        "    .m_size = 0,\n"
        "    .m_methods = PhaseMethods,\n"
        "    .m_slots = phase_slots,\n"
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_phasedemo(void) {\n"
        "    return PyModuleDef_Init(&phasemodule);\n"
        "}\n",
    )


def test_pcc_native_multiphase_capi_surface_default_runtime(tmp_path):
    """Multi-phase (PEP 489) numpy-shaped C-API surface under the DEFAULT
    runtime (pcc-Python ports linked; no PCC_RUNTIME_CC=cc): every assertion
    here corresponds to a hole the 93cfbca5-era C->pcc-Python migration
    dropped and numpy 2.4.6 import tripped over."""
    site = _compile_phasedemo_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import phasedemo\n"
        'print("disp", phasedemo.Disp is not None)\n'
        'print("bv", phasedemo.bv["k"])\n'
        'print("opt", phasedemo.optimize)\n'
        "def f(a, b):\n"
        "    return (a is None, b)\n"
        'print("cb", phasedemo.callback(f))\n'
        'print("fk", phasedemo.fastkw(1, 2, c=3))\n'
        'print("kw", phasedemo.kwparse(5, b=7))\n'
        "d = phasedemo.Disp()\n"
        'print("mem", d.psize, len(d.letter))\n'
        'print("eq", d == 42, 42 == d)\n'
        "n = phasedemo.NoCmp()\n"
        'print("ncmp", n == 42, n == n)\n'
        "x = phasedemo.Num()\n"
        'print("mul", 6 * x, x * 6)\n',
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env.pop("PCC_RUNTIME_CC", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_SELF_LINK"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run_env = env.copy()
    run_env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=run_env,
    )
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert "disp True" in lines, run.stdout
    assert "bv v" in lines, run.stdout
    assert "opt 0" in lines, run.stdout
    assert "cb (True, 'alias')" in lines, run.stdout
    assert "fk (2, 1)" in lines, run.stdout
    assert "kw 12" in lines, run.stdout
    assert "mem 0 1" in lines, run.stdout
    assert "eq True True" in lines, run.stdout
    assert "ncmp False True" in lines, run.stdout
    assert "mul 42 42" in lines, run.stdout
