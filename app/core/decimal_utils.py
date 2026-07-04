"""
Small shared utility: Python's Decimal division can produce results like
Decimal('0E+4') when dividing a zero numerator by an operand that carries
extra scale (e.g. a Numeric(20,4) column value such as Decimal('10000.0000')).
This is mathematically correct but renders as confusing scientific notation
("0E+4") when serialized to JSON.

`clean_decimal` quantizes to a fixed number of decimal places, which always
produces a plain fixed-point representation -- simpler and more predictable
than `.normalize()`, which can itself flip a clean round number like
Decimal('100') into exponential form (Decimal('1E+2')).

Only needed at the boundary where a *computed ratio/percentage* (as opposed
to a plain DB column value or a sum/difference) is placed into an API
response -- see risk.py and portfolio_service.py for the call sites where
this actually occurs.
"""
from decimal import Decimal

_DEFAULT_PLACES = Decimal("0.0001")


def clean_decimal(value: Decimal, places: Decimal = _DEFAULT_PLACES) -> Decimal:
    return value.quantize(places)
