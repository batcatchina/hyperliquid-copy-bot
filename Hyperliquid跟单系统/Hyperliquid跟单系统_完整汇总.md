# Hyperliquid 跟单系统 — 完整汇总

> 整理时间：2026-06-27

---

## 一、核心账户信息

| 项目 | 内容 |
|------|------|
| **GitHub 仓库** | https://github.com/batcatchina/hyperliquid-copy-bot |
| **GitHub 账号** | batcatChina |

### 钱包配置

| 角色 | 地址 |
|------|------|
| **主钱包（资金）** | `0x63D1ef97F8EE1C703118F6cbea7b986FA54132f` |
| **API 钱包（跟单签名）** | `0x3b8cfF67f3a5DdbDA610bdB7B0eC74cCA0fd3F55` |
| **目标钱包（信号源）** | `0x3Db8f7bC6D744bEAE458207C85F46B5d0349e5ef` |

> ⚠️ API钱包授权后需在Hyperliquid官网确认已完成签名授权
> ⚠️ 私钥请查看本地 config.json 或向管理员索取

---

## 二、信号源分析（目标钱包）

- **地址**：`0x3Db8f7bC6D744bEAE458207C85F46B5d0349e5ef`
- **2026-02 至 2026-06 累计盈利**：+$10,923
- **胜率**：65%（367胜/197负）
- **风格**：宏观事件驱动 + 趋势跟踪
- **6月交易量**：$167K（较4月 $2.3M 骤降至 1/14）
- **当前持仓**：HYPE(浮盈+$2983)、XRP、ETH、LINK、ONDO、SUI、RENDER、INJ

### 评审结论
- 跟单比例不超过 **5%**
- 至少观察 **30 天**
- 单日最大亏损 **2%** 自动停跟
- 单合约最大杠杆 ≤ **10x**
- 连续2个月月交易额低于 500K 直接终止跟单

---

## 三、文件清单

### 3.1 核心代码

| 文件 | 行数 | 说明 |
|------|------|------|
| `hyperliquid_copy_bot_api.py` | 372 | API层 v3.3，含place_order/签名/Hyperliquid官方SDK调用 |
| `hltrade_core.py` | 380 | 底层通信：requests零依赖实现、EIP-712 secp256k1签名、clearinghouseState |
| `hltrade_bot.py` | 470 | 跟单Bot主逻辑：持仓同步、盈亏计算、停损机制 |
| `run.py` | 56 | 启动脚本，支持 monitor / live 模式 |
| `sim_exchange.py` | — | 模拟交易所（SIMULATE 模式） |

### 3.2 前端 Dashboard

| 文件 | 说明 |
|------|------|
| `dashboard_standalone.html` | **主力**：纯前端，零后端，直接调 Hyperliquid 公开 API（无签名），手机端适配 |
| `dashboard.html` | PC版旧版 |
| `dashboard_mobile.html` | 移动端旧版 |
| `public/index.html` | Vercel 部署专用（dashboard_standalone.html 副本） |

### 3.3 配置与状态

| 文件 | 说明 |
|------|------|
| `config.json` | 运行配置：跟单比例、止损、轮询间隔、主钱包/私钥 |
| `bot_state.json` | 运行时状态：当前模式、持仓快照 |
| `sim_state.json` | 模拟交易状态 |

---

## 四、部署信息

### 4.1 Vercel 账号

| 项目 | 内容 |
|------|------|
| 账号 | batcatchina |
| 登录方式 | Continue with GitHub（batcatChina） |

> ⚠️ Token 请查看本地配置或向管理员索取

### 4.2 历史部署（均已失效）

| 项目名 | URL | 状态 |
|--------|-----|------|
| hl-deploy | `https://hl-deploy-jhcdf4gva-batcatchinas-projects.vercel.app` | ❌ 已失效 |
| fresh-dash | `https://fresh-dash-k0jlwk7gb-batcatchinas-projects.vercel.app` | ❌ 已失效 |
| dashdeploy | `https://dashdeploy-i3ebhpjpe-batcatchinas-projects.vercel.app` | ❌ 已失效 |

> Vercel 免费版部署会自动过期，需重新部署

### 4.3 重新部署命令

```bash
# 1. 克隆仓库
git clone https://github.com/batcatchina/hyperliquid-copy-bot.git
cd hyperliquid-copy-bot

# 2. 进入跟单系统目录
cd Hyperliquid跟单系统

# 3. 部署（需配置 Vercel Token）
vercel --token <YOUR_VERCEL_TOKEN>

# 4. 或者直接部署 public 目录
cd public
vercel
```

---

## 五、技术架构

### 5.1 API 调用规则

```
Hyperliquid 公开 API（/info 端点）：完全免费，无需签名
  └─ clearinghouseState  → 实时持仓/账户状态
  └─ userFills          → 历史成交记录
  └─ allMids            → 实时价格

Hyperliquid 交易 API（/exchange 端点）：需 EIP-712 secp256k1 签名
  └─ send_order         → 实盘下单（用 API 钱包私钥签名）
  └─ cancel_order       → 取消订单
```

### 5.2 三种运行模式

| 模式 | 说明 |
|------|------|
| `MONITOR` | 监控模式：只读信号源，不下单 |
| `SIMULATE` | 模拟模式：用 sim_exchange.py 模拟成交 |
| `LIVE` | 实盘模式：用 API 钱包签名自动跟单 |

### 5.3 启动命令

```bash
# 监控模式（默认）
python3 run.py monitor

# 实盘模式
python3 run.py live

# 带超时（60分钟后自动停止）
python3 run.py live 60
```

---

## 六、当前状态（2026-06-27）

- **运行模式**：MONITOR（监控）
- **实盘**：未启用（TESTNET: true）
- **当前持仓**：SUI 等（详见 `bot_state.json`）
- **最后更新时间**：2026-06-27T12:40:22

---

## 七、待办事项

- [ ] **实盘授权确认**：API 钱包 `0x3b8cf...` 是否已在 Hyperliquid 官网完成签名授权
- [ ] **TESTNET → MAINNET**：`config.json` 中 `TESTNET: true` 改为 `false` 后启用实盘
- [ ] **Dashboard 重新部署**：Vercel 链接已失效，需重新 deploy
- [ ] **跟单比例**：当前 `COPY_RATIO: 0.1`（10%），评审建议 ≤5%，考虑调低
- [ ] **轮询常驻**：监控/LIVE模式需部署在服务器或云电脑常驻运行

---

## 八、联系方式与资源

- Hyperliquid 官网：https://hyperliquid.com
- API 文档：https://hyperliquid.gitbook.io/hyperliquid-docs
- 仓库地址：https://github.com/batcatchina/hyperliquid-copy-bot
