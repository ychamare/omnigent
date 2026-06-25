"""Tests for the server-side intelligent model routing module.

Covers tier inference, judge call parsing, model clamping, and the
public ``route_turn`` entry point.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from omnigent.server.smart_routing import (
    _VALID_TIERS,
    _build_rubric,
    _call_judge,
    infer_tiers,
    route_turn,
)

_DB_ENV = {
    "DATABRICKS_HOST": "https://test.cloud.databricks.com",
    "DATABRICKS_TOKEN": "tok",
}


# ── infer_tiers ─────────────────────────────────────────────────────


def test_infer_tiers_claude_sdk() -> None:
    """claude-sdk maps to the claude tier template."""
    tiers = infer_tiers("claude-sdk")
    assert tiers is not None
    assert "cheap" in tiers
    assert "medium" in tiers
    assert "expensive" in tiers
    assert any("haiku" in m for m in tiers["cheap"])
    assert any("opus" in m for m in tiers["expensive"])


def test_infer_tiers_claude_native() -> None:
    tiers = infer_tiers("claude-native")
    assert tiers is not None


def test_infer_tiers_unknown_harness() -> None:
    """Unknown harnesses return None (not routable)."""
    assert infer_tiers("openai-agents") is None
    assert infer_tiers("codex") is None
    assert infer_tiers(None) is None


# ── _build_rubric ───────────────────────────────────────────────────


def test_build_rubric_includes_all_tiers() -> None:
    tiers = {
        "cheap": ["m-cheap"],
        "medium": ["m-mid"],
        "expensive": ["m-exp"],
    }
    rubric = _build_rubric(tiers)
    assert "m-cheap" in rubric
    assert "m-mid" in rubric
    assert "m-exp" in rubric
    assert "strict JSON" in rubric


# ── _call_judge ─────────────────────────────────────────────────────


def _mock_judge_response(
    verdict: dict[str, Any],
) -> httpx.Response:
    """Build a fake httpx.Response with an OpenAI-compatible body."""
    body = {
        "choices": [{"message": {"content": json.dumps(verdict)}}],
    }
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_call_judge_returns_parsed_verdict() -> None:
    verdict = {
        "tier": "expensive",
        "model": "databricks-claude-opus-4-8",
        "rationale": "hard",
    }
    tiers = infer_tiers("claude-sdk")
    assert tiers is not None
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_judge_response(verdict)
    with (
        patch.dict("os.environ", _DB_ENV),
        patch(
            "omnigent.server.smart_routing._get_judge_client",
            return_value=mock_client,
        ),
    ):
        result = await _call_judge("refactor the auth module", tiers)
    assert result is not None
    assert result["tier"] == "expensive"
    assert result["model"] == "databricks-claude-opus-4-8"


@pytest.mark.asyncio
async def test_call_judge_returns_none_without_credentials() -> None:
    tiers = infer_tiers("claude-sdk")
    assert tiers is not None
    with patch.dict(
        "os.environ",
        {"DATABRICKS_HOST": "", "DATABRICKS_TOKEN": ""},
        clear=False,
    ):
        result = await _call_judge("hello", tiers)
    assert result is None


@pytest.mark.asyncio
async def test_call_judge_returns_none_on_http_error() -> None:
    tiers = infer_tiers("claude-sdk")
    assert tiers is not None
    mock_client = AsyncMock()
    mock_client.post.return_value = httpx.Response(500)
    with (
        patch.dict("os.environ", _DB_ENV),
        patch(
            "omnigent.server.smart_routing._get_judge_client",
            return_value=mock_client,
        ),
    ):
        result = await _call_judge("hello", tiers)
    assert result is None


# ── route_turn ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_turn_returns_model_and_verdict() -> None:
    verdict = {
        "tier": "cheap",
        "model": "databricks-claude-haiku-4-5",
        "rationale": "trivial",
    }
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_judge_response(verdict)
    with (
        patch.dict("os.environ", _DB_ENV),
        patch(
            "omnigent.server.smart_routing._get_judge_client",
            return_value=mock_client,
        ),
    ):
        model, v = await route_turn("claude-sdk", "hello")
    assert model == "databricks-claude-haiku-4-5"
    assert v is not None
    assert v["tier"] == "cheap"


@pytest.mark.asyncio
async def test_route_turn_clamps_hallucinated_model() -> None:
    """Judge returns a model not in the tier → clamp to first."""
    verdict = {
        "tier": "expensive",
        "model": "hallucinated-model",
        "rationale": "hard",
    }
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_judge_response(verdict)
    with (
        patch.dict("os.environ", _DB_ENV),
        patch(
            "omnigent.server.smart_routing._get_judge_client",
            return_value=mock_client,
        ),
    ):
        model, _v = await route_turn("claude-sdk", "hard task")
    assert model == "databricks-claude-opus-4-8"


@pytest.mark.asyncio
async def test_route_turn_unknown_harness() -> None:
    model, _v = await route_turn("openai-agents", "hello")
    assert model is None
    assert _v is None


@pytest.mark.asyncio
async def test_route_turn_rejects_unknown_tier() -> None:
    verdict = {"tier": "gigantic", "model": "m", "rationale": "x"}
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_judge_response(verdict)
    with (
        patch.dict("os.environ", _DB_ENV),
        patch(
            "omnigent.server.smart_routing._get_judge_client",
            return_value=mock_client,
        ),
    ):
        model, _v = await route_turn("claude-sdk", "hello")
    assert model is None


@pytest.mark.asyncio
async def test_route_turn_rejects_empty_model() -> None:
    verdict = {"tier": "cheap", "model": "", "rationale": "x"}
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_judge_response(verdict)
    with (
        patch.dict("os.environ", _DB_ENV),
        patch(
            "omnigent.server.smart_routing._get_judge_client",
            return_value=mock_client,
        ),
    ):
        model, _v = await route_turn("claude-sdk", "hello")
    assert model is None


def test_valid_tiers_constant() -> None:
    assert frozenset({"cheap", "medium", "expensive"}) == _VALID_TIERS
