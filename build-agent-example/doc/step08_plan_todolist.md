# s08: 计划与 Todolist (Plan)

`s01 > s02 > s03 > s04 | s05 > s06 > s07 | [ s08 ] > s09 > s10 > s11 > s12 > s13`

> *"先列清单, 再逐项执行"* —— 把"想清楚"显式化、可观测、可强约束。
>
> **架构层**: 工具不只接收命令, 也输出**结构化任务状态**。

## 问题

复杂任务步数一多 (5、10 步以上), 模型容易:

- **跳步** —— 漏掉中间环节;
- **反复折返** —— 把已做完的事再做一遍;
- **失焦** —— 做着做着偏题。

需要让它**先把整个流程写下来**, 再按表执行。这就是 plan / todolist。

## 解决方案

新增 `update_todos` 工具: 模型把整份 todolist **一次性全量传进来** (而非增量), 每项有 `id / content / status (pending|in_progress|completed)`。每完成一步就再调一次工具更新状态。

```
plan once  →  update status step by step  →  done
   ▲                  ▲                       ▲
   └ update_todos     └ update_todos          └ 终端可视化检查
```

注意: 这里的 plan 不是单独的 Python 函数, 而是**让模型调用的工具** + **内存状态** + **system prompt 约束** 三者配合。

## 工作原理

1. 内存中维护 `TODOS: list[dict]`, 终端渲染:

```
[~] 1. 抓三个网页
[ ] 2. 整理标题清单
[ ] 3. 用一段话总结
```

2. **system prompt 立规矩** (这步开始, prompt 中出现"行事规矩"段):

> 1. 多步骤任务先调用 update_todos 拆计划。
> 2. 开始某一步前改 in_progress (同一时间至多一项), 完成立刻改 completed, 再开始下一项。
> 3. 简单一句话问答不必生成 todolist。

3. **工具内置校验**, 让模型自纠:

```python
def update_todos(todos):
    in_progress = [t for t in todos if t["status"] == "in_progress"]
    if len(in_progress) > 1:
        return "Error: 同一时间只能有一个 in_progress 任务，请重新规划。"
    TODOS = todos
    return f"todos updated: ...\n\n当前列表：\n{render_todos(TODOS)}"
```

4. **工具返回值带上当前清单文本**, 下一轮模型直接看到自己的进度, 不需要额外查询。

5. **收尾闭环 —— 完成校验 + 重置**: 模型自报"办完了"靠不住, 它常会在还有 `pending` 任务时就给最终回答; 同时上一轮残留的 todolist 也会污染下一轮新差事。所以在 `stop_reason != "tool_use"` 那一刻, 由代码再校一遍:

```python
if message.stop_reason != "tool_use":
    reply = next(b.text for b in message.content if b.type == "text")
    print(f"[Agent回答]: {reply}")
    if TODOS:
        unfinished = [t for t in TODOS if t["status"] != "completed"]
        if unfinished:
            history.append({"role": "user", "content":
                "差事尚未办妥, 以下任务仍未完成, 请按计划继续执行:\n"
                + render_todos(TODOS)})
            continue                  # 残单回推, 让模型继续干
        print("[最终计划状态 - 全部办妥]")
        TODOS = []                    # 收口重置, 下一轮从空白开始
    break
```

- **未完成**: 把残单作为 user 消息塞回 history, `continue` 内层循环让模型继续推进、按规矩更新状态。
- **全完成**: 打印最终清单后 `TODOS = []` 清空, 防止上一差事的计划状态串场到下一轮新差事。
- **无 todolist 的简单问答**: 直接 `break`, 不受影响。

完整代码: [code/step08_plan_todolist.py](../code/step08_plan_todolist.py)

## 变更内容

| 组件                | 之前 (s07)        | 之后 (s08)                              |
|---------------------|-------------------|-----------------------------------------|
| 工具数              | 3                 | 4 (新增 `update_todos`)                 |
| 任务状态            | 隐式 (在模型脑里) | 显式 (内存 list + 终端可视化)           |
| 复杂任务可控性      | 弱                | 可观测、可强约束 (in_progress 单点限制) |
| system prompt       | 仅人设            | 人设 + 行事规矩                         |
| 收尾                | 模型说完就完      | 代码校验残单: 未完回推 / 完成则重置     |

## 试一试

```sh
python build-agent-example/code/step08_plan_todolist.py
```

- `创建一个 demo 项目: 新建目录 demo, 在里面写 README.md 和一个能跑的 hello.py, 跑通后告诉朕结果`
- `朕要写一篇博文 -- 先帮朕拟提纲, 再写引言, 再写正文三段, 最后写结语`

观察终端: 先打印 `[计划已更新]` 给出 todo 清单 → 逐项 `[~]` → `[x]` → 回答结束时若仍有未完项, 看到 `[计划尚未办妥, 继续执行...]` 然后模型自动续跑; 全部 `[x]` 时打印 `[最终计划状态 - 全部办妥]`, `TODOS` 立刻清空, 下一轮从空白开始。

模型现在会规划了, 但执行细节 (大量工具输出) 仍然全部进了主 history。这是 s09 要处理的事。
