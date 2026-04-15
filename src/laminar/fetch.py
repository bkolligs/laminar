from __future__ import annotations

from urllib.request import Request, urlopen


USER_AGENT = "laminar/0.1 (+https://github.com/bkolligs/laminar)"


def fetch_text(url: str, timeout: float = 30.0) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")
