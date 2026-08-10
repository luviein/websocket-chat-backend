import asyncio
import json
import uuid
import websockets

from test_helpers import get_token


async def recv_json(ws):
    return json.loads(await ws.recv())


async def main():
    room = f"history-test-room-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token("alice")
    bob, bob_token = get_token("bob")
    carol, carol_token = get_token("carol")

    # first user joins, sends two messages, leaves
    async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={alice_token}") as ws:
        await recv_json(ws)  # system: "alice joined the room"
        await recv_json(ws)  # presence snapshot
        await ws.send(json.dumps({"type": "chat", "content": "message one"}))
        await recv_json(ws)  # echo of "message one"
        await ws.send(json.dumps({"type": "chat", "content": "message two"}))
        await recv_json(ws)  # echo of "message two"

    # second user joins later - should receive history + "missed messages" header
    received = []
    async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={bob_token}") as ws:
        while True:
            msg = await recv_json(ws)
            received.append(msg)
            if msg.get("type") == "system" and f"{bob} joined the room" in msg.get("content", ""):
                break

    print(f"Second session ({bob}) received on join:", received)

    assert received[0] == {"type": "system", "content": "Messages you have missed:"}, (
        "header should be sent first, before history"
    )
    assert any(m.get("content") == "message one" for m in received), "history should include 'message one'"
    assert any(m.get("content") == "message two" for m in received), "history should include 'message two'"
    print("PASS: history is prefixed with 'missed messages' header")

    # a brand new, empty room should NOT show the header (nothing to miss)
    empty_room = f"empty-room-{uuid.uuid4().hex[:8]}"
    async with websockets.connect(f"ws://localhost:8000/ws/{empty_room}?token={carol_token}") as ws:
        first_msg = await recv_json(ws)

    assert first_msg == {"type": "system", "content": f"{carol} joined the room"}, (
        "empty room should skip straight to join notice, no header"
    )
    print("PASS: empty room correctly skips the 'missed messages' header")


asyncio.run(main())
