# s09: Agent Team（固定班底）

`s01 > s02 > s03 > s04 | s05 > s06 | s07 > s08 > [ s09 ]`

> *"养一支班底"* —— 不再只是临时派小太监，而是让多个有名字、有职司、有 inbox 的队友持续协作。
>
> **架构层**: 持久 teammate loop + 文件 inbox + team config + lead 调度。

## 问题

s08 的 `dispatch_subagent` 解决的是上下文污染：复杂细节交给一次性子代理，主线只听最后回禀。

但有些任务不是“一次查完就散”：

- 长期项目需要固定 coder / reviewer / researcher；
- 一个角色做完当前步骤后，还要等后续指令继续工作；
- 多个角色之间需要互相发消息，而不是只把总结回传给主 agent；
- 你想观察“团队成员状态”，而不是只看一次工具调用结果。

这就是 s09 的 agent team：**固定班底，可持续协作**。

## 解决方案

新增两块核心结构：

| 组件 | 作用 |
|------|------|
| `MessageBus` | 每个队友一个 JSONL inbox，发送消息就是追加一行，读取消息就是 drain 后清空 |
| `TeammateManager` | 管理团队配置、成员状态、线程启动和 teammate loop |

运行期文件放在：

```text
build-agent-example/code/.team/
├── config.json
└── inbox/
    ├── lead.jsonl
    ├── alice.jsonl
    └── reviewer.jsonl
```

`.team/config.json` 记录组织结构：

```json
{
  "team_name": "default",
  "members": [
    {"name": "alice", "role": "coder", "status": "offline"}
  ]
}
```

成员状态含义：

| status | 含义 |
|--------|------|
| `working` | 本进程里有队友线程，正在处理任务 |
| `idle` | 本进程里有队友线程，正在等待 inbox |
| `offline` | config 里有这个队友，但当前进程没有对应线程 |
| `shutdown` | 队友收到 shutdown 请求后主动退出 |

进程关闭后，Python 线程会消失；再次启动时，程序会把上次遗留的 `idle/working` 队友标成 `offline`。这表示“花名册还在，但人没有上线”。要让它继续处理 inbox，需要再次调用 `spawn_teammate` 唤回同名队友。

## Subagent vs Agent Team

两者不冲突，解决的问题不同：

| 形态 | 生命周期 | 通信方式 | 适合场景 |
|------|----------|----------|----------|
| `dispatch_subagent` | 临时创建，办完即散 | 只给主 agent 回传一段总结 | 上下文隔离、一次性探索、并发查找 |
| `spawn_teammate` | 固定成员，空闲后继续等待 | 通过 inbox 给 lead 或队友发消息 | 长期项目、多角色协作、持续分工 |

一句话：

```text
subagent = 派一次差
agent team = 养一支班底
```

s09 保留 s08 的 `dispatch_subagent`，同时新增 team 工具。这样教学上能看清楚：临时外包和固定团队是两层能力，不是二选一。

## 新增工具

主 agent 在 s09 中多了 5 个 team 工具：

| 工具 | 说明 |
|------|------|
| `spawn_teammate` | 召入固定队友，启动独立线程和 teammate loop |
| `list_teammates` | 查看团队成员、职司和状态 |
| `send_message` | 给某位队友发送 inbox 消息 |
| `read_inbox` | 读取 lead 自己的 inbox，查看队友回禀 |
| `broadcast` | 向所有队友广播消息 |

teammate 自己也能使用：

- 基础工具：`run_command/web_fetch/load_skill/read_file/write_file/glob/grep`
- 通信工具：`send_message/read_inbox`

但 teammate **不能**调用 `dispatch_subagent`。这是为了让教学示例保持简单，避免“队友再派子代理再组队”的递归调度把概念搅浑。

## 工作原理

### 1. 发送消息：写入 JSONL

```python
BUS.send("lead", "alice", "请检查这个 bug")
```

会追加一行到：

```text
.team/inbox/alice.jsonl
```

消息格式：

```json
{
  "type": "message",
  "from": "lead",
  "content": "请检查这个 bug",
  "timestamp": 1710000000.0
}
```

### 2. 读取消息：drain inbox

```python
BUS.read_inbox("alice")
```

读取所有 JSONL 行后立刻清空文件。这样 inbox 像一个最小消息队列：消息不会反复处理。

### 3. 固定队友：独立线程 + 独立 messages

`spawn_teammate` 会启动一个 daemon thread：

```text
lead tool_use(spawn_teammate)
        │
        ▼
Thread: alice
  teammate_loop
  ├─ 处理初始 prompt
  ├─ 调用工具完成任务
  ├─ send_message 回禀 lead
  ├─ status -> idle
  └─ 继续轮询 inbox，等待下一件差事
```

队友完成当前任务后不会销毁，而是回到 `idle`，继续等待后续 inbox。

如果关闭程序再启动：

```text
config.json 还在
inbox/*.jsonl 还在
旧 teammate 线程不在
状态自动改为 offline
再次 spawn_teammate("alice", ...) 后重新上线
```

### 4. Lead 负责调度

主 agent 的 system prompt 会要求它区分：

- 一次性复杂细节：派 `dispatch_subagent`
- 长期固定协作：用 `spawn_teammate`

所以同一个任务里可以同时存在：

```text
Lead Agent
├── dispatch_subagent: 临时查资料
└── Agent Team
    ├── alice: coder
    └── bob: reviewer
```

## 快捷命令

```sh
python build-agent-example/code/step09_agent_team.py
```

启动后可输入：

```text
/team
```

查看当前固定队友：

```text
Team: default
  - alice（coder）：idle
```

输入：

```text
/inbox
```

读取并清空 lead 的 inbox。

## 试一试

```text
皇上要组一个小队：alice 做 coder，bob 做 reviewer。先让 alice 写一个 hello.py，再让 bob 等 alice 回禀后检查。
```

再试：

```text
给所有队友广播：现在先暂停手头差事，回禀各自当前状态。
```

观察 `.team/config.json` 和 `.team/inbox/*.jsonl`，就能看到 agent team 的两个核心信号：

- 团队成员是持久记录的；
- 协作消息是通过 inbox 异步流动的。
- 重启程序后，成员会显示为 `offline`，再次召入同名队友后才会继续处理消息。

## 教学版限制

这一步故意保持小而清楚：

- inbox 是文件 JSONL，不是数据库或消息队列；
- 线程生命周期很简单，`shutdown_request` 只是预留了消息类型和基础处理；
- 文件读写只适合教学演示，不代表生产级并发安全；
- teammate 不再派 subagent，避免递归调度分散注意力。

下一步如果继续演进，可以讲：优雅 shutdown、计划审批、队友投票、消息锁、任务路由和 team memory。
