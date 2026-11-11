"""A dependent type checker for a total fragment of Python.

``pcc.proof`` reads ordinary Python modules twice.  The execution reading is
Python's own: CPython runs the module and pcc compiles it.  The logical reading
treats a restricted fragment as terms of a dependent type theory: annotations
are types, indexed constructor classes are inductive families, and a function
whose result type is a proposition is a proof.

Layers, from trusted to untrusted:

``terms``      core terms, values and case trees
``machine``    evaluation, forcing, readback and conversion
``kernel``     typing, inductive families, case trees, coverage, termination
``surface``    Python syntax to a small surface tree (fragment validation)
``elaborate``  implicit arguments, pattern compilation, operators

The elaborator produces fully explicit core declarations.  The kernel checks
every one of them again and has no knowledge of metavariables or of Python.

Only the Python standard library is used.
"""
