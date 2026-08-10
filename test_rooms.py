import asyncio
import json
import websockets

from test_helpers import get_token


async def listen(ws, received):
    try:
        while True:
            received.append(json.loads(await ws.recv()))
    except websockets.exceptions.ConnectionClosed:
        pass


async def main():
    alice, alice_token = get_token("alice")
    bob, bob_token = get_token("bob")
    carol, carol_token = get_token("carol")

    room_a_msgs, room_a2_msgs, room_b_msgs = [], [], []

    async with websockets.connect(f"ws://localhost:8000/ws/roomA?token={alice_token}") as a1, \
               websockets.connect(f"ws://localhost:8000/ws/roomA?token={bob_token}") as a2, \
               websockets.connect(f"ws://localhost:8000/ws/roomB?token={carol_token}") as b1:

        t1 = asyncio.create_task(listen(a1, room_a_msgs))
        t2 = asyncio.create_task(listen(a2, room_a2_msgs))
        t3 = asyncio.create_task(listen(b1, room_b_msgs))

        await asyncio.sleep(0.3)  # let join broadcasts settle
        await a1.send(json.dumps({"type": "chat", "content": "hello room A"}))
        await asyncio.sleep(0.3)

        t1.cancel()
        t2.cancel()
        t3.cancel()

    def has_chat(msgs, username, content):
        return any(
            m.get("type") == "chat" and m.get("username") == username and m.get("content") == content
            for m in msgs
        )

    print(f"{alice} (roomA) received:", room_a_msgs)
    print(f"{bob}   (roomA) received:", room_a2_msgs)
    print(f"{carol} (roomB) received:", room_b_msgs)

    assert has_chat(room_a2_msgs, alice, "hello room A"), "bob should see alice's message (same room)"
    assert not has_chat(room_b_msgs, alice, "hello room A"), "carol should NOT see alice's message (different room)"
    print("\nPASS: room isolation works correctly")


asyncio.run(main())
