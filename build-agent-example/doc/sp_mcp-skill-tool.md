# 特别篇：MCP × Skill × Tool —— 一道红烧肉走完三层分工

`s05(Tool) + s06(Skill) + s11(MCP) → [ 附录串联实战 ]`

> 本文是专题附录，不参与 step01-step13 主线编号。

> *"接单靠外卖，菜谱靠翻书，动手靠厨具"* —— 三个概念各司其职，缺一不可。

## 三个概念的边界

| 层 | 是什么 | 负责什么 | 不负责什么 |
|----|--------|----------|------------|
| **MCP** | 外部服务接口（标准协议） | 把第三方能力接进来，暴露高层服务 | 怎么做、做哪步 |
| **Skill** | SOP 文件（按需加载） | 告诉 Agent 分哪几步、调哪些工具 | 真正执行 |
| **Tool** | 本地函数（function calling） | 真正动手，返回结果 | 流程编排 |

**容易混淆的地方**：prep/fry/cook/plate 是 **Tool**（Agent 自带的内置工具），不是 MCP 工具。MCP Server 暴露的是**接单能力**（`cook_dish`），而不是烹饪步骤。

## 架构图

```
用户："来份红烧肉"
        │
        ▼
┌───────────────────────────────────────────────┐
│  Agent                                        │
│                                               │
│  system prompt:                               │
│    MCP 可用：mcp_waimai_cook_dish             │
│    Skill 菜单：red-braised-pork (描述)        │  ← 只有名字+描述，不加载正文
│    内置工具：prep/fry/cook/plate              │
└───────────┬───────────────────────────────────┘
            │ ① tool_call: mcp_waimai_cook_dish
            ▼
┌───────────────────┐
│  MCPClient        │  →  WaimaiServer.call_tool("cook_dish")
│  (转发层)         │  ←  "已接单：红烧肉"
└───────────────────┘
            │ ② tool_call: use_skill("red-braised-pork")
            ▼
┌───────────────────┐
│  skills/          │  读取 red-braised-pork/SKILL.md 正文
│  red-braised-pork │  返回 SOP 步骤给 Agent
│  /SKILL.md        │
└───────────────────┘
            │ ③④⑤⑥ tool_call: prep → fry → cook → plate
            ▼
┌───────────────────┐
│  内置 Tool        │  本地函数，直接执行，返回结果字符串
└───────────────────┘
```

## 关键机制

### 1. MCP：只接单，不做菜

`WaimaiServer` 只暴露一个工具 `cook_dish`，接到菜名后返回确认。它不知道怎么做菜，也不调用任何烹饪工具。

```python
class WaimaiServer:
    _SCHEMAS = [{"name": "cook_dish", ...}]   # 只有这一个

    def call_tool(self, name, args):
        if name == "cook_dish":
            return f"已接单：{args['dish']}。请按 SOP 步骤制作。"
```

`MCPClient.connect()` 是启动握手：拉取 schema → 注册为 `mcp_waimai_cook_dish`。之后每次 tool call 经 `MCPClient.call()` 转发，终端可见 `→` 和 `←` 方向。

### 2. Skill：渐进式披露（只看菜名，要用才翻书）

启动时 `scan_skills()` 只读 frontmatter，把 `name + description` 放进 system prompt：

```
## 可用 Skill（按需加载）
  - red-braised-pork: 制作红烧肉的 SOP，接到订单后依次调用内置厨具工具完成出品
```

正文不加载。Agent 需要用时调用 `use_skill("red-braised-pork")` 工具，此时才读取完整 `SKILL.md`，把 SOP 步骤作为 tool result 返回。

```python
def scan_skills(skills_dir):          # 只提取 frontmatter → 菜单
    ...

def load_skill(name, skills_dir):     # 读完整正文 → Agent 按需触发
    ...
```

**为什么这样设计**：把所有 Skill 全文一次性塞进 system prompt 会白白占用 context。只在需要时加载，节省 token，也让 Skill 数量可以无限扩展。

### 3. Tool：本地执行，结果即时返回

prep/fry/cook/plate 是普通 Python 函数，直接注册进 `tool_schemas`，不经过 MCP：

```python
tool_schemas = mcp_schemas + INTERNAL_TOOL_SCHEMAS   # MCP工具 + 内置工具并列
```

调用时按 tool name 路由：

```python
if mcp.owns(block.name):
    result = mcp.call(block.name, block.input)          # → MCP 转发
else:
    result = execute_internal(block.name, block.input)  # → 本地执行
```

## 终端输出示意

```
[MCP] 正在连接 'waimai'...
[MCP] ← 'waimai' 报菜单：['cook_dish']
[MCP] ✓ 注册完成 → ['mcp_waimai_cook_dish']

[Skill] 可用 Skill 菜单（N 个）：
        red-braised-pork: 制作红烧肉的 SOP...

你: 来份红烧肉
  → [tool_call] mcp_waimai_cook_dish  args={'dish': '红烧肉'}
       [MCP] → 转发给 'waimai'：cook_dish({'dish': '红烧肉'})
       [MCP] ← 'waimai' 返回：已接单：红烧肉。请按 SOP 步骤制作。
  → [tool_call] use_skill  args={'name': 'red-braised-pork'}
[Skill] Agent 选择了 Skill: red-braised-pork
[Skill] ✓ 已加载 red-braised-pork/SKILL.md
  → [tool_call] prep_tool  args={'action': '切肉、备香料'}
       [Tool] 备料完成：切肉、备香料。五花肉切块，香料备齐。
  → [tool_call] fry_tool  args={'temp': '180°C'}
       [Tool] 炒糖色完成：180°C，冰糖成焦糖色，肉块上色均匀。
  → [tool_call] cook_tool  args={'time': '40min'}
       [Tool] 焖煮完成：40min，肉已软糯入味，汤汁浓郁。
  → [tool_call] plate_tool  args={'style': '精致摆盘'}
       [Tool] 装盘完成：精致摆盘，摆盘精致，出品！

[Agent]: 红烧肉已完成出品！...
```

## 文件结构

```
build-agent-example/code/sp_mcp-skill-tool.py   # 本期代码
skills/red-braised-pork/SKILL.md                 # Skill SOP 文件
```

## 运行方式

```bash
cd "/path/to/agnet示例"
source .venv/bin/activate
python build-agent-example/code/sp_mcp-skill-tool.py
```

`.env` 需要：

```
ANTHROPIC_API_KEY=sk-...
ANTHROPIC_MODEL=claude-sonnet-4-6
```
