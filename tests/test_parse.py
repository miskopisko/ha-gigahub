"""Parser tests. The fixtures mirror the shapes sagemcom_api returns from a 5690,
with made-up addresses and names."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load parse.py on its own so the tests don't need Home Assistant installed.
_PATH = Path(__file__).parents[1] / "custom_components" / "gigahub" / "parse.py"
_spec = importlib.util.spec_from_file_location("gigahub_parse", _PATH)
parse = importlib.util.module_from_spec(_spec)
sys.modules["gigahub_parse"] = parse
_spec.loader.exec_module(parse)

RADIO_5G = "Device/WiFi/Radios/Radio[RADIO5G]"
RADIO_2G4 = "Device/WiFi/Radios/Radio[RADIO2G4]"
RADIO_6G = "Device/WiFi/Radios/Radio[RADIO6G]"

FAST_RAW = {
    "wan": {
        "interface": {
            "status": "UP",
            "connection_status": "CONNECTED",
            "last_change": 86400,
            "last_connection_error": "ERROR_NONE",
            "stats": {
                "bytes_received": "7000000000000",
                "bytes_sent": "1500000000000",
                "errors_received": 0,
                "errors_sent": 0,
            },
        }
    },
    "public_ip": [{"enable": True, "ip_address": "203.0.113.7"}],
    "optical": {
        "interface": {
            "status": "UP",
            "optical_signal_level": -14449,
            "transmit_optical_level": 5084,
            "temperature": 30871,
            "voltage": 3296700,
            "bias_current": 17630,
            "alarm": "NONE",
            "optical_vendor_name": "MACOM",
            "optical_part_number": "M02181",
            "stats": {"errors_received": 0, "errors_sent": 0},
        }
    },
    "radios": [
        {
            "alias": "RADIO2G4",
            "enable": True,
            "status": "UP",
            "operating_frequency_band": "2_4GHZ",
            "channel": 11,
            "current_operating_channel_bandwidth": "20MHz",
            "auto_channel_enable": False,
            "operating_standards": "b,g,n,ax",
            "transmit_power": 100,
            "stats": {"bytes_sent": "10", "bytes_received": "20", "noise": -93},
        },
        {
            "alias": "RADIO5G",
            "enable": True,
            "status": "UP",
            "operating_frequency_band": "5GHZ",
            "channel": 157,
            "current_operating_channel_bandwidth": "80MHz",
            "stats": {"bytes_sent": "30", "bytes_received": "40", "noise": -91},
        },
    ],
    "ssids": [
        {
            "uid": 1,
            "enable": True,
            "lower_layers": RADIO_2G4,
            "stats": {"bytes_sent": "100", "bytes_received": "200"},
        },
        {
            "uid": 2,
            "enable": False,
            "lower_layers": RADIO_2G4,
            "stats": {"bytes_sent": "999", "bytes_received": "999"},
        },
        {
            "uid": 3,
            "enable": True,
            "lower_layers": RADIO_5G,
            "stats": {"bytes_sent": "1000", "bytes_received": "10"},
        },
        {
            "uid": 4,
            "enable": True,
            "lower_layers": RADIO_5G,
            "stats": {"bytes_sent": "5000", "bytes_received": "50"},
        },
    ],
    "ports": [
        {
            "uid": 1,
            "alias": "PHY1",
            "role": "LAN",
            "enable": True,
            "status": "UP",
            "current_bit_rate": 1000,
            "stats": {"bytes_sent": "11", "bytes_received": "22"},
        },
        {
            "uid": 3,
            "alias": "PHY3",
            "role": "LAN",
            "enable": True,
            "status": "DORMANT",
            "current_bit_rate": 100,
            "stats": {"bytes_sent": "0", "bytes_received": "0"},
        },
        {"uid": 6, "alias": "PHY6", "role": "WAN", "enable": False, "status": "DOWN"},
    ],
    "memory": {"memory_status": {"total": 1000, "free": 250}},
    "uptime": 123456,
}


def test_fast_snapshot_units() -> None:
    snap = parse.parse_fast(FAST_RAW)

    assert snap.wan.connection_status == "CONNECTED"
    assert snap.wan.bytes_received == 7_000_000_000_000
    assert snap.wan.seconds_since_change == 86400
    assert snap.public_ip == "203.0.113.7"

    optical = snap.optical
    assert optical.rx_power_dbm == pytest.approx(-14.45)
    assert optical.tx_power_dbm == pytest.approx(5.08)
    assert optical.temperature_c == pytest.approx(30.9)
    assert optical.voltage_v == pytest.approx(3.297)
    assert optical.bias_current_ma == pytest.approx(17.63)
    assert optical.module == "MACOM M02181"

    assert snap.memory_used_pct == 75.0
    assert snap.uptime_s == 123456


def test_radios_keyed_by_band() -> None:
    snap = parse.parse_fast(FAST_RAW)
    assert set(snap.radios) == {"2g4", "5g"}
    assert snap.radios["5g"].channel == 157
    assert snap.radios["2g4"].noise_dbm == -93


def test_wifi_traffic_sums_enabled_ssids_per_band() -> None:
    snap = parse.parse_fast(FAST_RAW)
    # Both 5 GHz SSIDs count; the disabled 2.4 GHz guest network does not.
    assert snap.wifi_traffic["5g"].bytes_sent == 6000
    assert snap.wifi_traffic["5g"].bytes_received == 60
    assert snap.wifi_traffic["2g4"].bytes_sent == 100


def test_ports_lan_only_and_down_ports_report_zero() -> None:
    ports = parse.parse_fast(FAST_RAW).ports
    assert [p.number for p in ports] == [1, 3]
    assert ports[0].speed_mbps == 1000
    assert ports[1].speed_mbps == 0  # DORMANT ports keep a stale bit rate


def test_missing_paths_do_not_crash() -> None:
    snap = parse.parse_fast({})
    assert snap.wan is None and snap.optical is None and snap.public_ip is None
    assert snap.radios == {} and snap.ports == [] and snap.memory_used_pct is None


def test_rate_mbps() -> None:
    assert parse.rate_mbps(125_000_000, 0, 1.0) == 1000.0
    assert parse.rate_mbps(10, 20, 1.0) is None  # counter reset after a reboot
    assert parse.rate_mbps(10, None, 1.0) is None
    assert parse.rate_mbps(10, 0, 0) is None


HOSTS = [
    {
        "phys_address": "AA:AA:AA:00:00:01",
        "user_host_name": "Laptop",
        "ip_address": "192.168.2.10",
        "active": True,
        "interface_type": "WiFi",
        "layer1_interface": RADIO_5G,
    },
    {
        "phys_address": "AA:AA:AA:00:00:02",
        "host_name": "nas",
        "ip_address": "192.168.2.11",
        "active": True,
        "interface_type": "Ethernet",
        "layer1_interface": "Device/Ethernet/Interfaces/Interface[PHY1]",
    },
    {
        "phys_address": "AA:AA:AA:00:00:03",
        "host_name": "",
        "ip_address": "",
        "active": False,
        "interface_type": "WiFi",
        "layer1_interface": "",
    },
    {"phys_address": "", "host_name": "ghost"},
]

ASSOC_5G = [
    {
        "mac_address": "aa:aa:aa:00:00:01",
        "active": True,
        "signal_strength": -61,
        "operating_standard": "ax",
        "last_data_downlink_rate": 1200000,
        "last_data_uplink_rate": 960000,
    },
    {"mac_address": "aa:aa:aa:00:00:09", "active": False, "signal_strength": -80},
]

SPEED_TEST = {
    "speed_test": {
        "diagnostics_state": "COMPLETE",
        "jitter": 1.5,
        "packet_loss": 0,
        "history": {
            "timestamp": "1700000300,1700000100,1700000200",
            "selected_server_address": "c.example,a.example,b.example",
            "block_traffic": "0,0,1",
            "latency": "5,3,4",
            "upload": "900.5,1000.1,950.2",
            "download": "1500.1,1700.2,1600.3",
        },
    }
}


def test_slow_snapshot() -> None:
    snap = parse.parse_slow(
        {
            "hosts": HOSTS,
            "ap_4": ASSOC_5G,
            "speed_test": SPEED_TEST,
            "firmware": "2.14.2",
            "reboot_count": 29,
        },
        {4: "5g"},
    )
    assert [h.name for h in snap.hosts] == ["Laptop", "nas", "AA:AA:AA:00:00:03"]
    assert len(snap.active_hosts) == 2
    assert snap.hosts[0].band == "5g"

    clients = snap.clients_on("5g")
    assert len(clients) == 1  # inactive history entries are dropped
    assert clients[0].signal_dbm == -61
    assert clients[0].downlink_mbps == 1200
    assert snap.name_for(clients[0].mac) == "Laptop"
    assert snap.name_for("bb:bb:bb:00:00:00") == "BB:BB:BB:00:00:00"

    assert snap.firmware == "2.14.2" and snap.reboot_count == 29


def test_speed_test_picks_newest_by_timestamp() -> None:
    result, state = parse.parse_speed_test(SPEED_TEST)
    assert state == "COMPLETE"
    assert result.timestamp == 1700000300
    assert result.server == "c.example"
    assert result.download_mbps == 1500.1
    assert result.upload_mbps == 900.5
    assert result.latency_ms == 5.0
    assert result.jitter_ms == 1.5


def test_speed_test_empty_history() -> None:
    result, state = parse.parse_speed_test(
        {"speed_test": {"diagnostics_state": "NONE", "history": {"timestamp": ""}}}
    )
    assert result is None and state == "NONE"


def test_discovery() -> None:
    ip_interfaces = [{"uid": 1, "alias": "IP_BR_LAN"}, {"uid": 2, "alias": "IP_DATA"}]
    assert parse.find_ip_interface_uid(ip_interfaces) == 2
    assert parse.find_access_points(FAST_RAW["ssids"]) == {1: "2g4", 3: "5g", 4: "5g"}


def test_speed_test_status_field_used_by_5690() -> None:
    raw = {
        "speed_test": {
            **SPEED_TEST["speed_test"],
            "diagnostics_state": "NONE",
            "diagnostics_status": "COMPLETE",
        }
    }
    result, state = parse.parse_speed_test(raw)
    assert state == "COMPLETE"
    assert result.jitter_ms == 1.5


GB = 1_000_000_000


def test_ledger_excludes_speed_test_traffic() -> None:
    ledger = parse.TrafficLedger()
    assert ledger.update(100 * GB, 10 * GB, excluding=False) == (100 * GB, 10 * GB)
    assert ledger.update(101 * GB, 11 * GB, excluding=False) == (101 * GB, 11 * GB)
    # A speed test pushes 6 GB down and 7 GB up while excluding: totals stay flat.
    assert ledger.update(107 * GB, 18 * GB, excluding=True) == (101 * GB, 11 * GB)
    # Normal traffic afterwards counts again, on top of the adjusted totals.
    assert ledger.update(108 * GB, 19 * GB, excluding=False) == (102 * GB, 12 * GB)
    assert ledger.offset_rx == 6 * GB and ledger.offset_tx == 7 * GB


def test_ledger_resets_offsets_when_hub_reboots() -> None:
    ledger = parse.TrafficLedger(offset_rx=6 * GB, offset_tx=7 * GB, last_rx=108 * GB, last_tx=19 * GB)
    # Counters restart near zero after a reboot; the offsets belonged to the old counters.
    assert ledger.update(1 * GB, 1 * GB, excluding=False) == (1 * GB, 1 * GB)
    assert ledger.offset_rx == 0 and ledger.offset_tx == 0


def test_ledger_round_trips_and_handles_reboot_while_away() -> None:
    saved = parse.TrafficLedger(offset_rx=5 * GB, offset_tx=5 * GB, last_rx=None, last_tx=None).as_dict()
    ledger = parse.TrafficLedger.from_dict(saved)
    # No last reading, and the counters are already below the offsets: the hub rebooted.
    assert ledger.update(2 * GB, 3 * GB, excluding=False) == (2 * GB, 3 * GB)
    assert parse.TrafficLedger.from_dict(None) == parse.TrafficLedger()


def test_ledger_ignores_missing_counters() -> None:
    ledger = parse.TrafficLedger()
    assert ledger.update(None, 5, excluding=True) == (None, None)
    assert ledger.last_rx is None


def test_short_vendor() -> None:
    assert parse.short_vendor("Nintendo Co.,Ltd") == "Nintendo"
    assert parse.short_vendor("Google, Inc.") == "Google"
    assert parse.short_vendor("FN-LINK TECHNOLOGY Ltd.") == "FN-LINK"
    assert parse.short_vendor("Vizio, Inc") == "Vizio"
    assert parse.short_vendor("Commscope") == "Commscope"
    assert parse.short_vendor("Hon Hai Precision Ind. Co.,Ltd.") == "Hon Hai Precision Ind"
    assert parse.short_vendor(None) is None


def test_private_mac_and_mac_like_names() -> None:
    assert parse.is_private_mac("4e:00:00:11:22:33")
    assert not parse.is_private_mac("48:a5:e7:12:34:56")
    assert parse.looks_like_mac("E8:5C:5F:AB:CD:EF")
    assert parse.looks_like_mac("e85c5fa8039c")
    assert not parse.looks_like_mac("Google-Home-Mini")


NAMING_HOSTS = [
    {"phys_address": "48:A5:E7:12:34:56", "host_name": "48:A5:E7:12:34:56", "active": True},
    {
        "phys_address": "E8:5C:5F:AB:CD:EF",
        "host_name": "E8:5C:5F:AB:CD:EF",
        "vendor_class_id": "android-dhcp-14",
        "active": True,
    },
    {"phys_address": "4E:00:00:11:22:33", "host_name": "4e:00:00:11:22:33", "active": True},
    {"phys_address": "00:11:22:33:44:55", "host_name": "", "active": True},
    {
        "phys_address": "AA:BB:CC:00:00:01",
        "user_friendly_name": "",
        "host_name": "Google-Home-Mini",
        "active": True,
    },
    {
        "phys_address": "AA:BB:CC:00:00:02",
        "user_friendly_name": "Kitchen speaker",
        "host_name": "x",
        "active": True,
    },
]
VENDORS = {
    "48:a5:e7": "Nintendo Co.,Ltd",
    "e8:5c:5f": "FN-LINK TECHNOLOGY Ltd.",
    "4e:00:00": "Should Not Be Used",
    "aa:bb:cc": "Google, Inc.",
}


def test_assign_names() -> None:
    snap = parse.parse_slow({"hosts": NAMING_HOSTS}, {})
    parse.assign_names(snap, lambda mac: VENDORS.get(mac[:8]), {"AA:BB:CC:00:00:01": "Living room"})
    names = [h.name for h in snap.hosts]
    assert names == [
        "Nintendo (12:34:56)",  # vendor from the MAC
        "Android device (AB:CD:EF)",  # DHCP hint beats the Wi-Fi chip's maker
        "Private address (11:22:33)",  # randomised MAC: no vendor lookup
        "00:11:22:33:44:55",  # nothing known
        "Living room",  # the user's name beats the hub's
        "Kitchen speaker",  # a name set in the hub's web page
    ]
    assert snap.hosts[2].vendor is None
    assert snap.vendors["48:a5:e7:12:34:56"] == "Nintendo"
    # Wi-Fi clients missing from the host table still get a friendly name.
    assert snap.name_for("48:a5:e7:00:00:01") == "Nintendo (00:00:01)"


def test_assign_names_survives_lookup_errors() -> None:
    snap = parse.parse_slow({"hosts": NAMING_HOSTS[:1]}, {})

    def broken(mac: str) -> str:
        raise RuntimeError("no database")

    parse.assign_names(snap, broken, None)
    assert snap.hosts[0].name == "48:A5:E7:12:34:56"


MB = 1_000_000


def test_optical_byte_counters() -> None:
    raw = {
        "optical": {
            "interface": {
                **FAST_RAW["optical"]["interface"],
                "stats": {"bytes_received": "123", "bytes_sent": "45"},
            }
        }
    }
    optical = parse.parse_fast(raw).optical
    assert (optical.bytes_received, optical.bytes_sent) == (123, 45)


def test_rolling_rate_smooths_batched_counters() -> None:
    # 300 Mbit/s of real traffic, but the hub only publishes the counter every 10 s,
    # and we poll every 10 s slightly out of phase: per-poll deltas go 0, 750 MB, 0, ...
    rate = parse.RollingRate(window=20)
    counter, readings = 0, []
    for i in range(12):
        if i % 2 == 1:
            counter += 750 * MB  # two 10-s syncs' worth at once
        readings.append(rate.update(i * 10.0, counter))
    steady = readings[3:]
    assert all(r is not None and 270 <= r <= 330 for r in steady), steady


def test_rolling_rate_first_reading_and_reset() -> None:
    rate = parse.RollingRate(window=20)
    assert rate.update(0.0, 1000) is None  # nothing to compare against yet
    assert rate.update(10.0, 1000 + 125 * MB) == 100.0  # uses what it has before the window fills
    # Counter drops (hub reboot): start over rather than report a negative rate.
    assert rate.update(20.0, 5) is None
    assert rate.update(30.0, 5 + 125 * MB) == 100.0
