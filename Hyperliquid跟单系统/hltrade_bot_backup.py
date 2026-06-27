import json
import time
import signal
from datetime import datetime, date

from hltrade_core import get_user_fills, get_all_mids, calc_size, send_order, sign_order


class CopyBot:
    def __init__(self, config_path="config.json", live=False):
        self.config_path = config_path
        self.live = live
        self.running = False

        self.config = self._load_config(config_path)
        self.target_wallet = self.config["TARGET_WALLET"]
        self.copy_ratio = float(self.config["COPY_RATIO"])
        self.max_position = float(self.config["MAX_POSITION"])
        self.poll_interval = float(self.config["POLL_INTERVAL"])
        self.testnet = bool(self.config["TESTNET"])
        self.daily_loss_limit = float(self.config["DAILY_LOSS_LIMIT"])

        self.seen_fill_ids = set()
        self.positions = {}
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.daily_pnl = 0.0
        self.current_day = date.today()
        self.stop_reason = None

        self.max_slippage_bps = float(self.config.get("MAX_SLIPPAGE_BPS", 50))
        self.default_order_type = self.config.get("ORDER_TYPE", "market")
        self.account_address = self.config.get("ACCOUNT_ADDRESS")
        self.secret_key = self.config.get("SECRET_KEY")

        signal.signal(signal.SIGINT, self._handle_sigint)

    def _load_config(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _ts(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _log(self, msg):
        print(f"[{self._ts()}] {msg}", flush=True)

    def _handle_sigint(self, sig, frame):
        self._log("🛑 SIGINT received, shutting down gracefully...")
        self.running = False
        self.stop_reason = "SIGINT"

    def _reset_daily_pnl_if_needed(self):
        today = date.today()
        if today != self.current_day:
            self.current_day = today
            self.realized_pnl = 0.0
            self.unrealized_pnl = 0.0
            self.daily_pnl = 0.0
            self._log("📅 New day detected, daily PnL reset.")

    def _update_position_and_pnl(self, fill):
        coin = fill.get("coin") or fill.get("symbol") or fill.get("asset")
        side = str(fill.get("side", "")).lower()
        px = float(fill.get("px") or fill.get("price") or 0.0)
        sz = float(fill.get("sz") or fill.get("size") or fill.get("qty") or 0.0)

        if not coin or px <= 0 or sz <= 0 or side not in ("buy", "sell"):
            return

        pos = self.positions.get(
            coin,
            {"size": 0.0, "avg_price": 0.0, "realized_pnl": 0.0}
        )

        signed_sz = sz if side == "buy" else -sz
        cur_size = pos["size"]
        avg_price = pos["avg_price"]

        if cur_size == 0 or (cur_size > 0 and signed_sz > 0) or (cur_size < 0 and signed_sz < 0):
            new_size = cur_size + signed_sz
            if abs(new_size) > 0:
                if abs(cur_size) == 0:
                    new_avg = px
                else:
                    new_avg = ((abs(cur_size) * avg_price) + (abs(signed_sz) * px)) / abs(new_size)
            else:
                new_avg = 0.0
            pos["size"] = new_size
            pos["avg_price"] = new_avg
        else:
            closing_size = min(abs(cur_size), abs(signed_sz))
            if cur_size > 0:
                trade_pnl = (px - avg_price) * closing_size
            else:
                trade_pnl = (avg_price - px) * closing_size

            self.realized_pnl += trade_pnl
            pos["realized_pnl"] += trade_pnl

            new_size = cur_size + signed_sz
            pos["size"] = new_size
            if new_size == 0:
                pos["avg_price"] = 0.0
            elif (cur_size > 0 > new_size) or (cur_size < 0 < new_size):
                pos["avg_price"] = px

        self.positions[coin] = pos
        self.daily_pnl = self.realized_pnl + self.unrealized_pnl

    def _check_daily_loss_limit(self):
        if self.daily_pnl <= -abs(self.daily_loss_limit):
            self._log(f"🛑 Daily loss limit reached: {self.daily_pnl:.4f} <= -{abs(self.daily_loss_limit):.4f}")
            self.running = False
            self.stop_reason = "DAILY_LOSS_LIMIT"
            return True
        return False

    def _extract_fill_id(self, fill):
        return (
            fill.get("tid")
            or fill.get("tradeId")
            or fill.get("fill_id")
            or fill.get("id")
            or f"{fill.get('time')}-{fill.get('coin')}-{fill.get('side')}-{fill.get('px')}-{fill.get('sz')}"
        )

    def new_fills(self):
        fills = get_user_fills(self.target_wallet)
        fresh = []

        if not fills:
            return fresh

        for fill in fills:
            fill_id = self._extract_fill_id(fill)
            if fill_id in self.seen_fill_ids:
                continue
            self.seen_fill_ids.add(fill_id)
            fresh.append(fill)
            self._update_position_and_pnl(fill)

        if fresh:
            self._log(f"📥 New fills detected: {len(fresh)} | Daily PnL: {self.daily_pnl:.4f}")

        return fresh

    def size(self, fill):
        coin = fill.get("coin") or fill.get("symbol") or fill.get("asset")
        px = float(fill.get("px") or fill.get("price") or 0.0)
        source_sz = float(fill.get("sz") or fill.get("size") or fill.get("qty") or 0.0)
        side = str(fill.get("side", "")).lower()

        if not coin or px <= 0 or source_sz <= 0 or side not in ("buy", "sell"):
            return 0.0

        desired_sz = source_sz * self.copy_ratio

        try:
            computed_sz = calc_size(coin, desired_sz, px)
        except TypeError:
            computed_sz = calc_size(coin, desired_sz)
        except Exception:
            computed_sz = desired_sz

        current_pos = self.positions.get(coin, {}).get("size", 0.0)
        signed_order_sz = computed_sz if side == "buy" else -computed_sz
        projected_pos = current_pos + signed_order_sz

        if abs(projected_pos) > self.max_position:
            remaining = max(0.0, self.max_position - abs(current_pos))
            capped = min(abs(computed_sz), remaining)
            computed_sz = capped

        if computed_sz < 0:
            computed_sz = 0.0

        return float(computed_sz)

    def _slippage_ok(self, coin, ref_px):
        mids = get_all_mids()
        mid = mids.get(coin) if isinstance(mids, dict) else None
        if mid is None:
            self._log(f"⚠️ No mid price for {coin}, skipping slippage check.")
            return True

        mid = float(mid)
        ref_px = float(ref_px)
        if mid <= 0 or ref_px <= 0:
            return False

        slippage_bps = abs(mid - ref_px) / ref_px * 10000.0
        if slippage_bps > self.max_slippage_bps:
            self._log(
                f"⚠️ Slippage too high for {coin}: {slippage_bps:.2f} bps > {self.max_slippage_bps:.2f} bps"
            )
            return False
        return True

    def order_signal(self, fill):
        coin = fill.get("coin") or fill.get("symbol") or fill.get("asset")
        side = str(fill.get("side", "")).lower()
        ref_px = float(fill.get("px") or fill.get("price") or 0.0)
        order_sz = self.size(fill)

        if not coin or side not in ("buy", "sell") or ref_px <= 0 or order_sz <= 0:
            self._log(f"🤖 Invalid order signal skipped: {fill}")
            return None

        if self._check_daily_loss_limit():
            return None

        if not self._slippage_ok(coin, ref_px):
            self._log(f"🤖 Signal blocked by slippage filter: {side.upper()} {coin} size={order_sz}")
            return None

        msg = f"🤖 {('LIVE' if self.live else 'MONITOR')} SIGNAL | {side.upper()} {coin} | size={order_sz:.6f} | ref={ref_px:.6f}"
        self._log(msg)

        if not self.live:
            return {
                "coin": coin,
                "side": side,
                "size": order_sz,
                "ref_px": ref_px,
                "mode": "monitor",
            }

        try:
            order_req = {
                "coin": coin,
                "is_buy": side == "buy",
                "size": order_sz,
                "order_type": self.default_order_type,
                "testnet": self.testnet,
            }

            try:
                signed = sign_order(order_req, self.secret_key)
                resp = send_order(signed)
            except TypeError:
                resp = send_order(
                    coin=coin,
                    is_buy=(side == "buy"),
                    size=order_sz,
                    order_type=self.default_order_type,
                    testnet=self.testnet,
                )

            self._log(f"✅ Order sent: {coin} {side.upper()} size={order_sz:.6f} | resp={resp}")
            return resp
        except Exception as e:
            self._log(f"❌ Order failed: {coin} {side.upper()} size={order_sz:.6f} | error={e}")
            return None

    def run(self):
        self.running = True
        self._log(
            f"🚀 CopyBot started | mode={'LIVE' if self.live else 'MONITOR'} | target={self.target_wallet} | testnet={self.testnet}"
        )

        try:
            bootstrap_fills = get_user_fills(self.target_wallet) or []
            for fill in bootstrap_fills:
                self.seen_fill_ids.add(self._extract_fill_id(fill))
            self._log(f"🧩 Bootstrapped {len(self.seen_fill_ids)} existing fills.")
        except Exception as e:
            self._log(f"⚠️ Bootstrap failed: {e}")

        while self.running:
            try:
                self._reset_daily_pnl_if_needed()

                if self._check_daily_loss_limit():
                    break

                fills = self.new_fills()
                for fill in fills:
                    if not self.running:
                        break
                    self.order_signal(fill)

            except Exception as e:
                self._log(f"⚠️ Main loop error: {e}")

            time.sleep(self.poll_interval)

        self._log(f"👋 CopyBot stopped. reason={self.stop_reason or 'NORMAL_EXIT'}")