"""Per-region trading rule enforcers for the Futumoo arena.

Each region — HK, US, and mainland China — owns its own session window, calendar source,
order-validation rules, and fee schedule. Each agent has a single
currency (`HKD`, `USD`, or `CNY`) and only ever interacts with one region:
HKD → HK, USD → US, CNY → CN. The top-level `FutumooArenaService` picks the
region by agent currency and rejects any code whose prefix doesn't
match.

HK:
    * 9:30–12:00 + 13:00–16:00 HKT, Mon–Fri excluding HK holidays.
    * Buy quantity must be a multiple of the per-symbol board lot
      reported in Futu's snapshot (`lot_size`); sell quantity may be
      any amount up to the held position (odd-lot sells tolerated).
    * Stamp duty configurable via `FutumooHKFeeConfig.stamp_tax_bps`
      (default 0.10% each side, HK rule since 2021).
    * No T+1 / T+2 settlement holdback.

US:
    * 9:30–16:00 ET, Mon–Fri excluding US holidays.
    * Whole shares only.
    * Pattern-Day-Trader gate: while total equity (USD) sits below
      `FutumooConfig.pdt_equity_threshold_usd`, the rolling 5
      US-business-day window may contain at most
      `FutumooConfig.pdt_max_day_trades` day-trades. A "day trade"
      counts as one event per (US trading date, code) pair where both
      a buy and a sell have filled — a deliberate simplification of
      FINRA Rule 4210(f)(8)'s direction-change counting that's easy to
      predict from the pending order before it fills.

CN:
    * 9:30–11:30 + 13:00–15:00 China time, Mon–Fri excluding mainland
      market holidays from Futu's CN calendar.
    * Symbols must use `SH.` or `SZ.`.
    * Buy quantity must be a multiple of the per-symbol lot size reported
      in Futu's snapshot; sell quantity may be any amount up to inventory.
    * No T+1 settlement holdback in this paper arena.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from logging import getLogger
from zoneinfo import ZoneInfo

from quant_arena.config import FutumooCNFeeConfig, FutumooConfig, FutumooHKFeeConfig, FutumooUSFeeConfig
from quant_arena.errors import BadRequestError, ServiceError
from quant_arena.futumoo.models import (
    DayTradeRecord,
    FutumooAgentState,
    FutumooPosition,
)
from quant_arena.futumoo.service import FutumooService
from quant_arena.models import FillRecord, OrderRecord, SubmitOrder

logger = getLogger(__name__)


HK_TZ = ZoneInfo("Asia/Hong_Kong")
US_TZ = ZoneInfo("America/New_York")
CN_TZ = ZoneInfo("Asia/Shanghai")


class RegionArena(ABC):
    """Abstract base for one regional arena."""

    region: str  # "HK", "US", or "CN"
    currency: str  # "HKD", "USD", or "CNY"
    futu_market: str  # value passed to `service.request_trading_days`
    tz: ZoneInfo

    _TRADING_DAY_FAILURE_BACKOFF = timedelta(minutes=15)

    def __init__(self, market: FutumooService):
        self.market = market
        self._trading_day_cache: tuple[date, date, set[date]] | None = None
        self._trading_day_failure_until: datetime | None = None

    # ----- code / clock -----

    @classmethod
    def code_prefix(cls) -> str:
        return f"{cls.region}."

    @classmethod
    def code_format(cls) -> str:
        return f"{cls.code_prefix()}<symbol>"

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
        if cache is not None and cache[0] <= target <= cache[1]:
            return target in cache[2]
        now = datetime.now(self.tz)
        if (
            self._trading_day_failure_until is not None
            and now < self._trading_day_failure_until
        ):
            return target.weekday() < 5
        window_start = target - timedelta(days=10)
        window_end = target + timedelta(days=10)
        try:
            days = self.market.request_trading_days(
                self.futu_market, window_start, window_end
            )
        except ServiceError as exc:
            logger.warning(
                "%s trading-day lookup failed (%s); falling back to Mon-Fri "
                "for %s",
                self.region,
                exc,
                self._TRADING_DAY_FAILURE_BACKOFF,
            )
            self._trading_day_failure_until = now + self._TRADING_DAY_FAILURE_BACKOFF
            return target.weekday() < 5
        except Exception:
            logger.exception(
                "%s trading-day lookup raised; falling back to Mon-Fri for %s",
                self.region,
                self._TRADING_DAY_FAILURE_BACKOFF,
            )
            self._trading_day_failure_until = now + self._TRADING_DAY_FAILURE_BACKOFF
            return target.weekday() < 5
        self._trading_day_failure_until = None
        self._trading_day_cache = (window_start, window_end, days)
        return target in days

    def previous_n_trading_days(self, ref: date, n: int) -> list[date]:
        days: list[date] = []
        cursor = ref
        guard = 0
        while len(days) < n and guard < 60:
            if self.is_trading_day(cursor):
                days.append(cursor)
            cursor -= timedelta(days=1)
            guard += 1
        return days

    # ----- order entry -----

    def validate_submission(
        self,
        state: FutumooAgentState,
        request: SubmitOrder,
        snapshot_row: dict,
        now: datetime,
    ) -> None:
        """Reject the submission with `BadRequestError` if any rule is violated."""
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
        if state.cash < cost:
            raise BadRequestError(
                f"Insufficient {self.currency} cash to buy {request.quantity} "
                f"{request.code} @ {request.limit_price}: need "
                f"{cost:.2f} {self.currency}, have {state.cash:.2f}."
            )

    def _validate_sell(self, state: FutumooAgentState, request: SubmitOrder) -> None:
        position = state.positions.get(request.code)
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
        notional = order.quantity * market_price
        commission, stamp_tax = self.fees_for(notional, side=order.side)
        if order.side == "buy":
            cost = notional + commission + stamp_tax
            state.cash -= cost
            existing = state.positions.get(order.code)
            if existing is None or existing.quantity == 0:
                effective_cost = cost / order.quantity
                state.positions[order.code] = FutumooPosition(
                    quantity=order.quantity, avg_cost=round(effective_cost, 4)
                )
            else:
                new_qty = existing.quantity + order.quantity
                new_cost = (existing.avg_cost * existing.quantity + cost) / new_qty
                state.positions[order.code] = FutumooPosition(
                    quantity=new_qty, avg_cost=round(new_cost, 4)
                )
        else:
            position = state.positions[order.code]
            consumed_cost = position.avg_cost * order.quantity
            proceeds = notional - commission - stamp_tax
            state.cash += proceeds
            state.realized_pnl += proceeds - consumed_cost
            new_qty = position.quantity - order.quantity
            if new_qty <= 0:
                del state.positions[order.code]
            else:
                state.positions[order.code] = FutumooPosition(
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
    # FINRA pattern-day-trader minimum equity. Below this, the rolling-window
    # day-trade limit is enforced. Hardcoded — the value is regulatory, not
    # a deployment knob.
    _PDT_EQUITY_THRESHOLD_USD = 25_000.0

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
        if equity_usd >= self._PDT_EQUITY_THRESHOLD_USD:
            return
        if not self._would_create_new_day_trade(state, request, now.date()):
            return
        window_count = self._day_trade_count_in_window(state, now.date())
        if window_count + 1 > self.config.pdt_max_day_trades:
            raise BadRequestError(
                f"Pattern-day-trader limit: account equity {equity_usd:.2f} USD "
                f"is below the {self._PDT_EQUITY_THRESHOLD_USD:.0f} USD "
                f"threshold and this order would be the {window_count + 1}th "
                f"day-trade in the trailing {self.config.pdt_window_business_days} "
                f"US business days (max {self.config.pdt_max_day_trades})."
            )

    def _equity_usd(
        self,
        state: FutumooAgentState,
        latest_snapshot_row: dict,
        latest_code: str,
    ) -> float:
        market_value = 0.0
        for code, position in state.positions.items():
            if code == latest_code:
                price = float(latest_snapshot_row.get("last_price", position.avg_cost))
            else:
                price = position.avg_cost
            market_value += position.quantity * price
        return state.cash + market_value

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
            if fill.executed_at.astimezone(self.tz).date() != today_local:
                continue
            if fill.side == "buy":
                same_day_buys = True
            else:
                same_day_sells = True
        if same_day_buys and same_day_sells:
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


class CNRegionArena(RegionArena):
    region = "CN"
    currency = "CNY"
    futu_market = "CN"
    tz = CN_TZ

    _MORNING_OPEN = time(9, 30)
    _MORNING_CLOSE = time(11, 30)
    _AFTERNOON_OPEN = time(13, 0)
    _AFTERNOON_CLOSE = time(15, 0)

    def __init__(self, market: FutumooService, fees: FutumooCNFeeConfig):
        super().__init__(market)
        self.fees = fees

    @classmethod
    def code_format(cls) -> str:
        return "SH.<code> or SZ.<code>"

    def owns_code(self, code: str) -> bool:
        return code.startswith("SH.") or code.startswith("SZ.")

    def in_session(self, moment: datetime) -> bool:
        local = moment.astimezone(self.tz).time()
        if self._MORNING_OPEN <= local <= self._MORNING_CLOSE:
            return True
        if self._AFTERNOON_OPEN <= local <= self._AFTERNOON_CLOSE:
            return True
        return False

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
                f"Could not resolve CN lot size for {request.code} (got {lot_size_raw!r})."
            )
        if lot_size <= 0:
            raise BadRequestError(
                f"Invalid CN lot size {lot_size} for {request.code}."
            )
        if request.quantity % lot_size != 0:
            raise BadRequestError(
                f"CN buy quantity {request.quantity} for {request.code} must be a "
                f"multiple of the lot size {lot_size}."
            )
        super()._validate_buy(state, request, snapshot_row)

    def fees_for(self, notional: float, side: str) -> tuple[float, float]:
        commission = self._bps_commission(notional, self.fees)
        stamp_tax = (
            round(notional * self.fees.stamp_tax_bps / 10000.0, 2)
            if side == "sell" and notional > 0 and self.fees.stamp_tax_bps > 0
            else 0.0
        )
        return commission, stamp_tax
