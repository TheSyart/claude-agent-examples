# s13: 目标驱动 (Goal)

`s01 > s02 > s03 > s04 | s05 > s06 > s07 | s08 > s09 > s10 > s11 > s12 > [ s13 ]`

> *"领旨后自会把差事办妥"* —— 接到复杂目标后，Agent 自己拆分、推进、验收。
>
> **架构层**: 目标树 + 依赖 + 自主循环 + 完成判定。

## 问题

s12 之前的 Agent 主要是被动响应：用户说一句，Agent 跑一轮，最后回一句。

复杂目标会让用户不断催促：

- “先列出文件”；
- “再统计行数”；
- “再找最长文件”；
- “最后写报告”。

这会让多步骤任务的控制权停留在用户手里。s13 要解决的是：**用户给出一个高层目标，Agent 自主拆分、执行、推进，直到目标完成或达到轮数上限**。

## 解决方案

新增 Goal 系统：

| 组件 | 作用 |
|------|------|
| `Goal` | 单个目标节点 |
| `GoalStore` | 目标树存储、渲染、持久化 |
| `update_goals` | 让模型全量更新目标树 |
| `/goal` | 进入自主目标模式 |

目标节点包含：

```python
{
    "id": "count_lines",
    "content": "统计每个 .py 文件的行数",
    "status": "pending",
    "parent_id": "root",
    "depends_on": ["list_files"],
    "success_criteria": "得到每个文件的行数",
    "result": ""
}
```

用户输入：

```text
/goal 统计当前目录下所有 .py 文件行数，并告诉我哪个文件最长
```

Agent 会多轮自主推进，直到目标树没有 `pending / in_progress`，或达到 `MAX_AUTONOMOUS_TURNS`。

## 工作原理

1. `GoalStore` 管理目标树。

```python
GOAL_STORE = GoalStore(GOALS_FILE)
GOAL_STORE.load_latest()
```

目标状态支持：

```text
pending / in_progress / completed / failed
```

每次更新会追加快照到 `.goals.jsonl`。

2. `update_goals` 工具让模型维护目标树。

```python
elif name == "update_goals":
    errors = GOAL_STORE.update(inp.get("goals", []))
    output = "目标树已更新：\n" + GOAL_STORE.render()
```

它和 `update_todos` 一样采用全量覆盖，避免模型只传增量导致状态丢失。

3. `build_system_prompt()` 注入当前目标树。

```python
【Goal 目标树】
当前目标树：
{GOAL_STORE.render()}
```

模型每轮都能看到最新目标状态，知道下一步该推进哪个目标。

4. `/goal` 进入自主循环。

```python
def run_goal_mode(goal_text, history):
    GOAL_STORE.update([{"id": "root", "content": goal_text, "status": "in_progress"}])
    for turn in range(MAX_AUTONOMOUS_TURNS):
        final_reply = run_agent_cycle(history)
        if "DONE:" in final_reply:
            GOAL_STORE.complete("root", final_reply)
        if not GOAL_STORE.has_pending_or_in_progress():
            break
```

`run_agent_cycle()` 仍然是完整的 s12 Agent 循环，所以 goal 模式下依然能使用 tool、skill、memory、todolist、subagent、Agent Team、MCP 和 Hooks。

完整代码: [code/step13_goal.py](../code/step13_goal.py)

## 变更内容

| 组件 | 之前 (s12) | 之后 (s13) |
|------|------------|------------|
| Hooks / MCP / Team / Subagent / Plan | 已具备 | 继续保留 |
| 长期目标 | 无统一结构 | 新增 `Goal` / `GoalStore` |
| 目标推进 | 用户一轮轮催促 | `/goal` 自主循环推进 |
| 状态持久化 | todolist 只在内存 | goal 快照写入 `.goals.jsonl` |
| 完成判定 | 普通回复结束 | 目标树完成或回复包含 `DONE:` |

## 试一试

```sh
python build-agent-example/code/step13_goal.py
```

可以输入：

```text
/goal 找出当前目录下所有 .py 文件，并把它们的名字写入 files.txt
/goals
```

观察：

- Agent 会先创建目标树；
- 中途可调用所有前面步骤积累的能力；
- 目标状态会写入 `.goals.jsonl`；
- 完成后最终回复应包含 `DONE:`。
