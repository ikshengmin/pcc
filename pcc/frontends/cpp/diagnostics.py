"""Stable, source-located diagnostics for the owned C++ frontend."""


class CppDiagnostic(ValueError):
    def __init__(self, code, message, coord):
        self.code = code
        self.message = message
        self.coord = coord
        super().__init__(f"{coord}: {code}: {message}")
