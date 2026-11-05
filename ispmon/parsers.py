"""Parsers for the V-SOL OLT CLI tables. Pure functions: text in, typed rows out."""
from __future__ import annotations

import re
from dataclasses import dataclass

OPM_RE = re.compile(
    r"^\s*(EPON\S+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$"
)
_TS = r"(?:\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}|N/A)"
STATUS_RE = re.compile(
    r"^\s*(EPON\S+)\s+(online|offline)\s+([0-9a-fA-F:]{17})\s+(\d+)\s+(\d+)\s+"
    rf"({_TS})\s+({_TS})\s+(.+?)\s+(?:(\d+)\s+)?(\d{{2}}:\d{{2}}:\d{{2}})\s+(\S+)\s*$"
)


@dataclass(frozen=True, slots=True)
class OnuOptics:
    onu_id: str
    temperature: float
    voltage: float
    bias_current: float
    tx_power: float
    rx_power: float


@dataclass(frozen=True, slots=True)
class OnuStatus:
    onu_id: str
    status: str
    mac: str
    distance_m: int
    rtt_tq: int
    last_reg_time: str
    last_dereg_time: str
    last_dereg_reason: str
    alive_seconds: int


def alive_to_seconds(days: str | None, hms: str) -> int:
    hours, minutes, seconds = (int(part) for part in hms.split(":"))
    return int(days or 0) * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_optics(text: str) -> list[OnuOptics]:
    rows = []
    for line in text.splitlines():
        match = OPM_RE.match(line)
        if match:
            rows.append(
                OnuOptics(
                    onu_id=match.group(1),
                    temperature=float(match.group(2)),
                    voltage=float(match.group(3)),
                    bias_current=float(match.group(4)),
                    tx_power=float(match.group(5)),
                    rx_power=float(match.group(6)),
                )
            )
    return rows


def parse_status(text: str) -> list[OnuStatus]:
    rows = []
    for line in text.splitlines():
        match = STATUS_RE.match(line)
        if match:
            rows.append(
                OnuStatus(
                    onu_id=match.group(1),
                    status=match.group(2),
                    mac=match.group(3).lower(),
                    distance_m=int(match.group(4)),
                    rtt_tq=int(match.group(5)),
                    last_reg_time=match.group(6),
                    last_dereg_time=match.group(7),
                    last_dereg_reason=match.group(8).strip(),
                    alive_seconds=alive_to_seconds(match.group(9), match.group(10)),
                )
            )
    return rows
