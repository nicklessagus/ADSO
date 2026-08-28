"""Per-call timeout for the classification call to Gemini.

Measured in production (RPi4, Aug 2026): `classify` has a floor of 1.5s and a
p50 of ~2.2s, and no legitimate input goes past ~3s. But ~20% of the calls stall
server-side — same input, same token counts, a single HTTP request that returns
`200 OK` after 5.7 / 6.7 / 10.1 / 19.6 / 34.4 / 35.0 seconds. It is not an SDK
retry and it is not a rate limit; there is nothing to fix on our side except
refusing to wait for it.

Without a timeout (`HttpOptions.timeout` defaults to `None`) the bot eats a 35s
stall whole. With one, the stall aborts and the retry loop that already exists
takes over, which in practice resolves faster than the stall would have.

Two things these tests pin down that are easy to get wrong:

- **The timeout is expressed in milliseconds.** `HttpOptions.timeout` is an int
  in ms; passing `8` instead of `8000` would abort every single call after 8ms
  and send every capture to degraded mode.
- **It is set per call, not on the client.** `_get_genai_client()` is shared with
  Gemini Vision, whose calls legitimately take much longer (rasterized PDF
  pages). A timeout on the client would break OCR of scanned documents.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adso import llm_client


def _response(text: str = '{"ok": true}') -> MagicMock:
    """Build a minimal Gemini response object."""
    response = MagicMock()
    response.text = text
    return response


def _captured_config(generate_content: MagicMock):
    """Return the `config` kwarg of the single generate_content call."""
    assert generate_content.call_count == 1
    return generate_content.call_args.kwargs.get("config")


class TestClassifyTimeout:
    async def test_the_classification_call_declares_a_timeout(self) -> None:
        """A stalled request must be abandoned, not waited out."""
        client = MagicMock()
        client.models.generate_content = MagicMock(return_value=_response())

        with patch.object(llm_client, "_get_genai_client", return_value=client):
            await llm_client._call_gemini("system", "<input>hola</input>")

        config = _captured_config(client.models.generate_content)
        assert config is not None and config.http_options is not None, (
            "the call carries no http_options, so it inherits the SDK default "
            "of no timeout and a 35s stall is waited out whole"
        )
        assert config.http_options.timeout is not None, (
            "http_options is present but declares no timeout"
        )

    async def test_the_timeout_is_in_milliseconds_and_clears_a_slow_but_real_call(
        self,
    ) -> None:
        """`HttpOptions.timeout` is milliseconds — and 3s is a legitimate call.

        The lower bound catches the seconds/milliseconds mix-up (a value of `8`
        would abort after 8ms and degrade every capture); the upper bound keeps
        the timeout from being so generous that it never fires on the stalls it
        exists for.
        """
        client = MagicMock()
        client.models.generate_content = MagicMock(return_value=_response())

        with patch.object(llm_client, "_get_genai_client", return_value=client):
            await llm_client._call_gemini("system", "<input>hola</input>")

        timeout = _captured_config(client.models.generate_content).http_options.timeout
        assert 10_000 <= timeout <= 30_000, (
            f"timeout is {timeout}: expected milliseconds, at or above the 10s "
            "floor the API enforces (see the test below) and below the stalls "
            "the timeout targets"
        )

    async def test_the_timeout_clears_the_floor_the_api_enforces(self) -> None:
        """Gemini rejects a deadline under 10s outright — with a 400, instantly.

        Deployed 2026-08-27 with `CLASSIFY_TIMEOUT_MS = 8_000`, every single
        classification came back as::

            400 INVALID_ARGUMENT: Manually set deadline 8s is too short.
                                  Minimum allowed deadline is 10s.

        The call never reached the model: it burnt the three retries in ~6s and
        sent every capture to degraded mode ("no se pudo clasificar bien"). No
        mocked test can see this — the floor lives on the server — so the
        constant is pinned here against the error message that reported it.
        """
        assert llm_client.CLASSIFY_TIMEOUT_MS >= 10_000, (
            f"CLASSIFY_TIMEOUT_MS is {llm_client.CLASSIFY_TIMEOUT_MS}ms: Gemini "
            "rejects any deadline under 10s with a 400, so every capture "
            "degrades without ever calling the model"
        )

    async def test_vision_is_not_capped_by_the_classification_timeout(self) -> None:
        """Counter-case: OCR of a rasterized PDF legitimately takes far longer.

        The shared client must stay free of a global timeout, so a Vision call
        that passes no config of its own is not capped by it.
        """
        client = MagicMock()
        client.models.generate_content = MagicMock(return_value=_response("texto"))

        with patch.object(llm_client, "_get_genai_client", return_value=client):
            await llm_client.describe_image_with_vision([(b"\x89PNG", "image/png")])

        config = _captured_config(client.models.generate_content)
        timeout = getattr(getattr(config, "http_options", None), "timeout", None)
        assert timeout is None, (
            f"the Vision call is capped at {timeout}ms: the timeout leaked onto "
            "the shared client and will abort OCR of scanned PDFs"
        )

    async def test_a_timed_out_call_keeps_the_full_retry_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A timeout is a network error, not a rate limit and not a bad answer."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        gemini = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))

        with patch.object(llm_client, "_call_gemini", gemini), patch.object(
            llm_client.asyncio, "sleep", AsyncMock()
        ):
            result = await llm_client.classify(
                content="apuntes de la reunión",
                media_type="text",
                existing_projects=[],
                existing_areas=[],
                existing_tags=[],
            )

        assert gemini.call_count == llm_client.MAX_RETRIES, (
            "a timeout must exhaust the generic retry budget: the stall is "
            "intermittent, so a retry usually succeeds"
        )
        assert result["mode"] == "degraded"
        assert "apuntes de la reunión" in result["payload"]["body"]
