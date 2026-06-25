# s10: MCP 集成 —— 接入外部工具服务器

`s01 > s02 > s03 > s04 | s05 > s06 | s07 > s08 > s09 > [ s10 ]`

> *"宫里不够用，请外援"* —— 用标准化协议把外部工具服务器接进 Agent。
>
> **架构层**: stdio transport + 工具名映射 + 同步封装。

## 什么是 MCP

**MCP = Model Context Protocol**，模型上下文协议，Anthropic 2024 年底发布的开放标准。

拆开三个词理解：

| 词 | 含义 |
|----|------|
| **Model** | 这套协议专门为大语言模型设计，规定的是"模型怎么和外部世界交互" |
| **Context** | 外部工具提供的数据、能力，最终都要进入模型的上下文（system prompt / tool result）才能被用 |
| **Protocol** | 是一套标准通信协议，不是某个库或框架。任何语言、任何工具只要实现这套协议，就能被任何支持 MCP 的 Agent 调用 |

**一句话**：MCP 是一个标准接口，让任何外部工具都能以同一种方式"插"进任何 Agent，就像 USB 接口让外设不需要知道电脑型号一样。

### 为什么需要 MCP

没有 MCP 之前，每接一个外部工具（GitHub、数据库、浏览器……）都要手动写适配代码，格式各异、无法复用。有了 MCP：

- 工具开发者只需实现一次 MCP Server，所有支持 MCP 的 Agent 都能调用；
- Agent 开发者只需写一个 MCP Client，就能接入所有 MCP Server；
- 生态可以沉淀：文件系统、日历、代码执行、浏览器控制……已有数百个现成 Server。

### 两阶段机制

MCP 的核心是两个阶段，缺一不可：

```
阶段一：启动握手（Schema 发现）
  Agent Host ──── list_tools() ───► MCP Server
  Agent Host ◄─── [工具schema列表] ── MCP Server
  Agent 把这些工具注册进模型的可用工具列表

阶段二：运行时调用（Tool Call 路由）
  模型 ──── tool_use: mcp_xxx_yyy ───► Agent Host
  Agent Host ── call_tool(name, args) ► MCP Server
  Agent Host ◄─────── 结果 ─────────── MCP Server
  Agent Host ── tool_result ──────────► 模型
```

两个阶段分开是关键：**模型永远不直接和 MCP Server 通信**，它只看到工具名和结果字符串；路由和协议转换全在 Host 层完成。

## 问题

s01-s09 的工具都是写在代码里的**内置工具**。实际使用时，我们希望：

1. **复用现成的工具生态**：比如文件系统、数据库、浏览器、日历、GitHub 等，已经有人写好了 MCP Server；
2. **动态扩展**：不重启主 Agent 就能新增/删除工具；
3. **隔离职责**：外部工具跑在独立进程里，崩了不影响主 Agent。

手动把每个外部工具都写成 `agent/tools/*.py` 太重复。MCP 就是解决这个问题的：它定义了一套标准协议，让 Agent 和外部工具服务器"说同一种语言"。

## 解决方案

在主 Agent 里加一个 **MCP 客户端层**：

- 通过 `mcp_servers.json` 配置要连接的 server（命令 + 参数）；
- 启动每个 server 作为独立子进程（stdio transport）；
- 把 server 提供的工具拉取下来，注册成本地 `Tool`；
- 调用时再把参数转发给 server，把结果转回字符串。

```
┌─────────────┐      stdio      ┌─────────────────┐
│  AgentLoop  │ ◄──────────────► │  MCP time server │
│             │   JSON-RPC      │  - get_current_time
│  ToolRegistry│                 │  - get_current_date
│  - mcp_time_*│                 └─────────────────┘
└─────────────┘
```

对皇上（用户）和模型来说，`mcp_time_get_current_time` 看起来和 `run_command` 没什么区别；区别只在它由外部 server 实现。

## 代码结构速览

| 代码区域 | 作用 | 为什么需要 |
|----------|------|------------|
| `agent/tools/mcp/client.py` | 同步封装异步 MCP 客户端 | Agent 主循环是同步的，MCP SDK 是异步的，需要桥接 |
| `agent/tools/mcp/tool.py` | `MCPTool`：把 MCP 工具包装成 `Tool` | 复用现有 `ToolRegistry` 和并发执行逻辑 |
| `agent/tools/mcp/config.py` | 读取 `mcp_servers.json` | 把 server 配置独立出来，方便增删 |
| `agent/tools/mcp/builtin.py` | `list_mcp_servers` 工具 | 让用户/模型能查看已连接 server |
| `agent/loop.py` | 启动 MCP client 并注册工具 | 把 MCP 能力接入主 Agent |
| `agent/context.py` | 在 system prompt 注入 MCP 工具清单 | 让模型知道有哪些外部工具可用 |
| `build-agent-example/code/step10_mcp.py` | 最小可运行教学示例 | 单文件展示完整流程 |
| `build-agent-example/mcp_server_time.py` | 示例 MCP Server | 用于本地验证 |

## 关键机制

### 1. 同步封装：用 asyncio.run() 把异步会话包成同步调用

MCP Python SDK 是异步的（`async with stdio_client(...) as (read, write)`），但本示例的主循环是同步的。step10_mcp.py 为了**代码简洁好懂**，采用最直接的桥接方式：每个方法内部用 `asyncio.run()` 跑完一次完整会话——起进程、initialize、调用、关进程，一气呵成。

```python
class MCPClient:
    def list_tools(self):
        if self._tools is None:
            self._tools = asyncio.run(self._alist_tools())   # 起一次会话拉工具表
        return list(self._tools)

    def call_tool(self, name, arguments=None):
        return asyncio.run(self._acall_tool(name, arguments))  # 每次调用起一次会话
```

优点是没有线程、没有长驻 loop，读起来一目了然；代价是**每次调用都重启一次 server 子进程**。

> 生产版（`agent/tools/mcp/client.py`）改用"后台线程 + 长驻 event loop + worker coroutine"复用同一个子进程，避免反复启停，并保证 anyio 的 context manager scope 始终在同一个 task 里。教学这里不需要那么复杂。

### 2. 工具名映射

MCP 工具名可能包含点号等字符，而 Anthropic API 要求工具名匹配 `^[a-zA-Z0-9_-]{1,64}$`。因此做了一层映射：

```python
def mcp_tool_name(server_name, mcp_name):
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", mcp_name)
    return f"mcp_{server_name}_{sanitized}"
```

实际注册名如 `mcp_time_get_current_time`，既能避免冲突，也便于用户识别来源。

### 3. 结果压平

MCP `call_tool` 返回的是结构化内容列表（text / image / ...）。为了兼容现有工具返回字符串的约定，统一压成字符串：

- text 类型直接拼接；
- 其他类型序列化为 JSON。

### 4. 默认只读、可并发（生产版）

本教学文件按 `tool_use` 顺序逐个执行工具，不做并发。在生产版里，`MCPTool.read_only = True`，所以无状态 MCP 工具会被 `AgentRunner._execute_tool_blocks()` 并发执行，和 `web_fetch` / `read_file` 一样。

## 配置示例

项目根目录的 `mcp_servers.json`：

```json
{
  "servers": {
    "time": {
      "enabled": true,
      "command": "python",
      "args": ["build-agent-example/mcp_server_time.py"]
    }
  }
}
```

启动主 Agent 时会看到：

```text
[MCP] connected 'time' with 2 tool(s)
```

## 完整调用链示例

皇上输入："现在几点？"

1. 模型从 system prompt 的 **MCP Server 说明**看到 `mcp_time_get_current_time`；
2. 模型输出 `tool_use` 调用该工具；
3. 主循环发现 `block.name` 在 `MCP_TOOL_MAP` 里，取出对应的 `(mcp_client, tool)`；
4. 调用 `mcp_client.call_tool("get_current_time", block.input)`；
5. `asyncio.run()` 起一次会话，通过 `ClientSession.call_tool` 把请求发给 time server；
6. server 返回 `"2026-06-23T18:14:44"`，经 `_result_to_text` 压平成字符串；
7. 结果作为 `tool_result` 回传模型；
8. 模型生成最终回答："奉天承运皇帝诏曰，启禀皇上，现在是酉时三刻……"

## 教学示例：step10_mcp.py

`build-agent-example/code/step10_mcp.py` 是自包含版本：

- 为了代码简洁，每次调用都**独立启动一次 stdio server**（`asyncio.run` 包裹整个会话）;
- 生产版本（`agent/tools/mcp/client.py`）则复用后台 loop，避免重复起进程。

运行方式：

```bash
python build-agent-example/code/step10_mcp.py
# 然后输入：现在几点？
```

## 动手试一试

```bash
# 1. 确认依赖已安装
pip install -r requirements.txt

# 2. 启动教学示例
python build-agent-example/code/step10_mcp.py

# 3. 输入以下指令观察 MCP 工具调用
现在几点？
今天日期是多少？
列出所有 MCP Server
```

也可以启动主 Agent：

```bash
python agent.py
# 输入：调用 list_mcp_servers 查看工具，或直接用 mcp_time_get_current_time
```

## 本期变更

| 组件 | 变更 |
|------|------|
| `requirements.txt` | 新增 `mcp==1.28.0` |
| `agent/tools/mcp/` | 新增 client / tool / config / builtin 四个模块 |
| `agent/loop.py` | 启动 MCP server 并注册工具 |
| `agent/context.py` | system prompt 注入 MCP 工具清单 |
| `templates/agent/identity.md` | 增加 MCP 工具说明 |
| `mcp_servers.json` | 新增示例配置 |
| `build-agent-example/mcp_server_time.py` | 新增示例 MCP Server |
| `build-agent-example/code/step10_mcp.py` | 新增教学代码 |
| `build-agent-example/doc/step10_mcp.md` | 本文档 |

## 下一步

MCP 解决了"外部工具从哪来"。下一期（s11）我们将在 Agent 主循环里埋下 **Hooks**，在不改核心逻辑的情况下插入监控、限流、审计等横切能力。
