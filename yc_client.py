"""
Yandex Cloud VPC REST API wrapper.

Approach chosen: direct HTTPS REST calls via httpx rather than the
yandexcloud gRPC SDK.  Reasons:
  - The gRPC SDK bundles ~350 MB of protobuf stubs and requires a working
    gRPC channel; httpx needs nothing beyond a pip install.
  - The VPC Address REST surface is tiny (create + delete), so a thin
    wrapper is easier to audit than opaque generated stubs.
  - The async-operation pattern (poll operation.api.cloud.yandex.net until
    done=true) is identical whether you use REST or gRPC, so there's no
    speed advantage to the heavier SDK for this workload.

Auth:
  Set ONE of these environment variables before running:
    YC_TOKEN   — a short-lived IAM token (recommended; get via `yc iam create-token`)
    YC_API_KEY — a long-lived API key created in the Yandex Cloud console

  The IAM token is passed as:  Authorization: Bearer <token>
  The API key is passed as:    Authorization: Api-Key <key>
"""

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

_VPC_BASE = "https://vpc.api.cloud.yandex.net/vpc/v1"
_OP_BASE = "https://operation.api.cloud.yandex.net/operations"

_MAX_RETRIES = 3
_TIMEOUT = 10.0          # seconds per request
_OP_POLL_INTERVAL = 0.5  # seconds between operation status polls
_OP_MAX_WAIT = 30.0      # seconds to wait for an operation before giving up


class AuthError(Exception):
    pass


class APIError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class YandexCloudClient:
    def __init__(self) -> None:
        iam_token = os.environ.get("YC_TOKEN")
        api_key = os.environ.get("YC_API_KEY")

        if iam_token:
            auth_header = f"Bearer {iam_token}"
            logger.debug("Auth: IAM token (YC_TOKEN)")
        elif api_key:
            auth_header = f"Api-Key {api_key}"
            logger.debug("Auth: API key (YC_API_KEY)")
        else:
            raise AuthError(
                "No credentials found. "
                "Set YC_TOKEN (IAM token) or YC_API_KEY (API key)."
            )

        self._http = httpx.Client(
            headers={"Authorization": auth_header},
            timeout=_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> dict:
        """
        Send an HTTP request with retry logic for 429/503 and timeouts.
        Raises APIError on non-2xx responses after retries are exhausted.
        """
        backoff = 1.0
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._http.request(method, url, **kwargs)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "Request timed out (attempt %d/%d), retrying in %.1fs…",
                        attempt + 1, _MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
                continue

            if resp.status_code in (429, 503):
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "HTTP %d (attempt %d/%d), retrying in %.1fs…",
                        resp.status_code, attempt + 1, _MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise APIError(resp.status_code, resp.text)

            if not resp.is_success:
                raise APIError(resp.status_code, resp.text)

            # 204 No Content (e.g. some delete responses) has no body
            if resp.status_code == 204 or not resp.content:
                return {}

            return resp.json()

        if last_exc:
            raise APIError(0, f"Request timed out after {_MAX_RETRIES} retries: {last_exc}")
        raise APIError(0, "Max retries exceeded")

    def _poll_operation(self, operation_id: str) -> dict:
        """
        Block until the Yandex Cloud async operation reaches done=true.
        Returns the completed operation dict, or raises APIError.
        """
        url = f"{_OP_BASE}/{operation_id}"
        deadline = time.monotonic() + _OP_MAX_WAIT

        while time.monotonic() < deadline:
            data = self._request("GET", url)
            if data.get("done"):
                if "error" in data:
                    err = data["error"]
                    raise APIError(err.get("code", 0), err.get("message", "unknown error"))
                return data
            time.sleep(_OP_POLL_INTERVAL)

        raise APIError(
            0,
            f"Operation {operation_id!r} did not complete within {_OP_MAX_WAIT}s",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allocate_ip(self, folder_id: str, zone: str, name: str) -> tuple[str, str]:
        """
        Reserve a new static external IPv4 address.

        Returns (address_id, ip_address) once the async operation completes.
        The returned IP is immediately reserved under your folder and starts
        accruing the idle-address fee if not attached to a resource.
        """
        payload = {
            "folderId": folder_id,
            "name": name,
            "externalIpv4AddressSpec": {"zoneId": zone},
        }
        op = self._request("POST", f"{_VPC_BASE}/addresses", json=payload)
        op_id = op["id"]
        logger.debug("Waiting for operation %s…", op_id)

        completed = self._poll_operation(op_id)
        addr = completed["response"]
        address_id: str = addr["id"]
        ip_address: str = addr["externalIpv4Address"]["address"]
        logger.debug("Allocated %s → %s", ip_address, address_id)
        return address_id, ip_address

    def release_ip(self, address_id: str) -> None:
        """
        Delete a reserved static IP address.
        The DELETE call also returns an async operation; we poll it so callers
        can be sure the address is gone before the next allocation attempt.
        """
        op = self._request("DELETE", f"{_VPC_BASE}/addresses/{address_id}")
        if op and op.get("id"):
            # Poll so we know the delete landed before the next allocate
            try:
                self._poll_operation(op["id"])
            except APIError as exc:
                # A 404 on poll means it's already gone — that's fine
                if exc.status_code not in (404,):
                    raise
        logger.debug("Released address %s", address_id)

    def close(self) -> None:
        self._http.close()
