import asyncio
import logging
import os

from google import genai

logger = logging.getLogger(__name__)

BOT_NAME = "Gemini"
INVITE_COMMAND = "/invite-gemini"
MENTION_PREFIX = "@gemini"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
# The SDK doesn't fail fast on its own (e.g. an invalid key can hang far
# longer than expected, apparently retrying internally) - a hard timeout
# here matters for more than just tests: a slow/unresponsive Gemini call
# would otherwise block this WebSocket connection's message loop indefinitely.
GEMINI_TIMEOUT_SECONDS = 15

GEMINI_ENABLED = bool(GEMINI_API_KEY)

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_ENABLED else None


def _generate_sync(prompt: str):
    """Runs on a worker thread via asyncio.to_thread, never directly on the
    event loop. Testing showed client.aio's "async" call still performs
    blocking I/O internally (at least for certain failure modes like an
    invalid key) - awaiting it directly froze the ENTIRE server's event
    loop, not just this one connection, until the call finally gave up on
    its own. asyncio.wait_for alone can't fix that: cancellation only takes
    effect at a cooperative yield point, and a genuinely blocking call
    never yields one. Running it in a thread keeps the event loop free for
    every other connection regardless of how the SDK behaves internally.
    """
    return gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)


async def ask_gemini(prompt: str) -> str:
    """Returns Gemini's reply text, or a friendly message if unconfigured/failed/slow.

    Never surfaces raw exception details to chat - those get logged
    server-side instead, since they could leak internal information.
    """
    if not GEMINI_ENABLED or gemini_client is None:
        return "Gemini isn't configured on this server."

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_generate_sync, prompt),
            timeout=GEMINI_TIMEOUT_SECONDS,
        )
        return response.text or "(Gemini returned an empty response)"
    except TimeoutError:
        logger.warning("Gemini API call timed out after %ss", GEMINI_TIMEOUT_SECONDS)
        return "Sorry, Gemini took too long to respond."
    except Exception:
        logger.exception("Gemini API call failed")
        return "Sorry, I ran into an error trying to respond."
