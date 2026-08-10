import json
import uuid

import pytest
import websockets

from test_helpers import get_token, ws_url


async def recv_json(ws):
    return json.loads(await ws.recv())


@pytest.mark.asyncio
async def test_history_replayed_with_missed_messages_header(server):
    room = f"history-test-room-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")

    # first user joins, sends two messages, leaves
    async with websockets.connect(ws_url(server, room, alice_token)) as ws:
        await recv_json(ws)  # system: "alice joined the room"
        await recv_json(ws)  # presence snapshot
        await ws.send(json.dumps({"type": "chat", "content": "message one"}))
        await recv_json(ws)  # echo of "message one"
        await ws.send(json.dumps({"type": "chat", "content": "message two"}))
        await recv_json(ws)  # echo of "message two"

    # second user joins later - should receive history + "missed messages" header
    received = []
    async with websockets.connect(ws_url(server, room, bob_token)) as ws:
        while True:
            msg = await recv_json(ws)
            received.append(msg)
            if msg.get("type") == "system" and f"{bob} joined the room" in msg.get("content", ""):
                break

    assert received[0] == {"type": "system", "content": "Messages you have missed:"}, (
        "header should be sent first, before history"
    )
    assert any(m.get("content") == "message one" for m in received), "history should include 'message one'"
    assert any(m.get("content") == "message two" for m in received), "history should include 'message two'"


@pytest.mark.asyncio
async def test_empty_room_skips_missed_messages_header(server):
    carol, carol_token = get_token(server, "carol")
    empty_room = f"empty-room-{uuid.uuid4().hex[:8]}"

    async with websockets.connect(ws_url(server, empty_room, carol_token)) as ws:
        first_msg = await recv_json(ws)

    assert first_msg == {"type": "system", "content": f"{carol} joined the room"}, (
        "empty room should skip straight to join notice, no header"
    )
