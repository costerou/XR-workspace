"""Immutable diagnostic result models."""
from dataclasses import asdict, dataclass
import json
from typing import Literal


@dataclass(frozen=True)
class ProbeResult:
    status: Literal['ok', 'missing', 'permission_denied', 'timeout', 'error', 'truncated']
    data: object | None
    detail: str


@dataclass(frozen=True)
class SessionCandidate:
    instance: str
    pid: int
    wayland_socket: str | None
    outputs: list[dict]
    classification: Literal['physical', 'headless_only', 'unknown']


@dataclass(frozen=True)
class HostReport:
    schema_version: int
    collected_at: str
    host: dict
    session: dict
    usb: ProbeResult
    drm: ProbeResult
    capabilities: dict[str, dict]


def to_dict(value: ProbeResult | SessionCandidate | HostReport) -> dict:
    """Return a detached, recursively serialized snapshot of a model.

    Dataclasses freeze field assignment; their specified list/dict fields are
    not deeply immutable. Treat those fields as snapshots rather than mutating
    them. Validation of report schema/capability policy belongs to report.py.
    """
    return asdict(value)


def to_json(value: ProbeResult | SessionCandidate | HostReport) -> str:
    """Serialize deterministically, refusing non-JSON NaN/Infinity values."""
    return json.dumps(to_dict(value), ensure_ascii=False, sort_keys=True,
                      indent=2, allow_nan=False)
