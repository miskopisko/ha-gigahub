"""Constants for the Giga Hub integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "gigahub"
MANUFACTURER: Final = "Sagemcom"

DEFAULT_HOST: Final = "192.168.2.1"
DEFAULT_USERNAME: Final = "admin"

CONF_FAST_INTERVAL: Final = "fast_interval"
CONF_SLOW_INTERVAL: Final = "slow_interval"
CONF_BLOCK_TRAFFIC: Final = "block_traffic_during_speed_test"
CONF_SPEED_TEST_INTERVAL: Final = "speed_test_interval"  # hours, 0 = off

DEFAULT_FAST_INTERVAL: Final = 30  # seconds: traffic, fibre, radios, ports
DEFAULT_SLOW_INTERVAL: Final = 300  # seconds: device list, Wi-Fi clients, speed test history
MIN_FAST_INTERVAL: Final = 10
MIN_SLOW_INTERVAL: Final = 60

SPEED_TEST_TIMEOUT: Final = timedelta(minutes=3)
SPEED_TEST_POLL_SECONDS: Final = 3

# Bands as reported by Device/WiFi/Radios/Radio/OperatingFrequencyBand.
BAND_2G4: Final = "2g4"
BAND_5G: Final = "5g"
BAND_6G: Final = "6g"
BANDS: Final = (BAND_2G4, BAND_5G, BAND_6G)

EVENT_NEW_DEVICE: Final = "new_device"
