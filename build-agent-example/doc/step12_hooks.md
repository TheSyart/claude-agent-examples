# s12: Hooks 生命周期 (Hooks)

`s01 > s02 > s03 > s04 | s05 > s06 > s07 | s08 > s09 > s10 > s11 > [ s12 ] > s13`

> *"暗桩眼线，随时禀报"* —— Hook 是 Runtime 的生命周期检查点。
>
> **架构层**: Hook 基类 + HookRegistry + HookDecision。教学版用 Python 类方法实现，保留 Claude Code Hooks 的核心结构：`Event -> Matcher -> Handler -> Output/Decision`。

## 问题

s11 已经有了一个累积式 Agent：tool、skill、memory、todolist、subagent、Agent Team、MCP 都在。

但有些能力不应该继续塞进主循环：

- 每轮 LLM 调用花了多久；
- 写文件和执行命令要不要审计；
- `.env`、私钥、生产配置能不能写；
- 破坏性命令、发布命令这类高风险动作能不能执行；
- 工具输出太长时，能不能先截断再回填给模型；
- Agent 想结束时，能不能检查任务是否真的完成。

这些都不是 Agent 的核心推理流程。它们横跨模型调用、工具调用、回答结束等多个位置。如果都写成主循环里的 `if`，代码会越来越难讲，也越来越难维护。

s12 要解决的是：**不破坏主循环结构，也能在关键生命周期节点插入逻辑**。

真实工程里的 Hook 至少还要回答三个问题：

1. **什么时候触发**。模型调用前、工具调用前、工具调用后、回答结束前，位置不同，能力也不同。
2. **对谁触发**。审计只关心写文件和命令；敏感文件保护关心 `write_file`；危险命令拦截只关心 `run_command`。
3. **触发后能改变什么**。有的 Hook 只记录日志，有的能拒绝工具，有的能问用户确认，有的能改写参数或工具输出。

## 解决方案

s12 新增 Hooks 系统，把生命周期扩展点统一成四层模型：

```text
Event（事件）
  -> Matcher（匹配器）
    -> Handler（处理器）
      -> Decision（决策）
```

教学版内置 5 个代表性 Hook，覆盖观察、工具策略、审计、输出整理和 Stop 门禁：

| Hook | 事件 | Matcher | 决策/行为 | 作用 |
|------|------|---------|-----------|------|
| `LoggingHook` | before/after turn | turn 级 | 观察 | 打印 LLM 耗时和 token |
| `ToolPolicyHook` | before_tool_call | `write_file\|run_command` | allow / deny / ask | 统一演示路径改写、敏感文件保护、破坏性命令拦截和人工确认 |
| `ToolAuditHook` | after_tool_call | `write_file\|run_command` | 观察 | 写审计日志到 `.hooks_audit.jsonl` |
| `OutputFormattingHook` | after_tool_call | `*` | 改写 output | 截断过长工具输出 |
| `StopQualityGateHook` | on_stop | turn 级 | block | 回答结束前检查完整性和未完成待办 |

### 与 Claude Code 的对应关系

| 层 | 教学版 | Claude Code 官方概念 |
|----|--------|----------------------|
| Event | `before_tool_call`、`after_tool_call`、`on_stop` | `PreToolUse`、`PostToolUse`、`Stop` 等事件 |
| Matcher | `Hook.matcher = "write_file\|run_command"` | matcher / 正则 / 条件过滤 |
| Handler | Python 类方法 | command / http / mcp_tool / prompt / agent 五类 Handler |
| Decision / Output | `HookDecision`、修改 `ctx` | `permissionDecision`、`updatedInput`、`additionalContext`、stdout JSON / exit code |

本项目不实现 Claude Code 的 settings.json 配置式 Hook，也不执行外部 command/http/mcp_tool/prompt/agent handler。这里先把生命周期和决策语义讲清楚。

### HookDecision 决策类型

| 决策 | 含义 | 是否短路 |
|------|------|----------|
| `allow` | 放行，不短路；可附带 `updated_input` 改写工具参数 | 否 |
| `deny` | 拒绝，工具不执行，原因反馈给 Agent | 是 |
| `ask` | 请求用户确认；终端输入 `y` 才继续，非交互环境默认拒绝 | 是 |
| `block` | 阻止当前结束点，用于 Stop 质量门禁 | 是 |

## 工作原理

1. `Hook` 基类定义生命周期事件和 matcher。

```python
class Hook:
    name = ""
    matcher = "*"

    def matches(self, tool_name): ...
    def before_turn(self, ctx): pass
    def after_turn(self, ctx): pass
    def before_tool_call(self, ctx): pass
    def after_tool_call(self, ctx): pass
    def on_stop(self, ctx): pass
```

2. `HookRegistry.emit()` 按注册顺序触发。

```python
decision = HOOKS.emit(
    "before_tool_call",
    tool_ctx,
    tool_matcher=tool_name,
)
```

核心规则：

- 按注册顺序执行；
- 工具级事件按 `matcher` 过滤；
- hook 可以原地修改 `ctx`；
- `allow` 不短路，后续 hook 继续执行；
- `deny / ask / block` 短路，返回给 runner 处理；
- 单个 hook 抛异常时打印 `[hook error]`，继续 fail-open。

教学版还做了一个特别处理：system prompt 不暴露 Hook 规则。模型只看到普通工具使用约定，真正的拒绝、确认、改写由 Runtime Hook 在工具边界强制执行。

所以演示时如果看到：

```text
[hook:tool_policy] ...
[HookDecision: 拒绝] ...
```

这才说明 `ToolPolicyHook` 真正进入了 `before_tool_call` 并完成拦截。只看到 `[hook:logging]`，通常表示模型没有调用工具。

3. `LoggingHook` 用于观察一轮模型调用。

`LoggingHook` 在 `before_turn` 记录开始时间，在 `after_turn` 打印耗时和 token。它不改变行为，只负责让运行过程可见。

4. `ToolPolicyHook` 集中演示 `before_tool_call` 的三类能力。

路径改写：

```python
demo_production/report.txt
-> sandbox/demo_production/report.txt
```

当模型调用 `write_file` 写入 `demo_production/...` 时，`ToolPolicyHook` 会把目标路径改到 `sandbox/demo_production/...`。

它返回 `allow + updated_input`，runner 使用改写后的 `ctx["input"]` 执行工具。

执行完成后，runner 会把“实际执行路径”附回工具结果里。模型看到实际路径后，应以工具结果为准，不再把文件复制回原始路径。

安全拒绝：

`ToolPolicyHook` 会检查 `write_file` 的 `path`。如果目标是 `.env`、私钥、生产配置、secrets 目录这类敏感路径，就直接拒绝。

命令工具只做命令风险判断，例如破坏性命令直接拒绝，提交、发布、部署这类高敏感命令先问用户确认。

这里刻意不做 shell 语义解析。比如 `>`、管道、heredoc 这些属于更复杂的命令解析器或真实安全沙箱范畴，不放进教学版，避免把 Hook 主线讲散。

一旦工具结果是 `HookDecision: 拒绝` 或 `HookDecision: 需要确认`，runner 会直接结束本轮并回禀原因，不再把控制权交回模型继续尝试换路径、换命令绕过策略。

人工确认：

`ToolPolicyHook` 对提交、发布、部署等高敏感命令返回 `ask`。runner 会同步询问用户，只有输入 `y` 才执行命令；非交互环境默认拒绝。

5. `after_tool_call` 可以观察和改写输出。

`ToolAuditHook` 记录写文件和命令调用。`OutputFormattingHook` 如果发现输出太长，会修改 `ctx["output"]`，runner 最后把改写后的输出返回给模型。

6. `on_stop` 用于回答结束前的质量门禁。

`StopQualityGateHook` 检查回答是否过短、是否还有未完成 todolist。如果阻止结束，runner 会追加一次提醒，让模型继续处理。它最多触发一次自动续跑，避免 Stop Hook 递归循环。

完整代码: [code/step12_hooks.py](../code/step12_hooks.py)

## 变更内容

| 组件 | 之前 (s11) | 之后 (s12) |
|------|------------|------------|
| 生命周期扩展 | 散落在主循环里 | `Hook` / `HookRegistry` 统一触发 |
| Hook 模型 | 无统一概念 | `Event -> Matcher -> Handler -> Decision` |
| 决策类型 | 字符串短路 | `HookDecision(allow/deny/ask/block)` |
| Before 工具策略 | 分散在主循环里 | `ToolPolicyHook` 统一处理改写、拒绝、确认 |
| 工具参数 | 直接执行模型给的参数 | `updated_input` 可改写参数后放行 |
| 工具输出 | 原样回填模型 | `after_tool_call` 可改写 `ctx["output"]` |
| 高敏感命令 | 无确认机制 | `ask` 同步确认，默认拒绝 |
| Stop 门禁 | 只看 todolist | `on_stop` 最多触发一次继续提醒 |
| 教学取舍 | — | 不实现 settings.json、外部脚本、HTTP/MCP/prompt/agent handler、async hook、Plugin/Managed Settings |

## 试一试

```sh
python build-agent-example/code/step12_hooks.py
```

也可以用完整路径执行：

```sh
cd "/Users/anhuike/Documents/workspace/claude code/agnet示例" && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python build-agent-example/code/step12_hooks.py
```

可以输入：

```text
写一份奏折到 weather_memorial.txt
请使用 write_file 工具写一份奏折到 demo_production/report.txt
请使用 write_file 工具把 API_KEY=123 写入 .env
执行 git commit --dry-run -m "hook permission test"
执行 python -c "print('x' * 9000)"
```

观察：

- 普通写文件会执行，并写入 `.hooks_audit.jsonl`；
- `write_file` 写 `demo_production/report.txt` 会被 `ToolPolicyHook` 改写到 `sandbox/demo_production/report.txt`；
- 如果路径被改写，Agent 会回禀实际路径，不再尝试写回原路径；
- `write_file` 写 `.env` 等敏感路径会进入 `before_tool_call`，然后被 `ToolPolicyHook` 拒绝，并打印 `[hook:tool_policy]`；本轮不会继续换路径绕过；
- 高敏感命令会由 `ToolPolicyHook` 触发 `ask`，未输入 `y` 不执行；
- 超长命令输出会被 `OutputFormattingHook` 截断；
- 回答结束前 `StopQualityGateHook` 会检查未完成待办，最多自动提醒一次。
