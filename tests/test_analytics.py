import pandas as pd

from ispmon import analytics


def test_throughput_skips_resets_and_gaps():
    df = pd.DataFrame(
        {
            "timestamp": ["2026-10-01 10:00:00", "2026-10-01 10:01:00", "2026-10-01 10:02:00",
                          "2026-10-01 10:30:00", "2026-10-01 10:31:00"],
            "display_name": "ether1",
            "rx_byte": [0, 7_500_000, 100, 200, 7_500_200],  # reset between 10:01 and 10:02
            "tx_byte": [0, 0, 0, 0, 0],
        }
    )
    out = analytics.compute_throughput(df)
    assert len(out) == 2  # 10:01 (1 Mbps) and 10:31; the reset and the 28-minute gap are skipped
    assert round(out["rx_mbps"].iloc[0], 3) == 1.0


def test_classify_rx():
    assert analytics.classify_rx(-26, "online", -24, -25) == "Critical"
    assert analytics.classify_rx(-24.5, "online", -24, -25) == "Weak"
    assert analytics.classify_rx(-10, "online", -24, -25) == "OK"
    assert analytics.classify_rx(-10, "offline", -24, -25) == "Offline"
    assert analytics.classify_rx(float("nan"), "online", -24, -25) == "No data"


def drops(rows):
    return pd.DataFrame(rows, columns=["timestamp", "olt_ip", "onu_id", "event", "dereg_reason", "after_gap"])


def test_cluster_outages_separates_power_event_from_single_fault():
    rows = [(f"2026-10-01 04:23:{17 + i:02d}", "a", f"EPON0/1:{i}", "went_offline", "Power Off", 0) for i in range(6)]
    rows.append(("2026-10-01 09:00:00", "a", "EPON0/1:20", "went_offline", "Wire Down", 0))
    rows.append(("2026-10-01 09:00:05", "b", "EPON0/1:2", "went_offline", "Power Off", 0))
    rows.append(("2026-10-01 11:00:00", "a", "EPON0/1:9", "went_offline", "Power Off", 1))  # after a gap: ignored
    incidents, _ = analytics.cluster_outages(drops(rows), window_s=120, min_size=3)
    kinds = sorted(incidents["kind"])
    assert kinds == ["Area power outage", "Customer power off", "Drop fibre fault"]
    big = incidents[incidents["onus"] == 6].iloc[0]
    assert big["olt_ip"] == "a" and "Power Off x6" in big["reasons"]


def test_rx_flatness_flags_constant_readings():
    optics = pd.DataFrame(
        {
            "olt_ip": ["a"] * 40 + ["a"] * 40,
            "onu_id": ["u1"] * 40 + ["u2"] * 40,
            "rx_power": [-20.0] * 40 + [-20 + 0.01 * i for i in range(40)],
        }
    )
    flat = analytics.rx_flatness(optics).set_index("onu_id")["flat"]
    assert bool(flat["u1"]) and not bool(flat["u2"])


def test_build_fleet_hides_optics_for_offline_onus():
    status = pd.DataFrame({"olt_ip": ["a", "a"], "onu_id": ["EPON0/1:1", "EPON0/1:2"], "status": ["online", "offline"]})
    optics = pd.DataFrame({"olt_ip": ["a", "a"], "onu_id": ["EPON0/1:1", "EPON0/1:2"], "rx_power": [-26.0, -10.0]})
    fleet = analytics.build_fleet(status, optics, -24, -25).set_index("onu_id")
    assert fleet.loc["EPON0/1:1", "health"] == "Critical"
    assert fleet.loc["EPON0/1:2", "health"] == "Offline" and pd.isna(fleet.loc["EPON0/1:2", "rx_power"])
