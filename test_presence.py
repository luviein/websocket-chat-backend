import json
import uuid

import pytest
import websockets

from test_helpers import get_token, ws_url


async def recv_json(ws):
    return json.loads(await ws.recv())


async def recv_until_presence(ws):
    """Skip past any system/chat messages and return the next presence snapshot."""
    while True:
        msg = await recv_json(ws)
        if msg.get("type") == "presence":
            return msg["users"]


@pytest.mark.asyncio
async def test_presence_snapshot_updates_through_join_and_leave(server):
    room = f"presence-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")
    carol, carol_token = get_token(server, "carol")

    async with websockets.connect(ws_url(server, room, alice_token)) as ws_alice:
        # alice joins alone - presence snapshot should list just herself
        await recv_json(ws_alice)  # system: "alice joined the room"
        alice_roster = await recv_until_presence(ws_alice)
        assert [u["username"] for u in alice_roster] == [alice], alice_roster
        assert alice_roster[0]["status"] == "available", "default status should be 'available'"

        async with websockets.connect(ws_url(server, room, bob_token)) as ws_bob:
            # bob joins - both should now see alice AND bob in the roster
            bob_roster = await recv_until_presence(ws_bob)
            assert {u["username"] for u in bob_roster} == {alice, bob}, bob_roster

            alice_roster = await recv_until_presence(ws_alice)
            assert {u["username"] for u in alice_roster} == {alice, bob}, (
                "existing user should receive an updated presence snapshot when someone joins"
            )

            async with websockets.connect(ws_url(server, room, carol_token)) as ws_carol:
                carol_roster = await recv_until_presence(ws_carol)
                assert {u["username"] for u in carol_roster} == {alice, bob, carol}, carol_roster

                # drain the "carol joined" presence update on the other connections
                # now, so it isn't mistaken for the "carol left" update below
                await recv_until_presence(ws_alice)
                await recv_until_presence(ws_bob)

            # carol left - alice and bob should get an updated snapshot without her
            alice_roster = await recv_until_presence(ws_alice)
            assert {u["username"] for u in alice_roster} == {alice, bob}, (
                "presence snapshot should update after a user disconnects"
            )
