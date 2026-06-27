import json
import time
import signal
from datetime import datetime, date

from hltrade_core import get_user_fills, get_all_mids, get_clearinghouse_state, calc_size, send_order, sign_order
from sim_exchange import SimExchange

# Absolute path to working directory
WORK_DIR = "/app/data/所有对话/主对话"
STATE_FILE = f"{WORK_DIR}/bot_state.json"
MODE_FILE = f"{WORK_DIR}/mode_switch.json"


class CopyBot:
    def __init__(self, config_path="config.json", live=False, simulate=False):
        self.config_path = config_path
        self.live = live
        self.simulate = simulate
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

        # --- new tracking fields ---
        self.recent_fills = []      # last 20 fills for dashboard
        self.total_fills = 0
        self.copy_trades = 0
        self.errors = 0
        self.start_time = None
        # Simulated exchange for SIMULATE mode
        self.sim_exchange = SimExchange()
        # ---------------------------

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
            price_map = get_all_mids() or {}
            result = calc_size(fill, price_map, self.copy_ratio, self.max_position)
            if isinstance(result, dict):
                computed_sz = float(result.get("size", desired_sz) or desired_sz)
            else:
                computed_sz = float(result)
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
            # ── SIMULATE mode: 模拟成交 ──────────────────────────────
            if self.simulate:
                mids = get_all_mids() or {}
                mid_px = float(mids.get(coin, ref_px)) if coin in mids else ref_px
                sim_order = self.sim_exchange.execute_order(
                    coin=coin,
                    side=side,
                    size=order_sz,
                    mid_px=mid_px,
                )
                self.copy_trades += 1
                self._log(
                    f"🎭 SIM FILLED | {sim_order.coin} {sim_order.side.upper()} "
                    f"size={sim_order.size:.6f} @ {sim_order.filled_px:.6f} | "
                    f"realized_pnl={sim_order.realized_pnl:.4f}"
                )
                return {"mode": "simulate", "sim_order": sim_order.__dict__}
            # ── MONITOR mode: 只返回信号 ──────────────────────────────
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
            self.errors += 1
            return None

    # ------------------------------------------------------------------
    # State file writing — called every poll cycle
    # ------------------------------------------------------------------
    def _build_position_snapshot(self):
        """Return positions dict with mark_px from current mids."""
        mids = get_all_mids() or {}
        snapshot = {}
        for coin, pos in self.positions.items():
            size = pos.get("size", 0.0)
            if size == 0.0:
                continue
            entry = pos.get("avg_price", 0.0)
            mid = mids.get(coin)
            if mid is not None:
                try:
                    mid = float(mid)
                    unrealized = (mid - entry) * size if size > 0 else (entry - mid) * abs(size)
                except (ValueError, TypeError):
                    unrealized = 0.0
            else:
                unrealized = 0.0
            snapshot[coin] = {
                "size": size,
                "entry_px": entry,
                "mark_px": float(mid) if mid is not None else 0.0,
                "unrealized_pnl": unrealized,
            }
        return snapshot

    def _build_fill_record(self, fill):
        return {
            "id": self._extract_fill_id(fill),
            "side": str(fill.get("side", "")).upper(),
            "symbol": fill.get("coin") or fill.get("symbol") or fill.get("asset", ""),
            "size": float(fill.get("sz") or fill.get("size") or fill.get("qty") or 0.0),
            "price": float(fill.get("px") or fill.get("price") or 0.0),
            "time": fill.get("time") or datetime.now().isoformat(),
        }

    def _write_state(self, recent_new_fills=None):
        """Write current bot state to bot_state.json."""
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        total_pnl = self.realized_pnl + self.unrealized_pnl

        # Build recent_fills list (up to 20), prepend new fills
        entries = []
        if recent_new_fills:
            for f in reversed(recent_new_fills):
                entries.append(self._build_fill_record(f))
        entries.extend(self.recent_fills)
        entries = entries[:20]
        self.recent_fills = entries

        # ── Fetch real positions from Hyperliquid clearinghouse ──────────────
        real_positions = {}
        try:
            ch_state = get_clearinghouse_state(self.target_wallet)
            asset_positions = ch_state.get("assetPositions") or []
            mids = get_all_mids() or {}
            for ap in asset_positions:
                pos = ap.get("position") or {}
                coin = pos.get("coin")
                if not coin:
                    continue
                szi = float(pos.get("szi", 0) or 0)
                if szi == 0:
                    continue
                entry_px = float(pos.get("entryPx", 0) or 0)
                unrealized_pnl = float(pos.get("unrealizedPnl", 0) or 0)
                # Try to get mark price from mids, else derive from positionValue
                mark_px = None
                if coin in mids:
                    try:
                        mark_px = float(mids[coin])
                    except (ValueError, TypeError):
                        pass
                if mark_px is None:
                    pos_value = float(pos.get("positionValue", 0) or 0)
                    if pos_value != 0 and szi != 0:
                        mark_px = abs(pos_value / szi)
                    else:
                        mark_px = entry_px
                real_positions[coin] = {
                    "size": szi,
                    "entry_px": entry_px,
                    "mark_px": mark_px,
                    "unrealized_pnl": unrealized_pnl,
                }
        except Exception as e:
            print(f"[{now}] ⚠️ Failed to fetch clearinghouse state: {e}", flush=True)
            # Fallback: use tracked positions
            real_positions = self._build_position_snapshot()
        # ─────────────────────────────────────────────────────────────────────

        state = {
            "updated_at": now,
            "mode": "SIMULATE" if self.simulate else ("LIVE" if self.live else "MONITOR"),
            "live": self.live,
            "target_wallet": self.target_wallet,
            "positions": real_positions,
            "recent_fills": entries,
            "pnl": {
                "daily": round(self.daily_pnl, 4),
                "total": round(total_pnl, 4),
            },
            "stats": {
                "total_fills": self.total_fills,
                "copy_trades": self.copy_trades,
                "errors": self.errors,
            },
        }

        # ── Simulated account state (SIMULATE mode) ──────────────────────
        if self.simulate:
            mids = get_all_mids() or {}
            state["sim_account"] = self.sim_exchange.summary(mids)
            state["sim_account"]["mode"] = "SIMULATE"
        # ─────────────────────────────────────────────────────────────────

        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[{now}] ⚠️ Failed to write state file: {e}", flush=True)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self):
        self.running = True
        self.start_time = time.time()
        mode_str = "LIVE" if self.live else ("SIMULATE" if self.simulate else "MONITOR")
        self._log(
            f"🚀 CopyBot started | mode={mode_str} | target={self.target_wallet[:20]}... | testnet={self.testnet}"
        )

        try:
            bootstrap_fills = get_user_fills(self.target_wallet) or []
            for fill in bootstrap_fills:
                fid = self._extract_fill_id(fill)
                self.seen_fill_ids.add(fid)
                self.total_fills += 1
            self._log(f"🧩 Bootstrapped {len(self.seen_fill_ids)} existing fills (history).")
            if self.simulate:
                self._log(f"🎭 Simulate mode: capital=${self.sim_exchange.capital:.2f} | reset={self.simulate}")
        except Exception as e:
            self._log(f"⚠️ Bootstrap failed: {e}")

        while self.running:
            try:
                self._reset_daily_pnl_if_needed()

                if self._check_daily_loss_limit():
                    break

                fresh = self.new_fills()
                for fill in fresh:
                    self.total_fills += 1
                    if not self.running:
                        break
                    resp = self.order_signal(fill)
                    if resp is not None:
                        self.copy_trades += 1

                # Write state file every poll cycle
                self._write_state(recent_new_fills=fresh)

            except Exception as e:
                self.errors += 1
                self._log(f"⚠️ Main loop error: {e}")

            time.sleep(self.poll_interval)

        # Final state write on exit
        self._write_state()
        self._log(f"👋 CopyBot stopped. reason={self.stop_reason or 'NORMAL_EXIT'}")
