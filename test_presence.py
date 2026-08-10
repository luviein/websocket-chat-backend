import asyncio
import uuid
import websockets

from test_helpers import get_token


async def main():
    room = f"presence-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token("alice")
    bob, bob_token = get_token("bob")
    carol, carol_token = get_token("carol")

    # alice joins an empty room - should NOT see an "online now" message,
    # since no one else is there yet
    async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={alice_token}") as ws_alice:
        first_msg = await ws_alice.recv()
        assert first_msg == f"{alice} joined the room", (
            f"empty room should skip the online-now header, got: {first_msg!r}"
        )
        print("PASS: first joiner sees no 'online now' header")

        # bob joins next - should see alice listed as already online
        async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={bob_token}") as ws_bob:
            bob_first_msgs = [await ws_bob.recv(), await ws_bob.recv()]
            assert f"--- Online now: {alice} ---" in bob_first_msgs, bob_first_msgs
            print("PASS: second joiner sees the first user listed as online")

            # alice should also see bob's join notice live
            alice_next = await ws_alice.recv()
            assert alice_next == f"{bob} joined the room", alice_next

            # carol joins - should see BOTH alice and bob listed
            async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={carol_token}") as ws_carol:
                carol_first = await ws_carol.recv()
                assert carol_first.startswith("--- Online now:"), carol_first
                assert alice in carol_first and bob in carol_first, carol_first
                print("PASS: third joiner sees both prior users listed as online")


asyncio.run(main())
