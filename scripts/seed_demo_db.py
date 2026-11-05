"""Create a database full of SYNTHETIC telemetry for previewing the dashboard and for UI tests.

    python scripts/seed_demo_db.py data/demo.db
    set DB_PATH=data/demo.db   (then start the dashboard)

Never point this at the production database: it refuses to overwrite an existing file.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ispmon import db  # noqa: E402
from ispmon.config import TS_FMT  # noqa: E402

OLTS = {"10.0.0.11": 24, "10.0.0.12": 20}


def seed(path: Path, days: int = 3, step_s: int = 60, rng_seed: int = 7) -> None:
    rng = random.Random(rng_seed)
    conn = db.connect(path)
    db.migrate(conn)
    end = datetime.now().replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    times = []
    t = start
    gap_start, gap_end = end - timedelta(hours=40), end - timedelta(hours=37)  # collector blind spot
    while t <= end:
        if not (gap_start <= t < gap_end):
            times.append(t)
        t += timedelta(seconds=step_s)

    onus = [(olt, f"EPON0/1:{n}") for olt, count in OLTS.items() for n in range(1, count + 1)]
    base = {k: rng.uniform(-21, -8) for k in onus}
    base[("10.0.0.11", "EPON0/1:7")], base[("10.0.0.11", "EPON0/1:22")] = -25.8, -26.4
    base[("10.0.0.12", "EPON0/1:4")], base[("10.0.0.12", "EPON0/1:16")] = -24.6, -23.7
    drifting = ("10.0.0.12", "EPON0/1:16")          # slowly losing signal
    flat = {("10.0.0.11", f"EPON0/1:{n}") for n in (1, 2, 3)}  # readings that never change
    temp0 = {k: rng.uniform(36, 54) for k in onus}

    outages: dict[tuple, list] = {k: [] for k in onus}
    power_start = end - timedelta(hours=52)
    for n in range(1, 15):
        outages[("10.0.0.12", f"EPON0/1:{n}")].append((power_start + timedelta(seconds=n), power_start + timedelta(minutes=41), "Power Off"))
    outages[("10.0.0.12", "EPON0/1:18")].append((end - timedelta(hours=41), end - timedelta(hours=38), "Wire Down"))  # ends inside the collector gap
    outages[("10.0.0.11", "EPON0/1:5")].append((end - timedelta(hours=20), end - timedelta(hours=18), "Wire Down"))
    outages[("10.0.0.11", "EPON0/1:9")].append((end - timedelta(hours=6), end - timedelta(hours=5, minutes=30), "Power Off"))
    outages[("10.0.0.11", "EPON0/1:20")].append((end - timedelta(minutes=4), end + timedelta(hours=1), "Power Off"))

    def offline(key, when):
        return next((r for s, e, r in outages[key] if s <= when < e), None)

    previous: dict[tuple, str] = {}
    optics, status, events, heartbeat, devices = [], [], [], [], []
    for i, when in enumerate(times):
        ts = when.strftime(TS_FMT)
        after_gap = int(i > 0 and (when - times[i - 1]).total_seconds() > 5 * step_s)
        for key in onus:
            olt, onu = key
            reason = offline(key, when)
            state = "offline" if reason else "online"
            if key in previous and previous[key] != state:
                events.append((ts, olt, onu, "went_offline" if state == "offline" else "came_online",
                               reason or "Power Off", after_gap))
            previous[key] = state
            last_reason = reason or ("Wire Down" if key[1].endswith(":5") else "Power Off")
            status.append((ts, olt, onu, state, f"02:00:00:00:00:{int(onu.split(':')[1]):02x}", 150 + 25 * int(onu.split(':')[1]), 200,
                           "2044/06/01 08:00:00", "2044/06/01 07:59:00", last_reason, 0 if reason else 3600 + i * step_s))
            if reason:
                continue
            rx = base[key]
            if key == drifting:
                rx -= 0.9 * (i / len(times))
            if key not in flat:
                rx += rng.gauss(0, 0.12)
            hour = when.hour + when.minute / 60
            temp = temp0[key] + 3 * math.sin((hour - 9) / 24 * 2 * math.pi) + rng.gauss(0, 0.2)
            optics.append((ts, olt, onu, round(temp, 2), round(3.28 + rng.gauss(0, 0.01), 3), round(12 + rng.gauss(0, 0.4), 2),
                           round(2.2 + rng.gauss(0, 0.04), 2), round(rx, 2)))
        ok_olts = len(OLTS)
        heartbeat.append((ts, 1, ok_olts, len(onus), round(7 + rng.random() * 3, 1)))
        devices += [(ts, "mikrotik 172.17.1.1", 1, 12, 0.4, ""), (ts, "olt 10.0.0.11", 1, 24, 3.5, ""), (ts, "olt 10.0.0.12", 1, 20, 3.8, "")]

    conn.executemany("INSERT INTO olt_optical_stats (timestamp, olt_ip, onu_id, temperature, voltage, bias_current, tx_power, rx_power) VALUES (?,?,?,?,?,?,?,?)", optics)
    conn.executemany("INSERT INTO onu_status (timestamp, olt_ip, onu_id, status, mac, distance_m, rtt_tq, last_reg_time, last_dereg_time, last_dereg_reason, alive_seconds) VALUES (?,?,?,?,?,?,?,?,?,?,?)", status)
    conn.executemany("INSERT INTO onu_events (timestamp, olt_ip, onu_id, event, dereg_reason, after_gap) VALUES (?,?,?,?,?,?)", events)
    conn.executemany("INSERT INTO collector_heartbeat (timestamp, mikrotik_ok, olts_ok, onus_logged, cycle_seconds) VALUES (?,?,?,?,?)", heartbeat)
    conn.executemany("INSERT INTO device_heartbeat (timestamp, device, ok, items, duration_s, error) VALUES (?,?,?,?,?,?)", devices)

    counters = {"ether1": [0, 0], "ether2": [0, 0], "sfp1": [0, 0]}
    comments = {"ether1": "uplink", "ether2": "to-OLT-1", "sfp1": "to-OLT-2"}
    iface, health = [], []
    for when in times:
        ts = when.strftime(TS_FMT)
        load = 0.55 + 0.4 * math.sin((when.hour - 14) / 24 * 2 * math.pi)
        for name, c in counters.items():
            c[0] += int(abs(rng.gauss(90, 25)) * load * 1e6 / 8 * step_s / 10)
            c[1] += int(abs(rng.gauss(15, 5)) * load * 1e6 / 8 * step_s / 10)
            iface.append((ts, name, comments[name], 1, c[0], c[1], 0, 0))
        health.append((ts, int(min(95, abs(rng.gauss(12, 5)))), 380 + rng.gauss(0, 3)))
    conn.executemany("INSERT INTO interface_stats (timestamp, interface_name, interface_comment, running, rx_byte, tx_byte, rx_drop, tx_drop) VALUES (?,?,?,?,?,?,?,?)", iface)
    conn.executemany("INSERT INTO system_health (timestamp, cpu_load, free_memory_mb) VALUES (?,?,?)", health)

    today = end.strftime("%Y-%m-%d")
    logs = [(today, f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}", "system,error,critical",
             f"login failure for user {rng.choice(['admin', 'root', 'user'])} from {rng.choice(['203.0.113.5', '198.51.100.23'])} via ssh")
            for _ in range(40)]
    logs += [(today, f"10:{m:02d}:00", "system,info", f"item {m} changed by admin") for m in range(30)]
    conn.executemany("INSERT OR IGNORE INTO security_logs (ingest_date, router_time, topics, message) VALUES (?,?,?,?)", logs)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    if args.path.exists():
        sys.exit(f"{args.path} already exists; refusing to touch it.")
    seed(args.path, args.days)
    print(f"Demo database written to {args.path} (synthetic data).")
