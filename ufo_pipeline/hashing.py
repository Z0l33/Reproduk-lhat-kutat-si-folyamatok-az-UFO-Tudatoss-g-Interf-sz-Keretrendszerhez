"""Streaming file hashing and checksum comparison."""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Dict, Iterable, Tuple, Union

CHUNK_SIZE = 1024 * 1024
SUPPORTED_ALGORITHMS = ("md5", "sha1", "sha256", "sha512")
_HEX_LENGTH_TO_ALGORITHM = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}

PathLike = Union[str, Path]


def hash_file(path: PathLike, algorithms: Iterable[str] = ("sha256",)) -> Dict[str, str]:
    """Hash a file in a single streaming pass with every requested algorithm."""
    algorithms = tuple(algorithms)
    for name in algorithms:
        if name not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported hash algorithm: {name}")
    hashers = {name: hashlib.new(name) for name in algorithms}
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            for hasher in hashers.values():
                hasher.update(chunk)
    return {name: hasher.hexdigest() for name, hasher in hashers.items()}


def sha256_file(path: PathLike) -> str:
    return hash_file(path, ("sha256",))["sha256"]


def parse_checksum(value: str) -> Tuple[str, str]:
    """Parse 'md5:abcd...' (or a bare hex digest) into (algorithm, lowercase hex)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty checksum")
    value = value.strip().lower()
    if ":" in value:
        algorithm, digest = value.split(":", 1)
    else:
        digest = value
        algorithm = _HEX_LENGTH_TO_ALGORITHM.get(len(digest), "")
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(f"unsupported or unrecognised checksum algorithm in {value!r}")
    expected_length = {v: k for k, v in _HEX_LENGTH_TO_ALGORITHM.items()}[algorithm]
    if len(digest) != expected_length or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError(f"malformed {algorithm} digest: {value!r}")
    return algorithm, digest


def digests_equal(a: str, b: str) -> bool:
    """Constant-time comparison of two hex digests (case-insensitive)."""
    return hmac.compare_digest(a.strip().lower().encode(), b.strip().lower().encode())
