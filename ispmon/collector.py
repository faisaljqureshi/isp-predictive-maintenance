"""Telemetry collector service: polls the router and OLTs and stores everything in SQLite.

Run:  python -m ispmon.collector          (continuous)
      python -m ispmon.collector --once   (single cycle, handy for checking connectivity)
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from ispmon import db
from ispmon.config import TS_FMT, ConfigError, Settings, load_settings
from ispmon.events import State, detect_events
from ispmon.log import setup_logging
from ispmon.mikrotik import MikroTikClient, MikroTikSample
from ispmon.olt import OltClient
from ispmon.parsers import OnuOptics, OnuStatus

log = logging.getLogger("ispmon.collector")


@dataclass
class OltResult:
    host: str
    ok: bool
    duration_s: float
    optics: list[OnuOptics] = field(default_factory=list)
    status: list[OnuStatus] = field(default_factory=list)
    error: str = ""


class Collector:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.stop = threading.Event()
        self.conn = db.connect(settings.db_path)
        db.migrate(self.conn)
        self.mikrotik = MikroTikClient(
            settings.mk_host, settings.mk_user, settings.mk_password, settings.mk_port, settings.mk_use_ssl
        )
        self.olts = {
            host: OltClient(
                host,
                settings.olt_user,
                settings.olt_password,
                settings.pon_ports,
                settings.olt_cmd_wait_s,
                settings.olt_connect_timeout_s,
            )
            for host in settings.olt_hosts
        }
        self.pool = ThreadPoolExecutor(max_workers=max(1, len(self.olts)), thread_name_prefix="olt")
        self.previous: dict[tuple[str, str], State] = self._load_previous_states()
        self.last_seen: dict[str, datetime] = self._load_last_seen()

    # -- startup state ------------------------------------------------------------------------
    def _load_previous_states(self) -> dict[tuple[str, str], State]:
        rows = self.conn.execute(
            """SELECT s.olt_ip, s.onu_id, s.status, s.last_reg_time FROM onu_status s
               JOIN (SELECT olt_ip, MAX(timestamp) AS ts FROM onu_status GROUP BY olt_ip) m
                 ON s.olt_ip = m.olt_ip AND s.timestamp = m.ts"""
        ).fetchall()
        return {(r[0], r[1]): (r[2], r[3]) for r in rows}

    def _load_last_seen(self) -> dict[str, datetime]:
        rows = self.conn.execute("SELECT olt_ip, MAX(timestamp) FROM onu_status GROUP BY olt_ip").fetchall()
        return {r[0]: datetime.strptime(r[1], TS_FMT) for r in rows if r[1]}

    # -- per-device collection (never raises) --------------------------------------------------
    def _poll_mikrotik(self) -> tuple[MikroTikSample | None, float, str]:
        started = time.monotonic()
        try:
            return self.mikrotik.collect(), time.monotonic() - started, ""
        except Exception as exc:  # noqa: BLE001 - one failing device must not stop the cycle
            log.warning("MikroTik %s failed: %s", self.s.mk_host, exc)
            return None, time.monotonic() - started, str(exc)[:200]

    def _poll_olt(self, host: str) -> OltResult:
        started = time.monotonic()
        try:
            optics, status = self.olts[host].collect()
            return OltResult(host, True, time.monotonic() - started, optics, status)
        except Exception as exc:  # noqa: BLE001
            return OltResult(host, False, time.monotonic() - started, error=str(exc)[:200])

    # -- one cycle ----------------------------------------------------------------------------
    def cycle(self) -> dict:
        started = time.monotonic()
        now = datetime.now()
        ts = now.strftime(TS_FMT)

        sample, mk_duration, mk_error = self._poll_mikrotik()
        results = list(self.pool.map(self._poll_olt, self.olts))

        new_events = 0
        with self.conn:  # one transaction per cycle
            c = self.conn
            if sample is not None:
                c.execute(
                    "INSERT INTO system_health (timestamp, cpu_load, free_memory_mb) VALUES (?,?,?)",
                    (ts, sample.cpu_load, sample.free_memory_mb),
                )
                c.executemany(
                    "INSERT INTO interface_stats (timestamp, interface_name, interface_comment, running,"
                    " rx_byte, tx_byte, rx_drop, tx_drop) VALUES (?,?,?,?,?,?,?,?)",
                    [(ts, *row) for row in sample.interfaces],
                )
                c.executemany(
                    "INSERT OR IGNORE INTO security_logs (ingest_date, router_time, topics, message) VALUES (?,?,?,?)",
                    [(ts[:10], *row) for row in sample.logs],
                )
            c.execute(
                "INSERT INTO device_heartbeat (timestamp, device, ok, items, duration_s, error) VALUES (?,?,?,?,?,?)",
                (ts, f"mikrotik {self.s.mk_host}", int(sample is not None),
                 len(sample.interfaces) if sample else 0, mk_duration, mk_error),
            )
            onus_logged = 0
            for res in results:
                c.execute(
                    "INSERT INTO device_heartbeat (timestamp, device, ok, items, duration_s, error) VALUES (?,?,?,?,?,?)",
                    (ts, f"olt {res.host}", int(res.ok), len(res.status), res.duration_s, res.error),
                )
                if not res.ok:
                    continue  # a failed scrape must never mark ONUs offline
                onus_logged += len(res.status)
                c.executemany(
                    "INSERT INTO olt_optical_stats (timestamp, olt_ip, onu_id, temperature, voltage, bias_current,"
                    " tx_power, rx_power) VALUES (?,?,?,?,?,?,?,?)",
                    [(ts, res.host, o.onu_id, o.temperature, o.voltage, o.bias_current, o.tx_power, o.rx_power)
                     for o in res.optics],
                )
                c.executemany(
                    "INSERT INTO onu_status (timestamp, olt_ip, onu_id, status, mac, distance_m, rtt_tq,"
                    " last_reg_time, last_dereg_time, last_dereg_reason, alive_seconds) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(ts, res.host, o.onu_id, o.status, o.mac, o.distance_m, o.rtt_tq, o.last_reg_time,
                      o.last_dereg_time, o.last_dereg_reason, o.alive_seconds) for o in res.status],
                )
                last = self.last_seen.get(res.host)
                after_gap = last is not None and (now - last).total_seconds() > 5 * self.s.interval_s
                events = detect_events(self.previous, res.host, res.status, ts, after_gap)
                c.executemany(
                    "INSERT INTO onu_events (timestamp, olt_ip, onu_id, event, dereg_reason, after_gap)"
                    " VALUES (?,?,?,?,?,?)",
                    events,
                )
                self.last_seen[res.host] = now
                new_events += len(events)
            elapsed = time.monotonic() - started
            c.execute(
                "INSERT INTO collector_heartbeat (timestamp, mikrotik_ok, olts_ok, onus_logged, cycle_seconds)"
                " VALUES (?,?,?,?,?)",
                (ts, int(sample is not None), sum(r.ok for r in results), onus_logged, elapsed),
            )

        summary = {
            "timestamp": ts,
            "mikrotik_ok": sample is not None,
            "olts": {r.host: (len(r.status) if r.ok else f"FAILED: {r.error}") for r in results},
            "events": new_events,
            "seconds": round(elapsed, 1),
        }
        log.info(
            "cycle ok: mikrotik=%s olts=%s events=%d (%.1fs)",
            "ok" if sample else "FAIL", summary["olts"], new_events, elapsed,
        )
        return summary

    # -- lifecycle ----------------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        def _stop(signum, _frame):
            log.info("received signal %s, shutting down after this cycle", signum)
            self.stop.set()

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _stop)
                except ValueError:  # not in the main thread
                    pass

    def run(self) -> None:
        self._install_signal_handlers()
        log.info(
            "collector started: router=%s olts=%s interval=%ss db=%s",
            self.s.mk_host, ",".join(self.olts), self.s.interval_s, self.s.db_path,
        )
        last_prune: float | None = None
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.cycle()
            except Exception:  # noqa: BLE001 - keep the service alive whatever happens
                log.exception("cycle failed")
            if last_prune is None or time.monotonic() - last_prune > 86400:
                try:
                    log.info("retention: removed %s", db.prune(
                        self.conn, self.s.retention_interface_days, self.s.retention_log_days))
                except Exception:  # noqa: BLE001
                    log.exception("retention job failed")
                last_prune = time.monotonic()
            self.stop.wait(max(1.0, self.s.interval_s - (time.monotonic() - started)))
        self.shutdown()

    def shutdown(self) -> None:
        log.info("closing connections")
        self.pool.shutdown(wait=False, cancel_futures=True)
        for client in self.olts.values():
            client.close()
        self.mikrotik.close()
        self.conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ISP telemetry collector")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        settings.require_collector_credentials()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    setup_logging("collector", settings.log_dir, settings.log_level)
    collector = Collector(settings)
    if args.once:
        try:
            collector.cycle()
        finally:
            collector.shutdown()
        return 0
    collector.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
