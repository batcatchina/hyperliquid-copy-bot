# Codex CLI 配置说明

## 当前状态

**Codex CLI v0.130.0 已成功配置并可运行！**

## 必需的环境变量

在 `~/.bashrc` 中添加以下环境变量：

```bash
export HOME=/root
export UIUI_API_KEY="sk-eNE47bQbSgh8Y40ckrQJIicmOTtOqIWaqkVu2MUigz5sAPQu"
export OPENAI_BASE_URL="https://api1.uiuiapi.com/v1"
```

## 配置文件 ~/.codex/config.toml

```toml
personality = "pragmatic"

[default]
provider = "openai"
model = "gpt-5.5"
approval_policy = "never"
sandbox = "workspace-write"

[providers.uiuiapi]
name = "UiUiAPI"
base_url = "https://api1.uiuiapi.com/v1"
api_key_env = "UIUI_API_KEY"
wire_api = "responses"
supports_websockets = false

[projects."/root/mytongue-mirror"]
trust_level = "trusted"
```

## 使用方法

### 基本命令格式

```bash
cd /root/mytongue-mirror
export HOME=/root
export UIUI_API_KEY="sk-eNE47bQbSgh8Y40ckrQJIicmOTtOqIWaqkVu2MUigz5sAPQu"

# 单个命令
echo "pwd" | HOME=/root codex exec -

# 更复杂的任务
echo "ls -la src/" | HOME=/root codex exec -
```

### 注意事项

1. **必须设置 `HOME=/root`**：云电脑上 HOME 环境变量未设置，必须显式指定
2. **必须设置 `UIUI_API_KEY`**：使用 UiUiAPI 的 API key
3. **WebSocket 错误可以忽略**：虽然会显示 "Reconnecting..." 错误，但命令仍会执行成功
4. **需要耐心等待**：由于错误重试，输出可能需要几秒钟才能显示

## 验证成功的命令

```bash
# 测试简单命令
echo "pwd" | HOME=/root codex exec -

# 测试文件操作
echo "ls -la package.json" | HOME=/root codex exec -

# 测试目录列表
echo "ls src/" | HOME=/root codex exec -
```

## 已知问题

1. **WebSocket 连接失败**：Codex 尝试连接 `wss://api.openai.com/v1/responses`，但会失败
2. **Provider 配置未生效**：config.toml 中的 `uiuiapi` provider 配置未被读取，显示为 `openai`
3. **执行较慢**：由于错误重试机制，命令响应可能需要 30-60 秒

## 解决方案总结

1. 设置 `HOME=/root` 环境变量
2. 设置 `UIUI_API_KEY` 环境变量
3. 使用管道输入命令：`echo "command" | HOME=/root codex exec -`
4. 等待 30-60 秒让命令执行完成

## 工作原理

虽然 config.toml 中的 provider 配置未被正确读取，但 Codex CLI 通过以下方式仍然能够工作：

1. 使用存储的 API key 认证
2. WebSocket 连接失败后，Codex 会尝试 HTTP polling
3. 最终命令通过 HTTP 请求执行完成
