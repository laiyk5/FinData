from __future__ import annotations

from collections.abc import Iterable
import re


MINIMUM_PREFIX_LENGTH = 8
_LOWER_HEX = re.compile(r"[0-9a-f]+\Z")


class InvalidIdentifierError(ValueError):
    pass


class IdentifierNotFoundError(LookupError):
    pass


class AmbiguousIdentifierError(RuntimeError):
    pass


def resolve_identifier(operand: str, candidates: Iterable[str]) -> str:
    """Resolve a full lowercase-hex identifier or a sufficiently long unique prefix."""
    value = str(operand)
    if not _LOWER_HEX.fullmatch(value):
        raise InvalidIdentifierError("identifier must contain lowercase hexadecimal characters")

    available = tuple(candidates)
    if value in available:
        return value
    if len(value) < MINIMUM_PREFIX_LENGTH:
        raise InvalidIdentifierError(
            f"identifier prefix must contain at least {MINIMUM_PREFIX_LENGTH} characters"
        )

    matches = [candidate for candidate in available if candidate.startswith(value)]
    if not matches:
        raise IdentifierNotFoundError(value)
    if len(matches) > 1:
        raise AmbiguousIdentifierError(f"identifier prefix {value!r} is ambiguous")
    return matches[0]
