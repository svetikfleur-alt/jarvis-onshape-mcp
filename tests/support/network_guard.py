"""Offline-by-default socket and DNS policy for pytest."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import socket


NETWORK_ACCESS_FORBIDDEN_IN_TEST = "NETWORK_ACCESS_FORBIDDEN_IN_TEST"
_CAPABILITY = object()
_network_capability: ContextVar[object | None] = ContextVar("guarded_network", default=None)


def _require_permit(*args: object, **kwargs: object) -> None:
    if _network_capability.get() is not _CAPABILITY:
        raise RuntimeError(NETWORK_ACCESS_FORBIDDEN_IN_TEST)


@contextmanager
def _permit_guarded_network():
    token = _network_capability.set(_CAPABILITY)
    try:
        yield
    finally:
        _network_capability.reset(token)


def install_network_guard(monkeypatch) -> None:
    original_socketpair = socket.socketpair

    def guarded(original):
        def call(*args, **kwargs):
            _require_permit()
            return original(*args, **kwargs)

        return call

    def local_socketpair(*args, **kwargs):
        with _permit_guarded_network():
            return original_socketpair(*args, **kwargs)

    for owner, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket.socket, "sendto"),
        (socket, "getaddrinfo"),
        (socket, "gethostbyname"),
        (socket, "gethostbyname_ex"),
        (socket, "gethostbyaddr"),
        (socket, "getnameinfo"),
    ):
        monkeypatch.setattr(owner, name, guarded(getattr(owner, name)))
    monkeypatch.setattr(socket, "socketpair", local_socketpair)
    for name in ("getaddrinfo", "getnameinfo"):
        if hasattr(asyncio.BaseEventLoop, name):
            monkeypatch.setattr(
                asyncio.BaseEventLoop, name, guarded(getattr(asyncio.BaseEventLoop, name))
            )
    loop_classes = {
        asyncio.BaseEventLoop,
        getattr(asyncio, "ProactorEventLoop", asyncio.BaseEventLoop),
        getattr(asyncio, "SelectorEventLoop", asyncio.BaseEventLoop),
    }
    for loop_class in loop_classes:
        monkeypatch.setattr(loop_class, "sock_connect", guarded(loop_class.sock_connect))
