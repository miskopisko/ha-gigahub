"""Turn raw Giga Hub API replies into typed snapshots.

Nothing in here imports Home Assistant, so it can be unit tested on its own.
The sagemcom_api library snake_cases every key (``BytesSent`` -> ``bytes_sent``),
and the hub sends big counters as strings, so every number goes through ``_int``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

BAND_BY_FREQUENCY = {"2_4GHZ": "2g4", "5GHZ": "5g", "6GHZ": "6g"}
BAND_BY_RADIO_ALIAS = {"RADIO2G4": "2g4", "RADIO5G": "5g", "RADIO6G": "6g"}


def _int(value: Any) -> int | None:
    """Parse an int the hub may send as int, numeric string or empty string."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unwrap(value: Any, key: str) -> Any:
    """Single-object xpaths come back wrapped, e.g. ``{"interface": {...}}``."""
    if isinstance(value, dict) and key in value and len(value) == 1:
        return value[key]
    return value


def _band_from_ref(ref: str | None) -> str | None:
    """``Device/WiFi/Radios/Radio[RADIO5G]`` -> ``5g``."""
    if not ref:
        return None
    for alias, band in BAND_BY_RADIO_ALIAS.items():
        if f"[{alias}]" in ref:
            return band
    return None


_PORT_REF_RE = re.compile(r"Interface\[PHY(\d+)\]")


def _port_from_ref(ref: str | None) -> int | None:
    """``Device/Ethernet/Interfaces/Interface[PHY2]`` -> ``2``."""
    match = _PORT_REF_RE.search(ref or "")
    return int(match.group(1)) if match else None


def normalize_mac(mac: str | None) -> str:
    return (mac or "").strip().lower()


class RollingRate:
    """Mbit/s over a sliding window of at least `window` seconds.

    The hub only refreshes its byte counters every ~10 s (and the internet counter in
    lumpy 15-25 s batches), so a rate over a single short poll swings between 0 and
    double. Measuring against the newest sample that is at least `window` old keeps the
    reading steady while it still updates on every poll.
    """

    def __init__(self, window: float) -> None:
        self.window = window
        self._samples: list[tuple[float, int]] = []

    def update(self, at: float, value: int | None) -> float | None:
        if value is None:
            return None
        if self._samples and value < self._samples[-1][1]:
            self._samples.clear()  # counter reset (hub reboot)
        self._samples.append((at, value))
        old = [s for s in self._samples if at - s[0] >= self.window]
        if old:
            # Keep one sample at or beyond the window edge, drop anything older.
            self._samples = self._samples[self._samples.index(old[-1]) :]
            base_at, base = old[-1]
        else:
            base_at, base = self._samples[0]
        return rate_mbps(value, base, at - base_at) if at > base_at else None


def rate_mbps(new: int | None, old: int | None, seconds: float) -> float | None:
    """Average Mbit/s between two byte counters. None after a counter reset."""
    if new is None or old is None or seconds <= 0 or new < old:
        return None
    return round((new - old) * 8 / seconds / 1_000_000, 3)


# --------------------------------------------------------------------------- speed-test exclusion


@dataclass(slots=True)
class TrafficLedger:
    """Keeps the hub's own speed tests out of the internet traffic totals.

    While a test the integration started is running, every byte the WAN counters
    gain goes into an offset instead, and the reported totals are counter - offset.
    The hub's counters reset to zero when it reboots; the offsets go with them.
    """

    offset_rx: int = 0
    offset_tx: int = 0
    last_rx: int | None = None
    last_tx: int | None = None

    def update(self, rx: int | None, tx: int | None, excluding: bool) -> tuple[int | None, int | None]:
        if rx is None or tx is None:
            return None, None
        if self.last_rx is not None and self.last_tx is not None:
            if rx < self.last_rx or tx < self.last_tx:
                self.offset_rx = self.offset_tx = 0
            elif excluding:
                self.offset_rx += rx - self.last_rx
                self.offset_tx += tx - self.last_tx
        if rx < self.offset_rx or tx < self.offset_tx:
            # The hub rebooted while we weren't watching.
            self.offset_rx = self.offset_tx = 0
        self.last_rx, self.last_tx = rx, tx
        return rx - self.offset_rx, tx - self.offset_tx

    def as_dict(self) -> dict[str, int | None]:
        return {
            "offset_rx": self.offset_rx,
            "offset_tx": self.offset_tx,
            "last_rx": self.last_rx,
            "last_tx": self.last_tx,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TrafficLedger:
        data = data or {}
        return cls(
            offset_rx=_int(data.get("offset_rx")) or 0,
            offset_tx=_int(data.get("offset_tx")) or 0,
            last_rx=_int(data.get("last_rx")),
            last_tx=_int(data.get("last_tx")),
        )


# --------------------------------------------------------------------------- fast


@dataclass(slots=True)
class WanStatus:
    status: str | None
    connection_status: str | None
    seconds_since_change: int | None
    bytes_received: int | None
    bytes_sent: int | None
    errors_received: int | None
    errors_sent: int | None
    last_error: str | None


@dataclass(slots=True)
class OpticalStatus:
    status: str | None
    rx_power_dbm: float | None
    tx_power_dbm: float | None
    temperature_c: float | None
    voltage_v: float | None
    bias_current_ma: float | None
    alarm: str | None
    errors_received: int | None
    errors_sent: int | None
    module: str | None
    # Everything on the fibre: internet plus Fibe TV, voice and Bell's own services.
    # The hub syncs these about every 10 s, while the PPP (internet) counters arrive in
    # irregular batches 15-25 s late, so these are the better source for live speed.
    bytes_received: int | None = None
    bytes_sent: int | None = None


@dataclass(slots=True)
class RadioStatus:
    band: str
    enabled: bool
    status: str | None
    channel: int | None
    bandwidth: str | None
    auto_channel: bool | None
    standards: str | None
    noise_dbm: int | None
    transmit_power_pct: int | None
    bytes_sent: int | None
    bytes_received: int | None
    errors: int | None


@dataclass(slots=True)
class PortStatus:
    alias: str
    number: int
    role: str | None
    status: str | None
    speed_mbps: int | None
    bytes_sent: int | None
    bytes_received: int | None


@dataclass(slots=True)
class BandTraffic:
    """Bytes for every enabled SSID on a band, from the hub's side (sent = to clients)."""

    bytes_sent: int
    bytes_received: int


@dataclass(slots=True)
class FastSnapshot:
    wan: WanStatus | None
    public_ip: str | None
    optical: OpticalStatus | None
    radios: dict[str, RadioStatus]
    wifi_traffic: dict[str, BandTraffic]
    ports: list[PortStatus]
    uptime_s: int | None
    memory_total_kb: int | None
    memory_free_kb: int | None

    @property
    def memory_used_pct(self) -> float | None:
        if not self.memory_total_kb or self.memory_free_kb is None:
            return None
        used = self.memory_total_kb - self.memory_free_kb
        return round(100 * used / self.memory_total_kb, 1)


def parse_wan(raw: Any) -> WanStatus | None:
    ppp = _unwrap(raw, "interface")
    if not isinstance(ppp, dict):
        return None
    stats = ppp.get("stats") or {}
    return WanStatus(
        status=ppp.get("status"),
        connection_status=ppp.get("connection_status"),
        seconds_since_change=_int(ppp.get("last_change")),
        bytes_received=_int(stats.get("bytes_received")),
        bytes_sent=_int(stats.get("bytes_sent")),
        errors_received=_int(stats.get("errors_received")),
        errors_sent=_int(stats.get("errors_sent")),
        last_error=ppp.get("last_connection_error"),
    )


def parse_public_ip(raw: Any) -> str | None:
    """IPv4Addresses of the IP_DATA interface: a list (or one wrapped object)."""
    addresses = _unwrap(raw, "i_pv4_address")
    if isinstance(addresses, dict):
        addresses = [addresses]
    if not isinstance(addresses, list):
        return None
    for address in addresses:
        ip = (address or {}).get("ip_address")
        if ip and address.get("enable", True):
            return ip
    return None


def parse_optical(raw: Any) -> OpticalStatus | None:
    optical = _unwrap(raw, "interface")
    if not isinstance(optical, dict):
        return None
    stats = optical.get("stats") or {}

    def scaled(key: str, divisor: float, digits: int) -> float | None:
        value = _float(optical.get(key))
        return None if value is None else round(value / divisor, digits)

    vendor = optical.get("optical_vendor_name") or ""
    part = optical.get("optical_part_number") or ""
    return OpticalStatus(
        status=optical.get("status"),
        # The hub reports optical levels in thousandths of a dBm, temperature in
        # thousandths of a degree, voltage in microvolts and bias in microamps.
        rx_power_dbm=scaled("optical_signal_level", 1000, 2),
        tx_power_dbm=scaled("transmit_optical_level", 1000, 2),
        temperature_c=scaled("temperature", 1000, 1),
        voltage_v=scaled("voltage", 1_000_000, 3),
        bias_current_ma=scaled("bias_current", 1000, 2),
        alarm=optical.get("alarm"),
        errors_received=_int(stats.get("errors_received")),
        errors_sent=_int(stats.get("errors_sent")),
        module=f"{vendor} {part}".strip() or None,
        bytes_received=_int(stats.get("bytes_received")),
        bytes_sent=_int(stats.get("bytes_sent")),
    )


def parse_radios(raw: Any) -> dict[str, RadioStatus]:
    radios = _unwrap(raw, "radio")
    if isinstance(radios, dict):
        radios = [radios]
    result: dict[str, RadioStatus] = {}
    for radio in radios or []:
        band = BAND_BY_FREQUENCY.get(str(radio.get("operating_frequency_band", "")).upper())
        band = band or BAND_BY_RADIO_ALIAS.get(radio.get("alias", ""))
        if band is None:
            continue
        stats = radio.get("stats") or {}
        errors = [_int(stats.get("errors_sent")), _int(stats.get("errors_received"))]
        result[band] = RadioStatus(
            band=band,
            enabled=bool(radio.get("enable")),
            status=radio.get("status"),
            channel=_int(radio.get("channel")),
            bandwidth=radio.get("current_operating_channel_bandwidth"),
            auto_channel=radio.get("auto_channel_enable"),
            standards=radio.get("operating_standards"),
            noise_dbm=_int(stats.get("noise")),
            transmit_power_pct=_int(radio.get("transmit_power")),
            bytes_sent=_int(stats.get("bytes_sent")),
            bytes_received=_int(stats.get("bytes_received")),
            errors=None if None in errors else sum(errors),  # type: ignore[arg-type]
        )
    return result


def parse_wifi_traffic(raw: Any) -> dict[str, BandTraffic]:
    """Sum SSID counters per band.

    A radio's own counters only cover its first SSID (on 5 GHz that's the hidden
    Fibe TV network), so the main network's traffic has to come from the SSIDs.
    """
    ssids = _unwrap(raw, "ssid")
    if isinstance(ssids, dict):
        ssids = [ssids]
    result: dict[str, BandTraffic] = {}
    for ssid in ssids or []:
        band = _band_from_ref(ssid.get("lower_layers"))
        stats = ssid.get("stats") or {}
        sent, received = _int(stats.get("bytes_sent")), _int(stats.get("bytes_received"))
        if band is None or not ssid.get("enable") or sent is None or received is None:
            continue
        total = result.setdefault(band, BandTraffic(0, 0))
        total.bytes_sent += sent
        total.bytes_received += received
    return result


def parse_ports(raw: Any) -> list[PortStatus]:
    ports = _unwrap(raw, "interface")
    if isinstance(ports, dict):
        ports = [ports]
    result = []
    for port in ports or []:
        if port.get("role") != "LAN" or not port.get("enable"):
            continue
        alias = port.get("alias") or ""
        stats = port.get("stats") or {}
        number = _int(alias.removeprefix("PHY")) or _int(port.get("uid")) or 0
        up = port.get("status") == "UP"
        result.append(
            PortStatus(
                alias=alias,
                number=number,
                role=port.get("role"),
                status=port.get("status"),
                speed_mbps=_int(port.get("current_bit_rate")) if up else 0,
                bytes_sent=_int(stats.get("bytes_sent")),
                bytes_received=_int(stats.get("bytes_received")),
            )
        )
    return sorted(result, key=lambda p: p.number)


def parse_fast(raw: dict[str, Any]) -> FastSnapshot:
    memory = _unwrap(raw.get("memory"), "memory_status") or {}
    return FastSnapshot(
        wan=parse_wan(raw.get("wan")),
        public_ip=parse_public_ip(raw.get("public_ip")),
        optical=parse_optical(raw.get("optical")),
        radios=parse_radios(raw.get("radios")),
        wifi_traffic=parse_wifi_traffic(raw.get("ssids")),
        ports=parse_ports(raw.get("ports")),
        uptime_s=_int(raw.get("uptime")),
        memory_total_kb=_int(memory.get("total")),
        memory_free_kb=_int(memory.get("free")),
    )


# --------------------------------------------------------------------------- slow


@dataclass(slots=True)
class Host:
    mac: str
    name: str
    ip: str | None
    active: bool
    interface_type: str | None
    band: str | None
    port: int | None = None  # LAN port number, for wired devices
    hub_name: str | None = None  # a real name from the hub, never a bare MAC
    vendor_class: str | None = None  # DHCP option 60, e.g. "android-dhcp-14"
    vendor: str | None = None  # manufacturer from the MAC's OUI, filled in by assign_names
    private: bool = False  # randomised ("private Wi-Fi") address


# --------------------------------------------------------------------------- naming

_MAC_RE = re.compile(r"^[0-9a-f]{2}([:-]?[0-9a-f]{2}){5}$", re.IGNORECASE)
_VENDOR_SUFFIXES = re.compile(
    r"[\s,.]*\b(inc|incorporated|corp|corporation|co|ltd|limited|llc|gmbh|ag|sa|bv|plc|"
    r"technology|technologies|electronics|communications|international|holdings|group)\b\.?",
    re.IGNORECASE,
)
_ANDROID_RE = re.compile(r"^android-dhcp-(\d+)", re.IGNORECASE)


def is_private_mac(mac: str) -> bool:
    """Phones and laptops with private Wi-Fi addresses set the locally-administered bit."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except ValueError:
        return False


def looks_like_mac(name: str | None) -> bool:
    return bool(name) and bool(_MAC_RE.match(name.strip()))  # type: ignore[union-attr]


def short_vendor(vendor: str | None) -> str | None:
    """ "Nintendo Co.,Ltd" -> "Nintendo", "FN-LINK TECHNOLOGY Ltd." -> "FN-LINK"."""
    if not vendor:
        return None
    short = vendor
    while True:
        trimmed = _VENDOR_SUFFIXES.sub("", short).strip(" ,.")
        if trimmed == short or not trimmed:
            break
        short = trimmed
    return short or vendor.strip()


def suggested_name(host: Host) -> str:
    """Best name we can make without the user's help."""
    if host.hub_name:
        return host.hub_name
    tail = host.mac.upper()[-8:]
    if host.vendor_class and _ANDROID_RE.match(host.vendor_class):
        return f"Android device ({tail})"
    if host.vendor:
        return f"{host.vendor} ({tail})"
    if host.private:
        return f"Private address ({tail})"
    return host.mac.upper()


def assign_names(
    snapshot: SlowSnapshot,
    vendor_lookup: Callable[[str], str | None] | None,
    custom_names: dict[str, str] | None,
) -> None:
    """Fill in vendors and pick each device's display name: the user's name first,
    then the hub's, then one made from the manufacturer or DHCP hints."""
    custom = {normalize_mac(k): v for k, v in (custom_names or {}).items() if v}

    def vendor_of(mac: str) -> str | None:
        if vendor_lookup is None or is_private_mac(mac):
            return None
        try:
            return short_vendor(vendor_lookup(mac))
        except Exception:  # a lookup failure just means no vendor
            return None

    for host in snapshot.hosts:
        host.vendor = vendor_of(host.mac)
        host.name = custom.get(host.mac) or suggested_name(host)
    snapshot.names = {h.mac: h.name for h in snapshot.hosts}
    snapshot.vendors = {h.mac: h.vendor for h in snapshot.hosts if h.vendor}

    def fallback(mac: str) -> str:
        stray = Host(mac, mac.upper(), None, False, None, None, private=is_private_mac(mac))
        stray.vendor = vendor_of(mac)
        return custom.get(mac) or suggested_name(stray)

    snapshot.namer = fallback


@dataclass(slots=True)
class WifiClient:
    mac: str
    band: str
    signal_dbm: int | None
    standard: str | None
    downlink_mbps: int | None
    uplink_mbps: int | None
    # Byte counters since this device last associated (they restart on reconnect).
    bytes_down: int | None = None  # hub -> device
    bytes_up: int | None = None  # device -> hub
    connected_since: str | None = None  # ISO 8601 with offset, as the hub reports it


@dataclass(slots=True)
class SpeedTestResult:
    timestamp: int
    server: str | None
    latency_ms: float | None
    download_mbps: float | None
    upload_mbps: float | None
    jitter_ms: float | None = None
    packet_loss_pct: float | None = None


@dataclass(slots=True)
class SlowSnapshot:
    hosts: list[Host]
    wifi_clients: list[WifiClient]
    speed_test: SpeedTestResult | None
    speed_test_state: str | None
    firmware: str | None
    reboot_count: int | None
    # LAN ports again (the fast poll has them too), so wired-device rows get port
    # totals on the same schedule as the device list.
    ports: list[PortStatus] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    vendors: dict[str, str] = field(default_factory=dict)
    namer: Callable[[str], str] | None = None
    # Filled in by the coordinator from DeviceTrafficTracker.
    device_traffic: dict[str, DeviceTraffic] = field(default_factory=dict)

    @property
    def active_hosts(self) -> list[Host]:
        return [h for h in self.hosts if h.active]

    def host(self, mac: str) -> Host | None:
        return next((h for h in self.hosts if h.mac == mac), None)

    def clients_on(self, band: str) -> list[WifiClient]:
        return [c for c in self.wifi_clients if c.band == band]

    def name_for(self, mac: str) -> str:
        if mac in self.names:
            return self.names[mac]
        return self.namer(mac) if self.namer else mac.upper()


def parse_hosts(raw: Any) -> list[Host]:
    hosts = _unwrap(raw, "host")
    if isinstance(hosts, dict):
        hosts = [hosts]
    result = []
    for host in hosts or []:
        mac = normalize_mac(host.get("phys_address"))
        if not mac:
            continue
        hub_name = next(
            (
                n.strip()
                for n in (
                    host.get("user_friendly_name"),
                    host.get("user_host_name"),
                    host.get("host_name"),
                )
                if n and n.strip() and not looks_like_mac(n)
            ),
            None,
        )
        result.append(
            Host(
                mac=mac,
                name=hub_name or mac.upper(),
                ip=host.get("ip_address") or None,
                active=bool(host.get("active")),
                interface_type=host.get("interface_type") or None,
                band=_band_from_ref(host.get("layer1_interface")),
                port=_port_from_ref(host.get("layer1_interface")),
                hub_name=hub_name,
                vendor_class=host.get("vendor_class_id") or None,
                private=is_private_mac(mac),
            )
        )
    return result


def parse_wifi_clients(raw: Any, band: str) -> list[WifiClient]:
    """AssociatedDevices of one access point. The table keeps history, so filter on Active."""
    devices = _unwrap(raw, "associated_device")
    if isinstance(devices, dict):
        devices = [devices]
    result = []
    for device in devices or []:
        if not device.get("active"):
            continue
        down = _int(device.get("last_data_downlink_rate"))
        up = _int(device.get("last_data_uplink_rate"))
        stats = device.get("stats") or {}
        result.append(
            WifiClient(
                mac=normalize_mac(device.get("mac_address")),
                band=band,
                signal_dbm=_int(device.get("signal_strength")),
                standard=device.get("operating_standard") or None,
                # Link rates are reported in kbit/s.
                downlink_mbps=None if down is None else down // 1000,
                uplink_mbps=None if up is None else up // 1000,
                bytes_down=_int(stats.get("bytes_sent")),
                bytes_up=_int(stats.get("bytes_received")),
                connected_since=device.get("association_time") or None,
            )
        )
    return result


@dataclass(slots=True)
class DeviceTraffic:
    download_mbps: float | None  # average since the previous device-list poll
    upload_mbps: float | None
    total_down: int  # bytes, accumulated across reconnects
    total_up: int


class DeviceTrafficTracker:
    """Per-device Wi-Fi traffic from the hub's association counters.

    The counters restart from zero whenever a device reconnects, so running totals add
    up the increases and, after a restart, the new counter's whole value.
    """

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        state = state or {}
        # mac -> [last_down, last_up, total_down, total_up]
        self._devices: dict[str, list[int]] = {
            mac: [int(v) for v in values] for mac, values in (state.get("devices") or {}).items()
        }
        self._last_at: dict[str, float] = {}

    def update(self, clients: list[WifiClient], at: float) -> dict[str, DeviceTraffic]:
        result: dict[str, DeviceTraffic] = {}
        for client in clients:
            if client.bytes_down is None or client.bytes_up is None:
                continue
            down, up = client.bytes_down, client.bytes_up
            known = self._devices.get(client.mac)
            seconds = at - self._last_at[client.mac] if client.mac in self._last_at else 0
            if known is None:
                # First sight: start the totals from what it's used this connection.
                self._devices[client.mac] = [down, up, down, up]
                rate_down = rate_up = None
            else:
                last_down, last_up, total_down, total_up = known
                reconnected = down < last_down or up < last_up
                add_down = down if reconnected else down - last_down
                add_up = up if reconnected else up - last_up
                self._devices[client.mac] = [down, up, total_down + add_down, total_up + add_up]
                rate_down = round(add_down * 8 / seconds / 1e6, 3) if seconds > 0 else None
                rate_up = round(add_up * 8 / seconds / 1e6, 3) if seconds > 0 else None
            self._last_at[client.mac] = at
            _, _, total_down, total_up = self._devices[client.mac]
            result[client.mac] = DeviceTraffic(rate_down, rate_up, total_down, total_up)
        return result

    def totals(self, mac: str) -> tuple[int, int] | None:
        known = self._devices.get(mac)
        return (known[2], known[3]) if known else None

    def as_dict(self) -> dict[str, Any]:
        return {"devices": self._devices}


def parse_speed_test(raw: Any) -> tuple[SpeedTestResult | None, str | None]:
    """Return the newest result from the hub's speed test history, plus the run state."""
    node = _unwrap(raw, "speed_test")
    if not isinstance(node, dict):
        return None, None
    # The 5690 reports progress in DiagnosticsStatus (REQUESTED -> COMPLETE) and
    # leaves DiagnosticsState at NONE; other firmware may use the standard field.
    state = node.get("diagnostics_status") or node.get("diagnostics_state")
    history = node.get("history") or {}
    stamps = str(history.get("timestamp") or "")
    if not stamps:
        return None, state

    def column(key: str) -> list[str]:
        return str(history.get(key) or "").split(",")

    timestamps = [_int(t) for t in stamps.split(",")]
    servers, latency = column("selected_server_address"), column("latency")
    download, upload = column("download"), column("upload")
    newest = max(
        (i for i, t in enumerate(timestamps) if t is not None),
        key=lambda i: timestamps[i] or 0,
        default=None,
    )
    if newest is None:
        return None, state

    def at(values: list[str]) -> str | None:
        return values[newest] if newest < len(values) else None

    result = SpeedTestResult(
        timestamp=timestamps[newest] or 0,
        server=at(servers) or None,
        latency_ms=_float(at(latency)),
        download_mbps=_float(at(download)),
        upload_mbps=_float(at(upload)),
    )
    # Jitter and packet loss only exist for the latest run, at the top of the node.
    if str(state).upper().startswith("COMPLETE"):
        result.jitter_ms = _float(node.get("jitter"))
        result.packet_loss_pct = _float(node.get("packet_loss"))
    return result, state


def parse_slow(raw: dict[str, Any], access_points: dict[int, str]) -> SlowSnapshot:
    hosts = parse_hosts(raw.get("hosts"))
    clients: list[WifiClient] = []
    for uid, band in access_points.items():
        clients.extend(parse_wifi_clients(raw.get(f"ap_{uid}"), band))
    speed_test, state = parse_speed_test(raw.get("speed_test"))
    return SlowSnapshot(
        hosts=hosts,
        wifi_clients=clients,
        speed_test=speed_test,
        speed_test_state=state,
        firmware=raw.get("firmware") if isinstance(raw.get("firmware"), str) else None,
        reboot_count=_int(raw.get("reboot_count")),
        ports=parse_ports(raw.get("ports")),
        names={h.mac: h.name for h in hosts},
    )


# --------------------------------------------------------------------------- discovery


def find_ip_interface_uid(raw: Any, alias: str = "IP_DATA") -> int | None:
    interfaces = _unwrap(raw, "interface")
    if isinstance(interfaces, dict):
        interfaces = [interfaces]
    for interface in interfaces or []:
        if interface.get("alias") == alias:
            return _int(interface.get("uid"))
    return None


def find_access_points(raw: Any) -> dict[int, str]:
    """Map enabled SSIDs to their band.

    The hub numbers AccessPoint[N] to match SSID[N], and reading an access point's
    SSIDReference directly comes back mangled by the library, so use the SSID table.
    """
    ssids = _unwrap(raw, "ssid")
    if isinstance(ssids, dict):
        ssids = [ssids]
    result = {}
    for ssid in ssids or []:
        uid = _int(ssid.get("uid"))
        band = _band_from_ref(ssid.get("lower_layers"))
        if uid is not None and band and ssid.get("enable"):
            result[uid] = band
    return result
