"""Source-derived crate injection policy, not an external-check permission.

Pinned rustc 1.90 standard_library_imports::inject suppresses both the implicit
library item and its edition prelude for no_core. no_std selects core instead
of std; it is not equivalent to no_core. These are planned dependencies, never
fabricated definitions in the owned resolver. Lang-item registration/semantics
remain required before a no_core crate can enter the existing type checker.
"""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class CratePolicy:
    edition: int = 2021
    no_std: object = None
    no_core: object = None

    def with_no_std(self, token):
        return replace(self, no_std=token)

    def with_no_core(self, token):
        return replace(self, no_core=token)

    @property
    def implicit_library_import(self):
        if self.no_core is not None:
            return None
        return 'core' if self.no_std is not None else 'std'

    @property
    def implicit_extern_prelude(self):
        if self.no_core is not None:
            return ()
        return ('core',) if self.no_std is not None else ('core', 'std')

    @property
    def standard_prelude_path(self):
        library = self.implicit_library_import
        return None if library is None else (library, 'prelude', 'rust_' + str(self.edition))

    def require_owned_lang_item_context(self):
        if self.no_core is not None:
            # No marker spelling, user-defined Sized/Copy name, caller flag or
            # checked-source token establishes a canonical lang item. Until
            # actual registration and its required semantics exist, do not
            # reuse normal-crate primitive Copy or implicit Sized assumptions.
            raise self.no_core.error(
                'no_core requires an owned compiler/core context for prelude suppression and lang-item obligations; '
                'canonical lang-item registration and semantics are not implemented')
