"""What the checked fragment needs at run time.

These names are primitives of the logic.  The checker knows what they mean and
never reads this file; Python needs the definitions below so that a checked
module is also an ordinary importable, runnable module.

==============  ==========================  =================================
name            in the logic                when the module runs
==============  ==========================  =================================
``Type``        the universe of types       a subscriptable placeholder
``Type1``       the universe above it       a subscriptable placeholder
``Pi``, ``Fn``  function types              subscriptable placeholders
``Implicit``    marks an implicit family    a subscriptable placeholder
                parameter
``Data``        base of inductive families  a base class
``Nat``         zero and successor          ``int`` (non-negative)
``Bool``        false and true              ``bool``
``suc``         the successor constructor   ``n + 1``
``impossible``  a case with no instances    raises if ever reached
==============  ==========================  =================================
"""

from __future__ import annotations


class _TypeForm:
    """A name that only ever appears inside types."""

    __slots__ = ("_name",)

    def __init__(self, name):
        self._name = name

    def __getitem__(self, item):
        return self

    def __repr__(self):
        return self._name


Type = _TypeForm("Type")
Type1 = _TypeForm("Type1")
Pi = _TypeForm("Pi")
Fn = _TypeForm("Fn")
Implicit = _TypeForm("Implicit")


class Data:
    """Base class of every inductive family.

    Subscripting a family, as in ``Step[t, u]``, is how the source writes a
    type.  At run time it gives back the class, so a constructor may name its
    indexed family as a base.
    """

    __slots__ = ()

    def __class_getitem__(cls, item):
        return cls


Nat = int
Bool = bool


def suc(n):
    return n + 1


def impossible(witness):
    raise AssertionError("a case the checker proved impossible was reached")
