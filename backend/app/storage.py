"""Readiness probe for the local S3-compatible object store.

The first local Compose setup uses RustFS's unauthenticated health endpoint.
This probe does not read credentials or enumerate private objects.
"""

import os
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def object_store_ready() -> bool:
    endpoint = os.environ.get("S3_ENDPOINT_URL")
    if not endpoint:
        return False

    health_url = endpoint.rstrip("/") + "/health"
    try:
        with urlopen(health_url, timeout=2) as response:
            return response.status == 200
    except (HTTPError, URLError, ValueError, TimeoutError):
        return False
