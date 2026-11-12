"""Sensors for the Giga Hub integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfDataRate,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from . import GigaHubConfigEntry
from .const import BANDS
from .coordinator import FastData, GigaHubFastCoordinator, GigaHubSlowCoordinator
from .entity import GigaHubEntity
from .parse import SlowSnapshot

PARALLEL_UPDATES = 0

BAND_LABEL = {"2g4": "2.4 GHz", "5g": "5 GHz", "6g": "6 GHz"}

type Value = StateType | datetime


@dataclass(frozen=True, kw_only=True)
class FastSensorDescription(SensorEntityDescription):
    value_fn: Callable[[FastData], Value]
    attrs_fn: Callable[[FastData], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class SlowSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SlowSnapshot], Value]
    attrs_fn: Callable[[SlowSnapshot], dict[str, Any]] | None = None


def _ago(seconds: int | None) -> datetime | None:
    if seconds is None:
        return None
    return dt_util.utcnow() - timedelta(seconds=seconds)


def _rate(key: str, translation_key: str, **kwargs: Any) -> FastSensorDescription:
    return FastSensorDescription(
        key=key,
        translation_key=translation_key,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        value_fn=lambda d, key=key: d.rates.get(key),
        **kwargs,
    )


# ----------------------------------------------------------------------------- internet

INTERNET: tuple[FastSensorDescription, ...] = (
    _rate("wan_download", "download_speed"),
    _rate("wan_upload", "upload_speed"),
    FastSensorDescription(
        key="data_downloaded",
        translation_key="data_downloaded",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        value_fn=lambda d: d.snapshot.wan.bytes_received if d.snapshot.wan else None,
    ),
    FastSensorDescription(
        key="data_uploaded",
        translation_key="data_uploaded",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        value_fn=lambda d: d.snapshot.wan.bytes_sent if d.snapshot.wan else None,
    ),
    FastSensorDescription(
        key="internet_status",
        translation_key="internet_status",
        value_fn=lambda d: (
            ((d.snapshot.wan.connection_status or "").lower() or None) if d.snapshot.wan else None
        ),
        attrs_fn=lambda d: {"last_error": d.snapshot.wan.last_error} if d.snapshot.wan else {},
    ),
    FastSensorDescription(
        key="connected_since",
        translation_key="connected_since",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _ago(d.snapshot.wan.seconds_since_change) if d.snapshot.wan else None,
    ),
    FastSensorDescription(
        key="public_ip",
        translation_key="public_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.snapshot.public_ip,
    ),
)

# ----------------------------------------------------------------------------- fibre

FIBRE: tuple[FastSensorDescription, ...] = (
    FastSensorDescription(
        key="fibre_rx_power",
        translation_key="fibre_rx_power",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        suggested_display_precision=2,
        value_fn=lambda d: d.snapshot.optical.rx_power_dbm if d.snapshot.optical else None,
        attrs_fn=lambda d: {"module": d.snapshot.optical.module} if d.snapshot.optical else {},
    ),
    FastSensorDescription(
        key="fibre_tx_power",
        translation_key="fibre_tx_power",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        suggested_display_precision=2,
        value_fn=lambda d: d.snapshot.optical.tx_power_dbm if d.snapshot.optical else None,
    ),
    FastSensorDescription(
        key="fibre_temperature",
        translation_key="fibre_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda d: d.snapshot.optical.temperature_c if d.snapshot.optical else None,
    ),
    FastSensorDescription(
        key="fibre_voltage",
        translation_key="fibre_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.snapshot.optical.voltage_v if d.snapshot.optical else None,
    ),
    FastSensorDescription(
        key="fibre_bias_current",
        translation_key="fibre_bias_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.snapshot.optical.bias_current_ma if d.snapshot.optical else None,
    ),
    FastSensorDescription(
        key="fibre_errors",
        translation_key="fibre_errors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: (
            (d.snapshot.optical.errors_received or 0) + (d.snapshot.optical.errors_sent or 0)
            if d.snapshot.optical
            else None
        ),
    ),
    FastSensorDescription(
        key="fibre_alarm",
        translation_key="fibre_alarm",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: ((d.snapshot.optical.alarm or "").lower() or None) if d.snapshot.optical else None,
    ),
)

# ----------------------------------------------------------------------------- hub

HUB_FAST: tuple[FastSensorDescription, ...] = (
    FastSensorDescription(
        key="last_boot",
        translation_key="last_boot",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _ago(d.snapshot.uptime_s),
    ),
    FastSensorDescription(
        key="memory_used",
        translation_key="memory_used",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda d: d.snapshot.memory_used_pct,
    ),
)

HUB_SLOW: tuple[SlowSensorDescription, ...] = (
    SlowSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.firmware,
    ),
    SlowSensorDescription(
        key="reboot_count",
        translation_key="reboot_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda s: s.reboot_count,
    ),
)

# ----------------------------------------------------------------------------- devices


def _names(s: SlowSnapshot, hosts) -> list[str]:
    return sorted((h.name for h in hosts), key=str.lower)


def _hub_radio_macs(s: SlowSnapshot) -> set[str]:
    return {c.mac for c in s.wifi_clients}


def _pod_clients(s: SlowSnapshot):
    on_hub = _hub_radio_macs(s)
    return [
        h
        for h in s.active_hosts
        if h.interface_type == "WiFi" and h.mac not in on_hub and "pod" not in h.name.lower()
    ]


DEVICES: tuple[SlowSensorDescription, ...] = (
    SlowSensorDescription(
        key="devices_connected",
        translation_key="devices_connected",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: len(s.active_hosts),
        attrs_fn=lambda s: {"devices": _names(s, s.active_hosts)},
    ),
    SlowSensorDescription(
        key="wired_devices",
        translation_key="wired_devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: sum(1 for h in s.active_hosts if h.interface_type == "Ethernet"),
        attrs_fn=lambda s: {
            "devices": _names(s, [h for h in s.active_hosts if h.interface_type == "Ethernet"])
        },
    ),
    SlowSensorDescription(
        key="wifi_devices",
        translation_key="wifi_devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: sum(1 for h in s.active_hosts if h.interface_type == "WiFi"),
        attrs_fn=lambda s: {"devices": _names(s, [h for h in s.active_hosts if h.interface_type == "WiFi"])},
    ),
    SlowSensorDescription(
        key="wifi_pod_clients",
        translation_key="wifi_pod_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: len(_pod_clients(s)),
        attrs_fn=lambda s: {"devices": _names(s, _pod_clients(s))},
    ),
)

# ----------------------------------------------------------------------------- speed test

SPEED_TEST: tuple[SlowSensorDescription, ...] = (
    SlowSensorDescription(
        key="speed_test_download",
        translation_key="speed_test_download",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=0,
        value_fn=lambda s: s.speed_test.download_mbps if s.speed_test else None,
    ),
    SlowSensorDescription(
        key="speed_test_upload",
        translation_key="speed_test_upload",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=0,
        value_fn=lambda s: s.speed_test.upload_mbps if s.speed_test else None,
    ),
    SlowSensorDescription(
        key="speed_test_latency",
        translation_key="speed_test_latency",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        suggested_display_precision=0,
        value_fn=lambda s: s.speed_test.latency_ms if s.speed_test else None,
    ),
    SlowSensorDescription(
        key="speed_test_last_run",
        translation_key="speed_test_last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: dt_util.utc_from_timestamp(s.speed_test.timestamp) if s.speed_test else None,
        attrs_fn=lambda s: (
            {
                "server": s.speed_test.server,
                "jitter_ms": s.speed_test.jitter_ms,
                "packet_loss_pct": s.speed_test.packet_loss_pct,
            }
            if s.speed_test
            else {}
        ),
    ),
)

# ----------------------------------------------------------------------------- per band / port


def _band_fast(band: str) -> tuple[FastSensorDescription, ...]:
    label = {"band": BAND_LABEL[band]}

    def radio(d: FastData):
        return d.snapshot.radios.get(band)

    return (
        _rate(
            f"radio_{band}_download",
            "wifi_band_download",
            translation_placeholders=label,
        ),
        _rate(
            f"radio_{band}_upload",
            "wifi_band_upload",
            translation_placeholders=label,
        ),
        FastSensorDescription(
            key=f"radio_{band}_channel",
            translation_key="wifi_band_channel",
            translation_placeholders=label,
            value_fn=lambda d: r.channel if (r := radio(d)) else None,
            attrs_fn=lambda d: (
                {
                    "bandwidth": r.bandwidth,
                    "auto_channel": r.auto_channel,
                    "standards": r.standards,
                    "transmit_power_pct": r.transmit_power_pct,
                    "status": r.status,
                }
                if (r := radio(d))
                else {}
            ),
        ),
        FastSensorDescription(
            key=f"radio_{band}_noise",
            translation_key="wifi_band_noise",
            translation_placeholders=label,
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda d: r.noise_dbm if (r := radio(d)) else None,
        ),
    )


def _band_slow(band: str) -> tuple[SlowSensorDescription, ...]:
    label = {"band": BAND_LABEL[band]}

    def weakest(s: SlowSnapshot):
        signals = [c for c in s.clients_on(band) if c.signal_dbm is not None]
        return min(signals, key=lambda c: c.signal_dbm, default=None)

    return (
        SlowSensorDescription(
            key=f"wifi_{band}_clients",
            translation_key="wifi_band_clients",
            translation_placeholders=label,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda s: len(s.clients_on(band)),
            attrs_fn=lambda s: {
                "clients": {
                    s.name_for(c.mac): {
                        "signal_dbm": c.signal_dbm,
                        "standard": c.standard,
                        "link_down_mbps": c.downlink_mbps,
                        "link_up_mbps": c.uplink_mbps,
                    }
                    for c in sorted(s.clients_on(band), key=lambda c: c.signal_dbm or -999, reverse=True)
                }
            },
        ),
        SlowSensorDescription(
            key=f"wifi_{band}_weakest_signal",
            translation_key="wifi_band_weakest_signal",
            translation_placeholders=label,
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
            value_fn=lambda s: c.signal_dbm if (c := weakest(s)) else None,
            attrs_fn=lambda s: {"device": s.name_for(c.mac)} if (c := weakest(s)) else {},
        ),
    )


def _port(number: int, enabled: bool) -> tuple[FastSensorDescription, ...]:
    label = {"port": str(number)}

    def port(d: FastData):
        return next((p for p in d.snapshot.ports if p.number == number), None)

    return (
        FastSensorDescription(
            key=f"port_{number}_link_speed",
            translation_key="port_link_speed",
            translation_placeholders=label,
            device_class=SensorDeviceClass.DATA_RATE,
            native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
            entity_registry_enabled_default=enabled,
            value_fn=lambda d: p.speed_mbps if (p := port(d)) else None,
            attrs_fn=lambda d: {"status": p.status} if (p := port(d)) else {},
        ),
        _rate(
            f"port_{number}_download",
            "port_download",
            translation_placeholders=label,
            entity_registry_enabled_default=enabled,
        ),
        _rate(
            f"port_{number}_upload",
            "port_upload",
            translation_placeholders=label,
            entity_registry_enabled_default=enabled,
        ),
    )


# ----------------------------------------------------------------------------- entities


class GigaHubFastSensor(GigaHubEntity[GigaHubFastCoordinator], SensorEntity):
    entity_description: FastSensorDescription

    @property
    def native_value(self) -> Value:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class GigaHubSlowSensor(GigaHubEntity[GigaHubSlowCoordinator], SensorEntity):
    entity_description: SlowSensorDescription

    @property
    def native_value(self) -> Value:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class GigaHubStableTimestamp(GigaHubFastSensor):
    """ "Connected since" / "last boot" are derived from a seconds counter, so the
    computed instant wobbles by the poll latency. Only move it when it really changes."""

    _value: datetime | None = None

    @property
    def native_value(self) -> datetime | None:
        new = self.entity_description.value_fn(self.coordinator.data)
        if not isinstance(new, datetime):
            return None
        if self._value is None or abs((new - self._value).total_seconds()) > 90:
            self._value = new.replace(microsecond=0)
        return self._value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GigaHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    fast, slow = runtime.fast, runtime.slow

    fast_descriptions: list[FastSensorDescription] = [*INTERNET, *FIBRE, *HUB_FAST]
    slow_descriptions: list[SlowSensorDescription] = [*HUB_SLOW, *DEVICES, *SPEED_TEST]
    for band in BANDS:
        if band in fast.data.snapshot.radios:
            fast_descriptions.extend(_band_fast(band))
            slow_descriptions.extend(_band_slow(band))
    for port in fast.data.snapshot.ports:
        fast_descriptions.extend(_port(port.number, enabled=port.status == "UP"))

    entities: list[SensorEntity] = []
    for description in fast_descriptions:
        cls = (
            GigaHubStableTimestamp
            if description.device_class == SensorDeviceClass.TIMESTAMP
            else GigaHubFastSensor
        )
        entities.append(cls(fast, entry, description))
    entities.extend(GigaHubSlowSensor(slow, entry, d) for d in slow_descriptions)
    async_add_entities(entities)
