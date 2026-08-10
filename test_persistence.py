import asyncio
import uuid
import websockets

from test_helpers import get_token


async def main():
    room = f"history-test-room-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token("alice")
    bob, bob_token = get_token("bob")
    carol, carol_token = get_token("carol")

    # first user joins, sends two messages, leaves
    async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={alice_token}") as ws:
        await ws.recv()  # "alice joined the room"
        await ws.send("message one")
        await ws.recv()  # echo of "message one"
        await ws.send("message two")
        await ws.recv()  # echo of "message two"

    # second user joins later - should receive history + "missed messages" header
    received = []
    async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={bob_token}") as ws:
        while True:
            msg = await ws.recv()
            received.append(msg)
            if f"{bob} joined the room" in msg:
                break

    print(f"Second session ({bob}) received on join:", received)

    assert received[0] == "--- Messages you have missed ---", "header should be sent first, before history"
    assert any("message one" in m for m in received), "history should include 'message one'"
    assert any("message two" in m for m in received), "history should include 'message two'"
    print("PASS: history is prefixed with 'missed messages' header")

    # a brand new, empty room should NOT show the header (nothing to miss)
    empty_room = f"empty-room-{uuid.uuid4().hex[:8]}"
    async with websockets.connect(f"ws://localhost:8000/ws/{empty_room}?token={carol_token}") as ws:
        first_msg = await ws.recv()

    assert first_msg == f"{carol} joined the room", "empty room should skip straight to join notice, no header"
    print("PASS: empty room correctly skips the 'missed messages' header")


asyncio.run(main())
