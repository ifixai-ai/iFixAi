"""Dispose SDK pools while allowing newly created clients to remain cached."""

from collections.abc import MutableMapping
from typing import Protocol, TypeVar


class _ClosableClient(Protocol):
    async def close(self) -> None: ...


_Key = TypeVar("_Key")
_Client = TypeVar("_Client", bound=_ClosableClient)


async def close_cached_clients(clients: MutableMapping[_Key, _Client]) -> None:
    # Detach before awaiting so no caller can reuse an old client, and a client
    # created during teardown is not discarded at the end of this close pass.
    closing = list(clients.values())
    clients.clear()
    first_error: Exception | None = None
    for client in closing:
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001 -- close every pool before propagating the first failure
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
