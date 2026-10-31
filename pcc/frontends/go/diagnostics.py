"""Stable Go capability diagnostics, separate from invalid-program errors.

Call sites choose codes explicitly. Messages are explanatory text, never a
classifier or a dispatch key. The canonical feature map owns each feature ID.
"""


UNIMPLEMENTED_FEATURES = {
    'go.unsupported.aggregate-comparison': 'go.capability.aggregate-comparison',
    'go.unsupported.aggregate-switch': 'go.capability.aggregate-switch',
    'go.unsupported.array-bounds-panic': 'go.capability.array-bounds-panic',
    'go.unsupported.array-element-types': 'go.capability.array-element-types',
    'go.unsupported.blank-function-declarations': 'go.capability.blank-function-declarations',
    'go.unsupported.blank-method-declarations': 'go.capability.blank-method-declarations',
    'go.unsupported.blank-package-variables': 'go.capability.blank-package-variables',
    'go.unsupported.blank-struct-fields': 'go.capability.blank-struct-fields',
    'go.unsupported.blank-type-declarations': 'go.capability.blank-type-declarations',
    'go.unsupported.complex-types': 'go.capability.complex-types',
    'go.unsupported.division-panic': 'go.capability.division-panic',
    'go.unsupported.dot-imports': 'go.capability.dot-imports',
    'go.unsupported.dynamic-string-concatenation': 'go.capability.dynamic-string-concatenation',
    'go.unsupported.dynamic-string-conversion': 'go.capability.dynamic-string-conversion',
    'go.unsupported.embedded-fields': 'go.capability.embedded-fields',
    'go.unsupported.floating-types': 'go.capability.floating-types',
    'go.unsupported.interface-types': 'go.capability.interface-types',
    'go.unsupported.limit-array-elements': 'go.capability.limit-array-elements',
    'go.unsupported.limit-constant-precision': 'go.capability.limit-constant-precision',
    'go.unsupported.limit-function-arity': 'go.capability.limit-function-arity',
    'go.unsupported.limit-record-layout': 'go.capability.limit-record-layout',
    'go.unsupported.limit-string-bytes': 'go.capability.limit-string-bytes',
    'go.unsupported.limit-struct-fields': 'go.capability.limit-struct-fields',
    'go.unsupported.limit-type-expansion': 'go.capability.limit-type-expansion',
    'go.unsupported.local-type-declarations': 'go.capability.local-type-declarations',
    'go.unsupported.method-values': 'go.capability.method-values',
    'go.unsupported.negative-shift-panic': 'go.capability.negative-shift-panic',
    'go.unsupported.non-array-range': 'go.capability.non-array-range',
    'go.unsupported.package-initialization': 'go.capability.package-initialization',
    'go.unsupported.pointer-receivers': 'go.capability.pointer-receivers',
    'go.unsupported.predeclared-shadowing': 'go.capability.predeclared-shadowing',
    'go.unsupported.slice-storage': 'go.capability.slice-storage',
    'go.unsupported.string-bounds-panic': 'go.capability.string-bounds-panic',
    'go.unsupported.struct-tags': 'go.capability.struct-tags',
}


class GoDiagnostic(ValueError):
    """Source-located diagnostic; uncoded legacy sites are not fully classified."""
    code = None
    feature_id = None
    kind = "unclassified"

    def __init__(self, token, message):
        self.filename, self.line, self.column = token.filename, token.line, token.column
        self.message = message
        suffix = " [" + self.code + "]" if self.code is not None else ""
        super().__init__(f"{self.filename}:{self.line}:{self.column}: Go: {message}{suffix}")


class UnsupportedGo(GoDiagnostic):
    """A recognized but unimplemented capability or implementation limit."""
    kind = "unsupported"

    def __init__(self, token, code, message):
        if code not in UNIMPLEMENTED_FEATURES:
            raise ValueError("unknown Go capability diagnostic code: " + str(code))
        self.code = code
        self.feature_id = UNIMPLEMENTED_FEATURES[code]
        super().__init__(token, message)
