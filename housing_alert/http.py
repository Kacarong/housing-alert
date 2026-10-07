"""예의 있는 HTTP 클라이언트: 브라우저 UA, 요청 간 최소 간격, 지수 백오프 재시도."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36"
)


class FetchError(RuntimeError):
    pass


class PoliteClient:
    """출처 하나당 하나씩 만든다. 같은 출처에 대한 연속 요청 사이에 `delay`초 이상 쉰다."""

    def __init__(
        self,
        delay: float = 2.0,
        retries: int = 3,
        backoff_base: float = 2.0,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.delay = delay
        self.retries = retries
        self.backoff_base = backoff_base
        self._sleep = sleep
        self._last_request = 0.0
        self.request_count = 0
        self._client = httpx.Client(
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
            },
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _wait_turn(self) -> None:
        if self.delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            self._sleep(self.delay - elapsed)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            self._wait_turn()
            try:
                self.request_count += 1
                resp = self._client.request(method, url, **kwargs)
                self._last_request = time.monotonic()
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise FetchError(f"HTTP {resp.status_code} {url}")
                return resp
            except (httpx.HTTPError, FetchError) as exc:
                self._last_request = time.monotonic()
                last_exc = exc
                if attempt < self.retries - 1:
                    wait = self.backoff_base * (2 ** attempt)
                    log.warning("요청 실패(%s), %.0f초 후 재시도: %s", exc, wait, url)
                    self._sleep(wait)
        raise FetchError(str(last_exc))

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def get_html(self, url: str, **kwargs: Any) -> str:
        resp = self.get(url, **kwargs)
        return self._check_html(resp)

    def post_html(self, url: str, **kwargs: Any) -> str:
        resp = self.post(url, **kwargs)
        return self._check_html(resp)

    @staticmethod
    def _check_html(resp: httpx.Response) -> str:
        if resp.status_code != 200:
            raise FetchError(f"HTTP {resp.status_code} {resp.request.url}")
        return resp.text
