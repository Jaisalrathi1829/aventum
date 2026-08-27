"""
Deterministic pseudo-random streams for synthetic generation.

Determinism contract (Day 2B §13): the same
    (transaction_id, source_ingestion_run_id, generation_config_version, generation_seed)
must always produce the same synthetic assignment.

Therefore this module:
  - uses hashlib.sha256, NOT Python's built-in hash() -- the latter is salted per
    process via PYTHONHASHSEED and would silently differ between runs;
  - never consults wall-clock time, row order, insertion order, or DB state;
  - derives several INDEPENDENT streams from one digest by slicing disjoint byte ranges,
    so drawing a gateway cannot perturb the latency draw.

One SHA-256 per transaction yields 256 bits, sliced into four 64-bit lanes.
"""

from __future__ import annotations

import hashlib
from statistics import NormalDist

# Lane assignments over the 32-byte digest. Disjoint by construction.
LANE_GATEWAY = 0        # bytes 0-7   -- routing selection
LANE_RESPONSE = 1       # bytes 8-15  -- response/error family selection
LANE_LATENCY = 2        # bytes 16-23 -- latency draw
LANE_RESERVED = 3       # bytes 24-31 -- reserved for Day 2C (incident-time draws)

_LANE_COUNT = 4
_U64_MAX = float(1 << 64)

# Module-level: constructing NormalDist per call would dominate the runtime.
_STANDARD_NORMAL = NormalDist(0.0, 1.0)


def assignment_key(
    transaction_id: str,
    source_ingestion_run_id: int,
    generation_config_version: str,
    generation_seed: str,
) -> str:
    """
    The canonical, stable key string that identifies one synthetic draw.

    Field order and the '|' separator are part of the determinism contract: changing
    either changes every downstream value, so they must not be edited casually.
    Including the ingestion run means synthetic values are bound to the exact canonical
    load they were generated against.
    """
    return (
        f"{transaction_id}|{source_ingestion_run_id}|"
        f"{generation_config_version}|{generation_seed}"
    )


def digest_for(
    transaction_id: str,
    source_ingestion_run_id: int,
    generation_config_version: str,
    generation_seed: str,
) -> bytes:
    key = assignment_key(
        transaction_id, source_ingestion_run_id, generation_config_version, generation_seed
    )
    return hashlib.sha256(key.encode("utf-8")).digest()


def lane_uniform(digest: bytes, lane: int) -> float:
    """
    Draw a uniform value in [0, 1) from one 64-bit lane of the digest.

    Deterministic and stable across platforms and Python versions: it reads fixed bytes
    big-endian and divides by 2**64.
    """
    if not 0 <= lane < _LANE_COUNT:
        raise ValueError(f"lane must be in 0..{_LANE_COUNT - 1}, got {lane}")
    start = lane * 8
    value = int.from_bytes(digest[start:start + 8], byteorder="big", signed=False)
    return value / _U64_MAX


def weighted_choice(uniform: float, options: list[tuple[str, float]]) -> str:
    """
    Pick an option by cumulative weight from a pre-drawn uniform.

    `options` must be supplied in a STABLE order (callers sort by key) so the mapping
    from uniform value to outcome is reproducible. Weights need not sum to 1; they are
    normalised here.
    """
    if not options:
        raise ValueError("weighted_choice requires at least one option")

    total = sum(weight for _, weight in options)
    if total <= 0:
        raise ValueError("weighted_choice requires a positive total weight")

    threshold = uniform * total
    cumulative = 0.0
    for name, weight in options:
        cumulative += weight
        if threshold < cumulative:
            return name
    # Floating-point guard: fall through to the last option rather than raising.
    return options[-1][0]


def lognormal_from_uniform(
    uniform: float,
    median: float,
    sigma: float,
    floor: float,
    cap: float,
) -> float:
    """
    Inverse-CDF lognormal draw, giving the right-skewed shape real latency exhibits.

    Uses the inverse normal CDF rather than a sampling loop so the value is a pure
    deterministic function of the uniform. `median` is the lognormal median (exp(mu)),
    which is the intuitive knob for a latency centre. The result is clamped to
    [floor, cap] to keep every regime inside its documented band -- this is what stops
    the NORMAL regime from ever emitting a timeout-looking value.
    """
    # Guard the open interval: inv_cdf is undefined at exactly 0 or 1.
    clamped = min(max(uniform, 1e-12), 1.0 - 1e-12)
    z = _STANDARD_NORMAL.inv_cdf(clamped)
    value = median * pow(2.718281828459045, sigma * z)
    return min(max(value, floor), cap)
