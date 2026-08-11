import asyncio
import json
import uuid

import pytest
import websockets

from test_helpers import get_token, ws_url


async def recv_json(ws, timeout=10.0):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def recv_until(ws, predicate, limit=10):
    """Skip past messages until one matches predicate, or give up after limit."""
    for _ in range(limit):
        msg = await recv_json(ws)
        if predicate(msg):
            return msg
    raise AssertionError(f"no matching message received within {limit} messages")


def dm_room(a, b):
    return "dm:" + ":".join(sorted([a, b]))


@pytest.mark.asyncio
async def test_dm_delivers_only_between_the_two_participants(server):
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")
    room = dm_room(alice, bob)

    async with websockets.connect(ws_url(server, room, alice_token)) as ws_alice:
        await recv_json(ws_alice)  # self
        await recv_json(ws_alice)  # block_list
        await recv_json(ws_alice)  # system: alice joined
        await recv_json(ws_alice)  # presence: alice only
        await recv_json(ws_alice)  # global_presence (also broadcast at the end of every connect)

        async with websockets.connect(ws_url(server, room, bob_token)) as ws_bob:
            await recv_json(ws_bob)  # self
            await recv_json(ws_bob)  # block_list
            await recv_json(ws_bob)  # system: bob joined (own copy)
            await recv_json(ws_bob)  # presence (own copy)
            await recv_json(ws_bob)  # global_presence (own copy)
            await recv_json(ws_alice)  # system: bob joined
            await recv_json(ws_alice)  # presence update
            await recv_json(ws_alice)  # global_presence update (from bob's connect)

            await ws_alice.send(json.dumps({"type": "chat", "content": "hey bob"}))

            own_echo = await recv_json(ws_alice)
            assert own_echo == {"type": "chat", "username": alice, "content": "hey bob"}

            bob_receives = await recv_json(ws_bob)
            assert bob_receives == {"type": "chat", "username": alice, "content": "hey bob"}


@pytest.mark.asyncio
async def test_dm_room_rejects_a_third_party(server):
    alice, alice_token = get_token(server, "alice")
    bob, _bob_token = get_token(server, "bob")
    _charlie, charlie_token = get_token(server, "charlie")
    room = dm_room(alice, bob)

    try:
        async with websockets.connect(ws_url(server, room, charlie_token)):
            pass
        assert False, "a third party should not be able to join someone else's DM"
    except (websockets.exceptions.InvalidStatus, websockets.exceptions.ConnectionClosedError):
        pass  # rejected at handshake or immediately after - both are correct


@pytest.mark.asyncio
async def test_block_prevents_starting_a_new_dm(server):
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")

    common_room = f"lobby-{uuid.uuid4().hex[:8]}"
    async with websockets.connect(ws_url(server, common_room, alice_token)) as ws_alice:
        await recv_json(ws_alice)  # self
        await recv_json(ws_alice)  # block_list
        await recv_json(ws_alice)  # system: joined
        await recv_json(ws_alice)  # presence
        await recv_json(ws_alice)  # global_presence (also broadcast at the end of every connect)

        await ws_alice.send(json.dumps({"type": "block", "username": bob}))
        block_list_msg = await recv_json(ws_alice)
        assert block_list_msg == {"type": "block_list", "blocked": [bob]}

        # rejected pre-accept (no message, matching the "not a participant"
        # case) so the client can tell "never connected" apart from "briefly
        # connected then got disconnected" just from whether the handshake
        # itself succeeded - lets it show a popup instead of navigating into
        # an empty DM view first
        room = dm_room(alice, bob)
        try:
            async with websockets.connect(ws_url(server, room, bob_token)):
                pass
            assert False, "bob should not be able to open a DM with alice, who blocked him"
        except (websockets.exceptions.InvalidStatus, websockets.exceptions.ConnectionClosedError):
            pass


@pytest.mark.asyncio
async def test_block_disconnects_an_active_dm_without_kicking_the_blocker(server):
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")
    room = dm_room(alice, bob)

    async with websockets.connect(ws_url(server, room, alice_token)) as ws_alice:
        await recv_json(ws_alice)  # self
        await recv_json(ws_alice)  # block_list
        await recv_json(ws_alice)  # system: alice joined
        await recv_json(ws_alice)  # presence
        await recv_json(ws_alice)  # global_presence (also broadcast at the end of every connect)

        async with websockets.connect(ws_url(server, room, bob_token)) as ws_bob:
            await recv_json(ws_bob)  # self
            await recv_json(ws_bob)  # block_list
            await recv_json(ws_bob)  # system: bob joined (own copy)
            await recv_json(ws_bob)  # presence (own copy)
            await recv_json(ws_bob)  # global_presence (own copy)
            await recv_json(ws_alice)  # system: bob joined
            await recv_json(ws_alice)  # presence update
            await recv_json(ws_alice)  # global_presence update (from bob's connect)

            await ws_alice.send(json.dumps({"type": "block", "username": bob}))

            # alice's still-open connection also sees "bob left the room" +
            # a presence update as a side effect of bob's forced disconnect,
            # racing against her own block_list reply and her own reference
            # to the two messages sent above from her own task - recv_until
            # skips past whichever arrives first
            block_list_msg = await recv_until(ws_alice, lambda m: m.get("type") == "block_list")
            assert block_list_msg == {"type": "block_list", "blocked": [bob]}

            bob_notice = await recv_json(ws_bob)
            assert bob_notice == {"type": "system", "content": "You can't message this user."}
            with pytest.raises(websockets.exceptions.ConnectionClosedError):
                await recv_json(ws_bob)

            # alice's own connection should be unaffected - she can still send
            await ws_alice.send(json.dumps({"type": "chat", "content": "still here"}))
            still_here_echo = await recv_until(
                ws_alice, lambda m: m == {"type": "chat", "username": alice, "content": "still here"}
            )
            assert still_here_echo == {"type": "chat", "username": alice, "content": "still here"}


@pytest.mark.asyncio
async def test_unblock_allows_a_new_dm_again(server):
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")

    common_room = f"lobby-{uuid.uuid4().hex[:8]}"
    async with websockets.connect(ws_url(server, common_room, alice_token)) as ws_alice:
        await recv_json(ws_alice)  # self
        await recv_json(ws_alice)  # block_list
        await recv_json(ws_alice)  # system: joined
        await recv_json(ws_alice)  # presence
        await recv_json(ws_alice)  # global_presence (also broadcast at the end of every connect)

        await ws_alice.send(json.dumps({"type": "block", "username": bob}))
        await recv_json(ws_alice)  # block_list: [bob]

        await ws_alice.send(json.dumps({"type": "unblock", "username": bob}))
        unblocked_msg = await recv_json(ws_alice)
        assert unblocked_msg == {"type": "block_list", "blocked": []}

        room = dm_room(alice, bob)
        async with websockets.connect(ws_url(server, room, bob_token)) as ws_bob:
            # no rejection this time - normal DM setup messages instead
            self_msg = await recv_json(ws_bob)
            assert self_msg == {"type": "self", "username": bob}


@pytest.mark.asyncio
async def test_block_hides_sender_messages_in_shared_room_one_way_only(server):
    """Blocking only restricts DMs - in a shared group room, the blocker
    silently stops receiving the blocked user's messages, but the blocked
    user is otherwise unrestricted (can still see/post normally, and their
    messages still reach everyone who HASN'T blocked them)."""
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")
    carol, carol_token = get_token(server, "carol")
    room = f"lobby-{uuid.uuid4().hex[:8]}"

    async with websockets.connect(ws_url(server, room, alice_token)) as ws_alice:
        await recv_json(ws_alice)  # self
        await recv_json(ws_alice)  # block_list
        await recv_json(ws_alice)  # system: joined
        await recv_json(ws_alice)  # presence
        await recv_json(ws_alice)  # global_presence (also broadcast at the end of every connect)

        await ws_alice.send(json.dumps({"type": "block", "username": bob}))
        await recv_json(ws_alice)  # block_list: [bob]

        async with websockets.connect(ws_url(server, room, bob_token)) as ws_bob:
            await recv_json(ws_bob)  # self
            await recv_json(ws_bob)  # block_list
            await recv_json(ws_bob)  # system: bob joined (own copy)
            await recv_json(ws_bob)  # presence (own copy)
            await recv_json(ws_bob)  # global_presence (own copy)
            await recv_until(ws_alice, lambda m: m.get("type") == "presence")  # alice sees bob join

            async with websockets.connect(ws_url(server, room, carol_token)) as ws_carol:
                await recv_json(ws_carol)  # self
                await recv_json(ws_carol)  # block_list
                await recv_json(ws_carol)  # system: carol joined (own copy)
                await recv_json(ws_carol)  # presence (own copy)
                await recv_json(ws_carol)  # global_presence (own copy)
                await recv_until(ws_alice, lambda m: m.get("type") == "presence")  # alice sees carol join
                await recv_json(ws_alice)  # global_presence (from carol's connect)
                await recv_until(ws_bob, lambda m: m.get("type") == "presence")  # bob sees carol join
                await recv_json(ws_bob)  # global_presence (from carol's connect)

                # bob (blocked by alice) sends a message
                await ws_bob.send(json.dumps({"type": "chat", "content": "hello from bob"}))

                # bob still sees his own message - he isn't restricted at all
                bob_echo = await recv_json(ws_bob)
                assert bob_echo == {"type": "chat", "username": bob, "content": "hello from bob"}

                # carol (hasn't blocked bob) still receives it normally
                carol_receives = await recv_json(ws_carol)
                assert carol_receives == {"type": "chat", "username": bob, "content": "hello from bob"}

                # alice (blocked bob) never receives it
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws_alice.recv(), timeout=1.5)

                # reverse direction is unaffected - alice's messages still
                # reach bob normally, since bob hasn't blocked alice
                await ws_alice.send(json.dumps({"type": "chat", "content": "hello from alice"}))
                alice_echo = await recv_json(ws_alice)
                assert alice_echo == {"type": "chat", "username": alice, "content": "hello from alice"}

                bob_receives = await recv_json(ws_bob)
                assert bob_receives == {"type": "chat", "username": alice, "content": "hello from alice"}


@pytest.mark.asyncio
async def test_dm_notifies_recipient_who_is_elsewhere(server):
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")
    group_room = f"lobby-{uuid.uuid4().hex[:8]}"
    room = dm_room(alice, bob)

    # bob is hanging out in a normal group room, NOT the DM
    async with websockets.connect(ws_url(server, group_room, bob_token)) as ws_bob:
        await recv_json(ws_bob)  # self
        await recv_json(ws_bob)  # block_list
        await recv_json(ws_bob)  # system: joined
        await recv_json(ws_bob)  # presence
        await recv_json(ws_bob)  # global_presence (also broadcast at the end of every connect)

        # alice DMs bob without bob ever opening that DM
        async with websockets.connect(ws_url(server, room, alice_token)) as ws_alice:
            await recv_json(ws_alice)  # self
            await recv_json(ws_alice)  # block_list
            await recv_json(ws_alice)  # system: joined
            await recv_json(ws_alice)  # presence

            await ws_alice.send(json.dumps({"type": "chat", "content": "hey bob"}))
            await recv_json(ws_alice)  # alice's own echo

            # bob gets a notification on his OTHER (group room) connection,
            # since he isn't in the DM room to see the message live - skip
            # past the global_presence update he also gets from alice's
            # connection changing the global roster
            notice = await recv_until(ws_bob, lambda m: m.get("type") == "dm_notification")
            assert notice == {"type": "dm_notification", "from": alice}


@pytest.mark.asyncio
async def test_dm_does_not_notify_recipient_already_viewing_it(server):
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")
    room = dm_room(alice, bob)

    async with websockets.connect(ws_url(server, room, bob_token)) as ws_bob:
        await recv_until(ws_bob, lambda m: m.get("type") == "presence")  # bob's own initial setup

        async with websockets.connect(ws_url(server, room, alice_token)) as ws_alice:
            await recv_until(ws_alice, lambda m: m.get("type") == "presence")  # alice's own initial setup
            await recv_until(ws_bob, lambda m: m.get("type") == "presence")  # bob sees alice join

            await ws_alice.send(json.dumps({"type": "chat", "content": "hey bob"}))
            await recv_until(ws_alice, lambda m: m.get("type") == "chat")  # alice's own echo

            # bob receives the real chat message (he's right there), not a
            # separate notification - the message itself is his notice.
            # recv_until (not a raw recv) because a *global* broadcast can
            # now reach every connection on the server regardless of room -
            # a previous test's connections still finishing their async
            # disconnect cleanup can leak a stray global_presence in here
            bob_msg = await recv_until(ws_bob, lambda m: m.get("type") == "chat")
            assert bob_msg == {"type": "chat", "username": alice, "content": "hey bob"}
