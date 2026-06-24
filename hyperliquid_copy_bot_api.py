#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hyperliquid 跟单机器人 v3.1
===========================
修复: 监控端强制主网，交易端按TESTNET切换
依赖: requests (pip install requests)
运行: python3 hyperliquid_copy_bot_api.py [分钟数]
"""

import requests, time, json, logging, sys
from datetime import datetime
from typing import Optional

# ========== 配置区 (修改这里) ==========
TARGET_WALLET  = "0x3Db8f7bC6D744bEAE458207C85F46B5d0349e5ef"
COPY_RATIO     = 0.10      # 跟单比例 10%
MAX_POSITION   = 100.0     # 单笔最大仓位$
POLL_INTERVAL  = 15       # 秒
TESTNET        = True      # True=模拟/测试网下单 False=主网真单

INFO_URL   = "https://api.hyperliquid.xyz/info"          # 监控永远用主网
TRADE_URL  = "https://api.hyperliquid-testnet.xyz" if TESTNET else "https://api.hyperliquid.xyz"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("copybot")

# ========== 核心函数 ==========
def info_req(payload: dict, timeout=15, retries=3) -> Optional[dict]:
    """查链上数据（固定主网），自动重试限流"""
    for attempt in range(retries):
        try:
            r = requests.post(INFO_URL, json=payload, timeout=timeout)
            if r.status_code == 429:
                wait = 2 ** attempt  # 指数退避: 1s, 2s, 4s
                logger.warning(f"⏳ API 限流，等待 {wait}s (重试 {attempt+1}/{retries})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            logger.warning(f"⏱ 请求超时 (重试 {attempt+1}/{retries})")
            if attempt < retries - 1:
                time.sleep(1)
        except Exception as e:
            logger.error(f"查询失败: {e}")
            return None
    logger.error("查询失败: 已达最大重试次数")
    return None

def get_all_mids() -> dict:
    r = info_req({"type": "allMids"})
    return r if isinstance(r, dict) else {}

def get_user_fills(wallet: str) -> list:
    r = info_req({"type": "userFills", "user": wallet})
    # Hyperliquid userFills 直接返回 fills 数组，非 dict 包装
    return r if isinstance(r, list) else []

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

    def refresh_prices(self):
        self.prices = get_all_mids()
        logger.info(f"📡 价格 {len(self.prices)} 币种 OK")

    def fetch_fills(self) -> list:
        return get_user_fills(self.target)

    def new_fills(self, fills: list) -> list:
        out = []
        for f in fills:
            fid = f.get("tid") or f.get("fillId") or f"{f['coin']}_{f['time']}_{f['sz']}"
            if fid not in self.seen:
                self.seen.add(fid)
                out.append(f)
        # 按时间正序返回所有新成交，不限制数量
        return sorted(out, key=lambda x: x["time"])

    def size(self, fill: dict) -> dict:
        sz, px = float(fill["sz"]), float(fill["px"])
        val    = sz * px
        cap    = min(val * self.ratio, self.max_pos)
        coin   = fill["coin"]
        cur    = float(self.prices.get(coin, px))
        return {"coin": coin, "sz": cap/cur if cur else 0, "value": cap}

    def order_signal(self, fill: dict, o: dict):
        net   = "🟡 TESTNET" if TESTNET else "🟠 MAINNET"
        side  = "开空" if fill["side"] == "S" else "开多"
        logger.info(f"  🤖 跟单 → {side} {o['coin']} {o['sz']:.4f} (≈{fmt(o['value'])}) | {net}")

    def run(self, dur=0):
        self.running = True
        mode = "🟡 测试网" if TESTNET else "🟠 主网"
        logger.info(f"\n{'='*50}")
        logger.info(f"  Hyperliquid 跟单机器人 v3.1")
        logger.info(f"  监控: 主网 | 下单: {mode}")
        logger.info(f"  目标: {self.target[:10]}...")
        logger.info(f"  跟单: {self.ratio*100:.0f}% | 上限: {fmt(self.max_pos)}")
        logger.info(f"{'='*50}\n")

        self.refresh_prices()
        hist = self.fetch_fills()
        logger.info(f"📊 历史成交: {len(hist)} 笔 (已跳过)")
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
                        self.order_signal(f, self.size(f))
                else:
                    logger.info(f"  ✓ 无新成交")
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
