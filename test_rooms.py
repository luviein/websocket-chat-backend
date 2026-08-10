import asyncio
import json
import uuid

import pytest
import websockets

from test_helpers import get_token, ws_url


async def listen(ws, received):
    try:
        while True:
            received.append(json.loads(await ws.recv()))
    except websockets.exceptions.ConnectionClosed:
        pass


def has_chat(msgs, username, content):
    return any(
        m.get("type") == "chat" and m.get("username") == username and m.get("content") == content
        for m in msgs
    )


@pytest.mark.asyncio
async def test_room_isolation(server):
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")
    carol, carol_token = get_token(server, "carol")

    room_a = f"roomA-{uuid.uuid4().hex[:6]}"
    room_b = f"roomB-{uuid.uuid4().hex[:6]}"

    room_a_msgs, room_a2_msgs, room_b_msgs = [], [], []

    async with websockets.connect(ws_url(server, room_a, alice_token)) as a1, \
               websockets.connect(ws_url(server, room_a, bob_token)) as a2, \
               websockets.connect(ws_url(server, room_b, carol_token)) as b1:

        t1 = asyncio.create_task(listen(a1, room_a_msgs))
        t2 = asyncio.create_task(listen(a2, room_a2_msgs))
        t3 = asyncio.create_task(listen(b1, room_b_msgs))

        await asyncio.sleep(0.3)  # let join broadcasts settle
        await a1.send(json.dumps({"type": "chat", "content": "hello room A"}))
        await asyncio.sleep(0.3)

        t1.cancel()
        t2.cancel()
        t3.cancel()

    assert has_chat(room_a2_msgs, alice, "hello room A"), "bob should see alice's message (same room)"
    assert not has_chat(room_b_msgs, alice, "hello room A"), (
        "carol should NOT see alice's message (different room)"
    )
