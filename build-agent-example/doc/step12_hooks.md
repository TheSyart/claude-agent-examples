# s12: Hooks 生命周期 (Hooks)

`s01 > s02 > s03 > s04 | s05 > s06 > s07 | s08 > s09 > s10 > s11 > [ s12 ] > s13`

> *"暗桩眼线，随时禀报"* —— 在 Agent 关键节点插入拦截、改写和审计逻辑。
>
> **架构层**: 事件总线 + Hook 基类 + HookRegistry。

## 问题

s11 已经有了一个累积式 Agent：tool、skill、memory、todolist、subagent、Agent Team、MCP 都在。

但一些横切能力不适合继续塞进主循环里的 `if`：

- 打印每轮 LLM 耗时；
- 限制调用频率；
- 拦截危险命令；
- 阻止写入 `.env` 等敏感文件；
- 审计 `write_file` / `run_command`；
- 在模型调用前动态注入上下文。

s12 要解决的是：**不破坏主循环结构，也能在关键生命周期节点插入逻辑**。

## 解决方案

新增 Hooks 系统：

```text
before_turn
  -> LLM call
  -> before_tool_call
  -> tool execute
  -> after_tool_call
after_turn
```

每个 hook 是一个类，按需覆写事件方法；注册到 `HookRegistry` 后，主循环自动按顺序触发。

教学版内置 6 个 hook：

| Hook | 事件 | 作用 |
|------|------|------|
| `LoggingHook` | before/after turn | 打印 LLM 耗时和 token |
| `RateLimitHook` | before_turn | 窗口期调用限流 |
| `GuardHook` | before_tool_call | 阻止写敏感文件 |
| `BlockDangerousCommandHook` | before_tool_call | 拦截危险 shell 命令 |
| `ToolAuditHook` | after_tool_call | 记录写类和命令类工具调用 |
| `ContextInjectionHook` | before_turn | 动态注入当前工作目录 |

## 工作原理

1. `Hook` 基类定义生命周期事件。

```python
class Hook:
    def before_turn(self, ctx): pass
    def after_turn(self, ctx): pass
    def before_tool_call(self, ctx): pass
    def after_tool_call(self, ctx): pass
```

2. `HookRegistry.emit()` 按注册顺序执行。

```python
def emit(self, event, ctx):
    for hook in self._hooks:
        result = getattr(hook, event)(ctx)
        if result is not None:
            return result
```

规则很简单：

- hook 可以原地修改 `ctx`；
- hook 返回非 `None` 时短路；
- 单个 hook 抛异常时打印 `[hook error]`，继续 fail-open。

3. `before_turn` 可以拦截或改写模型调用。

```python
turn_ctx = {"system_prompt": build_system_prompt(), "history": history}
short = HOOKS.emit("before_turn", turn_ctx)
if isinstance(short, str):
    print(short)
```

`ContextInjectionHook` 就是修改 `turn_ctx["system_prompt"]`，让模型调用使用改写后的 prompt。

4. `before_tool_call` / `after_tool_call` 包住主 Agent 工具执行。

```python
tool_ctx = {"name": block.name, "input": block.input}
short = HOOKS.emit("before_tool_call", tool_ctx)
...
HOOKS.emit("after_tool_call", tool_ctx)
return tool_ctx.get("output", output)
```

`GuardHook` 和 `BlockDangerousCommandHook` 可以在工具执行前拒绝；`ToolAuditHook` 在执行后记录日志。

完整代码: [code/step12_hooks.py](../code/step12_hooks.py)

## 变更内容

| 组件 | 之前 (s11) | 之后 (s12) |
|------|------------|------------|
| MCP / Agent Team / subagent / plan | 已具备 | 继续保留 |
| 生命周期扩展 | 写死在主循环里 | 新增 `Hook` / `HookRegistry` |
| 模型调用前 | 直接调用 LLM | 先触发 `before_turn` |
| 工具调用前后 | 直接执行工具 | 触发 `before_tool_call` / `after_tool_call` |
| 审计 | 无统一入口 | 写操作追加到 `.hooks_audit.jsonl` |

## 试一试

```sh
python build-agent-example/code/step12_hooks.py
```

可以输入：

```text
写一份关于今日天气的奏折到 weather_memorial.txt
执行 ls -l
执行 rm -rf /
```

观察：

- `[hook:logging]` 打印耗时；
- `write_file` / `run_command` 被记录到 `.hooks_audit.jsonl`；
- 危险命令会被 before hook 拦截。
