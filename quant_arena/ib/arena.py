"""IB paper/real trading arena (gateway-backed, max two agents).

Inherits agent registry, daily reports and persistence from
`BaseArenaService`. The IB-specific parts are:

* Each agent is bound to one connection mode — paper or real — chosen
  at registration. At most one agent may exist per mode, so the arena
  ever holds at most two agents in total.
* The IB Gateway (or TWS) is the source of truth for cash, positions,
  and orders. The portfolio view is built live from
  `accountSummaryAsync` and `reqPositionsAsync` rather than from a
  locally-simulated state.
* HK vs US is *not* a per-agent property; an IB account can hold
  both. The contract's `exchange` and `currency` fields are supplied
  per order via the IB MCP tool surface, so a single agent can place
  HK and US orders side-by-side.
* Local state stores only the daily NetLiquidation snapshot history
  so the dashboard can plot a return-% curve. Order/fill state is
  intentionally not synthesized — agents query orders directly via
  the IB MCP tools.
"""

from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path
from typing import Literal

from quant_arena.arena_base import BaseArenaService
from quant_arena.config import AgentConfig
from quant_arena.errors import BadRequestError, ConflictError, ServiceError
from quant_arena.ib.models import IBAgentState
from quant_arena.ib.service import IBAccountValueInfo, IBService
from quant_arena.models import OperationLog, OrderRecord, PortfolioSnapshot, PositionSnapshot
from quant_arena.notifier import NotifierService

logger = getLogger(__name__)


IBMode = Literal["paper", "real"]


class IBArenaService(BaseArenaService[IBAgentState]):
    """Two-agent IB orchestrator. Reads live from IB Gateway."""

    def __init__(
        self,
        agents_root: Path,
        paper: IBService | None,
        real: IBService | None,
        notifier: NotifierService,
    ):
        super().__init__(
            agents_root=agents_root,
            notifier=notifier,
            state_cls=IBAgentState,
        )
        self.paper = paper
        self.real = real

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _service_for_mode(self, mode: IBMode) -> IBService:
        service = self.paper if mode == "paper" else self.real
        if service is None:
            raise ServiceError(
                f"IB {mode} connection is not configured on this server."
            )
        return service

    def service_for_agent(self, agent_id: str) -> IBService:
        agent = self.get_agent(agent_id)
        mode = self._agent_mode(agent)
        return self._service_for_mode(mode)

    @staticmethod
    def _agent_mode(agent: AgentConfig) -> IBMode:
        if agent.ib_mode is None:
            raise BadRequestError(
                "IB agent is missing required `ib_mode` field (paper or real)."
            )
        return agent.ib_mode

    # ----- agent registry -----

    def add_agent(self, agent_id: str, agent: AgentConfig) -> AgentConfig:
        mode = self._agent_mode(agent)
        for existing_id, existing in self._agents.items():
            if existing.ib_mode == mode:
                raise ConflictError(
                    f"An IB {mode} agent already exists ({existing_id}); "
                    "the IB arena allows at most one agent per mode."
                )
        return super().add_agent(agent_id, agent)

    # ----- live portfolio -----

    def _build_portfolio(self, state: IBAgentState) -> PortfolioSnapshot:
        agent = self._agents.get(state.agent_id)
        currency = agent.currency if agent is not None else "USD"
        if agent is None:
            return PortfolioSnapshot(
                agent_id=state.agent_id,
                currency=currency,
                cash=0.0,
                market_value=0.0,
                total_equity=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                positions=[],
                pending_orders=[],
                as_of=None,
            )
        try:
            service = self._service_for_mode(self._agent_mode(agent))
            summary = service.get_account_summary()
            ib_positions = service.get_positions()
        except ServiceError as exc:
            logger.warning("IB portfolio fetch failed for %s: %s", state.agent_id, exc.detail)
            last = state.equity_history[-1] if state.equity_history else None
            return PortfolioSnapshot(
                agent_id=state.agent_id,
                currency=currency,
                cash=last.cash if last else 0.0,
                market_value=last.market_value if last else 0.0,
                total_equity=last.total_equity if last else 0.0,
                realized_pnl=last.realized_pnl if last else 0.0,
                unrealized_pnl=last.unrealized_pnl if last else 0.0,
                positions=[],
                pending_orders=[],
                as_of=None,
            )

        net_liquidation = _pick_summary_value(summary, "NetLiquidation", currency)
        available_funds = _pick_summary_value(summary, "AvailableFunds", currency)
        unrealized = _pick_summary_value(summary, "UnrealizedPnL", currency) or 0.0
        realized = _pick_summary_value(summary, "RealizedPnL", currency) or 0.0
        total_equity = net_liquidation if net_liquidation is not None else 0.0
        cash = available_funds if available_funds is not None else 0.0
        market_value = max(total_equity - cash, 0.0)
        positions: list[PositionSnapshot] = []
        for ib_pos in ib_positions:
            quantity = int(round(ib_pos.quantity))
            if quantity == 0:
                continue
            code = _ib_code(ib_pos.contract.symbol, ib_pos.contract.currency)
            positions.append(
                PositionSnapshot(
                    code=code,
                    quantity=quantity,
                    sellable_quantity=quantity,
                    avg_cost=round(float(ib_pos.avg_cost), 4),
                    market_price=None,
                    market_value=0.0,
                    unrealized_pnl=0.0,
                )
            )
        return PortfolioSnapshot(
            agent_id=state.agent_id,
            currency=currency,
            cash=round(cash, 2),
            market_value=round(market_value, 2),
            total_equity=round(total_equity, 2),
            realized_pnl=round(realized, 2),
            unrealized_pnl=round(unrealized, 2),
            positions=positions,
            pending_orders=[],
            as_of=self._now(),
        )

    # ----- operations log -----

    def list_operations(
        self,
        agent_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> OperationLog:
        # IB orders/fills are intentionally not synthesized into the
        # legacy OrderRecord/FillRecord shape — agents query them via
        # the IB MCP tools (`get_open_trades`, `get_recent_fills`).
        self.get_agent(agent_id)
        return OperationLog(orders=[], fills=[])

    def cancel_order(self, agent_id: str, order_id: str) -> OrderRecord:
        # IB orders are cancelled through the IB MCP `cancel_order`
        # tool, which calls IBService.cancel_order directly. This
        # base hook is unused for IB and intentionally errors so a
        # mistaken caller fails loudly rather than silently no-ops.
        raise BadRequestError(
            "IB orders are managed via the IB MCP tools, not the arena cancel hook."
        )


def _pick_summary_value(
    summary: list[IBAccountValueInfo], tag: str, preferred_currency: str
) -> float | None:
    """Find the first matching account-summary value, preferring the agent's currency."""
    fallback: float | None = None
    for row in summary:
        if row.tag != tag:
            continue
        try:
            value = float(row.value)
        except (TypeError, ValueError):
            continue
        if row.currency and row.currency.upper() == preferred_currency.upper():
            return value
        if fallback is None:
            fallback = value
    return fallback


def _ib_code(symbol: str, currency: str) -> str:
    """Render an IB contract as a Futu-style `<region>.<symbol>` code.

    Used only for the dashboard's holdings table; pure presentation,
    not for round-trip parsing back into IB contracts.
    """
    code = (symbol or "").strip()
    if not code:
        return code
    region = (currency or "").upper()
    if region == "HKD":
        return f"HK.{code}"
    if region == "USD":
        return f"US.{code}"
    return code
