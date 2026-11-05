# ISP predictive maintenance

Collects telemetry from a MikroTik router and V-SOL EPON OLTs into SQLite, shows it in a multi-page dashboard,
and records ONU drop events as labelled failures for the prediction phase.

```
ispmon/            collector service and shared logic (no Streamlit imports)
  collector.py       python -m ispmon.collector
  olt.py mikrotik.py device clients      parsers.py events.py   pure parsing and state-change logic
  db.py              schema migration (idempotent) and retention
  analytics.py       throughput, health classes, outage grouping, data-quality checks
  alerts.py          alert rules; the dashboard shows them, a notifier can push them
dashboard/         Streamlit app (app.py, ui.py, data.py, views/*)
scripts/           backup_db.py, seed_demo_db.py (synthetic data only)
deploy/windows/    install_tasks.ps1
tests/             pytest suite; fixtures are your real OLT captures with MAC addresses anonymised
```

## Setup (Windows)

1. Unzip, open a terminal in the folder.
2. `python -m venv .venv` then `.venv\Scripts\activate` then `pip install -r requirements.txt`
3. `copy .env.example .env` and fill in the router and OLT credentials. Use a read-only router user.
4. **Existing data:** stop the old logger, copy `isp_telemetry.db` to `data\isp_telemetry.db` (keep the original as a backup).
   The first run upgrades the schema in place. Or leave the file where it is and set `DB_PATH` in `.env`.
5. Check connectivity once: `python -m ispmon.collector --once`
6. Run: `python -m ispmon.collector` (one window) and `streamlit run dashboard/app.py` (another, from this folder).

Preview the dashboard without live data:
`python scripts\seed_demo_db.py data\demo.db`, then `set DB_PATH=data\demo.db` before starting the dashboard.

## Running on the laptop (unattended)

1. Power: never sleep on AC (`powercfg /change standby-timeout-ac 0`, `powercfg /change hibernate-timeout-ac 0`),
   lid close = do nothing (Power Options), Windows Update active hours set so it does not reboot mid-day.
2. Use Ethernet and give the laptop a fixed address (or a DHCP reservation) with access to the OLT and router VLANs.
3. From an elevated PowerShell in the project folder:
   `powershell -ExecutionPolicy Bypass -File deploy\windows\install_tasks.ps1 -BackupDir D:\Backups\isp`
   This registers collector, dashboard and a daily 03:00 backup. All start at boot and restart after a crash.
4. Check after a reboot: `Get-ScheduledTask ISP-*` and the dashboard page "Collector and data".
   Reboots show up there as data gaps, which are excluded from event labelling automatically.

The dashboard listens on localhost only and has no login. Do not expose it to the network without adding authentication.
Logs: `logs\collector.log` (rotated).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `failed ... timed out` for every device | The machine is on a different network than the OLT/router VLANs |
| One OLT times out now and then | Switch port or cable to that OLT; compare `ping -n 60` against the other OLT |
| `Collector is not reporting` banner | Collector stopped; check `logs\collector.log` and the ISP-Collector task |
| Many flat Rx readings under "Data quality" | The OLT may serve cached optical readings; confirm before modelling on Rx trends |
| Task will not start under SYSTEM | Python installed per-user; re-register the tasks with your own account |

## Design notes and limits

- Timestamps are stored as local time (Pakistan has no DST). Moving to UTC would be a schema migration.
- OLT clocks are wrong (2044 and 2019 on the two units) and are never used as time. Only equality of the "last registration"
  string is used, to notice an ONU that dropped and returned between two polls.
- A failed OLT scrape never marks ONUs offline. Events found after the collector was blind carry `after_gap = 1`.
- Telnet and the plaintext RouterOS API are unencrypted. Enable api-ssl (`MK_USE_SSL=true`, `MK_PORT=8729`) and keep
  management traffic on its own VLAN.
- Optical and ONU history is kept forever (training data); interface counters 90 days, router logs 180 days.

## Tests

`pip install -r requirements-dev.txt` then `python -m pytest -q`
Covers the parsers (on real captures), event detection, schema migration from the first prototype's tables, the OLT session
logic (scripted fake Telnet), whole collector cycles (faked devices, real SQLite), alert rules, and every dashboard page rendered
against synthetic data.
