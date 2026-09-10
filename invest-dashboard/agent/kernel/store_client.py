"""Persistence boundary for agent v2.

With ``INVEST_MCP_URL`` configured, state is written/read through the invest
MCP server.  Without it, calls retain the existing SQLite behaviour.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import urlparse


def _mcp_url() -> str | None:
    value = os.environ.get("INVEST_MCP_URL", "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("INVEST_MCP_URL must be an http(s) URL")
    return value


def configured() -> bool:
    """Whether persistence is routed to the MCP server."""
    return _mcp_url() is not None


def _local():
    # Keep the fallback lazy: importing the client must not create/open SQLite.
    from agent import profile_store
    return profile_store


async def _call_tool_async(url: str, name: str, arguments: dict[str, Any]):
    try:
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "INVEST_MCP_URL is set, but the MCP client is not installed; "
            "install server/requirements.txt"
        ) from error

    # Транспорт streamable-HTTP: чистый request/response, без долгоживущего SSE
    # (у SSE клиентский teardown за прокси зависал). Токен — через http_client
    # (сам streamable_http_client headers не принимает); таймаут 30с.
    token = os.environ.get("INVEST_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # connect ограничен 8с — стайл коннекта через прокси не тянет десятки секунд;
    # read/write щедрее (крупный ответ канона).
    timeout = httpx2.Timeout(30.0, connect=8.0)
    client = httpx2.AsyncClient(headers=headers, timeout=timeout)
    try:
        async with streamable_http_client(url, http_client=client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
    finally:
        await client.aclose()
    if getattr(result, "isError", False):
        messages = [getattr(item, "text", str(item)) for item in result.content]
        raise RuntimeError(f"MCP tool {name} failed: {'; '.join(messages)}")
    # structuredContent was added after the first supported MCP SDK releases;
    # older clients still expose the JSON value as their sole text block.
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured)
    # The installed MCP SDK serialises a list-returning tool (e.g. get_drift)
    # as one text block per element rather than a single JSON array — unwrap
    # a lone block to its scalar value, but collect multiple blocks as a list.
    values = [json.loads(item.text) for item in result.content if hasattr(item, "text")]
    if len(values) == 1:
        return values[0]
    return values


def _call_tool(name: str, arguments: dict[str, Any]):
    url = _mcp_url()
    if url is None:
        raise RuntimeError("MCP endpoint is not configured")
    return asyncio.run(_call_tool_async(url, name, arguments))


def _as_list(value):
    """MCP отдаёт один блок скаляром, а не списком из одного — сгладить.

    Для list-контрактных читателей (материалы, дрейф, история) одна строка
    иначе прилетает dict'ом, и потребитель ломается на итерации по ключам.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _canonical_comps(comps):
    """Expand legacy ticker strings while retaining structured comp metadata."""
    if not isinstance(comps, (list, tuple)):
        return comps
    return [
        {"name": comp, "kind": "public"} if isinstance(comp, str) else comp
        for comp in comps
    ]


def record_baseline(ticker, measure, corridor, assumptions, status, run_at=None):
    if _mcp_url() is None:
        return _local().record_baseline(
            ticker, measure, corridor, assumptions, status, run_at=run_at
        )
    # The server state contract stores calculated corridors only.
    if status != "calculated" or corridor is None:
        return None
    return _call_tool("record_baseline", {
        "ticker": ticker,
        "measure": measure,
        "corridor": list(corridor),
        "assumptions": assumptions,
        "author": os.environ.get("INVEST_AUTHOR") or None,
    })


def record_fork(ticker, measure, label, corridor, assumptions, status, run_at=None):
    if _mcp_url() is None:
        return _local().record_fork(
            ticker, measure, label, corridor, assumptions, status, run_at=run_at
        )
    if status == "refused" or corridor is None:
        return None
    return _call_tool("record_fork", {
        "ticker": ticker,
        "label": label,
        "channel": assumptions.get("channel", ""),
        "corridor": list(corridor),
        "overrides": assumptions.get("overrides", {}),
        "thesis": assumptions.get("thesis", ""),
        "must_be_true": assumptions.get("must_be_true", []),
        "author": os.environ.get("INVEST_AUTHOR") or None,
    })


def get_drift(ticker):
    if _mcp_url() is None:
        return _local().baseline_drift(ticker)
    return _as_list(_call_tool("get_drift", {"ticker": ticker}))


def get_scenarios(ticker):
    if _mcp_url() is None:
        return _local().get_scenarios(ticker)
    return _call_tool("get_scenarios", {"ticker": ticker})


def get_scenario_detail(ticker):
    """Тяжёлый read (допущения baseline + тезис/условия/override'ы форков).

    Лаконичный get_scenarios контрактно тяжёлого не несёт (#92); в локальном
    режиме детали не хранятся — отдаём пусто."""
    if _mcp_url() is None:
        return {"baseline": None, "forks": []}
    return _call_tool("get_scenario_detail", {"ticker": ticker})


def list_tickers():
    """Роестр тикеров канона с активностью. В локальном режиме пусто."""
    if _mcp_url() is None:
        return []
    return _call_tool("list_tickers", {})


def get_profile(ticker):
    """Read the canonical profile, falling back to the local registry."""
    if _mcp_url() is None:
        from agent import profile
        return profile._build_registry(ticker)
    canonical = _call_tool("get_profile", {"ticker": ticker})
    if canonical is not None:
        canonical["comps"] = _canonical_comps(canonical.get("comps"))
        canonical.setdefault("segments", [])
        canonical.setdefault("scope", {})
        return canonical
    from agent import profile
    return profile._build_registry(ticker, replay_local=False)


def get_profile_history(ticker):
    if _mcp_url() is None:
        events = _local().timeline(ticker)
        return [
            {
                "field": event["field"],
                "old": event["old_value"],
                "new": event["new_value"],
                "author": event["author"],
                "reason": event["reason"],
                "at": event["created_at"],
            }
            for event in events
        ]
    return _as_list(_call_tool("get_profile_history", {"ticker": ticker}))


def list_materials(ticker=None):
    if _mcp_url() is None:
        # Shared materials are canonical-server-only; local mode stays usable.
        return []
    arguments = {} if ticker is None else {"ticker": ticker}
    return _as_list(_call_tool("list_materials", arguments))


def add_material(
    ticker,
    source_name,
    source_link,
    synthesis,
    raw_ref=None,
    author=None,
    for_measure=None,
):
    """Store an analyst-authored material, or safely no-op in local mode."""
    if _mcp_url() is None:
        return None
    return _call_tool("add_material", {
        "ticker": ticker,
        "source_name": source_name,
        "source_link": source_link,
        "synthesis": synthesis,
        "raw_ref": raw_ref,
        "for_measure": for_measure,
        "author": author if author is not None else (
            os.environ.get("INVEST_AUTHOR") or None
        ),
    })


def get_material(ticker):
    """Read parsed materials, returning an empty list in local mode."""
    if _mcp_url() is None:
        return []
    return _as_list(_call_tool("get_material", {"ticker": ticker}))


def materials_for_contract(ticker, measure):
    """Materials for the measure_contract gate — local mode stays usable.

    Shared materials are canon-only (`get_material` returns [] locally, same
    as `list_materials`/`add_material`): the material_for_measure node can
    never be met offline, so an unconfigured store vacuously satisfies it
    instead of refusing every local calculation forever. head_comps/part_comps
    stay fully enforced offline — only this canon-only node is relaxed.
    """
    if _mcp_url() is None:
        return [{"for_measure": measure}]
    return get_material(ticker)


def propose_profile_candidate(
    ticker, field, value, reason, anchor_class, source, author=None
):
    if _mcp_url() is None:
        return _local().record(
            ticker, field, value, reason, author=author or "model"
        )
    return _call_tool("propose_profile_candidate", {
        "ticker": ticker,
        "field": field,
        "value": value,
        "reason": reason,
        "anchor_class": anchor_class,
        "source": source,
        "author": author if author is not None else (
            os.environ.get("INVEST_AUTHOR") or None
        ),
    })


def approve_profile_candidate(ticker, candidate_id, author="human"):
    if _mcp_url() is None:
        return _local().confirm(ticker, candidate_id, author=author)
    return _call_tool("approve_candidate", {"id": candidate_id, "author": author})
