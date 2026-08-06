from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from aiohttp import web

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    MSG_TYPE_ALARM,
    MSG_TYPE_CALLING,
    MSG_TYPE_DEVICE_STATUS,
    MSG_TYPE_SHADOW_CHANGE,
)
from .device_manager import DeviceManager

_LOGGER = logging.getLogger(__name__)

SIGNAL_DEVICE_NEW = f"{DOMAIN}_device_new"
SIGNAL_DATA_RECEIVED = f"{DOMAIN}_data_received"

POWER_TYPE_MAP = {"0": "Charging", "1": "On Battery", 0: "Charging", 1: "On Battery"}


def parse_power_type(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return POWER_TYPE_MAP.get(value, "Unknown")


def parse_timestamp(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromtimestamp(float(value)).isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
        return value
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            return float(value.replace("%", "").strip())
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_get(data: dict, *keys, default=None):
    """Safely traverse nested dict/list by keys."""
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return default
        if current is None:
            return default
    return current


def extract_data(message_type: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Extract all values from webhook body, matching original addon mappings."""
    extracted: Dict[str, Any] = {}

    # Common fields for all message types
    extracted["last_event"] = message_type
    extracted["last_seen"] = datetime.now().astimezone().isoformat()
    extracted["online"] = True

    if message_type == MSG_TYPE_DEVICE_STATUS or message_type == MSG_TYPE_SHADOW_CHANGE:
        # ys.devicestatus / ys.shadow.change - reported is a list of status items
        raw_reported = body.get("reported") if isinstance(body, dict) else None

        # Collect all known fields from all items in the reported list
        merged = {}
        if isinstance(raw_reported, list):
            for item in raw_reported:
                if isinstance(item, dict):
                    merged.update(item)
        elif isinstance(raw_reported, dict):
            merged.update(raw_reported)

        if message_type == MSG_TYPE_SHADOW_CHANGE and merged:
            _LOGGER.debug("Shadow change fields: %s", list(merged.keys()))

        extracted["battery_level"] = _to_float(merged.get("powerRemaining"))
        extracted["wifi_signal"] = _to_float(merged.get("signal"))
        extracted["sd_health"] = _to_float(merged.get("healthLevel"))
        extracted["sd_capacity"] = _to_float(merged.get("capacity"))
        extracted["charging_status"] = parse_power_type(merged.get("powerType"))
        if "status" in merged:
            extracted["armed"] = merged.get("status")
        if "Enable" in merged:
            extracted["detection_enabled"] = merged.get("Enable")

    elif message_type == MSG_TYPE_ALARM:
        # ys.alarm
        extracted["alarm_time"] = parse_timestamp(body.get("alarmTime"))
        extracted["alarm_type"] = body.get("alarmType")
        extracted["channel_name"] = body.get("channelName")

        url = _safe_get(body, "pictureList", 0, "url")
        if url:
            extracted["alarm_picture_url"] = url
            _LOGGER.debug("Alarm picture URL: %s", url)

    elif message_type == MSG_TYPE_CALLING:
        # ys.calling
        extracted["calling_time"] = parse_timestamp(body.get("timestamp"))
        extracted["channel_name"] = body.get("channelName")
        extracted["calling_id"] = body.get("callingId")

        url = _safe_get(body, "coverUrl", "url")
        if url:
            extracted["calling_picture_url"] = url
            _LOGGER.debug("Calling picture URL: %s", url)

    else:
        _LOGGER.debug("Unhandled message type: %s", message_type)

    return extracted


async def handle_webhook(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
    try:
        body = await request.text()
        data = json.loads(body)
    except (json.JSONDecodeError, Exception) as err:
        _LOGGER.warning("Invalid webhook payload: %s", err)
        return web.json_response({"error": "invalid json"}, status=400)

    header = data.get("header", {})
    body_data = data.get("body", {})
    device_id = header.get("deviceId", "")
    message_type = header.get("type", "")
    message_id = header.get("messageId", "")

    if not device_id:
        _LOGGER.warning("Webhook missing deviceId")
        return web.json_response({"error": "missing deviceId"}, status=400)

    _LOGGER.info("Webhook: device=%s type=%s", device_id, message_type)

    if DOMAIN not in hass.data:
        return web.json_response({"error": "integration not loaded"}, status=500)

    device_manager: DeviceManager = hass.data[DOMAIN]["device_manager"]

    _, is_new = await device_manager.async_ensure_device(device_id)
    if is_new:
        _LOGGER.info("New device discovered: %s", device_id)
        async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device_id)

    extracted = extract_data(message_type, body_data)

    # Update device friendly name from channel name
    channel_name = extracted.get("channel_name")
    if channel_name:
        await device_manager.async_update_device_name(device_id, channel_name)

    extracted["_message_type"] = message_type
    extracted["_timestamp"] = datetime.now().isoformat()

    _LOGGER.debug("Extracted data for %s: %s", device_id, {k: v for k, v in extracted.items() if not k.startswith("_")})

    async_dispatcher_send(hass, f"{SIGNAL_DATA_RECEIVED}_{device_id}", extracted)

    return web.json_response({"messageId": message_id})
