from __future__ import annotations

import asyncio
import base64
import logging
from typing import Dict, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)


class ImageHandler:
    def __init__(self, timeout: int = 30, max_size: int = 5 * 1024 * 1024, enable_cache: bool = True) -> None:
        self.timeout = timeout
        self.max_size = max_size
        self.enable_cache = enable_cache
        self._cache: Dict[str, str] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(3)

    async def ensure_session(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def fetch_image(self, url: str) -> Optional[str]:
        if self.enable_cache and url in self._cache:
            return self._cache[url]
        if not self._session:
            return None

        async with self._semaphore:
            return await self._do_fetch(url)

    async def _do_fetch(self, url: str) -> Optional[str]:
        for attempt in range(2):
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                }

                async with self._session.get(url, headers=headers, allow_redirects=True) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("HTTP %s for image", resp.status)
                        if attempt == 0:
                            await asyncio.sleep(1)
                            continue
                        return None

                    content = await resp.read()
                    if len(content) > self.max_size:
                        _LOGGER.warning("Image too large: %d bytes", len(content))
                        return None

                    encoded = base64.b64encode(content).decode("utf-8")
                    if self.enable_cache:
                        self._cache[url] = encoded
                    _LOGGER.info("Image fetched OK: %d bytes (attempt %d)", len(content), attempt + 1)
                    return encoded

            except Exception as err:
                _LOGGER.warning("Fetch error (attempt %d): %s", attempt + 1, err)
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                return None
        return None