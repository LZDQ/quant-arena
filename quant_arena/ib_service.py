"""Interactive Brokers paper/real trading service.

Wraps `ib_insync` on a dedicated thread with its own asyncio event loop.
Public methods are synchronous; they dispatch coroutines onto the IB
thread's loop via `asyncio.run_coroutine_threadsafe`.

Only the connection's underlying account is the source of truth — we do
not maintain agent equity or positions ourselves. One IBService instance
maps to exactly one IB Gateway / TWS endpoint (paper or real).
"""

import asyncio
import math
import threading
from datetime import datetime
from logging import getLogger
from typing import Awaitable, Callable, Literal, TypeVar

from pydantic import BaseModel, Field

from quant_arena.config import IBConnectionConfig
from quant_arena.errors import BadRequestError, ConflictError, NotFoundError, ServiceError

T = TypeVar("T")

logger = getLogger(__name__)


IBMode = Literal["paper", "real"]
IBSide = Literal["buy", "sell"]
IBOrderType = Literal["MKT", "LMT"]


class IBContractInfo(BaseModel):
    """Marshalled IB contract."""

    con_id: int
    symbol: str
    sec_type: str
    exchange: str
    primary_exchange: str
    currency: str
    local_symbol: str


class IBPositionInfo(BaseModel):
    """One position reported by IB."""

    account: str
    contract: IBContractInfo
    quantity: float
    avg_cost: float


class IBAccountValueInfo(BaseModel):
    """One row of IB account summary."""

    account: str
    tag: str
    value: str
    currency: str


class IBOrderInfo(BaseModel):
    """Marshalled IB order plus current status."""

    order_id: int
    perm_id: int
    client_id: int
    action: str = Field(description='IB action, "BUY" or "SELL".')
    order_type: str
    total_quantity: float
    limit_price: float | None = None
    aux_price: float | None = None
    tif: str
    status: str = Field(description="ib_insync OrderStatus.status, e.g. Submitted, Filled, Cancelled.")
    filled: float
    remaining: float
    avg_fill_price: float | None = None
    why_held: str | None = None


class IBFillInfo(BaseModel):
    """One execution returned by IB."""

    exec_id: str
    order_id: int
    perm_id: int
    time: datetime
    contract: IBContractInfo
    side: str
    quantity: float
    price: float
    avg_price: float | None = None
    cum_qty: float | None = None
    commission: float | None = None
    realized_pnl: float | None = None


class IBTradeInfo(BaseModel):
    """An IB trade snapshot: contract, order, status, fills."""

    contract: IBContractInfo
    order: IBOrderInfo
    fills: list[IBFillInfo] = Field(default_factory=list)


class IBSubmitOrderRequest(BaseModel):
    """Domain submit-order request for IB."""

    symbol: str = Field(description="Underlying symbol, e.g. AAPL")
    side: IBSide
    quantity: float = Field(gt=0)
    order_type: IBOrderType = Field(default="LMT")
    limit_price: float | None = Field(
        default=None,
        gt=0,
        description="Required when order_type is LMT.",
    )
    exchange: str | None = Field(
        default=None,
        description="Override default exchange (e.g. SMART).",
    )
    currency: str | None = Field(
        default=None,
        description="Override default currency (e.g. USD).",
    )
    tif: str = Field(default="DAY", description="Time-in-force: DAY, GTC, IOC, etc.")


def _scrub_double(value: float) -> float | None:
    """Drop IB's UNSET_DOUBLE / NaN sentinel into a clean None."""

    if value is None:
        return None
    if math.isnan(value):
        return None
    if value > 1e300:
        return None
    return float(value)


class IBService:
    """Interactive Brokers paper/real trading service.

    Hosts an `ib_insync.IB` instance on a dedicated thread + asyncio loop.
    The connection is established lazily on the first request and reused
    for subsequent calls. A single `IBService` instance is bound to one
    mode (paper or real).
    """

    def __init__(
        self,
        mode: IBMode,
        connection: IBConnectionConfig,
        default_exchange: str,
        default_currency: str,
        request_timeout_seconds: float,
    ):
        self.mode = mode
        self.connection = connection
        self.default_exchange = default_exchange
        self.default_currency = default_currency
        self.request_timeout_seconds = request_timeout_seconds
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ib = None  # ib_insync.IB; lazy-imported inside the thread.
        self._ib_module = None  # ib_insync module reference.
        self._ready: threading.Event = threading.Event()
        self._init_error: BaseException | None = None
        self._connect_lock: asyncio.Lock | None = None

    def start(self) -> None:
        """Spin up the IB thread+loop. Does not connect; connection is lazy."""
        if self._thread is not None:
            return
        self._ready.clear()
        self._init_error = None
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"ib-{self.mode}-loop",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=10.0):
            raise RuntimeError(f"IB {self.mode} thread failed to initialize within 10s")
        if self._init_error is not None:
            raise self._init_error
        logger.info(
            "IB %s service started (host=%s port=%d clientId=%d)",
            self.mode,
            self.connection.host,
            self.connection.port,
            self.connection.client_id,
        )

    def close(self) -> None:
        """Disconnect and tear down the thread+loop."""
        loop = self._loop
        stop_event = self._stop_event
        if loop is not None and stop_event is not None and loop.is_running():
            loop.call_soon_threadsafe(stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=10.0)
        self._thread = None
        self._loop = None
        self._stop_event = None
        self._ib = None
        self._ib_module = None
        self._connect_lock = None
        logger.info("IB %s service stopped", self.mode)

    def _thread_main(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._stop_event = asyncio.Event()
            # Lazy-import ib_insync now that this thread has an event loop;
            # eventkit's module-level get_event_loop() requires one.
            import ib_insync
            self._ib_module = ib_insync
            self._ib = ib_insync.IB()
            self._connect_lock = asyncio.Lock()
        except BaseException as exc:
            self._init_error = exc
            self._ready.set()
            return
        self._ready.set()
        try:
            assert self._stop_event is not None
            loop.run_until_complete(self._stop_event.wait())
        except Exception:
            logger.exception("IB %s thread crashed", self.mode)
        finally:
            try:
                if self._ib is not None and self._ib.isConnected():
                    self._ib.disconnect()
            except Exception:
                logger.exception("Error disconnecting IB %s", self.mode)
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()

    async def _ensure_connected(self) -> None:
        ib = self._ib
        lock = self._connect_lock
        if ib is None or lock is None:
            raise RuntimeError(f"IB {self.mode} service is not started")
        if ib.isConnected():
            return
        async with lock:
            if ib.isConnected():
                return
            logger.info(
                "Connecting IB %s to %s:%d clientId=%d",
                self.mode,
                self.connection.host,
                self.connection.port,
                self.connection.client_id,
            )
            try:
                await ib.connectAsync(
                    self.connection.host,
                    self.connection.port,
                    clientId=self.connection.client_id,
                    timeout=self.request_timeout_seconds,
                )
            except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as exc:
                raise ServiceError(f"IB {self.mode} connect failed: {exc}") from exc

    def is_connected(self) -> bool:
        ib = self._ib
        return ib is not None and ib.isConnected()

    def _run(self, coro_factory: Callable[[], Awaitable[T]]) -> T:
        loop = self._loop
        if loop is None:
            raise RuntimeError(f"IB {self.mode} service is not started")

        async def _wrapper() -> T:
            await self._ensure_connected()
            return await coro_factory()

        future = asyncio.run_coroutine_threadsafe(_wrapper(), loop)
        return future.result(timeout=self.request_timeout_seconds + 5.0)

    # ----- public sync API -----

    def get_account_summary(self) -> list[IBAccountValueInfo]:
        async def _do() -> list[IBAccountValueInfo]:
            assert self._ib is not None
            values = await self._ib.accountSummaryAsync()
            return [
                IBAccountValueInfo(
                    account=v.account,
                    tag=v.tag,
                    value=v.value,
                    currency=v.currency or "",
                )
                for v in values
            ]

        return self._run(_do)

    def get_positions(self) -> list[IBPositionInfo]:
        async def _do() -> list[IBPositionInfo]:
            assert self._ib is not None
            positions = await self._ib.reqPositionsAsync()
            return [
                IBPositionInfo(
                    account=p.account,
                    contract=_marshal_contract(p.contract),
                    quantity=float(p.position),
                    avg_cost=float(p.avgCost),
                )
                for p in positions
            ]

        return self._run(_do)

    def get_open_trades(self) -> list[IBTradeInfo]:
        async def _do() -> list[IBTradeInfo]:
            assert self._ib is not None
            await self._ib.reqAllOpenOrdersAsync()
            trades = self._ib.openTrades()
            return [_marshal_trade(t) for t in trades]

        return self._run(_do)

    def get_recent_fills(self) -> list[IBFillInfo]:
        async def _do() -> list[IBFillInfo]:
            assert self._ib is not None
            fills = await self._ib.reqExecutionsAsync()
            return [_marshal_fill(f) for f in fills]

        return self._run(_do)

    def submit_order(self, request: IBSubmitOrderRequest) -> IBTradeInfo:
        if request.order_type == "LMT" and request.limit_price is None:
            raise BadRequestError("limit_price is required for LMT orders")
        if request.order_type == "MKT" and request.limit_price is not None:
            raise BadRequestError("limit_price must be omitted for MKT orders")
        action = "BUY" if request.side == "buy" else "SELL"
        exchange = request.exchange or self.default_exchange
        currency = request.currency or self.default_currency

        async def _do() -> IBTradeInfo:
            assert self._ib is not None and self._ib_module is not None
            stock = self._ib_module.Stock(request.symbol, exchange, currency)
            qualified = await self._ib.qualifyContractsAsync(stock)
            if not qualified:
                raise NotFoundError(f"Could not qualify IB contract for {request.symbol}")
            contract = qualified[0]
            if request.order_type == "MKT":
                order = self._ib_module.MarketOrder(action, request.quantity, tif=request.tif)
            else:
                assert request.limit_price is not None
                order = self._ib_module.LimitOrder(
                    action, request.quantity, request.limit_price, tif=request.tif
                )
            trade = self._ib.placeOrder(contract, order)
            # Give IB a brief moment to ack so status fields are populated.
            for _ in range(10):
                if trade.orderStatus.status not in ("", "PendingSubmit", "PreSubmitted"):
                    break
                await asyncio.sleep(0.2)
            return _marshal_trade(trade)

        return self._run(_do)

    def cancel_order(self, order_id: int) -> IBTradeInfo:
        async def _do() -> IBTradeInfo:
            assert self._ib is not None
            await self._ib.reqAllOpenOrdersAsync()
            target = None
            for trade in self._ib.openTrades():
                if trade.order.orderId == order_id:
                    target = trade
                    break
            if target is None:
                raise NotFoundError(f"No open IB order with orderId={order_id}")
            if target.orderStatus.status in ("Cancelled", "ApiCancelled", "Filled"):
                raise ConflictError(
                    f"Order {order_id} cannot be cancelled (status={target.orderStatus.status})"
                )
            self._ib.cancelOrder(target.order)
            for _ in range(10):
                if target.orderStatus.status in ("Cancelled", "ApiCancelled"):
                    break
                await asyncio.sleep(0.2)
            return _marshal_trade(target)

        return self._run(_do)


def _marshal_contract(contract) -> IBContractInfo:
    return IBContractInfo(
        con_id=int(contract.conId or 0),
        symbol=str(contract.symbol or ""),
        sec_type=str(contract.secType or ""),
        exchange=str(contract.exchange or ""),
        primary_exchange=str(contract.primaryExchange or ""),
        currency=str(contract.currency or ""),
        local_symbol=str(contract.localSymbol or ""),
    )


def _marshal_order(order, status) -> IBOrderInfo:
    return IBOrderInfo(
        order_id=int(order.orderId),
        perm_id=int(order.permId or 0),
        client_id=int(order.clientId or 0),
        action=str(order.action),
        order_type=str(order.orderType),
        total_quantity=float(order.totalQuantity),
        limit_price=_scrub_double(order.lmtPrice),
        aux_price=_scrub_double(order.auxPrice),
        tif=str(order.tif or ""),
        status=str(status.status or ""),
        filled=float(status.filled or 0.0),
        remaining=float(status.remaining or 0.0),
        avg_fill_price=_scrub_double(status.avgFillPrice),
        why_held=str(status.whyHeld) if status.whyHeld else None,
    )


def _marshal_trade(trade) -> IBTradeInfo:
    return IBTradeInfo(
        contract=_marshal_contract(trade.contract),
        order=_marshal_order(trade.order, trade.orderStatus),
        fills=[_marshal_fill(f) for f in trade.fills],
    )


def _marshal_fill(fill) -> IBFillInfo:
    execution = fill.execution
    report = fill.commissionReport
    return IBFillInfo(
        exec_id=str(execution.execId),
        order_id=int(execution.orderId or 0),
        perm_id=int(execution.permId or 0),
        time=fill.time,
        contract=_marshal_contract(fill.contract),
        side=str(execution.side),
        quantity=float(execution.shares),
        price=float(execution.price),
        avg_price=_scrub_double(execution.avgPrice),
        cum_qty=_scrub_double(execution.cumQty),
        commission=_scrub_double(report.commission) if report is not None else None,
        realized_pnl=_scrub_double(report.realizedPNL) if report is not None else None,
    )
