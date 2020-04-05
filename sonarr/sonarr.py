"""Asynchronous Python client for Sonarr."""
import asyncio
import json
from socket import gaierror as SocketGIAError
from typing import Any, List, Mapping, Optional

import aiohttp
import async_timeout
from yarl import URL

from .__version__ import __version__
from .exceptions import SonarrAccessRestricted, SonarrConnectionError, SonarrError
from .models import Application, Episode, QueueItem


class Sonarr:
    """Main class for handling connections with Sonarr API."""

    _application: Optional[Application] = None

    def __init__(
        self,
        host: str,
        api_key: str,
        base_path: str = "/api/",
        port: int = 8989,
        request_timeout: int = 8,
        session: aiohttp.client.ClientSession = None,
        user_agent: str = None,
    ) -> None:
        """Initialize connection with receiver."""
        self._session = session
        self._close_session = False

        self.api_key = api_key
        self.base_path = base_path
        self.host = host
        self.port = port
        self.request_timeout = request_timeout
        self.user_agent = user_agent

        if user_agent is None:
            self.user_agent = f"PythonSonarr/{__version__}"

        if self.base_path[-1] != "/":
            self.base_path += "/"

    async def _request(
        self,
        uri: str = "",
        method: str = "GET",
        data: Optional[Any] = None,
        params: Optional[Mapping[str, str]] = None,
    ) -> Any:
        """Handle a request to API."""
        scheme = "http"

        url = URL.build(
            scheme=scheme, host=self.host, port=self.port, path=self.base_path
        ).join(URL(uri))

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "X-Api-Key": self.api_key,
        }

        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._close_session = True

        try:
            with async_timeout.timeout(self.request_timeout):
                response = await self._session.request(
                    method, url, data=data, params=params, headers=headers,
                )
        except asyncio.TimeoutError as exception:
            raise SonarrConnectionError(
                "Timeout occurred while connecting to API"
            ) from exception
        except (aiohttp.ClientError, SocketGIAError) as exception:
            raise SonarrConnectionError(
                "Error occurred while communicating with API"
            ) from exception

        if response.status == 403:
            raise SonarrAccessRestricted(
                "Access restricted. Please ensure valid API Key is provided", {}
            )

        content_type = response.headers.get("Content-Type")

        if (response.status // 100) in [4, 5]:
            content = await response.read()
            response.close()

            if content_type == "application/json":
                raise SonarrError(
                    f"HTTP {response.status}", json.loads(content.decode("utf8"))
                )

            raise SonarrError(
                f"HTTP {response.status}",
                {
                    "content-type": content_type,
                    "message": content.decode("utf8"),
                    "status-code": response.status,
                },
            )

        if "application/json" in content_type:
            data = await response.json()
            return data

        return await response.text()

    @property
    def app(self) -> Optional[Application]:
        """Return the cached Application object."""
        return self._application

    async def update(self, full_update: bool = False) -> Application:
        """Get all information about the application in a single call."""
        if self._application is None or full_update:
            status = await self._request("system/status")
            if status is None:
                raise SonarrError("Sonarr returned an empty API status response")

            diskspace = await self._request("diskspace")
            if not diskspace or diskspace is None:
                raise SonarrError("Sonarr returned an empty API diskspace response")

            self._application = Application({"info": status, "diskspace": diskspace})
            return self._application

        diskspace = await self._request("diskspace")
        self._application.update_from_dict({"diskspace": diskspace})
        return self._application

    async def calendar(self, start: int = None, end: int = None) -> List[Episode]:
        """Get upcoming episodes.

        If start/end are not supplied, episodes airing
        today and tomorrow will be returned.
        """
        params = {}

        if start is not None:
            params["start"] = str(start)

        if end is not None:
            params["end"] = str(end)

        results = await self._request("calendar", params=params)

        return [Episode.from_dict(result) for result in results]

    async def queue(self) -> List[QueueItem]:
        """Get currently downloading info."""
        results = await self._request("queue")

        return [QueueItem.from_dict(result) for result in results]

    async def close(self) -> None:
        """Close open client session."""
        if self._session and self._close_session:
            await self._session.close()

    async def __aenter__(self) -> "Sonarr":
        """Async enter."""
        return self

    async def __aexit__(self, *exc_info) -> None:
        """Async exit."""
        await self.close()