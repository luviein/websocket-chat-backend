import asyncio
import websockets


async def collect(ws, count, received):
    for _ in range(count):
        received.append(await ws.recv())


async def main():
    room = "history-test-room"

    # first user joins, sends two messages, leaves
    first_pass_msgs = []
    async with websockets.connect(f"ws://localhost:8000/ws/{room}/alice") as ws:
        first_pass_msgs.append(await ws.recv())  # "alice joined the room"
        await ws.send("message one")
        first_pass_msgs.append(await ws.recv())  # echo of "message one"
        await ws.send("message two")
        first_pass_msgs.append(await ws.recv())  # echo of "message two"

    print("First session saw:", first_pass_msgs)

    # second user joins later - should receive history of the two messages
    async with websockets.connect(f"ws://localhost:8000/ws/{room}/bob") as ws:
        history = []
        # history messages arrive first, then the "bob joined" notice
        for _ in range(3):
            history.append(await ws.recv())

    print("Second session (bob) received on join:", history)

    assert any("message one" in m for m in history), "history should include 'message one'"
    assert any("message two" in m for m in history), "history should include 'message two'"
    print("\nPASS: message history persisted and replayed to new joiner")


asyncio.run(main())
