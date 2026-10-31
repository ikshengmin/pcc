"""Source-independent Java diagnostics; no compatibility decoder dependency."""


class JavaError(ValueError):
    """Invalid Java input or an explicitly unsupported compiler capability."""

    code = None


class UnsupportedJava(JavaError):
    """An explicit capability boundary, optionally carrying a stable code.

    Source entrypoints always supply a ``java.unsupported.*`` code. Frozen
    class-file compatibility callers retain their message-only API. The code
    is machine-readable metadata: ``str(error)`` and ``error.args`` preserve
    the original diagnostic text, including source locations.
    """

    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code
