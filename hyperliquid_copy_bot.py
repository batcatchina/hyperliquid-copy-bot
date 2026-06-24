#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hyperliquid 跟单机器人 v2.0 (移动端优化版)
==========================================
支持 Termux (Android) / Pythonista (iOS)

依赖安装: pip install hyperliquid-python requests python-dotenv

配置文件: config.json (编辑方便)
运行命令: python hyperliquid_copy_bot.py
"""

import os
import sys
import time
import json
import logging
from datetime import datetime
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from pathlib import Path

# 移动端兼容: 优先尝试 python-dotenv，失败则使用简单的 env 解析
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False
    # 简单 .env 解析器
    def load_dotenv():
        env_path = Path('config.env')
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

# 尝试导入 hyperliquid
try:
    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange
    from hyperliquid.api_types import OrderRequest, OrderType, Side, TimeInForce
    HYPERLIQUID_AVAILABLE = True
except ImportError:
    HYPERLIQUID_AVAILABLE = False

# ============================================================
# 配置加载 (支持 config.json 和环境变量)
# ============================================================

def get_config_path() -> Path:
    """获取配置文件路径"""
    return Path(__file__).parent / 'config.json'

def get_env_path() -> Path:
    """获取 .env 文件路径"""
    return Path(__file__).parent / 'config.env'

def load_config() -> Dict[str, Any]:
    """加载配置 (json 优先，env 兜底)"""
    config = {}
    config_path = get_config_path()
    env_path = get_env_path()
    
    # 1. 加载 .env (如果存在)
    if env_path.exists():
        load_dotenv()
        print(f"[配置] 已加载 .env 文件")
    
    # 2. 加载 config.json
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            print(f"[配置] 已加载 config.json")
        except Exception as e:
            print(f"[警告] config.json 解析失败: {e}")
    
    # 3. 环境变量覆盖 (最高优先级)
    env_map = {
        'TARGET_WALLET': 'target_wallet',
        'COPY_RATIO': 'copy_ratio',
        'MAX_POSITION_USD': 'max_position_usd',
        'POLL_INTERVAL': 'poll_interval',
        'SLIPPAGE': 'slippage',
        'MAX_LEVERAGE': 'max_leverage',
        'IS_TESTNET': 'is_testnet',
        'WALLET_ADDRESS': 'wallet_address',
        'LOG_LEVEL': 'log_level'
    }
    
    for env_key, config_key in env_map.items():
        if env_key in os.environ:
            value = os.environ[env_key]
            # 类型转换
            if config_key in ['copy_ratio', 'slippage']:
                config[config_key] = float(value)
            elif config_key in ['max_position_usd']:
                config[config_key] = float(value)
            elif config_key in ['poll_interval', 'max_leverage']:
                config[config_key] = int(value)
            elif config_key == 'is_testnet':
                config[config_key] = value.lower() in ('true', '1', 'yes')
            else:
                config[config_key] = value
    
    return config

# 加载配置
CONFIG = load_config()

# ============================================================
# 配置参数 (从 config.json 或环境变量读取)
# ============================================================

TARGET_WALLET = CONFIG.get('target_wallet', '0x3Db8f7bC6D744bEAE458207C85F46B5d0349e5ef')
COPY_RATIO = CONFIG.get('copy_ratio', 0.1)
MAX_POSITION_USD = CONFIG.get('max_position_usd', 100.0)
POLL_INTERVAL = CONFIG.get('poll_interval', 15)
SLIPPAGE = CONFIG.get('slippage', 0.005)
MAX_LEVERAGE = CONFIG.get('max_leverage', 5)
IS_TESTNET = CONFIG.get('is_testnet', True)
WALLET_ADDRESS = CONFIG.get('wallet_address', None)
LOG_LEVEL = CONFIG.get('log_level', 'INFO')

# ============================================================
# 日志配置 (移动端优化: 简洁输出)
# ============================================================

class MobileFormatter(logging.Formatter):
    """移动端优化的日志格式"""
    
    # 简洁的时间格式
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
    
    def format(self, record):
        # 简洁日志格式: [时间] 级别 消息
        return f"[{self.formatTime(record)}] {record.levelname[0]} {record.getMessage()}"

def setup_logging():
    """配置日志"""
    logger = logging.getLogger('copy_bot')
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    
    # 清除已有 handlers
    logger.handlers.clear()
    
    # 控制台输出
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(MobileFormatter())
    logger.addHandler(console)
    
    # 文件输出 (移动端也保留)
    try:
        log_file = Path(__file__).parent / 'copy_bot.log'
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        logger.addHandler(file_handler)
    except:
        pass
    
    return logger

logger = setup_logging()

# ============================================================
# 配置文件模板生成
# ============================================================

def create_default_config():
    """创建默认配置文件"""
    config_path = get_config_path()
    env_path = get_env_path()
    
    default_wallet = '0x3Db8f7bC6D744bEAE458207C85F46B5d0349e5ef'
    
    if not config_path.exists():
        default_config = {
            "target_wallet": default_wallet,
            "copy_ratio": 0.1,
            "max_position_usd": 100.0,
            "poll_interval": 15,
            "slippage": 0.005,
            "max_leverage": 5,
            "is_testnet": True,
            "wallet_address": "",
            "log_level": "INFO"
        }
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)
        print(f"[配置] 已创建默认 config.json")
    
    if not env_path.exists():
        env_content = f'''# Hyperliquid 跟单机器人配置文件
# 环境变量格式 (优先级高于 config.json)

# 目标钱包地址
TARGET_WALLET={default_wallet}

# 跟单比例 (0.1 = 10%)
COPY_RATIO=0.1

# 单笔最大金额 (USD)
MAX_POSITION_USD=100

# 轮询间隔 (秒)
POLL_INTERVAL=15

# 滑点 (0.005 = 0.5%)
SLIPPAGE=0.005

# 最大杠杆
MAX_LEVERAGE=5

# 测试网 (true/false)
IS_TESTNET=true

# 你的钱包地址 (留空则自动获取)
WALLET_ADDRESS=

# 日志级别 (DEBUG/INFO/WARNING/ERROR)
LOG_LEVEL=INFO
'''
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(env_content)
        print(f"[配置] 已创建默认 config.env")

# ============================================================
# 辅助类
# ============================================================

@dataclass
class Position:
    """持仓信息"""
    coin: str
    size: float
    entry_price: float
    unrealized_pnl: float
    
    @property
    def direction(self) -> str:
        return "多" if self.size > 0 else "空"
    
    def __str__(self) -> str:
        return f"{self.coin} {self.direction} {abs(self.size):.3f} @ {self.entry_price:.2f} (P: {self.unrealized_pnl:+.2f})"

@dataclass
class TradeSignal:
    """交易信号"""
    coin: str
    action: str  # OPEN / CLOSE / ADJUST
    target_size: float
    reason: str

# ============================================================
# Hyperliquid API
# ============================================================

class HyperliquidClient:
    """Hyperliquid API 客户端"""
    
    def __init__(self, is_testnet: bool = True, wallet_address: Optional[str] = None):
        self.is_testnet = is_testnet
        self.network = "testnet" if is_testnet else "mainnet"
        self.wallet_address = wallet_address or ""
        
        if not HYPERLIQUID_AVAILABLE:
            logger.error("hyperliquid-python 未安装")
            logger.info("运行: pip install hyperliquid-python")
            return
        
        try:
            self.info = Info(self.network)
            self.exchange = Exchange(self.network, self.wallet_address)
            logger.info(f"✓ 连接成功: {'测试网' if is_testnet else '主网'}")
        except Exception as e:
            logger.error(f"连接失败: {e}")
            self.info = None
            self.exchange = None
    
    def get_positions(self, wallet: str) -> Dict[str, Position]:
        """获取持仓"""
        if not self.info:
            return {}
        
        try:
            account = self.info.account(wallet)
            positions = {}
            
            for asset_pos in account.get('assetPositions', []):
                pos = asset_pos.get('position', {})
                size = float(pos.get('szi', 0))
                
                if size != 0:
                    coin = pos.get('coin', 'UNKNOWN')
                    positions[coin] = Position(
                        coin=coin,
                        size=size,
                        entry_price=float(pos.get('entryPx', 0)),
                        unrealized_pnl=float(pos.get('unrealizedPnl', 0))
                    )
            
            return positions
        except Exception as e:
            logger.debug(f"获取持仓失败: {e}")
            return {}
    
    def get_my_positions(self) -> Dict[str, Position]:
        """获取我的持仓"""
        if not self.wallet_address:
            return {}
        return self.get_positions(self.wallet_address)
    
    def place_order(self, coin: str, side: Side, size: float) -> bool:
        """下单"""
        if not self.exchange:
            return False
        
        try:
            order_req = OrderRequest(
                coin=coin,
                side=side,
                sz=size,
                ordType=OrderType.Market,
                timeInForce=TimeInForce.IOC,
                slippage=SLIPPAGE
            )
            
            result = self.exchange.order(order_req)
            
            if result and result.get('status') == 'ok':
                emoji = "▲" if side == Side.BUY else "▼"
                logger.info(f"✓ {emoji} {coin} {abs(size):.3f}")
                return True
            else:
                logger.error(f"✗ 下单失败: {result}")
                return False
                
        except Exception as e:
            logger.error(f"✗ {coin} 下单异常: {e}")
            return False
    
    def close_position(self, coin: str, size: float) -> bool:
        """平仓"""
        if not self.exchange:
            return False
        
        side = Side.SELL if size > 0 else Side.BUY
        return self.place_order(coin, side, abs(size))

# ============================================================
# 跟单机器人
# ============================================================

class CopyTradingBot:
    """跟单机器人"""
    
    def __init__(
        self,
        target_wallet: str,
        copy_ratio: float,
        max_position_usd: float,
        poll_interval: int,
        slippage: float,
        max_leverage: int,
        is_testnet: bool,
        wallet_address: Optional[str] = None
    ):
        self.target_wallet = target_wallet
        self.copy_ratio = copy_ratio
        self.max_position_usd = max_position_usd
        self.poll_interval = poll_interval
        self.max_leverage = max_leverage
        self.is_testnet = is_testnet
        
        # 状态
        self.last_positions: Dict[str, Position] = {}
        self.loop_count = 0
        
        # 初始化客户端
        self.client = HyperliquidClient(is_testnet, wallet_address)
        
        # 打印配置
        self._print_banner()
    
    def _print_banner(self):
        """打印配置信息"""
        print("\n" + "=" * 50)
        print("  Hyperliquid 跟单机器人 v2.0")
        print("  移动端优化版")
        print("=" * 50)
        print(f"  📋 目标钱包: {self.target_wallet[:10]}...")
        print(f"  📊 跟单比例: {self.copy_ratio * 100:.0f}%")
        print(f"  💰 最大仓位: ${self.max_position_usd}")
        print(f"  ⏱️  轮询间隔: {self.poll_interval}秒")
        print(f"  🌐 网络: {'测试网' if self.is_testnet else '主网'}")
        print("=" * 50 + "\n")
    
    def _detect_signals(
        self,
        current: Dict[str, Position],
        last: Dict[str, Position]
    ) -> List[TradeSignal]:
        """检测信号"""
        signals = []
        all_coins = set(last.keys()) | set(current.keys())
        
        for coin in all_coins:
            last_pos = last.get(coin)
            curr_pos = current.get(coin)
            
            if curr_pos and not last_pos:
                # 新开仓
                signals.append(TradeSignal(
                    coin=coin,
                    action="OPEN",
                    target_size=curr_pos.size,
                    reason="新仓位"
                ))
            elif not curr_pos and last_pos:
                # 平仓
                signals.append(TradeSignal(
                    coin=coin,
                    action="CLOSE",
                    target_size=0,
                    reason=f"平仓 (原: {last_pos})"
                ))
            elif curr_pos and last_pos:
                # 调整
                diff = abs(curr_pos.size - last_pos.size)
                if diff > 0.0001:
                    signals.append(TradeSignal(
                        coin=coin,
                        action="ADJUST",
                        target_size=curr_pos.size,
                        reason=f"调整 {last_pos.size:.3f}→{curr_pos.size:.3f}"
                    ))
        
        return signals
    
    def _calculate_size(self, pos: Position) -> float:
        """计算跟单数量"""
        notional = abs(pos.size * pos.entry_price)
        target = notional * self.copy_ratio
        # 限制最大仓位
        return min(target, self.max_position_usd)
    
    def _execute_signal(self, signal: TradeSignal) -> bool:
        """执行信号"""
        coin = signal.coin
        
        if signal.action == "CLOSE":
            logger.info(f"📤 平仓: {coin}")
            return self.client.close_position(coin, self.last_positions.get(coin, Position(coin, 0, 0, 0)).size)
        
        elif signal.action in ["OPEN", "ADJUST"]:
            target_size = self._calculate_size(Position(
                coin=signal.coin,
                size=signal.target_size,
                entry_price=1.0,  # 用名义价值计算
                unrealized_pnl=0
            ))
            
            if target_size < 1:  # 太小跳过
                return False
            
            side = Side.BUY if signal.target_size > 0 else Side.SELL
            action_str = "开多" if signal.target_size > 0 else "开空"
            
            logger.info(f"📥 {action_str}: {coin} × {target_size:.2f}")
            return self.client.place_order(coin, side, target_size)
        
        return False
    
    def run_once(self) -> bool:
        """执行一次轮询"""
        self.loop_count += 1
        print(f"\n─── 第 {self.loop_count} 轮 ───")
        
        try:
            # 获取目标持仓
            target_positions = self.client.get_positions(self.target_wallet)
            
            # 显示目标持仓
            if target_positions:
                positions_str = " | ".join([str(p) for p in target_positions.values()])
                print(f"👁️ 目标: {positions_str}")
            else:
                print("👁️ 目标: (空仓)")
            
            # 检测信号
            signals = self._detect_signals(target_positions, self.last_positions)
            
            if signals:
                print(f"📡 信号: {len(signals)} 个")
                for sig in signals:
                    emoji = {"OPEN": "🆕", "CLOSE": "❌", "ADJUST": "🔄"}.get(sig.action, "📊")
                    print(f"   {emoji} {sig.action} {sig.coin} - {sig.reason}")
                
                # 执行
                success = 0
                for sig in signals:
                    if self._execute_signal(sig):
                        success += 1
                print(f"✓ 执行: {success}/{len(signals)} 成功")
            else:
                print("📡 无信号")
            
            # 更新状态
            self.last_positions = target_positions
            
            # 显示我的持仓
            my_positions = self.client.get_my_positions()
            if my_positions:
                positions_str = " | ".join([str(p) for p in my_positions.values()])
                print(f"💼 我的: {positions_str}")
            else:
                print("💼 我的: (空仓)")
            
            return True
            
        except Exception as e:
            logger.error(f"轮询异常: {e}")
            return False
    
    def run(self):
        """运行"""
        logger.info("🤖 启动中... Ctrl+C 停止\n")
        
        # 首次
        self.run_once()
        
        # 循环
        while True:
            try:
                time.sleep(self.poll_interval)
                self.run_once()
            except KeyboardInterrupt:
                print("\n\n🛑 停止中...")
                break
            except Exception as e:
                logger.error(f"循环异常: {e}")
                time.sleep(self.poll_interval)

# ============================================================
# 主程序
# ============================================================

def main():
    """入口"""
    print("\n" + "▓" * 50)
    print("  Hyperliquid 跟单机器人")
    print("  移动端优化版 v2.0")
    print("▓" * 50)
    
    # 检查依赖
    if not HYPERLIQUID_AVAILABLE:
        print("\n[错误] hyperliquid-python 未安装")
        print("运行以下命令安装:")
        print("  pip install hyperliquid-python")
        return
    
    # 生成默认配置
    create_default_config()
    
    # 检查配置
    if not TARGET_WALLET or TARGET_WALLET == '0x3Db8f7bC6D744bEAE458207C85F46B5d0349e5ef':
        print("\n[警告] 请编辑 config.json 设置 TARGET_WALLET")
        print(f"配置文件: {get_config_path()}")
        print("或创建 config.env:\n")
        print('  TARGET_WALLET=你的目标钱包地址')
        return
    
    # 启动
    bot = CopyTradingBot(
        target_wallet=TARGET_WALLET,
        copy_ratio=COPY_RATIO,
        max_position_usd=MAX_POSITION_USD,
        poll_interval=POLL_INTERVAL,
        slippage=SLIPPAGE,
        max_leverage=MAX_LEVERAGE,
        is_testnet=IS_TESTNET,
        wallet_address=WALLET_ADDRESS if WALLET_ADDRESS else None
    )
    
    bot.run()

if __name__ == "__main__":
    main()
