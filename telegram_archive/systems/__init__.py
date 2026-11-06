"""Archive X modular system boundaries.

V28 introduces stable interfaces around the existing V27 implementation.  The
initial migration is deliberately additive: existing routes and services keep
running while new code can depend on these boundaries instead of importing
implementation modules directly.
"""
