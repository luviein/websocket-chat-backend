import asyncio
import json
import uuid

import pytest
import websockets

from test_helpers import get_token, ws_url


async def recv_json(ws, timeout=20.0):
    # Bounded so a server-side bug (e.g. a hanging external API call) fails
    # this one test loudly instead of hanging the whole suite indefinitely -
    # learned the hard way. 20s gives gemini.GEMINI_TIMEOUT_SECONDS (15s)
    # room to fire first and produce a real response.
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def recv_until(ws, predicate, limit=10):
    """Skip past messages until one matches predicate, or give up after limit."""
    for _ in range(limit):
        msg = await recv_json(ws)
        if predicate(msg):
            return msg
    raise AssertionError(f"no matching message received within {limit} messages")


@pytest.mark.asyncio
async def test_gemini_not_configured_returns_friendly_message():
    """Unit test, no server needed - imports gemini directly. The bare
    pytest process never has GEMINI_API_KEY in its environment (only the
    server subprocess loads .env, and only conftest.py's fake key gets
    passed to that subprocess), so this exercises the "unconfigured" path."""
    import gemini

    assert gemini.GEMINI_ENABLED is False
    reply = await gemini.ask_gemini("hello")
    assert reply == "Gemini isn't configured on this server."


@pytest.mark.asyncio
async def test_invite_gemini_adds_to_presence(server):
    room = f"gemini-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")

    async with websockets.connect(ws_url(server, room, alice_token)) as ws:
        await recv_json(ws)  # system: alice joined
        await recv_json(ws)  # presence: just alice

        await ws.send(json.dumps({"type": "chat", "content": "/invite-gemini"}))

        joined_msg = await recv_json(ws)
        assert joined_msg == {"type": "system", "content": "Gemini has joined the room"}, joined_msg

        presence_msg = await recv_json(ws)
        assert presence_msg["type"] == "presence"
        usernames = {u["username"] for u in presence_msg["users"]}
        assert "Gemini" in usernames, presence_msg


@pytest.mark.asyncio
async def test_invite_command_not_echoed_as_chat_message(server):
    room = f"gemini-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")

    async with websockets.connect(ws_url(server, room, alice_token)) as ws:
        await recv_json(ws)  # system: joined
        await recv_json(ws)  # presence

        await ws.send(json.dumps({"type": "chat", "content": "/invite-gemini"}))
        await recv_json(ws)  # system: gemini joined
        await recv_json(ws)  # presence update

        # send a real chat message right after - it should be the very next
        # thing received, proving the invite command itself was never
        # broadcast as a chat message
        await ws.send(json.dumps({"type": "chat", "content": "hello everyone"}))
        next_msg = await recv_json(ws)
        assert next_msg == {"type": "chat", "username": alice, "content": "hello everyone"}, next_msg


@pytest.mark.asyncio
async def test_invite_gemini_twice_says_already_in_room(server):
    room = f"gemini-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")

    async with websockets.connect(ws_url(server, room, alice_token)) as ws:
        await recv_json(ws)  # system: joined
        await recv_json(ws)  # presence

        await ws.send(json.dumps({"type": "chat", "content": "/invite-gemini"}))
        await recv_json(ws)  # system: joined
        await recv_json(ws)  # presence

        await ws.send(json.dumps({"type": "chat", "content": "/invite-gemini"}))
        second_response = await recv_json(ws)
        assert second_response == {"type": "system", "content": "Gemini is already in this room."}


@pytest.mark.asyncio
async def test_mention_gemini_triggers_a_response(server):
    room = f"gemini-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")

    async with websockets.connect(ws_url(server, room, alice_token)) as ws:
        await recv_json(ws)  # joined
        await recv_json(ws)  # presence
        await ws.send(json.dumps({"type": "chat", "content": "/invite-gemini"}))
        await recv_json(ws)  # gemini joined system msg
        await recv_json(ws)  # presence update

        await ws.send(json.dumps({"type": "chat", "content": "@gemini what is this project?"}))

        own_echo = await recv_json(ws)
        assert own_echo == {
            "type": "chat",
            "username": alice,
            "content": "@gemini what is this project?",
        }

        # fake API key guarantees the call can't succeed, but whether it
        # fails fast (auth error) or hangs until gemini.py's own timeout
        # fires depends on the SDK's internal retry behavior - either way,
        # this proves the whole trigger path works end-to-end
        gemini_reply = await recv_json(ws)
        assert gemini_reply["type"] == "chat"
        assert gemini_reply["username"] == "Gemini"
        assert gemini_reply["content"] in (
            "Sorry, I ran into an error trying to respond.",
            "Sorry, Gemini took too long to respond.",
        ), gemini_reply


@pytest.mark.asyncio
async def test_non_mention_message_does_not_trigger_gemini(server):
    room = f"gemini-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")
    bob, bob_token = get_token(server, "bob")

    async with websockets.connect(ws_url(server, room, alice_token)) as ws_alice:
        await recv_json(ws_alice)  # joined
        await recv_json(ws_alice)  # presence
        await ws_alice.send(json.dumps({"type": "chat", "content": "/invite-gemini"}))
        await recv_json(ws_alice)  # gemini joined
        await recv_json(ws_alice)  # presence update

        async with websockets.connect(ws_url(server, room, bob_token)) as ws_bob:
            await recv_until(ws_alice, lambda m: m.get("type") == "system")  # bob joined
            await recv_until(ws_alice, lambda m: m.get("type") == "presence")  # presence update
            # bob's own connection also receives his own join+presence
            # broadcast (everyone in the room gets it, including himself) -
            # drain those before checking what comes next on his socket
            await recv_json(ws_bob)  # bob joined (his own copy)
            await recv_json(ws_bob)  # presence update (his own copy)

            await ws_alice.send(json.dumps({"type": "chat", "content": "just a normal message"}))

            # bob should receive exactly the normal chat message next - not
            # a Gemini reply, proving non-mentions don't trigger the bot
            next_msg = await recv_json(ws_bob)
            assert next_msg == {
                "type": "chat",
                "username": alice,
                "content": "just a normal message",
            }

            # confirm nothing else arrives quickly afterward (i.e. no Gemini
            # reply is coming) by racing a short timeout
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws_bob.recv(), timeout=1.0)


@pytest.mark.asyncio
async def test_mention_without_invite_is_ignored(server):
    room = f"gemini-test-{uuid.uuid4().hex[:8]}"
    alice, alice_token = get_token(server, "alice")

    async with websockets.connect(ws_url(server, room, alice_token)) as ws:
        await recv_json(ws)  # joined
        await recv_json(ws)  # presence

        # Gemini was never invited to this room - @gemini should be treated
        # as a completely normal chat message
        await ws.send(json.dumps({"type": "chat", "content": "@gemini hello?"}))
        echo = await recv_json(ws)
        assert echo == {"type": "chat", "username": alice, "content": "@gemini hello?"}

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.recv(), timeout=1.0)
