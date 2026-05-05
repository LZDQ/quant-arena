"""Thin Futu OpenD wrapper used only for snapshot pricing.

Trading is fully offline — orders fill in our own ledger and never
reach OpenD. The only external dependency is `OpenQuoteContext.
get_market_snapshot` for daily equity-history mark-to-market.
The connection is lazy and reused across calls.
"""

import threading
from logging import getLogger

from quant_arena.errors import ServiceError

logger = getLogger(__name__)


class FutumooService:
    """One process-wide Futu `OpenQuoteContext`, lazily connected."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._quote_ctx = None  # futu.OpenQuoteContext, lazy

    def _ensure_quote_ctx(self):
        if self._quote_ctx is not None:
            return self._quote_ctx
        with self._lock:
            if self._quote_ctx is None:
                from futu import OpenQuoteContext

                self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
                logger.info(
                    "Futumoo quote context connected to %s:%d", self.host, self.port
                )
        return self._quote_ctx

    def get_snapshot(self, codes: list[str]) -> dict[str, float]:
        """Return `{code: last_price}` for each requested symbol.

        Codes carry their Futu region prefix verbatim, e.g. `US.AAPL`,
        `HK.00700`. Codes that have no usable last price are omitted.
        """
        if not codes:
            return {}
        ctx = self._ensure_quote_ctx()
        ret, data = ctx.get_market_snapshot(list(codes))
        if ret != 0:
            raise ServiceError(f"futu get_market_snapshot failed: {data}")
        out: dict[str, float] = {}
        for _, row in data.iterrows():
            code = str(row["code"])
            try:
                price = float(row["last_price"])
            except (TypeError, ValueError):
                continue
            if price > 0:
                out[code] = price
        return out

    def close(self) -> None:
        with self._lock:
            if self._quote_ctx is not None:
                try:
                    self._quote_ctx.close()
                except Exception:
                    logger.exception("Error closing Futu quote context")
                self._quote_ctx = None
