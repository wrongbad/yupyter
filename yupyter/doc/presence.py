"""Server-local awareness for agents (they have no websocket of their own).

Awareness shape (same as browsers publish)::

    {"user": {"id", "name", "color", "colorLight", "kind": "human"|"agent"},
     "cursor": {"cellId": str | None}}

An agent gets a stable pseudo client id derived from its name, so repeated
calls update one avatar. Browsers drop remote awareness states that are not
refreshed for 30s, so call ``set_agent_presence`` again at least every ~15s
while the agent is active (it just bumps the clock), and ``clear_agent_presence``
when done.
"""
from __future__ import annotations

import json
import zlib

from pycrdt import Encoder, create_awareness_message

AGENT_COLOR = "#d97757"


def agent_client_id(client_name: str) -> int:
    return (zlib.crc32(f"agent:{client_name}".encode()) | 0x80000000) & 0xFFFFFFFF


def _push(open_doc, client_id: int, state: dict | None) -> None:
    aw = open_doc.awareness
    clock = aw._meta.get(client_id, {}).get("clock", 0) + 1
    enc = Encoder()
    enc.write_var_uint(1)
    enc.write_var_uint(client_id)
    enc.write_var_uint(clock)
    enc.write_var_string(json.dumps(state, separators=(",", ":")))
    update = enc.to_bytes()
    aw.apply_awareness_update(update, "agent-presence")
    msg = create_awareness_message(update)
    for conn in list(open_doc.connections):
        conn.send(msg)


def set_agent_presence(
    open_doc, client_name: str, cell_id: str | None, *, color: str = AGENT_COLOR
) -> None:
    """Show agent ``client_name`` as present, "focused" on ``cell_id`` (or none)."""
    _push(
        open_doc,
        agent_client_id(client_name),
        {
            "user": {
                "id": f"agent:{client_name}",
                "name": client_name,
                "color": color,
                "colorLight": color + "33",
                "kind": "agent",
            },
            "cursor": {"cellId": cell_id},
        },
    )


def clear_agent_presence(open_doc, client_name: str) -> None:
    cid = agent_client_id(client_name)
    if cid in open_doc.awareness.states:
        _push(open_doc, cid, None)
