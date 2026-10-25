"""Polite HTTP helper: one session, browser UA, small delay, retry on
transient connection drops (the egress proxy occasionally resets POSTs)."""
from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import REQUEST_DELAY, REQUEST_TIMEOUT, USER_AGENT


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(total=4, backoff_factor=1.5,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET", "POST"}))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def get(session: requests.Session, url: str, **kw) -> requests.Response:
    time.sleep(REQUEST_DELAY)
    r = session.get(url, timeout=REQUEST_TIMEOUT, **kw)
    r.raise_for_status()
    return r


def post(session: requests.Session, url: str, **kw) -> requests.Response:
    time.sleep(REQUEST_DELAY)
    r = session.post(url, timeout=REQUEST_TIMEOUT, **kw)
    r.raise_for_status()
    return r
