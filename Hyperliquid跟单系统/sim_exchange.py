"""
Hyperliquid 模拟成交引擎 (SimExchange)
在真实 API 数据上叠一层模拟成交层，用于：
1. 接收 order_signal 信号
2. 立即模拟成交（按当前中间价）
3. 维护模拟持仓 & 账户台账
4. 提供实时净值查询
"""

import json
import time
import math
from datetime import datetime
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN

TARGET_CAPITAL = 10000.0  # 模拟账户初始本金 (USD)
STATE_FILE = "/app/data/所有对话/主对话/sim_state.json"


@dataclass
class SimPosition:
    size: float = 0.0
    entry_px: float = 0.0
    unrealized_pnl: float = 0.0

    def value(self, mark_px):
        return self.size * mark_px

    def pnl(self, mark_px):
        if self.size == 0 or self.entry_px == 0:
            return 0.0
        return (mark_px - self.entry_px) * self.size


@dataclass
class SimOrder:
    id: str
    coin: str
    side: str  # "buy" / "sell"
    size: float
    px: float
    filled_px: float
    time: str
    realized_pnl: float = 0.0
    closed_size: float = 0.0


class SimExchange:
    def __init__(self, capital=TARGET_CAPITAL):
        self.capital = capital
        self.cash = capital
        self.positions: dict[str, SimPosition] = {}
        self.orders: list[SimOrder] = []
        self.fees = 0.0
        self.order_counter = 0
        self.realized_pnl = 0.0  # track cumulative realized pnl
        self._load()

    # ── Persistence ──────────────────────────────────────────────

    def _load(self):
        try:
            with open(STATE_FILE, "r") as f:
                d = json.load(f)
                self.cash = d.get("cash", self.capital)
                self.capital = d.get("capital", self.capital)
                self.fees = d.get("fees", 0.0)
                self.order_counter = d.get("order_counter", 0)
                self.realized_pnl = d.get("realized_pnl", 0.0)
                self.orders = [SimOrder(**o) for o in d.get("orders", [])]
                for coin, p in d.get("positions", {}).items():
                    self.positions[coin] = SimPosition(**p)
        except FileNotFoundError:
            pass

    def save(self):
        d = {
            "cash": round(self.cash, 6),
            "capital": round(self.capital, 6),
            "fees": round(self.fees, 6),
            "order_counter": self.order_counter,
            "realized_pnl": round(self.realized_pnl, 6),
            "orders": [
                {**o.__dict__} for o in self.orders[-100:]
            ],
            "positions": {c: {**p.__dict__} for c, p in self.positions.items()},
        }
        with open(STATE_FILE, "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)

    # ── Core: 模拟下单 ───────────────────────────────────────────

    def execute_order(self, coin: str, side: str, size: float, mid_px: float, order_id=None) -> SimOrder:
        """
        立即以中间价成交（taker），扣除手续费，更新持仓。
        """
        FEE_RATE = 0.0004  # 0.04% maker/taker fee
        self.order_counter += 1
        oid = order_id or f"SIM_{self.order_counter:06d}"

        filled_px = mid_px
        notional = size * filled_px
        fee = notional * FEE_RATE
        self.fees += fee

        pos = self.positions.get(coin, SimPosition())
        prev_size = pos.size

        if side == "buy":
            # 开多或加仓
            if prev_size >= 0:
                new_size = prev_size + size
                new_entry = (prev_size * pos.entry_px + size * filled_px) / new_size if new_size > 0 else 0
                pos.size = new_size
                pos.entry_px = new_entry
            else:
                # 平空: 先平掉现有空仓
                close_sz = min(abs(prev_size), size)
                closed_pnl = (pos.entry_px - filled_px) * close_sz
                self.cash += closed_pnl - fee
                self.realized_pnl += closed_pnl
                pos.size = prev_size + size  # 负数减小
                if abs(pos.size) < 1e-9:
                    pos.size = 0.0
                    pos.entry_px = 0.0
        else:  # sell
            if prev_size <= 0:
                # 开空或加空
                pos.size = prev_size - size
                if pos.entry_px == 0:
                    pos.entry_px = filled_px
            else:
                # 平多
                close_sz = min(prev_size, size)
                closed_pnl = (filled_px - pos.entry_px) * close_sz
                self.cash += closed_pnl - fee
                self.realized_pnl += closed_pnl
                pos.size = prev_size - size
                if abs(pos.size) < 1e-9:
                    pos.size = 0.0
                    pos.entry_px = 0.0

        self.positions[coin] = pos
        order = SimOrder(
            id=oid,
            coin=coin,
            side=side,
            size=size,
            px=filled_px,
            filled_px=filled_px,
            time=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.orders.append(order)
        self.save()
        return order

    # ── 持仓 & 净值 ──────────────────────────────────────────────

    def update_marks(self, mids: dict):
        """用最新中间价更新所有持仓的未实现盈亏"""
        total_unrealized = 0.0
        for coin, pos in self.positions.items():
            if coin in mids:
                try:
                    mark = float(mids[coin])
                    pos.unrealized_pnl = pos.pnl(mark)
                    total_unrealized += pos.unrealized_pnl
                except (ValueError, TypeError):
                    pass
        return total_unrealized

    def total_value(self, mids: dict) -> float:
        """总资产 = 现金 + 持仓市值 + 未实现盈亏"""
        mark_value = sum(
            p.size * float(mids.get(coin, p.entry_px))
            for coin, p in self.positions.items()
        )
        unreal = self.update_marks(mids)
        return self.cash + mark_value + unreal

    def equity(self, mids: dict) -> float:
        return self.total_value(mids) - self.capital

    def summary(self, mids: dict) -> dict:
        total = self.total_value(mids)
        return {
            "cash": round(self.cash, 4),
            "total_value": round(total, 4),
            "equity": round(self.equity(mids), 4),
            "unrealized_pnl": round(sum(p.unrealized_pnl for p in self.positions.values()), 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "fees": round(self.fees, 4),
            "order_count": self.order_counter,
            "positions": {
                coin: {
                    "size": round(p.size, 6),
                    "entry_px": round(p.entry_px, 6),
                    "mark_px": round(float(mids.get(coin, p.entry_px)), 6),
                    "unrealized_pnl": round(p.unrealized_pnl, 4),
                    "notional": round(abs(p.size * float(mids.get(coin, p.entry_px))), 4),
                }
                for coin, p in self.positions.items()
                if p.size != 0
            },
        }

    def reset(self):
        """重置模拟账户"""
        self.cash = self.capital
        self.positions = {}
        self.orders = []
        self.fees = 0.0
        self.order_counter = 0
        self.save()
