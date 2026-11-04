"""Checked constructor declarations in the native static-only source domain.

An empty Java constructor is not a no-op native function: invoking it would
still require Object construction and managed instance publication. Retain the
original declaration, including default-constructor suppression, but do not
bind a constructor target or grant an instance ABI. Every native source method
is checked; construction expressions remain rejected even in uncalled bodies.

Recheck the actual mutable AST at declaration binding and before lowering.
A remembered admission flag would not protect against later body mutation.
The richer nonlowering class model has its own constructor semantic owner.
"""


def check_static_constructor_declarations(program):
    signatures = set()
    for constructor in program.constructors:
        signature = constructor.parameter_types
        if signature in signatures:
            constructor.token.error("duplicate constructor signature")
        signatures.add(signature)
        if (constructor.modifiers - {"private", "protected", "public"}
                or len(constructor.modifiers & {"private", "protected", "public"}) > 1):
            constructor.token.error("invalid constructor modifier")
    # Validate signatures across the retained declaration set before applying
    # the narrow native capability domain to any one overload.
    for constructor in program.constructors:
        if (constructor.is_constructor is not True or constructor.implicit is not False
                or constructor.token.text != program.simple_name
                or constructor.owner != program.name or constructor.result != "void"
                or constructor.modifiers != {"private"}
                or constructor.parameters or constructor.parameter_types or constructor.parameter_final
                or constructor.body.kind != "block" or constructor.body.children
                or constructor.locals or constructor.constructor_target is not None
                or constructor.initialization_steps
                or program.instance_initializers or program.initialization_steps
                or any("static" not in field.modifiers for field in program.fields)
                or any("static" not in method.modifiers for method in program.methods)):
            constructor.token.error(
                "native static source admits only private no-argument empty constructor declarations; "
                "constructor execution requires the shared managed runtime",
                code="java.unsupported.constructor-execution")
