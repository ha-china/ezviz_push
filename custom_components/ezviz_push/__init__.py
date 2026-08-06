from __future__ import annotations

import logging

from homeassistant.components.webhook import async_register, async_unregister
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import CONF_WEBHOOK_ID, DEFAULT_WEBHOOK_ID, DOMAIN
from .device_manager import DeviceManager
from .image_handler import ImageHandler
from .webhook import handle_webhook

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.CAMERA]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    webhook_id = entry.data.get(CONF_WEBHOOK_ID, DEFAULT_WEBHOOK_ID)

    device_manager = DeviceManager(hass)
    await device_manager.async_load()
    _LOGGER.info("Loaded %d devices from storage", device_manager.get_device_count())

    image_handler = ImageHandler()
    await image_handler.ensure_session()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["device_manager"] = device_manager
    hass.data[DOMAIN]["image_handler"] = image_handler
    hass.data[DOMAIN]["webhook_id"] = webhook_id

    async_register(hass, DOMAIN, "EZVIZ Cloud Push", webhook_id, handle_webhook)

    _LOGGER.info(
        "EZVIZ integration ready. Configure webhook URL in EZVIZ cloud: "
        "https://YOUR_HA_HOST/api/webhook/%s",
        webhook_id,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    webhook_id = hass.data[DOMAIN].get("webhook_id", DEFAULT_WEBHOOK_ID)
    async_unregister(hass, webhook_id)

    image_handler: ImageHandler = hass.data[DOMAIN]["image_handler"]
    await image_handler.close()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN, None)

    return unload_ok