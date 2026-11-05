"""Runtime configuration, read from the environment / .env file. No secrets live in code."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TS_FMT = "%Y-%m-%d %H:%M:%S"  # timestamps are stored as local time (Pakistan has no DST)


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _number(name: str, default, cast):
    raw = _get(name)
    if raw is None:
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = _get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> tuple[str, ...]:
    raw = _get(name, default) or ""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Thresholds:
    rx_warn_dbm: float
    rx_crit_dbm: float
    cpu_pct: int
    collector_stale_s: int
    mass_drop_min_onus: int
    mass_drop_window_s: int


@dataclass(frozen=True)
class Settings:
    db_path: Path
    log_dir: Path
    log_level: str
    interval_s: int
    mk_host: str
    mk_port: int
    mk_user: str
    mk_password: str
    mk_use_ssl: bool
    olt_hosts: tuple[str, ...]
    olt_user: str
    olt_password: str
    pon_ports: tuple[str, ...]
    olt_cmd_wait_s: float
    olt_connect_timeout_s: float
    retention_interface_days: int
    retention_log_days: int
    thresholds: Thresholds

    def require_collector_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("MK_USER", self.mk_user),
                ("MK_PASS", self.mk_password),
                ("OLT_USER", self.olt_user),
                ("OLT_PASS", self.olt_password),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing required settings: " + ", ".join(missing) + ". Add them to .env (see .env.example)."
            )
        if not self.olt_hosts:
            raise ConfigError("OLTS is empty. List the OLT addresses, comma separated, in .env.")


def load_settings() -> Settings:
    db_path = Path(_get("DB_PATH", str(ROOT / "data" / "isp_telemetry.db")))
    log_dir = Path(_get("LOG_DIR", str(ROOT / "logs")))
    return Settings(
        db_path=db_path if db_path.is_absolute() else ROOT / db_path,
        log_dir=log_dir if log_dir.is_absolute() else ROOT / log_dir,
        log_level=(_get("LOG_LEVEL", "INFO") or "INFO").upper(),
        interval_s=_number("INTERVAL_S", 60, int),
        mk_host=_get("MK_HOST", "172.17.1.1") or "",
        mk_port=_number("MK_PORT", 8728, int),
        mk_user=_get("MK_USER", "") or "",
        mk_password=_get("MK_PASS", "") or "",
        mk_use_ssl=_bool("MK_USE_SSL", False),
        olt_hosts=_list("OLTS", "172.30.49.11,172.30.49.12"),
        olt_user=_get("OLT_USER", "") or "",
        olt_password=_get("OLT_PASS", "") or "",
        pon_ports=_list("PON_PORTS", "0/1"),
        olt_cmd_wait_s=_number("OLT_CMD_WAIT_S", 3.0, float),
        olt_connect_timeout_s=_number("OLT_CONNECT_TIMEOUT_S", 10.0, float),
        retention_interface_days=_number("RETENTION_INTERFACE_DAYS", 90, int),
        retention_log_days=_number("RETENTION_LOG_DAYS", 180, int),
        thresholds=Thresholds(
            rx_warn_dbm=_number("RX_WARN_DBM", -24.0, float),
            rx_crit_dbm=_number("RX_CRIT_DBM", -25.0, float),
            cpu_pct=_number("CPU_ALERT_PCT", 80, int),
            collector_stale_s=_number("COLLECTOR_STALE_S", 180, int),
            mass_drop_min_onus=_number("MASS_DROP_MIN_ONUS", 5, int),
            mass_drop_window_s=_number("MASS_DROP_WINDOW_S", 120, int),
        ),
    )
