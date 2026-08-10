import asyncio
import json
import uuid
import websockets

from test_helpers import get_token


async def recv_json(ws):
    return json.loads(await ws.recv())


async def recv_until_presence(ws):
    while True:
        msg = await recv_json(ws)
        if msg.get("type") == "presence":
            return msg["users"]


def status_of(roster, username):
    return next(u["status"] for u in roster if u["username"] == username)


async def main():
    room = f"status-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token("alice")
    bob, bob_token = get_token("bob")

    async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={alice_token}") as ws_alice:
        await recv_json(ws_alice)  # system: joined
        roster = await recv_until_presence(ws_alice)
        assert status_of(roster, alice) == "available", "default status should be 'available'"
        print("PASS: default status is 'available'")

        async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={bob_token}") as ws_bob:
            await recv_until_presence(ws_bob)  # bob's own initial snapshot
            await recv_until_presence(ws_alice)  # alice's snapshot updated with bob

            # alice sets herself to "away" - both alice and bob should see the update
            await ws_alice.send(json.dumps({"type": "set_status", "status": "away"}))

            alice_roster = await recv_until_presence(ws_alice)
            assert status_of(alice_roster, alice) == "away", alice_roster
            print("PASS: alice sees her own status change reflected")

            bob_roster = await recv_until_presence(ws_bob)
            assert status_of(bob_roster, alice) == "away", bob_roster
            print("PASS: bob sees alice's status change too")

            # invalid status values should be ignored, not crash or corrupt state
            await ws_alice.send(json.dumps({"type": "set_status", "status": "not-a-real-status"}))
            await ws_alice.send(json.dumps({"type": "chat", "content": "still alive"}))
            confirm = await recv_json(ws_alice)  # echo of the chat message proves the connection is still fine
            assert confirm == {"type": "chat", "username": alice, "content": "still alive"}, confirm
            print("PASS: invalid status value is ignored without breaking the connection")


asyncio.run(main())
