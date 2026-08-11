import json
import uuid

import pytest
import websockets

from test_helpers import get_token, ws_url


async def recv_json(ws):
    return json.loads(await ws.recv())


async def recv_until_presence(ws):
    while True:
        msg = await recv_json(ws)
        if msg.get("type") == "presence":
            return msg["users"]


def status_of(roster, username):
    return next(u["status"] for u in roster if u["username"] == username)


@pytest.mark.asyncio
async def test_default_status_is_available(server):
    room = f"status-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")

    async with websockets.connect(ws_url(server, room, alice_token)) as ws_alice:
        await recv_json(ws_alice)  # self
        await recv_json(ws_alice)  # block_list
        await recv_json(ws_alice)  # system: joined
        roster = await recv_until_presence(ws_alice)
        assert status_of(roster, alice) == "available", "default status should be 'available'"


@pytest.mark.asyncio
async def test_status_change_broadcasts_and_ignores_invalid_values(server):
    room = f"status-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")

    async with websockets.connect(ws_url(server, room, alice_token)) as ws_alice:
        await recv_json(ws_alice)  # self
        await recv_json(ws_alice)  # block_list
        await recv_json(ws_alice)  # system: joined
        await recv_until_presence(ws_alice)  # alice's own initial snapshot

        async with websockets.connect(ws_url(server, room, bob_token)) as ws_bob:
            await recv_json(ws_bob)  # self
            await recv_json(ws_bob)  # block_list
            await recv_until_presence(ws_bob)  # bob's own initial snapshot
            await recv_until_presence(ws_alice)  # alice's snapshot updated with bob

            # alice sets herself to "away" - both alice and bob should see the update
            await ws_alice.send(json.dumps({"type": "set_status", "status": "away"}))

            alice_roster = await recv_until_presence(ws_alice)
            assert status_of(alice_roster, alice) == "away", "alice should see her own status change"
            await recv_json(ws_alice)  # global_presence (also broadcast on every status change)

            bob_roster = await recv_until_presence(ws_bob)
            assert status_of(bob_roster, alice) == "away", "bob should see alice's status change too"
            await recv_json(ws_bob)  # global_presence (also broadcast on every status change)

            # invalid status values should be ignored, not crash or corrupt state
            await ws_alice.send(json.dumps({"type": "set_status", "status": "not-a-real-status"}))
            await ws_alice.send(json.dumps({"type": "chat", "content": "still alive"}))
            confirm = await recv_json(ws_alice)  # echo of the chat message proves the connection is still fine
            assert confirm == {"type": "chat", "username": alice, "content": "still alive"}, confirm
