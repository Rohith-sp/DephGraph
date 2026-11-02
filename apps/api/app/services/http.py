import asyncio

import httpx

BACKOFF = 0.5  # seconds; doubled per retry (tests set to 0)
ATTEMPTS = 4


async def request(client: httpx.AsyncClient, method: str, url: str, **kw: object) -> httpx.Response:
    """Send with exponential backoff on 429/5xx/transport errors; raises httpx.HTTPError when exhausted."""
    err: httpx.HTTPError | None = None
    for attempt in range(ATTEMPTS):
        if attempt:
            await asyncio.sleep(BACKOFF * 2 ** (attempt - 1))
        try:
            r = await client.request(method, url, **kw)  # type: ignore[arg-type]
        except httpx.TransportError as e:
            err = e
            continue
        if r.status_code != 429 and r.status_code < 500:
            return r
        err = httpx.HTTPStatusError(str(r.status_code), request=r.request, response=r)
    assert err is not None
    raise err
