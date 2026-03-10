"""
simemu federation module — advertises this simemu instance on the local network
via mDNS using the same TXT record format as @vykeai/fed.

Advertises on:
  _vykeai._tcp.local  — shared vykeai bus (discovered by `fed status`)
  _simemu._tcp.local  — tool-specific bus
"""

from __future__ import annotations

import socket
from typing import Optional

try:
    from zeroconf import Zeroconf, ServiceInfo
except ImportError:
    raise ImportError(
        "Federation requires zeroconf.\n"
        "Install with:  pip install 'simemu[api]'"
    )

_zc: Optional[Zeroconf] = None
_infos: list[ServiceInfo] = []


def _make_service_info(
    service_type: str,
    identity: str,
    machine: str,
    fed_port: int,
    version: str,
) -> ServiceInfo:
    name = f"{machine}.{service_type}"
    properties = {
        b"identity": identity.encode(),
        b"machine":  machine.encode(),
        b"port":     str(fed_port).encode(),
        b"version":  version.encode(),
        b"service":  b"simemu",
    }
    return ServiceInfo(
        type_=service_type,
        name=name,
        addresses=[socket.inet_aton("127.0.0.1")],
        port=fed_port,
        properties=properties,
        server=f"{machine}.local.",
    )


def start_federation(identity: str, fed_port: int, version: str = "0.1.0") -> None:
    global _zc, _infos

    machine = socket.gethostname()
    _zc = Zeroconf()
    _infos = [
        _make_service_info("_vykeai._tcp.local.", identity, machine, fed_port, version),
        _make_service_info("_simemu._tcp.local.",  identity, machine, fed_port, version),
    ]

    for info in _infos:
        _zc.register_service(info)

    print(f"[simemu-fed] Advertising on mDNS (identity={identity}, fed_port={fed_port})", flush=True)


def stop_federation() -> None:
    global _zc, _infos

    if _zc is None:
        return

    for info in _infos:
        try:
            _zc.unregister_service(info)
        except Exception:
            pass

    _zc.close()
    _zc = None
    _infos = []
    print("[simemu-fed] mDNS stopped", flush=True)
