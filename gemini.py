import asyncio
import logging
import os

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

BOT_NAME = "Gemini"
INVITE_COMMAND = "/invite-gemini"
MENTION_PREFIX = "@gemini"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# "latest" alias, not a pinned version - a pinned model (e.g. gemini-2.0-flash,
# this project's original default) can and did get deprecated/removed by
# Google mid-project, breaking every call with a 404. The alias always
# points at Google's current recommended fast/cheap model instead.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
# The SDK doesn't fail fast on its own (e.g. an invalid key can hang far
# longer than expected, apparently retrying internally) - a hard timeout
# here matters for more than just tests: a slow/unresponsive Gemini call
# would otherwise block this WebSocket connection's message loop indefinitely.
# 30s, not something tighter: real-world testing showed longer/more
# open-ended prompts can legitimately take longer than 15s to generate,
# not just failures - too tight a timeout just trades "hangs" for
# "annoyingly frequent false timeouts" on normal use.
GEMINI_TIMEOUT_SECONDS = 30

# Test-only override so the test suite never depends on Google's real
# servers even for the "failure" path - see tests/conftest.py. Pointing at
# an address nothing listens on fails instantly and locally, instead of
# depending on how fast/slow Google's servers happen to reject a fake key
# on any given run (observed to vary a lot in practice).
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL")

GEMINI_ENABLED = bool(GEMINI_API_KEY)

if GEMINI_ENABLED:
    http_options = types.HttpOptions(
        base_url=GEMINI_BASE_URL,
        # we already enforce our own timeout+worker-thread handling above;
        # the SDK's own retries just add unpredictable extra delay on top
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    gemini_client = genai.Client(api_key=GEMINI_API_KEY, http_options=http_options)
else:
    gemini_client = None


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
