# s11: MCP 集成 (Model Context Protocol)

`s01 > s02 > s03 > s04 | s05 > s06 > s07 | s08 > s09 > s10 > [ s11 ] > s12 > s13`

> *"宫里不够用，请外援"* —— 用标准协议把外部工具服务器接进 Agent。
>
> **架构层**: stdio transport + 工具发现 + 调用路由。

## 问题

s10 之前的工具都写在代码里。实际做 Agent 时，我们还会遇到很多外部能力：文件系统、数据库、浏览器、日历、GitHub、内部服务。

如果每接一个服务都手写一套适配代码，会有三个问题：

- 工具格式不统一；
- 每个 Agent 都要重复接一遍；
- 外部服务崩溃或升级会影响主 Agent。

s11 要解决的是：**用 MCP 把外部工具以统一协议接入 Agent**。

## 解决方案

新增 MCP 客户端层：

1. 从项目根目录 `mcp_servers.json` 读取 server 配置；
2. 通过 stdio 启动外部 MCP Server；
3. 调用 `list_tools()` 发现外部工具；
4. 把外部工具映射成本地 `mcp_{server}_{tool}` 名称；
5. 模型调用 `mcp_*` 工具时，主循环转发到对应 MCP Server；
6. MCP 返回结果后压平成字符串，作为 `tool_result` 回给模型。

```
模型 tool_use: mcp_time_get_current_time
        │
        ▼
Agent Host
        │ call_tool("get_current_time")
        ▼
MCP time server
        │ result
        ▼
tool_result -> 模型
```

## 工作原理

1. MCP 工具发现。

```python
mcp_client = MCPClient(name, params)
tools = mcp_client.list_tools()
for tool in tools:
    tool_name = _mcp_tool_name(name, tool.name)
    MCP_TOOL_MAP[tool_name] = (mcp_client, tool)
```

2. 工具名映射。

Anthropic 工具名要求匹配 `^[a-zA-Z0-9_-]{1,64}$`，所以 MCP 原始工具名需要清洗：

```python
def _mcp_tool_name(server_name, mcp_name):
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", mcp_name)
    return f"mcp_{server_name}_{sanitized}"
```

3. 异步 MCP 会话同步封装。

MCP Python SDK 是异步的，但教学主循环是同步的，所以用 `asyncio.run()` 包一层：

```python
def call_tool(self, name, arguments=None):
    return asyncio.run(self._acall_tool(name, arguments))
```

教学版为了直观，每次调用都重新启动一次 stdio 会话。完整工程可以改成长驻 event loop 和复用 server 进程。

4. 动态构建工具列表。

```python
MCP_CLIENTS = load_mcp_clients(MCP_TOOL_MAP, MCP_CONFIG_PATH)
TOOLS = build_tool_schemas(TOOLS)
```

这样 s10 的所有工具仍然保留，MCP 工具只是追加进去。

5. 主循环分发 `mcp_*`。

```python
elif block.name in MCP_TOOL_MAP:
    mcp_client, tool = MCP_TOOL_MAP[block.name]
    results_map[block.id] = mcp_client.call_tool(tool.name, block.input)
```

完整代码: [code/step11_mcp.py](../code/step11_mcp.py)

## 变更内容

| 组件 | 之前 (s10) | 之后 (s11) |
|------|------------|------------|
| Agent Team | 固定队友 + inbox | 继续保留 |
| 子代理 / todolist / memory / skill | 已具备 | 继续保留 |
| 外部工具 | 只能写进代码 | 通过 MCP Server 动态发现 |
| 工具名 | 固定内置名称 | 新增 `mcp_{server}_{tool}` |
| 配置 | 无 MCP 配置 | 新增 `mcp_servers.json` |

## 试一试

```sh
python build-agent-example/code/step11_mcp.py
```

可以输入：

```text
现在几点？
今天日期是多少？
/mcp
```

启动时会看到类似：

```text
[MCP] 已连接 'time'，提供 2 个工具
```
