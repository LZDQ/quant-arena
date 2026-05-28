"""Shared HTTP auth layer for the quant-arena MCP services.

Every MCP service (ashare, futumoo, ib) authenticates the same way: a
per-agent token presented in the ``QUANT-ARENA-TOKEN`` header (raw value,
no prefix), falling back to ``Authorization: Bearer <token>`` for backward
compatibility. The dedicated header lets clients keep the standard
``Authorization`` header free for HTTP Basic auth.

This module owns the request-header decoding, token extraction, and the
generic per-agent ASGI wrapper. Services that need extra dispatch (e.g.
IB's paper/real routing and per-mode concurrency locks) compose the small
helpers here rather than reimplementing header parsing.
"""

from contextvars import ContextVar
from typing import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from quant_arena.arena_base import BaseArenaService

AGENT_TOKEN_HEADER = "quant-arena-token"


def request_headers(scope: Scope) -> dict[str, str]:
    """Decode an ASGI request's raw headers into a lowercased-key dict."""

    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def extract_agent_token(headers: dict[str, str]) -> str | None:
    """Resolve the agent token from already-lowercased request headers.

    Prefers the ``QUANT-ARENA-TOKEN`` header (raw token, no prefix) and
    falls back to ``Authorization: Bearer <token>``. Returns None when
    neither carries a token (an empty header value counts as absent).
    """

    token = headers.get(AGENT_TOKEN_HEADER) or None
    if token is None:
        authorization = headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[len("Bearer "):]
    return token


def ensure_json_accept(scope: Scope, headers: dict[str, str]) -> Scope:
    """Force an ``application/json`` Accept header onto the request scope.

    FastMCP's streamable-HTTP transport requires a JSON-capable Accept
    header. Returns the original scope when it already qualifies, otherwise
    a shallow copy with the header rewritten.
    """

    accept = headers.get("accept")
    if accept is not None and "application/json" in accept:
        return scope
    raw_headers = [
        (key, value)
        for key, value in scope.get("headers", [])
        if key.lower() != b"accept"
    ]
    raw_headers.append((b"accept", b"application/json"))
    scope = dict(scope)
    scope["headers"] = raw_headers
    return scope


def make_agent_auth_wrapper(
    mcp_app: ASGIApp,
    get_arena: Callable[[], BaseArenaService],
    current_agent_id: ContextVar[str | None],
    invalid_token_detail: str,
) -> ASGIApp:
    """Wrap an MCP app with token-based per-agent authentication.

    The token (see :func:`extract_agent_token`) must match an enabled
    agent's ``token_secret``. On success the resolved agent id is published
    on ``current_agent_id`` for the duration of the request.
    """

    async def authenticated_app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await mcp_app(scope, receive, send)
            return

        headers = request_headers(scope)
        token_value = extract_agent_token(headers)
        agent_id = None
        if token_value is not None:
            for candidate_id, agent in get_arena().list_agents():
                if agent.enabled and agent.token_secret == token_value:
                    agent_id = candidate_id
                    break
        if agent_id is None:
            response = JSONResponse(status_code=401, content={"detail": invalid_token_detail})
            await response(scope, receive, send)
            return

        scope = ensure_json_accept(scope, headers)
        token = current_agent_id.set(agent_id)
        try:
            await mcp_app(scope, receive, send)
        finally:
            current_agent_id.reset(token)

    return authenticated_app
