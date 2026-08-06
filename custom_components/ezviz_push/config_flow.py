from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import CONF_WEBHOOK_ID, DEFAULT_WEBHOOK_ID, DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_WEBHOOK_ID, default=DEFAULT_WEBHOOK_ID): cv.string,
})


class EZVIZConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                description_placeholders={"webhook_url": self._get_webhook_url(DEFAULT_WEBHOOK_ID)},
            )

        return self.async_create_entry(title="EZVIZ Cloud Push", data=user_input)

    @staticmethod
    def _get_webhook_url(webhook_id: str) -> str:
        return f"https://YOUR_HA_HOST/api/webhook/{webhook_id}"

    async def async_step_import(self, import_config: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_user(import_config)