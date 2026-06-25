#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hyperliquid 跟单机器人 v3.2
===========================
新增: 实盘自动下单 + 自动平仓
依赖: requests, eth_account (pip install requests eth_account)
运行: python3 hyperliquid_copy_bot_api.py [分钟数]
"""

import requests, time, json, logging, sys, msgpack
from datetime import datetime
from typing import Optional
from eth_account import Account
from eth_account.messages import encode_typed_data
from Crypto.Hash import keccak as keccak256
from hyperliquid.utils.signing import float_to_usd_int

# ========== 配置区 (修改这里) ==========
TARGET_WALLET  = "0x3Db8f7bC6D744bEAE458207C85F46B5d0349e5ef"
COPY_RATIO     = 0.10       # 跟单比例 10%
MAX_POSITION   = 100.0      # 单笔最大仓位$
POLL_INTERVAL  = 15         # 秒
TESTNET        = False      # True=模拟/测试网下单 False=主网真单

INFO_URL  = "https://api.hyperliquid.xyz/info"
TRADE_URL = "https://api.hyperliquid-testnet.xyz" if TESTNET else "https://api.hyperliquid.xyz"

# ========== 主钱包配置 ==========
import os
API_WALLET_SECRET_KEY = os.getenv("HL_SECRET_KEY") or "0x469eab8d26228a43fda248c3eb75e66c52b00a9cae44baa79e6a9c4cbc907b36"
WALLET_ADDRESS = "0xdAEb07e164D788CB14DbFfa7581170f9a3EB08a5"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

# ========== eth_account 签名 ==========
from eth_account import Account
# from eth_account.messages import encode_defunct, encode_structured_message
import hashlib

def sign_action(action: dict, nonce: int) -> str:
    """
    Hyperliquid EIP-712 签名 (SDK 正确格式)
    action: SDK格式的 order action (包含orders列表)
    nonce: 时间戳ms
    返回: 16进制签名字符串
    """
    # 1. keccak(msgpack(action) + nonce.to_bytes(8,"big") + b"\x00")
    packed = msgpack.packb(action)
    preimage = packed + nonce.to_bytes(8, "big") + b"\x00"
    h = keccak256.new(digest_bits=256)
    h.update(preimage)
    hash_bytes = h.digest()

    # 2. phantom_agent = {source: "a", connectionId: hash_bytes}
    phantom_agent = {"source": "a", "connectionId": hash_bytes}

    # 3. EIP-712 payload (domain: Exchange, chainId=1337)
    payload = {
        "domain": {
            "chainId": 1337,
            "name": "Exchange",
            "verifyingContract": "0x0000000000000000000000000000000000000000",
            "version": "1"
        },
        "types": {
            "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"}
            ],
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"}
            ]
        },
        "primaryType": "Agent",
        "message": phantom_agent
    }

    # 4. 签名
    acct = Account.from_key(API_WALLET_SECRET_KEY)
    typed_data = encode_typed_data(full_message=payload)
    sig = acct.sign_message(typed_data)
    return sig.signature.hex()

# ========== API 请求 ==========
def info_req(payload: dict, timeout=15, retries=3) -> Optional[dict]:
    for i in range(retries):
        try:
            r = requests.post(INFO_URL, json=payload, timeout=timeout)
            return r.json()
        except Exception as e:
            logger.warning(f"请求失败({i+1}): {e}")
            time.sleep(2)
    return None

def trade_req(payload: dict, timeout=15, retries=3) -> Optional[dict]:
    for i in range(retries):
        try:
            r = requests.post(TRADE_URL, json=payload, timeout=timeout)
            return r.json()
        except Exception as e:
            logger.warning(f"交易请求失败({i+1}): {e}")
            time.sleep(2)
    return None

def get_all_mids() -> dict:
    r = info_req({"type": "allMids"})
    return r if isinstance(r, dict) else {}

def get_user_fills(wallet: str) -> list:
    r = info_req({"type": "userFills", "user": wallet})
    return r if isinstance(r, list) else []

def get_clearinghouse_state(wallet: str) -> dict:
    r = info_req({"type": "clearinghouseState", "user": wallet})
    return r if isinstance(r, dict) else {}

def fmt(v: float) -> str:
    return f"${v:,.2f}"

def summarize(fill: dict) -> str:
    c, s, sz, px = fill["coin"], fill["side"], float(fill["sz"]), float(fill["px"])
    ts   = datetime.fromtimestamp(fill["time"]/1000).strftime("%m-%d %H:%M")
    pnl  = f" | PnL:{fmt(float(fill['closedPnl']))}" if fill.get("closedPnl") else ""
    return f"{ts} {'🔴S' if s=='S' else '🟢B'} {c} {sz:.4f}@{fmt(px)}{pnl}"

# ========== 跟单引擎 ==========
class CopyBot:
    def __init__(self, target: str, ratio: float, max_pos: float, poll: int):
        self.target  = target
        self.ratio   = ratio
        self.max_pos = max_pos
        self.poll    = poll
        self.seen    = set()
        self.prices  = {}
        self.running = False
        self.acct    = Account.from_key(API_WALLET_SECRET_KEY)
        # 追踪已开的仓位 {coin: {sz, entry_px}}
        self.open_positions = {}
        # coin name → asset ID 映射 (从 meta 加载)
        self.coin_to_asset = {}
        self._load_asset_map()

    def _load_asset_map(self):
        """从 meta 端点加载 coin→asset ID 映射"""
        try:
            r = info_req({"type": "meta"})
            if isinstance(r, dict):
                for a in r.get("universe", []):
                    self.coin_to_asset[a["name"]] = a["szDecimals"]  # szDecimals 是 index
                logger.info(f"📊 已加载 {len(self.coin_to_asset)} 个资产映射")
        except Exception as e:
            logger.warning(f"加载资产映射失败: {e}")

    def refresh_prices(self):
        self.prices = get_all_mids()
        logger.info(f"📡 价格 {len(self.prices)} 币种 OK")

    def fetch_fills(self) -> list:
        r = info_req({"type": "userFills", "user": self.target})
        if not isinstance(r, list):
            return []
        return sorted(r, key=lambda x: x["time"])

    def new_fills(self, fills: list) -> list:
        out = []
        for f in fills:
            fid = f.get("tid") or f.get("fillId") or f"{f['coin']}_{f['time']}_{f['sz']}"
            if fid not in self.seen:
                out.append(f)
        return out

    def calc_size(self, fill: dict) -> dict:
        sz, px = float(fill["sz"]), float(fill["px"])
        val    = sz * px
        cap    = min(val * self.ratio, self.max_pos)
        coin   = fill["coin"]
        cur    = float(self.prices.get(coin, px))
        return {"coin": coin, "sz": cap/cur if cur else 0, "value": cap}

    def sync_positions(self):
        """同步自己API钱包的当前仓位"""
        state = get_clearinghouse_state(f"0x{self.acct.address.lower()}")
        self.open_positions = {}
        for ap in (state.get("assetPositions") or []):
            pos = ap.get("position") or {}
            coin = pos.get("coin")
            sz = float(pos.get("szi") or 0)
            entry = float(pos.get("entryPx") or 0)
            if abs(sz) > 0.0001 and coin:
                self.open_positions[coin] = {"sz": sz, "entry_px": entry}

    def place_order(self, coin: str, side: str, sz: float, reduce_only: bool = False) -> bool:
        """
        下单或平仓
        side: 'B' = Buy/Long, 'S' = Sell/Short
        如果 sz=0 且 reduce_only=True，表示平仓
        """
        if not TESTNET:
            # 实盘下单
            action = {
                "type": "order",
                "order": {
                    "coin": coin,
                    "side": side,
                    "sz": sz,
                    "limit_px": float(self.prices.get(coin, 0)),
                    "order_type": {"type": "Market"},
                    "reduce_only": reduce_only,
                    "mmp": False
                }
            }
            sig = sign_action(action)
            nonce = int(time.time() * 1000)
            payload = {
                "action": action,
                "nonce": nonce,
                "signature": sig
            }
            resp = trade_req(payload)
            if resp and resp.get("status") == "ok":
                logger.info(f"  ✅ {'平仓' if reduce_only else '开单'}成功: {coin} {side} {sz}")
                return True
            else:
                logger.warning(f"  ❌ 下单失败: {resp}")
                return False
        else:
            # 测试网模式只打印
            logger.info(f"  🟡 [TESTNET] {'平仓' if reduce_only else '开单'}: {coin} {side} {sz}")
            return True

    def order_signal(self, fill: dict, o: dict, fill_side: str):
        """跟单: 目标开仓 -> 我也开仓"""
        coin = o["coin"]
        my_sz = o["sz"]
        net   = "🟡 TESTNET" if TESTNET else "🟠 MAINNET"
        side_str = "开多" if fill_side == "B" else "开空"

        if my_sz < 0.0001:
            logger.info(f"  ⏭ 仓位太小跳过: {coin}")
            return

        # 检查是否已开同向仓位 -> 叠加（暂时先跳过，做独立仓位）
        logger.info(f"  🤖 跟单 → {side_str} {coin} {my_sz:.4f} (≈{fmt(o['value'])}) | {net}")
        self.place_order(coin, fill_side, my_sz)

    def handle_close(self, fill: dict):
        """目标平仓 -> 我也平仓"""
        coin = fill["coin"]
        my_pos = self.open_positions.get(coin)
        if not my_pos:
            return
        my_sz = abs(my_pos["sz"])
        if my_sz < 0.0001:
            return
        # 平仓方向和开仓方向相反
        close_side = "S" if my_pos["sz"] > 0 else "B"
        net = "🟡 TESTNET" if TESTNET else "🟠 MAINNET"
        logger.info(f"  🏁 跟单平仓 {coin} {my_sz:.4f} | {net}")
        self.place_order(coin, close_side, my_sz, reduce_only=True)

    def run(self, dur=0):
        self.running = True
        mode = "🟡 测试网" if TESTNET else "🟠 主网"
        api_addr = f"0x{self.acct.address.lower()}"
        logger.info(f"\n{'='*50}")
        logger.info(f"  Hyperliquid 跟单机器人 v3.2")
        logger.info(f"  监控: 主网 | 下单: {mode}")
        logger.info(f"  目标: {self.target[:10]}...")
        logger.info(f"  我方: {api_addr[:10]}...")
        logger.info(f"  跟单: {self.ratio*100:.0f}% | 上限: {fmt(self.max_pos)}")
        logger.info(f"{'='*50}\n")

        self.refresh_prices()
        hist = self.fetch_fills()
        logger.info(f"📊 历史成交: {len(hist)} 笔 (已跳过)")

        # 同步初始仓位
        self.sync_positions()
        logger.info(f"📍 当前持仓: {list(self.open_positions.keys()) or '无'}")

        for f in hist:
            fid = f.get("tid") or f.get("fillId") or f"{f['coin']}_{f['time']}_{f['sz']}"
            self.seen.add(fid)

        start, n = time.time(), 0
        try:
            while self.running:
                n += 1
                logger.info(f"\n🔄 第{n}轮 {datetime.now().strftime('%H:%M:%S')}")
                self.refresh_prices()
                fills = self.fetch_fills()
                new   = self.new_fills(fills)

                if new:
                    logger.info(f"🆕 {len(new)}笔新成交!")
                    for f in new:
                        logger.info(f"  → {summarize(f)}")
                        o = self.calc_size(f)
                        fill_side = f["side"]  # 'B' or 'S'
                        self.order_signal(f, o, fill_side)
                        self.sync_positions()
                else:
                    logger.info(f"  ✓ 无新成交")

                # 检查目标平仓 -> 我也平
                latest = fills[-3:] if fills else []
                for f in latest:
                    fid = f.get("tid") or f.get("fillId") or f"{f['coin']}_{f['time']}_{f['sz']}"
                    if fid in self.seen:
                        continue
                    # 有 closedPnl 说明是平仓单
                    if f.get("closedPnl") and float(f["closedPnl"]) != 0:
                        logger.info(f"  🏁 检测到目标平仓: {summarize(f)}")
                        self.handle_close(f)
                        self.sync_positions()

                if dur > 0 and (time.time()-start) >= dur*60:
                    logger.info("⏹ 到达时长，停止"); break
                time.sleep(self.poll)
        except KeyboardInterrupt:
            logger.info("\n⏹ 手动停止")
        self.running = False

if __name__ == "__main__":
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if len(sys.argv) > 1:
        print(f"⏱ 模式: {'永久' if dur==0 else f'{dur}分钟'}")
    CopyBot(TARGET_WALLET, COPY_RATIO, MAX_POSITION, POLL_INTERVAL).run(dur)
