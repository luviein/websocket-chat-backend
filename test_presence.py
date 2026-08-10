import asyncio
import json
import uuid
import websockets

from test_helpers import get_token


async def recv_json(ws):
    return json.loads(await ws.recv())


async def recv_until_presence(ws):
    """Skip past any system/chat messages and return the next presence snapshot."""
    while True:
        msg = await recv_json(ws)
        if msg.get("type") == "presence":
            return msg["users"]


async def main():
    room = f"presence-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token("alice")
    bob, bob_token = get_token("bob")
    carol, carol_token = get_token("carol")

    async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={alice_token}") as ws_alice:
        # alice joins alone - presence snapshot should list just herself
        await recv_json(ws_alice)  # system: "alice joined the room"
        alice_roster = await recv_until_presence(ws_alice)
        assert [u["username"] for u in alice_roster] == [alice], alice_roster
        assert alice_roster[0]["status"] == "available", "default status should be 'available'"
        print("PASS: solo joiner sees themselves in the presence snapshot")

        async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={bob_token}") as ws_bob:
            # bob joins - both should now see alice AND bob in the roster
            bob_roster = await recv_until_presence(ws_bob)
            assert {u["username"] for u in bob_roster} == {alice, bob}, bob_roster
            print("PASS: second joiner's own presence snapshot includes both users")

            alice_roster = await recv_until_presence(ws_alice)
            assert {u["username"] for u in alice_roster} == {alice, bob}, alice_roster
            print("PASS: existing user receives an updated presence snapshot when someone joins")

            async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={carol_token}") as ws_carol:
                carol_roster = await recv_until_presence(ws_carol)
                assert {u["username"] for u in carol_roster} == {alice, bob, carol}, carol_roster
                print("PASS: third joiner sees all three users")

                # drain the "carol joined" presence update on the other connections
                # now, so it isn't mistaken for the "carol left" update below
                await recv_until_presence(ws_alice)
                await recv_until_presence(ws_bob)

            # carol left - alice and bob should get an updated snapshot without her
            alice_roster = await recv_until_presence(ws_alice)
            assert {u["username"] for u in alice_roster} == {alice, bob}, alice_roster
            print("PASS: presence snapshot updates after a user disconnects")


asyncio.run(main())
