# s08: 子代理 / 上下文隔离 (Subagent)

`s01 > s02 > s03 > s04 | s05 > s06 | s07 > [ s08 ]`

> *"派小太监去办差"* —— 把细节执行外包到独立上下文, 主线只听汇报。
>
> **架构层**: 嵌套循环 + 上下文压缩 + 角色化工具白名单。

## 问题

工具用得越多, 主 history 越脏:

- 一次 `web_fetch` 抓回 8000 字网页;
- 一次 `ls -R` 输出几百行;
- 反复试错的命令日志、粘进来的报错栈。

这些细节对**最终回答**几乎无用, 但会:

1. 占 token, 让对话越来越贵、越来越慢;
2. 稀释模型注意力, 后续推理质量下滑;
3. 触发上下文窗口上限, 早早把对话掐断。

s07 已经让主 agent 会列计划, 但计划里的某些步骤仍然可能产生大量中间输出。s08 要解决的是: **主线负责调度, 细节交给独立子代理执行**。

## 解决方案

把"细节执行"外包给一个**独立的 message loop** (子代理):

- 自己的 messages 列表 (不与主 history 共享);
- 自己的 system prompt (符合宫廷内官职位的人设);
- 自己的工具白名单 (按职位控制权限);
- 工具集**不含** `dispatch_subagent` (防递归) 与 `update_todos` (防状态污染);
- 跑完只把**最终一段文本**作为单条 `tool_result` 回传主 agent。

```
主 agent history:
  ... user ... assistant tool_use(dispatch_subagent) ... tool_result("总结 200 字") ...
                                                                    ▲
                                                                    │  子代理内部跑了 N 轮
                                                                    │  主线只收最终回禀
   子代理独立上下文 (用完即弃):
       user(差事) → llm → tool → llm → tool → ... → final_text
       └────────────── 只这一段回传主 agent ──────────────┘
```

## 子代理身份

这版不再只有一个"通用小太监", 而是预设多种宫廷职位。主 agent 派发时通过 `agent_type` 选择身份, 原则是**权限最窄、职司最贴合**。

| agent_type | 宫廷职位 | 适合任务 | 工具权限 |
|------------|----------|----------|----------|
| `xiaohuangmen` | 通传小黄门 | 短命令、快速确认、跑腿探路 | 只读: `run_command/read_file/glob/grep` |
| `sili_suitang` | 司礼监随堂小太监 | 阅读代码、查文书、整理提纲 | 只读: `load_skill/read_file/glob/grep` |
| `dongchang_tanshi` | 东厂探事小太监 | 抓网页、查资料、探索性搜索 | 只读: `run_command/web_fetch/load_skill/read_file/glob/grep` |
| `shangbao_dianbu` | 尚宝监典簿小太监 | 盘点文件、校对清单、检查遗漏 | 只读: `run_command/read_file/glob/grep` |
| `neiguan_yingzao` | 内官监营造小太监 | 修改文件、搭建工程、跑命令验收 | 可读写: `run_command/web_fetch/load_skill/read_file/write_file/glob/grep` |

旧版的 `researcher/general` 仍然兼容:

```python
SUBAGENT_ALIASES = {
    "researcher": "dongchang_tanshi",
    "general": "neiguan_yingzao",
}
```

## 工作原理

1. **公共工具分发函数**, 主/子共用。

除了 s06 的三件套, 这版还补了文件类工具, 方便不同职位按白名单取用:

```python
def execute_basic_tool(block, prefix=""):
    if block.name == "run_command":
        return subprocess.run(block.input["command"], ...).stdout
    if block.name == "web_fetch":
        return web_fetch(block.input["url"], ...)
    if block.name == "read_file":
        return Path(block.input["path"]).read_text(...)
    if block.name == "write_file":
        Path(block.input["path"]).write_text(...)
```

`prefix="子(东厂探事小太监)·"` 让终端打印能区分主/子上下文, 也能看出是谁在办差。

2. **角色配置表**, prompt 与工具白名单分离。

身份写在 `SUBAGENT_SPECS`, 每个职位都有 `title / system_prompt / tools / max_turns`:

```python
SUBAGENT_SPECS = {
    "dongchang_tanshi": {
        "title": "东厂探事小太监",
        "system_prompt": build_subagent_prompt(...),
        "tools": ["run_command", "web_fetch", "load_skill", "read_file", "glob", "grep"],
        "max_turns": 15,
    },
    "neiguan_yingzao": {
        "title": "内官监营造小太监",
        "tools": ["run_command", "web_fetch", "load_skill", "read_file", "write_file", "glob", "grep"],
        "max_turns": 20,
    },
}
```

注意: prompt 只负责人设和职责说明, **真正的权限由 `tools` 白名单决定**。比如司礼监随堂小太监即使想写文件, 也拿不到 `write_file` 工具。

3. **子代理函数**, 独立 messages, max_turns 上限防跑飞。

```python
def run_subagent(task, agent_type="neiguan_yingzao", purpose="", max_turns=None):
    agent_type = resolve_subagent_type(agent_type)
    spec = SUBAGENT_SPECS[agent_type]
    tools = [_TOOL_SCHEMAS[t] for t in spec["tools"]]

    messages = [{"role": "user", "content": task}]
    for _ in range(max_turns or spec["max_turns"]):
        msg = client.messages.create(
            model=MODEL,
            system=spec["system_prompt"],
            tools=tools,
            messages=messages,
        )
        ...
```

4. **主 agent 多一个工具** `dispatch_subagent(task, agent_type, purpose)`。

模型派发时必须传入 `agent_type`, schema 枚举来自:

```python
SUBAGENT_TYPE_OPTIONS = list(SUBAGENT_SPECS.keys())
```

主 prompt 中也写明了身份选择规则:

```text
优先选择权限最窄、职司最贴合的身份:
- xiaohuangmen: 轻量只读
- sili_suitang: 只读文书
- dongchang_tanshi: 只读查访
- shangbao_dianbu: 只读核验
- neiguan_yingzao: 可读写可执行
```

5. **多个子代理可并发派遣**。

如果同一次模型回复里出现多个 `dispatch_subagent`, 主循环用 `ThreadPoolExecutor` 并发执行:

```python
if len(dispatch_blocks) > 1:
    with ThreadPoolExecutor(max_workers=len(dispatch_blocks)) as pool:
        for block_id, summary in pool.map(_run_one, dispatch_blocks):
            results_map[block_id] = summary
```

最后仍然按原始 tool_use 顺序组装 `tool_results`, 避免并发完成顺序影响 Anthropic API 对 tool_result 的匹配。

6. **可视化上下文压缩**。

```
[派遣小太监 #1(东厂探事小太监 / dongchang_tanshi)]: 抓 HN 头条
  ┌── subagent context start ──
  [子(东厂探事小太监)·网页获取]: https://news.ycombinator.com
  [子(东厂探事小太监)·内容搜索]: ...
  └── subagent context end (内部 4 轮, 回传 215 字) ──
[小太监回禀]: ...(摘要)...
[主上下文压缩]: 子代理仅向主 history 追加 215 字
```

7. **沿用 s07 的收尾闭环**。

主 agent 的外层 `while` 在 `stop_reason != "tool_use"` 时仍会校验 `TODOS`, 残单回推 + `continue`, 全完则 `TODOS = []` 重置。

子代理工具集**不含** `update_todos`, 所以它无法私改主 todolist; 它的"完成"只是子任务局部完成, 是否真办妥仍由主 agent 在收尾时校验。

完整代码: [code/step08_subagent.py](../code/step08_subagent.py)

## 变更内容

| 组件 | 之前 (s07) | 之后 (s08) |
|------|------------|------------|
| 工具数 | 4 | 5 (新增 `dispatch_subagent`) |
| 上下文模型 | 单一 history | 主 + 子隔离 |
| 子代理身份 | 无 | 5 种宫廷职位预设 |
| 工具分发 | 重复 if/elif | 抽出 `execute_basic_tool` 共用 |
| 子代理权限 | 无分层 | 按职位配置工具白名单 |
| 并发能力 | 无 | 多个 `dispatch_subagent` 可并发执行 |
| 防御机制 | 单 in_progress + 残单回推 | + `max_turns` 限流 + 无递归 + 无私改 todo |
| 终端打印 | 单层 | 双层缩进, 显示职位与 context start/end |
| 收尾闭环 | 已具备 (校验 + 重置) | 沿用; 子代理只回传局部结果 |

## 试一试

```sh
python build-agent-example/code/step08_subagent.py
```

- `派通传小黄门去 ls -1 build-agent-example/code/, 把文件名整理成一句话回报朕`
- `派司礼监随堂小太监阅读 build-agent-example/code/step08_subagent.py, 总结子代理身份有哪些`
- `派东厂探事小太监去抓 https://example.com 和 https://example.org, 给朕一句话总结两站差异`
- `派尚宝监典簿小太监清点 build-agent-example/code 下每个 step 文件, 看是否从 01 到 08 连续`
- `朕要修改一个 demo 文件 -- 派内官监营造小太监去创建 tmp/demo.txt, 写入一句话后回报`

观察 `[派遣小太监 #N(职位 / agent_type)] ... [小太监回禀] ... [主上下文压缩]: X 字` —— 子代理跑了多少轮工具调用, 主 history 都只多 1 条结果。

教学闭环完成: s05 让 agent 能动手, s07 让它会规划, s08 让它能按职位委派。
