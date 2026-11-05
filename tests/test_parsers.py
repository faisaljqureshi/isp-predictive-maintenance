from ispmon.parsers import alive_to_seconds, parse_optics, parse_status


def test_olt11_row_counts(fixture_text):
    assert len(parse_optics(fixture_text("olt11_opm.txt"))) == 23
    assert len(parse_status(fixture_text("olt11_status.txt"))) == 27


def test_olt12_row_counts(fixture_text):
    assert len(parse_optics(fixture_text("olt12_opm.txt"))) == 20
    assert len(parse_status(fixture_text("olt12_status.txt"))) == 23


def test_optics_column_order(fixture_text):
    row = next(r for r in parse_optics(fixture_text("olt11_opm.txt")) if r.onu_id == "EPON0/1:22")
    assert (row.temperature, row.voltage, row.bias_current, row.tx_power, row.rx_power) == (51.0, 3.29, 8.0, 2.14, -26.38)


def test_status_fields(fixture_text):
    rows = {r.onu_id: r for r in parse_status(fixture_text("olt11_status.txt"))}
    wire = rows["EPON0/1:14"]
    assert wire.status == "online" and wire.last_dereg_reason == "Wire Down"
    assert rows["EPON0/1:7"].status == "offline"
    never = rows["EPON0/1:10"]
    assert never.last_reg_time == "N/A" and never.last_dereg_reason == "N/A" and never.alive_seconds == 0
    assert rows["EPON0/1:2"].alive_seconds == 1 * 86400 + 6 * 3600 + 5 * 60 + 37


def test_every_status_row_has_a_unique_mac_shape(fixture_text):
    for name in ("olt11_status.txt", "olt12_status.txt"):
        for row in parse_status(fixture_text(name)):
            assert len(row.mac.split(":")) == 6


def test_garbage_yields_nothing():
    assert parse_optics("% Unknown command.\nepon-olt(config)# ") == []
    assert parse_status("") == []


def test_alive_to_seconds():
    assert alive_to_seconds(None, "00:00:00") == 0
    assert alive_to_seconds("2", "14:21:50") == 2 * 86400 + 14 * 3600 + 21 * 60 + 50
