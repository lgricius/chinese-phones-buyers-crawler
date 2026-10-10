"""Browser-impersonating HTTP client.

curl_cffi reproduces real browsers at the TLS/HTTP2 fingerprint level and sends
the matching User-Agent + header set, which is what Cloudflare & co. actually
check. We pick a random recent browser profile per site session, add human-ish
jitter between requests, and rotate to a different profile when blocked.
"""

from __future__ import annotations

import asyncio
import logging
import random

from curl_cffi.requests import AsyncSession

log = logging.getLogger(__name__)

# Recent desktop browser profiles. The UA string and headers come from the
# profile itself so they always match the TLS fingerprint.
PROFILES = [
    "chrome145",
    "chrome146",
    "chrome150",
    "safari260",
    "safari184",
    "firefox147",
]

ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,de;q=0.8",
    "en-US,en;q=0.8,lt;q=0.6",
]

BLOCK_MARKERS = ("Attention Required! | Cloudflare", "cf-chl-", "Just a moment...")


class Blocked(Exception):
    pass


class BrowserClient:
    def __init__(self, concurrency: int = 4, delay: tuple[float, float] = (0.3, 1.2), timeout: int = 40):
        self.sem = asyncio.Semaphore(concurrency)
        self.delay = delay
        self.timeout = timeout
        self._new_session()

    def _new_session(self, avoid: str | None = None, cookies=None) -> None:
        self.profile = random.choice([p for p in PROFILES if p != avoid])
        self.session = AsyncSession(
            impersonate=self.profile,
            headers={"Accept-Language": random.choice(ACCEPT_LANGUAGES)},
            timeout=self.timeout,
        )
        # Keep shop session cookies (currency, cart) but not Cloudflare's, which
        # are tied to the previous browser fingerprint.
        for c in cookies or []:
            if not c.name.startswith(("__cf", "cf_")):
                self.session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        log.debug("using browser profile %s", self.profile)

    async def close(self) -> None:
        await self.session.close()

    async def request(self, method: str, url: str, *, retries: int = 4, **kw):
        last_exc: Exception | None = None
        for attempt in range(retries):
            async with self.sem:
                await asyncio.sleep(random.uniform(*self.delay))
                try:
                    r = await self.session.request(method, url, **kw)
                except Exception as e:  # network errors
                    last_exc = e
                    log.warning("%s %s failed (%s), retry %d", method, url, e, attempt + 1)
                    await asyncio.sleep(2**attempt + random.random())
                    continue
            blocked = r.status_code in (403, 429, 503) or any(m in r.text[:5000] for m in BLOCK_MARKERS)
            if blocked:
                last_exc = Blocked(f"{r.status_code} on {url} with {self.profile}")
                log.warning("blocked (%s) on %s, rotating browser profile", r.status_code, url)
                old = self.session
                self._new_session(avoid=self.profile, cookies=list(old.cookies.jar))
                await old.close()
                await asyncio.sleep(3 * (attempt + 1) + random.random() * 3)
                continue
            if r.status_code >= 500:
                last_exc = RuntimeError(f"HTTP {r.status_code} on {url}")
                await asyncio.sleep(2**attempt + random.random())
                continue
            return r
        raise last_exc or RuntimeError(f"failed {url}")

    async def get(self, url: str, **kw):
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw):
        return await self.request("POST", url, **kw)
