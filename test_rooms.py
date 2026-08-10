import asyncio
import websockets


async def listen(ws, received):
    try:
        while True:
            received.append(await ws.recv())
    except websockets.exceptions.ConnectionClosed:
        pass


async def main():
    room_a_msgs, room_a2_msgs, room_b_msgs = [], [], []

    async with websockets.connect("ws://localhost:8000/ws/roomA/alice") as a1, \
               websockets.connect("ws://localhost:8000/ws/roomA/bob") as a2, \
               websockets.connect("ws://localhost:8000/ws/roomB/carol") as b1:

        t1 = asyncio.create_task(listen(a1, room_a_msgs))
        t2 = asyncio.create_task(listen(a2, room_a2_msgs))
        t3 = asyncio.create_task(listen(b1, room_b_msgs))

        await asyncio.sleep(0.3)  # let join broadcasts settle
        await a1.send("hello room A")
        await asyncio.sleep(0.3)

        t1.cancel()
        t2.cancel()
        t3.cancel()

    print("alice (roomA) received:", room_a_msgs)
    print("bob   (roomA) received:", room_a2_msgs)
    print("carol (roomB) received:", room_b_msgs)

    assert any("hello room A" in m for m in room_a2_msgs), "bob should see alice's message (same room)"
    assert not any("hello room A" in m for m in room_b_msgs), "carol should NOT see alice's message (different room)"
    print("\nPASS: room isolation works correctly")


asyncio.run(main())
