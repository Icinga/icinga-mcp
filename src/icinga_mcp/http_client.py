"""Icinga Web HTTP client.

Author: Bernd Erk - Icinga GmbH
License: GPL-2.0-only

This module provides an async HTTP client wrapper around httpx with retry,
URL-safe query encoding and structured logging for interacting with the
Icinga Web 2 Icinga DB Web JSON endpoints.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
import httpx
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type
from .config import Settings
import structlog
from urllib.parse import urlencode, quote

log = structlog.get_logger(__name__)


class IcingaWebClient:
    """HTTP client for Icinga DB Web via httpx.

    Attributes:
        s: Settings used to configure timeouts, TLS, retries, and base_url.
    """
    def __init__(self, settings: Settings):
        self.s = settings
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure a singleton httpx.AsyncClient configured with auth, TLS, timeouts, and headers.

        Returns:
            httpx.AsyncClient: Reused client instance.
        """
        if self._client is None:
            verify: bool | str = self.s.verify_tls
            if self.s.ca_bundle:
                verify = self.s.ca_bundle

            # Build headers and auth. Use Basic auth only.
            headers = {
                "Accept": "application/json",
            }
            auth = (self.s.username, self.s.password.get_secret_value())

            self._client = httpx.AsyncClient(
                base_url=self.s.base_url.rstrip("/") + "/",
                auth=auth,
                timeout=self.s.request_timeout,
                verify=verify,
                headers=headers,
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, str] | None = None,
        json_body: Any | None = None,
        form_fields: Dict[str, str] | None = None,
    ) -> httpx.Response:
        """Perform an HTTP request against the configured Icinga Web base URL.

        Args:
            method: HTTP method (GET, POST, DELETE).
            path: Endpoint path relative to the Icinga Web root (e.g. "icingadb/hosts").
            params: Query parameters; spaces are encoded as %20 and certain characters kept safe.
            json_body: JSON serializable payload for application/json requests.
            form_fields: Key-value form fields for application/x-www-form-urlencoded requests.

        Returns:
            httpx.Response: Raw response object.

        Raises:
            ValueError: If both json_body and form_fields are provided.
            httpx.HTTPStatusError: For upstream redirects or 5xx responses (retryable).
            httpx.HTTPError: For transport-level errors (retryed per policy).
        """
        client = await self._ensure_client()
        url = path.lstrip("/")

        # Build query string using percent-encoding for spaces (i.e., %20 instead of +)
        # Keep certain safe characters unencoded, notably '!' so composite comment names remain readable:
        # Example desired: thermos.vm.icinga.com!Load!2caf6a2b-cf98-4b86-8736-cb0a77846746
        qp: str | None = None
        if params:
            # urlencode with quote_via=quote ensures spaces are encoded as %20
            # Allow unencoded: ! $ ' ( ) * , - . _ ~
            qp = urlencode(params, doseq=True, quote_via=quote, safe="!$'()*,-._~")

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.s.max_retries),
            wait=wait_exponential_jitter(initial=self.s.retry_backoff, max=5),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                # Build the request so we can log the exact URL before sending
                # Attach pre-encoded query to the URL to avoid '+' encoding of spaces.
                # Build via httpx.URL and set raw query bytes to preserve %20 exactly.
                if qp is None:
                    url_obj = httpx.URL(url)
                else:
                    url_obj = httpx.URL(url).copy_with(query=qp.encode("ascii"))
                prepared_url_str = str(url_obj)
                prepared_raw_path = url_obj.raw_path.decode("ascii")
                log.debug("upstream.request.prepared", method=method, request_url=prepared_url_str, raw_path=prepared_raw_path)

                # Ensure only one body type is provided
                if json_body is not None and form_fields is not None:
                    raise ValueError("Provide either json_body or form_fields, not both")

                req_kwargs: Dict[str, Any] = {}
                if json_body is not None:
                    req_kwargs["json"] = json_body
                elif form_fields is not None:
                    # Send as application/x-www-form-urlencoded key-value pairs
                    req_kwargs["data"] = form_fields

                req = client.build_request(method, url_obj, **req_kwargs)
                log.debug("upstream.request", method=method, url=str(req.url), raw_path=req.url.raw_path.decode("ascii"))
                resp = await client.send(req)

                # Log response meta for visibility
                log.debug(
                    "upstream.response",
                    method=method,
                    url=str(req.url),
                    raw_path=req.url.raw_path.decode("ascii"),
                    status=resp.status_code,
                    location=resp.headers.get("location"),
                )

                # Treat upstream redirects as an error (likely auth redirect or wrong endpoint mapping)
                if 300 <= resp.status_code < 400:
                    raise httpx.HTTPStatusError(
                        f"Upstream redirect to {resp.headers.get('location', '') or 'unknown'}",
                        request=resp.request,
                        response=resp,
                    )

                # Log a short body snippet for non-2xx to aid debugging (avoid dumping full bodies)
                if resp.status_code >= 400:
                    try:
                        snippet = resp.text[:512] if resp.text else ""
                    except Exception:
                        snippet = ""
                    log.warning(
                        "upstream.error",
                        method=method,
                        url=str(req.url),
                        status=resp.status_code,
                        body_snippet=snippet,
                    )

                if resp.status_code >= 500:
                    # transient upstream
                    raise httpx.HTTPStatusError(
                        f"Upstream {resp.status_code}", request=resp.request, response=resp
                    )
                return resp

    async def get_json(self, path: str, *, params: Dict[str, str] | None = None) -> Any:
        """GET JSON helper with raise_for_status and JSON decoding."""
        # Use endpoint path as provided (no forced trailing slash)
        resp = await self.request("GET", path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def post_json(self, path: str, *, payload: Any) -> Any:
        """POST JSON helper with raise_for_status and optional empty body handling."""
        resp = await self.request("POST", path, json_body=payload)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def delete(self, path: str, *, params: Dict[str, str] | None = None) -> int:
        """DELETE helper returning the HTTP status code."""
        resp = await self.request("DELETE", path, params=params)
        resp.raise_for_status()
        return resp.status_code
