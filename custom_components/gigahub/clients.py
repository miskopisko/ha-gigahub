"""Per-device traffic: Wi-Fi and wired device tables, and sensors for named Wi-Fi devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfDataRate,
    UnitOfInformation,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GigaHubConfigEntry
from .const import CONF_DEVICE_NAMES, DOMAIN
from .coordinator import GigaHubSlowCoordinator
from .entity import hub_device_info
from .parse import Host, SlowSnapshot

BAND_LABEL = {"2g4": "2.4 GHz", "5g": "5 GHz", "6g": "6 GHz"}
BUSY_THRESHOLD_MBPS = 0.1


def traffic_rows(s: SlowSnapshot) -> list[dict[str, Any]]:
    """Every Wi-Fi device on the hub's radios, busiest first."""
    rows = []
    tv = s.tv_macs
    for client in s.wifi_clients:
        if client.mac in tv:
            continue
        traffic = s.device_traffic.get(client.mac)
        host = s.host(client.mac)
        rows.append(
            {
                "name": s.name_for(client.mac),
                "mac": client.mac.upper(),
                "ip": host.ip if host else None,
                "band": BAND_LABEL.get(client.band, client.band),
                "signal_dbm": client.signal_dbm,
                "standard": client.standard,
                "link_down_mbps": client.downlink_mbps,
                "link_up_mbps": client.uplink_mbps,
                "download_mbps": traffic.download_mbps if traffic else None,
                "upload_mbps": traffic.upload_mbps if traffic else None,
                "downloaded_gb": round(client.bytes_down / 1e9, 2) if client.bytes_down is not None else None,
                "uploaded_gb": round(client.bytes_up / 1e9, 2) if client.bytes_up is not None else None,
                "connected_since": client.connected_since,
            }
        )
    rows.sort(key=lambda r: (r["download_mbps"] or 0) + (r["upload_mbps"] or 0), reverse=True)
    return rows


def pod_hosts(s: SlowSnapshot) -> list[Host]:
    """Wi-Fi devices the hub knows about that aren't on its own radios, so they're on a pod."""
    on_hub = {c.mac for c in s.wifi_clients}
    tv = s.tv_macs
    return [
        h
        for h in s.active_hosts
        if h.interface_type == "WiFi"
        and h.mac not in on_hub
        and h.mac not in tv
        and "pod" not in h.name.lower()
    ]


def pod_rows(s: SlowSnapshot) -> list[dict[str, Any]]:
    """Devices on a pod, by name. The pod's radio stats live in Plume's cloud, not the hub."""
    rows = [
        {
            "name": s.name_for(h.mac),
            "mac": h.mac.upper(),
            "ip": h.ip,
            "band": "Pod",
            "signal_dbm": None,
            "standard": None,
            "link_down_mbps": None,
            "link_up_mbps": None,
            "download_mbps": None,
            "upload_mbps": None,
            "downloaded_gb": None,
            "uploaded_gb": None,
            "connected_since": None,
        }
        for h in pod_hosts(s)
    ]
    rows.sort(key=lambda r: r["name"].lower())
    return rows


def _gb(value: int | None) -> float | None:
    return round(value / 1e9, 2) if value is not None else None


def wired_hosts(s: SlowSnapshot) -> list[Host]:
    """Wired devices, minus TV receivers (they get their own list)."""
    tv = s.tv_macs
    return [h for h in s.active_hosts if h.interface_type == "Ethernet" and h.mac not in tv]


def wired_rows(s: SlowSnapshot) -> list[dict[str, Any]]:
    """Every wired device with its IP and the link speed of the LAN port it's on."""
    ports = {p.number: p for p in s.ports}
    rows = []
    for host in wired_hosts(s):
        port = ports.get(host.port) if host.port is not None else None
        rows.append(
            {
                "name": host.name,
                "mac": host.mac.upper(),
                "ip": host.ip,
                "vendor": host.vendor,
                "port": host.port,
                "link_mbps": port.speed_mbps if port else None,
            }
        )
    rows.sort(key=lambda r: r["name"].lower())
    return rows


def tv_rows(s: SlowSnapshot) -> list[dict[str, Any]]:
    """TV receivers, on the hub's IPTV Wi-Fi or plugged into a LAN port."""
    ports = {p.number: p for p in s.ports}
    rows = []
    tv = s.tv_macs
    seen = set()
    for client in s.wifi_clients:
        if client.mac not in tv:
            continue
        seen.add(client.mac)
        rows.append(
            {
                "name": s.name_for(client.mac),
                "mac": client.mac.upper(),
                "vendor": s.vendors.get(client.mac),
                "connection": "Wi-Fi",
                "band": BAND_LABEL.get(client.band, client.band),
                "port": None,
                "signal_dbm": client.signal_dbm,
                "standard": client.standard,
                "link_down_mbps": client.downlink_mbps,
                "link_up_mbps": client.uplink_mbps,
                "downloaded_gb": _gb(client.bytes_down),
                "uploaded_gb": _gb(client.bytes_up),
                "connected_since": client.connected_since,
            }
        )
    for host in s.active_hosts:
        if host.mac in seen or host.mac not in tv or host.interface_type != "Ethernet":
            continue
        port = ports.get(host.port) if host.port is not None else None
        link = port.speed_mbps if port else None
        rows.append(
            {
                "name": host.name,
                "mac": host.mac.upper(),
                "vendor": host.vendor,
                "connection": "Wired",
                "band": None,
                "port": host.port,
                "signal_dbm": None,
                "standard": None,
                "link_down_mbps": link,
                "link_up_mbps": link,
                # A port's counters cover everything on it, so leave these out.
                "downloaded_gb": None,
                "uploaded_gb": None,
                "connected_since": None,
            }
        )
    rows.sort(key=lambda r: r["name"].lower())
    return rows


class GigaHubBusiestDevice(CoordinatorEntity[GigaHubSlowCoordinator], SensorEntity):
    """State: the busiest Wi-Fi device right now.

    Attributes: every device on the hub's radios by traffic, then devices on pods by name.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "busiest_wifi_device"

    def __init__(self, coordinator: GigaHubSlowCoordinator, entry: GigaHubConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{format_mac(entry.runtime_data.identity.mac)}_busiest_wifi_device"
        self._attr_device_info = hub_device_info(entry)

    @property
    def native_value(self) -> str | None:
        rows = traffic_rows(self.coordinator.data)
        if not rows or rows[0]["download_mbps"] is None:
            return None
        busiest = rows[0]
        if (busiest["download_mbps"] or 0) + (busiest["upload_mbps"] or 0) < BUSY_THRESHOLD_MBPS:
            return "Idle"
        return busiest["name"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {"devices": traffic_rows(data) + pod_rows(data)}


class GigaHubNewestDevice(CoordinatorEntity[GigaHubSlowCoordinator], SensorEntity):
    """State: the most recent device the hub had never seen before.

    Attributes keep the last 25 arrivals (time, name, maker, connection, IP). Unlike the
    event entity's logbook, restarts don't add "unavailable" noise to this list.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "newest_device"

    def __init__(self, coordinator: GigaHubSlowCoordinator, entry: GigaHubConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{format_mac(entry.runtime_data.identity.mac)}_newest_device"
        self._attr_device_info = hub_device_info(entry)

    @property
    def native_value(self) -> str | None:
        recent = self.coordinator.recent_new_devices
        return recent[0]["name"] if recent else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"recent": self.coordinator.recent_new_devices}


# --------------------------------------------------------------------------- named devices


@dataclass(frozen=True, kw_only=True)
class ClientSensorDescription(SensorEntityDescription):
    value_fn: Callable[[GigaHubSlowCoordinator, str], Any]


def _rate(key: str, attr: str) -> ClientSensorDescription:
    return ClientSensorDescription(
        key=key,
        translation_key=f"client_{key}",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        value_fn=lambda c, mac: getattr(t, attr) if (t := c.data.device_traffic.get(mac)) else None,
    )


def _total(key: str, index: int) -> ClientSensorDescription:
    return ClientSensorDescription(
        key=key,
        translation_key=f"client_{key}",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        value_fn=lambda c, mac: t[index] if c.traffic and (t := c.traffic.totals(mac)) else None,
    )


CLIENT_SENSORS: tuple[ClientSensorDescription, ...] = (
    _rate("download_speed", "download_mbps"),
    _rate("upload_speed", "upload_mbps"),
    _total("data_downloaded", 0),
    _total("data_uploaded", 1),
    ClientSensorDescription(
        key="signal",
        translation_key="client_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        value_fn=lambda c, mac: next((w.signal_dbm for w in c.data.wifi_clients if w.mac == mac), None),
    ),
)


class GigaHubClientSensor(CoordinatorEntity[GigaHubSlowCoordinator], SensorEntity):
    """One measurement for one named Wi-Fi device, grouped under its own HA device."""

    _attr_has_entity_name = True
    entity_description: ClientSensorDescription

    def __init__(
        self,
        coordinator: GigaHubSlowCoordinator,
        entry: GigaHubConfigEntry,
        mac: str,
        name: str,
        description: ClientSensorDescription,
        hub_device_id: str | None,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._mac = mac
        hub = format_mac(entry.runtime_data.identity.mac)
        self._attr_unique_id = f"{hub}_client_{format_mac(mac)}_{description.key}"
        # No MAC "connection" on purpose: it would merge this into any other
        # integration's device with the same MAC (Cast, etc.).
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"client_{format_mac(mac)}")},
            name=name,
            manufacturer=coordinator.data.vendors.get(mac),
            model="Wi-Fi device",
            via_device_id=hub_device_id,
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator, self._mac)


def named_wifi_devices(entry: GigaHubConfigEntry) -> dict[str, str]:
    """Devices the user named that the hub knows as Wi-Fi devices."""
    names = {k.lower(): v for k, v in (entry.options.get(CONF_DEVICE_NAMES) or {}).items() if v}
    snapshot = entry.runtime_data.slow.data
    wifi = {h.mac for h in snapshot.hosts if h.interface_type == "WiFi"}
    wifi |= {c.mac for c in snapshot.wifi_clients}
    return {mac: name for mac, name in names.items() if mac in wifi}


@callback
def remove_stale_client_devices(hass: HomeAssistant, entry: GigaHubConfigEntry, keep: set[str]) -> None:
    """Drop per-device entries for devices that are no longer named."""
    registry = dr.async_get(hass)
    wanted = {f"client_{format_mac(mac)}" for mac in keep}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        client_ids = {i for d, i in device.identifiers if d == DOMAIN and i.startswith("client_")}
        if client_ids and not client_ids & wanted:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


def client_entities(hass: HomeAssistant, entry: GigaHubConfigEntry) -> list[SensorEntity]:
    slow = entry.runtime_data.slow
    named = named_wifi_devices(entry)
    remove_stale_client_devices(hass, entry, set(named))
    entities: list[SensorEntity] = [GigaHubBusiestDevice(slow, entry), GigaHubNewestDevice(slow, entry)]
    # Link each device to the hub's registry entry (creating it if this runs first).
    hub_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **hub_device_info(entry)
    )
    for mac, name in named.items():
        entities.extend(
            GigaHubClientSensor(slow, entry, mac, name, description, hub_device.id)
            for description in CLIENT_SENSORS
        )
    return entities
