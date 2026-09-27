"""Prompt audit 2026-09-27: stale or self-contradicting prompt text.

- The manage section offered ten operations, but the only consumer
  (`_cb_intent_create`) reads `params.name`/`description` and ignores
  `operation`: a `rename_*` answer put the name in `old_name`/`new_name` and the
  bot fell back to the user's raw text.
- `priority` had two opposite rules in the same prompt ("medium for task/idea"
  vs "null for non-actionable types").
- The image Vision prompt used markdown bold while forbidding markdown.
- Token usage was never logged, so no prompt change could be measured.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from adso import llm_client
from adso.llm_client import _VISION_PROMPT_IMAGE, build_system_prompt
from adso.llm_schema import VALID_OPERATIONS


class TestManageSection:
    def test_only_create_operations_are_offered(self) -> None:
        prompt = build_system_prompt([], [])
        offered = {op for op in VALID_OPERATIONS if op in prompt}
        assert offered == {"create_project", "create_area"}

    def test_params_are_name_and_description(self) -> None:
        prompt = build_system_prompt([], [])
        assert "old_name" not in prompt
        assert "project_name" not in prompt


class TestPriorityRule:
    def test_no_contradicting_default_for_ideas(self) -> None:
        prompt = build_system_prompt([], [])
        assert "medium for task/idea" not in prompt
        assert "null for reference and idea" in prompt


class TestVisionPrompt:
    def test_image_prompt_does_not_use_the_markdown_it_forbids(self) -> None:
        assert "No uses formato markdown" in _VISION_PROMPT_IMAGE
        assert "**" not in _VISION_PROMPT_IMAGE


class TestTokenAccounting:
    async def test_classify_call_logs_token_usage(self, caplog) -> None:
        response = MagicMock()
        response.text = '{"ok": true}'
        response.usage_metadata.prompt_token_count = 1234
        response.usage_metadata.candidates_token_count = 321
        client = MagicMock()
        client.models.generate_content = MagicMock(return_value=response)

        with patch.object(llm_client, "_get_genai_client", return_value=client), \
                caplog.at_level(logging.INFO, logger=llm_client.logger.name):
            await llm_client._call_gemini("system", "<input>hola</input>")

        assert any("1234" in r.getMessage() and "321" in r.getMessage()
                   for r in caplog.records)

    async def test_missing_usage_metadata_does_not_break_the_call(self) -> None:
        response = MagicMock()
        response.text = '{"ok": true}'
        response.usage_metadata = None
        client = MagicMock()
        client.models.generate_content = MagicMock(return_value=response)

        with patch.object(llm_client, "_get_genai_client", return_value=client):
            assert await llm_client._call_gemini("system", "x") == '{"ok": true}'
