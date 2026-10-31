"""Source-independent Java diagnostics; no compatibility decoder dependency."""


class JavaError(ValueError):
    """Invalid Java input or an explicitly unsupported compiler capability."""


class UnsupportedJava(JavaError):
    """A Java feature outside the currently implemented source/runtime slice."""
