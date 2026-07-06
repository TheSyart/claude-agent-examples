# s09: 子代理 / 上下文隔离 (Subagent)

`s01 > s02 > s03 > s04 | s05 > s06 > s07 | s08 > [ s09 ] > s10 > s11 > s12 > s13`

> *"派小太监去办差"* —— 把细节执行外包到独立上下文，主线只听回禀。
>
> **架构层**: 嵌套 message loop + 上下文隔离 + 工具白名单。

## 问题

s08 已经让 Agent 会先列 todolist 再执行，但执行细节仍然全部进入主 `history`。

例如：

- `web_fetch` 抓回几千字网页；
- `run_command` 输出几百行日志；
- 多次试错产生大量中间报错。

这些信息对最终回答通常只需要一个结论，但它们会污染主上下文、增加 token 成本、稀释模型注意力。s09 要解决的是：**主 Agent 负责调度，细节交给独立子代理执行**。

## 解决方案

新增 `dispatch_subagent` 工具。主 Agent 遇到细节繁多、适合隔离的差事时，把任务交给一个独立子代理：

```
主 Agent history
  user -> assistant(tool_use dispatch_subagent) -> tool_result("简短回禀") -> final

子代理内部 history
  task -> tool_use -> tool_result -> tool_use -> ... -> final summary
```

子代理有自己的 `messages`、自己的 `system prompt`、自己的工具白名单。它内部跑了多少工具、读了多少文件，都不会直接进入主 Agent 的 `history`；主 Agent 只收到最后一段总结。

## 工作原理

1. 把基础工具 schema 集中到 `_TOOL_SCHEMAS`。

```python
_TOOL_SCHEMAS = {
    "run_command": {...},
    "web_fetch": {...},
    "load_skill": {...},
    "read_file": {...},
    "write_file": {...},
    "glob": {...},
    "grep": {...},
}
```

主 Agent 和子代理都从这里取工具定义，避免两边重复维护。

2. 用 `SUBAGENT_SPECS` 定义子代理身份和权限。

| agent_type | 宫廷职位 | 适合任务 | 工具权限 |
|------------|----------|----------|----------|
| `xiaohuangmen` | 通传小黄门 | 快速确认、跑腿探路 | 只读 |
| `sili_suitang` | 司礼监随堂小太监 | 阅读代码、整理提纲 | 只读文书 |
| `dongchang_tanshi` | 东厂探事小太监 | 抓网页、查资料 | 只读查访 |
| `shangbao_dianbu` | 尚宝监典簿小太监 | 盘点、核验、对账 | 只读核验 |
| `neiguan_yingzao` | 内官监营造小太监 | 改文件、搭工程、验收 | 可读写 |

prompt 只负责人设和职责，真正权限由 `tools` 白名单决定。子代理拿不到 `dispatch_subagent`，所以不能递归派遣；也拿不到 `update_todos`，所以不会污染主计划状态。

3. `run_subagent()` 启动独立循环。

```python
def run_subagent(task, agent_type="neiguan_yingzao", purpose=""):
    spec = SUBAGENT_SPECS[agent_type]
    tools = [_TOOL_SCHEMAS[t] for t in spec["tools"]]
    messages = [{"role": "user", "content": task}]

    for _ in range(spec["max_turns"]):
        msg = client.messages.create(
            model=MODEL,
            system=spec["system_prompt"],
            tools=tools,
            messages=messages,
        )
```

关键点：

- `messages` 从一条任务说明开始，不共享主 `history`；
- `tools` 按身份白名单注入；
- `max_turns` 防止子代理无限循环；
- 最后只返回一段 `final` 文本。

4. 主循环识别 `dispatch_subagent`。

如果模型在同一轮发出多个 `dispatch_subagent`，代码用 `ThreadPoolExecutor` 并发执行，再按原始 `tool_use` 顺序回填 `tool_result`。

```python
if len(dispatch_blocks) > 1:
    with ThreadPoolExecutor(max_workers=len(dispatch_blocks)) as pool:
        for block_id, summary in pool.map(_run_one, dispatch_blocks):
            results_map[block_id] = summary
```

完整代码: [code/step09_subagent.py](../code/step09_subagent.py)

## 变更内容

| 组件 | 之前 (s08) | 之后 (s09) |
|------|------------|------------|
| 计划 | `update_todos` 管理主任务 | 继续保留 |
| 工具 schema | 主 Agent 自己使用 | 集中到 `_TOOL_SCHEMAS`，主/子共用 |
| 执行上下文 | 所有工具结果进入主 `history` | 子代理内部执行，主线只收总结 |
| 权限控制 | 主 Agent 工具固定 | 子代理按身份使用工具白名单 |
| 并发 | 主线顺序执行 | 多个子代理可并发派遣 |

## 试一试

```sh
python build-agent-example/code/step09_subagent.py
```

可以输入：

```text
同时派三个小太监，分别统计 step01、step02、step03 的代码行数，最后汇总。
```

观察终端：

- 子代理会打印 `subagent context start/end`；
- 主上下文只追加子代理最终回禀；
- 多个互不依赖的派遣可以并发执行。
