"""A Python edition of the church-turing development.

Each module here is ordinary Python and is also checked by ``pcc.proof``:

    python -m pcc.proof check tests/proof/editions/church_turing

The modules follow the Agda edition one for one and keep its names where
Python allows.  The directory above this package is the import root, so the
package can be moved as it is.

===================  =====================  ================================
module               Agda module            contents
===================  =====================  ================================
``lambda_de_bruijn``  ``LambdaDeBruijn``     terms, lifting, substitution
``lambda_untyped``    ``LambdaUntyped``      beta reduction and its closure
``demo``              ``Demo`` (part)        closed examples and their values
===================  =====================  ================================
"""
