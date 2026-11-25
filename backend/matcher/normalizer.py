"""Cheap, notation-preserving normalization; algebra belongs in an opt-in tier."""
from functools import lru_cache

from backend.matcher.notation import notation, serialize


@lru_cache(maxsize=8192)
def normalize_latex(expr: str) -> str:
    return serialize(notation(expr).tokens)
