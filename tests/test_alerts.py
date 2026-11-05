from datetime import datetime

from ispmon import alerts, db
from ispmon.config import Thresholds

NOW = datetime(2026, 10, 1, 12, 0, 0)
TH = Thresholds(rx_warn_dbm=-24, rx_crit_dbm=-25, cpu_pct=80, collector_stale_s=180,
                mass_drop_min_onus=5, mass_drop_window_s=120)


def make_db(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    return conn


def titles(found):
    return [a.title for a in found]


def test_stale_collector_is_critical(tmp_path):
    conn = make_db(tmp_path)
    conn.execute("INSERT INTO collector_heartbeat VALUES ('2026-10-01 11:00:00',1,2,50,8)")
    found = alerts.evaluate(conn, TH, [], NOW)
    assert found[0].level == "critical" and "not reporting" in found[0].title


def test_optical_and_mass_drop_and_interface(tmp_path):
    conn = make_db(tmp_path)
    conn.execute("INSERT INTO collector_heartbeat VALUES ('2026-10-01 11:59:30',1,2,50,8)")
    conn.execute("INSERT INTO system_health VALUES ('2026-10-01 11:59:30', 95, 100)")
    conn.execute("INSERT INTO olt_optical_stats (timestamp, olt_ip, onu_id, rx_power) VALUES ('2026-10-01 11:59:30','10.0.0.11','EPON0/1:22',-26.4)")
    conn.execute("INSERT INTO olt_optical_stats (timestamp, olt_ip, onu_id, rx_power) VALUES ('2026-10-01 11:59:30','10.0.0.11','EPON0/1:1',-12.0)")
    for i in range(6):
        conn.execute("INSERT INTO onu_events VALUES ('2026-10-01 11:57:00','10.0.0.12',?,'went_offline','Power Off',0)", (f"EPON0/1:{i}",))
    conn.execute("INSERT INTO onu_events VALUES ('2026-10-01 11:58:00','10.0.0.11','EPON0/1:9','went_offline','Wire Down',0)")
    conn.execute("INSERT INTO onu_events VALUES ('2026-10-01 11:58:00','10.0.0.11','EPON0/1:10','went_offline','Power Off',1)")  # after gap
    conn.execute("INSERT INTO interface_stats (timestamp, interface_name, interface_comment, running, rx_byte, tx_byte) VALUES ('2026-10-01 11:55:00','ether1','uplink',1,0,0)")
    conn.execute("INSERT INTO interface_stats (timestamp, interface_name, interface_comment, running, rx_byte, tx_byte) VALUES ('2026-10-01 11:56:00','ether1','uplink',1,0,0)")
    conn.commit()
    found = alerts.evaluate(conn, TH, ["ether1 (uplink)"], NOW)
    text = " | ".join(titles(found))
    assert "Router CPU is high" in text
    assert "1 ONU(s) below -25 dBm" in text
    assert "6 ONUs dropped together on OLT 10.0.0.12" in text
    assert "1 ONU(s) went offline" in text            # the after-gap event is excluded
    assert "No traffic on a critical interface" in text
    assert [a.level for a in found] == sorted((a.level for a in found), key=lambda l: alerts.LEVELS[l])


def test_quiet_network_has_no_alerts(tmp_path):
    conn = make_db(tmp_path)
    conn.execute("INSERT INTO collector_heartbeat VALUES ('2026-10-01 11:59:30',1,2,50,8)")
    assert alerts.evaluate(conn, TH, [], NOW) == []
