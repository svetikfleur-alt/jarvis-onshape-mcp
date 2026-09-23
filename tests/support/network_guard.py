"""Offline-by-default socket and DNS policy for pytest."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import partial
import socket
import sys
from threading import Lock


NETWORK_ACCESS_FORBIDDEN_IN_TEST = "NETWORK_ACCESS_FORBIDDEN_IN_TEST"
_CAPABILITY = object()
_network_capability: ContextVar[object | None] = ContextVar("guarded_network", default=None)
_SOCKET_SEND_METHODS = ("connect", "connect_ex", "sendto", "sendmsg")
_AUDITED_NETWORK_EVENTS = frozenset(
    {
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.getnameinfo",
        "socket.sendmsg",
        "socket.sendto",
    }
)
_guard_state_lock = Lock()
_audit_hook_installed = False
_active_guard_sessions = 0


def _require_permit(*args: object, **kwargs: object) -> None:
    if _network_capability.get() is not _CAPABILITY:
        raise RuntimeError(NETWORK_ACCESS_FORBIDDEN_IN_TEST)


def _network_audit_hook(event: str, args: tuple[object, ...]) -> None:
    """Backstop aliases of the immutable low-level ``_socket.socket`` type."""
    if event not in _AUDITED_NETWORK_EVENTS:
        return
    with _guard_state_lock:
        active = _active_guard_sessions > 0
    if active:
        _require_permit()


@contextmanager
def _permit_guarded_network():
    token = _network_capability.set(_CAPABILITY)
    try:
        yield
    finally:
        _network_capability.reset(token)


def install_network_guard(monkeypatch) -> None:
    global _active_guard_sessions, _audit_hook_installed
    with _guard_state_lock:
        if not _audit_hook_installed:
            sys.addaudithook(_network_audit_hook)
            _audit_hook_installed = True
        _active_guard_sessions += 1

    original_socketpair = socket.socketpair
    original_getaddrinfo = socket.getaddrinfo
    original_getnameinfo = socket.getnameinfo

    def guarded(original):
        def call(*args, **kwargs):
            _require_permit()
            return original(*args, **kwargs)

        return call

    def local_socketpair(*args, **kwargs):
        with _permit_guarded_network():
            return original_socketpair(*args, **kwargs)

    guarded_boundaries = [
        (socket.socket, name)
        for name in _SOCKET_SEND_METHODS
        if hasattr(socket.socket, name)
    ]
    guarded_boundaries.extend(
        (
        (socket, "getaddrinfo"),
        (socket, "gethostbyname"),
        (socket, "gethostbyname_ex"),
        (socket, "gethostbyaddr"),
        (socket, "getnameinfo"),
        )
    )
    for owner, name in guarded_boundaries:
        monkeypatch.setattr(owner, name, guarded(getattr(owner, name)))
    monkeypatch.setattr(socket, "socketpair", local_socketpair)
    async def guarded_getaddrinfo(
        loop, host, port, *, family=0, type=0, proto=0, flags=0
    ):
        _require_permit()
        resolve = partial(
            copy_context().run,
            original_getaddrinfo,
            host,
            port,
            family,
            type,
            proto,
            flags,
        )
        return await loop.run_in_executor(None, resolve)

    async def guarded_getnameinfo(loop, sockaddr, flags=0):
        _require_permit()
        resolve = partial(copy_context().run, original_getnameinfo, sockaddr, flags)
        return await loop.run_in_executor(None, resolve)

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(asyncio.BaseEventLoop, "getnameinfo", guarded_getnameinfo)
    loop_classes = [asyncio.BaseEventLoop]
    try:
        from asyncio.selector_events import BaseSelectorEventLoop

        loop_classes.append(BaseSelectorEventLoop)
    except ImportError:
        pass
    try:
        from asyncio.proactor_events import BaseProactorEventLoop

        loop_classes.append(BaseProactorEventLoop)
    except ImportError:
        pass
    try:
        from asyncio.windows_events import ProactorEventLoop

        loop_classes.append(ProactorEventLoop)
    except ImportError:
        pass
    loop_classes.extend(
        loop_class
        for name in ("SelectorEventLoop", "ProactorEventLoop")
        if (loop_class := getattr(asyncio, name, None)) is not None
    )

    seen_classes: set[type] = set()
    seen_methods: set[int] = set()
    for loop_class in loop_classes:
        original = getattr(loop_class, "sock_connect", None)
        if loop_class in seen_classes or original is None or id(original) in seen_methods:
            continue
        seen_classes.add(loop_class)
        seen_methods.add(id(original))
        monkeypatch.setattr(loop_class, "sock_connect", guarded(original))


def uninstall_network_guard() -> None:
    """Deactivate one pytest session while leaving the process audit hook inert."""
    global _active_guard_sessions
    with _guard_state_lock:
        if _active_guard_sessions > 0:
            _active_guard_sessions -= 1
