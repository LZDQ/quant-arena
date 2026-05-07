"""Per-region trading rule enforcers for the Futumoo arena.

Each region — HK and US — owns its own session window, calendar source,
order-validation rules, and accounting view onto `FutumooAgentState`.
The top-level `FutumooArenaService` is a thin orchestrator that picks
the right region for an order code and delegates rule checking and
fill execution. Tests, comments, and reasoning kept in one place per
region to make the rules easy to audit.

HK:
    * 9:30–12:00 + 13:00–16:00 HKT, Mon–Fri excluding HK holidays.
    * Buy quantity must be a multiple of the per-symbol board lot
      reported in Futu's snapshot (`lot_size`); sell quantity may be
      any amount up to the held position (odd-lot sells are tolerated).
    * Stamp duty is configurable via `FutumooHKFeeConfig.stamp_tax_bps`
      and applied to both sides of a fill (HK rule since 2021).
    * No T+1 / T+2 sellable holdback — settlement is not modeled.

US:
    * 9:30–16:00 ET, Mon–Fri excluding US holidays.
    * Whole shares only.
    * Pattern-Day-Trader gate: while total USD-equivalent equity sits
      below `FutumooConfig.pdt_equity_threshold_usd`, the rolling 5
      US-business-day window may contain at most
      `FutumooConfig.pdt_max_day_trades` day-trades (default 3, i.e.
      the 4th day-trade is rejected at submission). A "day trade" is
      counted as one event per (US trading date, code) pair where both
      a buy and a sell have filled — a deliberate simplification of
      FINRA Rule 4210(f)(8)'s direction-change counting that's easy
      to predict from the pending order before it fills.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from logging import getLogger
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from quant_arena.config import FutumooConfig, FutumooHKFeeConfig, FutumooUSFeeConfig
from quant_arena.errors import BadRequestError, ServiceError
from quant_arena.futumoo.models import (
    DayTradeRecord,
    FutumooAgentState,
    FutumooPosition,
)
from quant_arena.models import FillRecord, OrderRecord, SubmitOrder

if TYPE_CHECKING:
    from quant_arena.futumoo.service import FutumooService

logger = getLogger(__name__)


HK_TZ = ZoneInfo("Asia/Hong_Kong")
US_TZ = ZoneInfo("America/New_York")


class RegionArena(ABC):
    """Abstract base for one regional book inside the Futumoo arena.

    Subclasses encode region-specific session windows, calendar lookups,
    fee schedules, and rule checks. Both regions read and write the
    same `FutumooAgentState` — the split is logical, not on disk.
    """

    region: str  # "HK" or "US"
    currency: str  # "HKD" or "USD"
    futu_market: str  # value passed to `service.request_trading_days`
    tz: ZoneInfo

    def __init__(self, market: FutumooService):
        self.market = market
        # Cache of trading days, refreshed when a query falls outside the cached window.
        self._trading_day_cache: tuple[date, date, set[date]] | None = None

    # ----- code / clock -----

    @classmethod
    def code_prefix(cls) -> str:
        return f"{cls.region}."

    def owns_code(self, code: str) -> bool:
        return code.startswith(self.code_prefix())

    def now(self) -> datetime:
        return datetime.now(self.tz)

    @abstractmethod
    def in_session(self, moment: datetime) -> bool:
        """Whether `moment` falls inside this region's continuous-trading window."""

    def is_trading_day(self, target: date) -> bool:
        """Best-effort trading-day check using OpenD with a Mon–Fri fallback."""
        cache = self._trading_day_cache
        if cache is None or target < cache[0] or target > cache[1]:
            window_start = target - timedelta(days=10)
            window_end = target + timedelta(days=10)
            try:
                days = self.market.request_trading_days(
                    self.futu_market, window_start, window_end
                )
            except ServiceError:
                logger.warning(
                    "request_trading_days failed for %s; falling back to Mon-Fri",
                    self.region,
                )
                return target.weekday() < 5
            self._trading_day_cache = (window_start, window_end, days)
            cache = self._trading_day_cache
        return target in cache[2]

    def previous_n_trading_days(self, ref: date, n: int) -> list[date]:
        """Return the `n` most recent trading days strictly on or before `ref`."""
        days: list[date] = []
        cursor = ref
        guard = 0
        while len(days) < n and guard < 60:
            if self.is_trading_day(cursor):
                days.append(cursor)
            cursor -= timedelta(days=1)
            guard += 1
        return days

    # ----- state views -----

    @abstractmethod
    def cash(self, state: FutumooAgentState) -> float: ...

    @abstractmethod
    def add_cash(self, state: FutumooAgentState, delta: float) -> None: ...

    @abstractmethod
    def add_realized_pnl(self, state: FutumooAgentState, delta: float) -> None: ...

    @abstractmethod
    def positions(self, state: FutumooAgentState) -> dict[str, FutumooPosition]: ...

    # ----- order entry -----

    def validate_submission(
        self,
        state: FutumooAgentState,
        request: SubmitOrder,
        snapshot_row: dict,
        now: datetime,
    ) -> None:
        """Reject the submission with `BadRequestError` if any rule is violated.

        Order of checks: session/trading-day, snapshot freshness, side-specific
        constraints (buy: lot/cash; sell: held quantity). Region subclasses may
        override to add region-only gates (PDT for US, board-lot for HK).
        """
        if not self.is_trading_day(now.date()):
            raise BadRequestError(
                f"{now.date().isoformat()} is not a {self.region} trading day."
            )
        if not self.in_session(now):
            raise BadRequestError(
                f"{self.region} continuous trading is closed at "
                f"{now.strftime('%Y-%m-%d %H:%M %Z')}."
            )
        if snapshot_row.get("suspension"):
            raise BadRequestError(f"{request.code} is suspended.")
        if request.side == "buy":
            self._validate_buy(state, request, snapshot_row)
        else:
            self._validate_sell(state, request)

    def _validate_buy(
        self,
        state: FutumooAgentState,
        request: SubmitOrder,
        snapshot_row: dict,
    ) -> None:
        notional = request.quantity * request.limit_price
        commission, stamp = self.fees_for(notional, side="buy")
        cost = notional + commission + stamp
        if self.cash(state) < cost:
            raise BadRequestError(
                f"Insufficient {self.currency} cash to buy {request.quantity} "
                f"{request.code} @ {request.limit_price}: need "
                f"{cost:.2f} {self.currency}, have {self.cash(state):.2f}."
            )

    def _validate_sell(self, state: FutumooAgentState, request: SubmitOrder) -> None:
        position = self.positions(state).get(request.code)
        held = position.quantity if position is not None else 0
        pending_sell = sum(
            order.quantity
            for order in state.orders
            if order.status == "pending"
            and order.side == "sell"
            and order.code == request.code
        )
        available = held - pending_sell
        if request.quantity > available:
            raise BadRequestError(
                f"Cannot sell {request.quantity} {request.code}: hold {held}, "
                f"{pending_sell} already encumbered by other pending sells."
            )

    # ----- fills -----

    @abstractmethod
    def fees_for(self, notional: float, side: str) -> tuple[float, float]:
        """Return `(commission, stamp_or_other_tax)` for a fill of size `notional`."""

    def fill_pending(
        self,
        state: FutumooAgentState,
        order: OrderRecord,
        market_price: float,
        executed_at: datetime,
    ) -> FillRecord:
        """Apply a fill to `state` and return the resulting FillRecord."""
        notional = order.quantity * market_price
        commission, stamp_tax = self.fees_for(notional, side=order.side)
        positions = self.positions(state)
        if order.side == "buy":
            cost = notional + commission + stamp_tax
            self.add_cash(state, -cost)
            existing = positions.get(order.code)
            if existing is None or existing.quantity == 0:
                effective_cost = cost / order.quantity
                positions[order.code] = FutumooPosition(
                    quantity=order.quantity, avg_cost=round(effective_cost, 4)
                )
            else:
                new_qty = existing.quantity + order.quantity
                new_cost = (existing.avg_cost * existing.quantity + cost) / new_qty
                positions[order.code] = FutumooPosition(
                    quantity=new_qty, avg_cost=round(new_cost, 4)
                )
        else:
            position = positions[order.code]
            consumed_cost = position.avg_cost * order.quantity
            proceeds = notional - commission - stamp_tax
            self.add_cash(state, proceeds)
            self.add_realized_pnl(state, proceeds - consumed_cost)
            new_qty = position.quantity - order.quantity
            if new_qty <= 0:
                del positions[order.code]
            else:
                positions[order.code] = FutumooPosition(
                    quantity=new_qty, avg_cost=position.avg_cost
                )
        order.status = "filled"
        order.filled_at = executed_at
        fill = FillRecord(
            order_id=order.order_id,
            agent_id=order.agent_id,
            code=order.code,
            side=order.side,
            quantity=order.quantity,
            executed_at=executed_at,
            executed_price=market_price,
            commission=commission,
            stamp_tax=stamp_tax,
        )
        state.fills.append(fill)
        self._on_fill_recorded(state, fill, executed_at)
        return fill

    def _on_fill_recorded(
        self,
        state: FutumooAgentState,
        fill: FillRecord,
        executed_at: datetime,
    ) -> None:
        """Hook for region-specific bookkeeping (e.g. day-trade ledger)."""

    @staticmethod
    def _bps_commission(notional: float, fees) -> float:
        """Shared commission helper: max(min, notional * bps / 10000)."""
        if notional <= 0 or fees.commission_bps <= 0:
            return 0.0
        return round(
            max(fees.min_commission, notional * fees.commission_bps / 10000.0), 2
        )


class HKRegionArena(RegionArena):
    region = "HK"
    currency = "HKD"
    futu_market = "HK"
    tz = HK_TZ

    _MORNING_OPEN = time(9, 30)
    _MORNING_CLOSE = time(12, 0)
    _AFTERNOON_OPEN = time(13, 0)
    _AFTERNOON_CLOSE = time(16, 0)

    def __init__(self, market: FutumooService, fees: FutumooHKFeeConfig):
        super().__init__(market)
        self.fees = fees

    def in_session(self, moment: datetime) -> bool:
        local = moment.astimezone(self.tz).time()
        if self._MORNING_OPEN <= local <= self._MORNING_CLOSE:
            return True
        if self._AFTERNOON_OPEN <= local <= self._AFTERNOON_CLOSE:
            return True
        return False

    def cash(self, state: FutumooAgentState) -> float:
        return state.cash_hkd

    def add_cash(self, state: FutumooAgentState, delta: float) -> None:
        state.cash_hkd += delta

    def add_realized_pnl(self, state: FutumooAgentState, delta: float) -> None:
        state.realized_pnl_hkd += delta

    def positions(self, state: FutumooAgentState) -> dict[str, FutumooPosition]:
        return state.positions_hk

    def _validate_buy(
        self,
        state: FutumooAgentState,
        request: SubmitOrder,
        snapshot_row: dict,
    ) -> None:
        lot_size_raw = snapshot_row.get("lot_size")
        try:
            lot_size = int(lot_size_raw)
        except (TypeError, ValueError):
            raise BadRequestError(
                f"Could not resolve HK board-lot size for {request.code} (got {lot_size_raw!r})."
            )
        if lot_size <= 0:
            raise BadRequestError(
                f"Invalid HK board-lot size {lot_size} for {request.code}."
            )
        if request.quantity % lot_size != 0:
            raise BadRequestError(
                f"HK buy quantity {request.quantity} for {request.code} must be a "
                f"multiple of the board lot {lot_size}."
            )
        super()._validate_buy(state, request, snapshot_row)

    def fees_for(self, notional: float, side: str) -> tuple[float, float]:
        commission = self._bps_commission(notional, self.fees)
        stamp_tax = (
            round(notional * self.fees.stamp_tax_bps / 10000.0, 2)
            if notional > 0 and self.fees.stamp_tax_bps > 0
            else 0.0
        )
        return commission, stamp_tax


class USRegionArena(RegionArena):
    region = "US"
    currency = "USD"
    futu_market = "US"
    tz = US_TZ

    _OPEN = time(9, 30)
    _CLOSE = time(16, 0)

    def __init__(
        self,
        market: FutumooService,
        fees: FutumooUSFeeConfig,
        config: FutumooConfig,
    ):
        super().__init__(market)
        self.fees = fees
        self.config = config

    def in_session(self, moment: datetime) -> bool:
        local = moment.astimezone(self.tz).time()
        return self._OPEN <= local <= self._CLOSE

    def cash(self, state: FutumooAgentState) -> float:
        return state.cash_usd

    def add_cash(self, state: FutumooAgentState, delta: float) -> None:
        state.cash_usd += delta

    def add_realized_pnl(self, state: FutumooAgentState, delta: float) -> None:
        state.realized_pnl_usd += delta

    def positions(self, state: FutumooAgentState) -> dict[str, FutumooPosition]:
        return state.positions_us

    def fees_for(self, notional: float, side: str) -> tuple[float, float]:
        return self._bps_commission(notional, self.fees), 0.0

    # ----- PDT bookkeeping -----

    def validate_submission(
        self,
        state: FutumooAgentState,
        request: SubmitOrder,
        snapshot_row: dict,
        now: datetime,
    ) -> None:
        super().validate_submission(state, request, snapshot_row, now)
        self._enforce_pdt(state, request, now, snapshot_row)

    def _enforce_pdt(
        self,
        state: FutumooAgentState,
        request: SubmitOrder,
        now: datetime,
        snapshot_row: dict,
    ) -> None:
        equity_usd = self._equity_usd(state, snapshot_row, request.code)
        if equity_usd >= self.config.pdt_equity_threshold_usd:
            return
        if not self._would_create_new_day_trade(state, request, now.date()):
            return
        window_count = self._day_trade_count_in_window(state, now.date())
        # The submission, if filled today, would add 1 — reject when adding it
        # crosses the maximum.
        if window_count + 1 > self.config.pdt_max_day_trades:
            raise BadRequestError(
                f"Pattern-day-trader limit: account USD-equivalent equity "
                f"{equity_usd:.2f} is below the "
                f"{self.config.pdt_equity_threshold_usd:.0f} USD threshold and "
                f"this order would be the {window_count + 1}th day-trade in the "
                f"trailing {self.config.pdt_window_business_days} US business days "
                f"(max {self.config.pdt_max_day_trades})."
            )

    def _equity_usd(
        self,
        state: FutumooAgentState,
        latest_snapshot_row: dict,
        latest_code: str,
    ) -> float:
        """Approximate total USD-equivalent equity used for the PDT threshold.

        Cash buckets are folded together via the configured FX rate. US
        positions are valued at `last_price`; HK positions are valued at
        `avg_cost` (we do not poll HK snapshots inside the US validator)
        and converted to USD. Good enough for a sub-25k gate.
        """
        fx = self.config.fx_hkd_per_usd
        hkd_market_value = sum(
            position.quantity * position.avg_cost
            for position in state.positions_hk.values()
        )
        us_market_value = 0.0
        for code, position in state.positions_us.items():
            if code == latest_code:
                price = float(latest_snapshot_row.get("last_price", position.avg_cost))
            else:
                price = position.avg_cost
            us_market_value += position.quantity * price
        return (
            state.cash_usd
            + state.cash_hkd / fx
            + hkd_market_value / fx
            + us_market_value
        )

    def _would_create_new_day_trade(
        self,
        state: FutumooAgentState,
        request: SubmitOrder,
        today_local: date,
    ) -> bool:
        same_day_buys = False
        same_day_sells = False
        for fill in state.fills:
            if fill.code != request.code:
                continue
            if not fill.code.startswith(self.code_prefix()):
                continue
            local_date = fill.executed_at.astimezone(self.tz).date()
            if local_date != today_local:
                continue
            if fill.side == "buy":
                same_day_buys = True
            else:
                same_day_sells = True
        if same_day_buys and same_day_sells:
            # already counted as 1 for the day; no marginal day-trade
            return False
        if request.side == "sell":
            return same_day_buys and not same_day_sells
        return same_day_sells and not same_day_buys

    def _day_trade_count_in_window(
        self, state: FutumooAgentState, ref_date: date
    ) -> int:
        window_dates = set(
            self.previous_n_trading_days(
                ref_date, self.config.pdt_window_business_days
            )
        )
        return sum(1 for entry in state.day_trades if entry.trade_date in window_dates)

    def _on_fill_recorded(
        self,
        state: FutumooAgentState,
        fill: FillRecord,
        executed_at: datetime,
    ) -> None:
        local_date = executed_at.astimezone(self.tz).date()
        opposite = "sell" if fill.side == "buy" else "buy"
        has_opposite = any(
            f.code == fill.code
            and f.side == opposite
            and f.fill_id != fill.fill_id
            and f.executed_at.astimezone(self.tz).date() == local_date
            for f in state.fills
        )
        if not has_opposite:
            return
        already_counted = any(
            entry.trade_date == local_date and entry.code == fill.code
            for entry in state.day_trades
        )
        if already_counted:
            return
        state.day_trades.append(DayTradeRecord(trade_date=local_date, code=fill.code))
