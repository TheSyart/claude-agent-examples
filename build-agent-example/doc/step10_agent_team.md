# s10: Agent Team（固定班底）

`s01 > s02 > s03 > s04 | s05 > s06 > s07 | s08 > s09 > [ s10 ] > s11 > s12 > s13`

> *"养一支班底"* —— 不再只是临时派差，而是让有名字、有职司、有 inbox 的队友持续协作。
>
> **架构层**: 持久 teammate loop + 文件 inbox + team config + lead 调度。

## 问题

s09 的 `dispatch_subagent` 适合一次性差事：派出去、办完、回传总结、上下文销毁。

但有些任务不是一次性的：

- 长期项目需要固定的 coder / reviewer / researcher；
- 一个角色做完当前步骤后，还要等待下一条指令；
- 多个角色之间需要互相发消息；
- 用户需要观察队友状态，而不是只看一次工具调用结果。

s10 要解决的是：**从临时子代理升级到固定 Agent Team**。

## 解决方案

新增两个核心对象：

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

主 Agent 新增 team 工具：

| 工具 | 说明 |
|------|------|
| `spawn_teammate` | 召入固定队友，启动独立线程 |
| `list_teammates` | 查看队友名字、职司和状态 |
| `send_message` | 给某位队友发送 inbox 消息 |
| `read_inbox` | 读取 lead 自己的 inbox |
| `broadcast` | 向所有队友广播消息 |

## 工作原理

1. `MessageBus.send()` 把消息写入目标 inbox。

```python
BUS.send("lead", "alice", "请检查这个 bug")
```

会追加一行 JSON 到：

```text
.team/inbox/alice.jsonl
```

2. `MessageBus.read_inbox()` 读取后清空 inbox。

```python
messages = BUS.read_inbox("alice")
```

这让文件 inbox 像一个最小消息队列：消息读过就不会重复处理。

3. `spawn_teammate` 启动固定队友线程。

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
  └─ 继续轮询 inbox
```

队友完成当前任务后不会销毁，而是进入 `idle`，继续等待后续消息。

4. 状态写入 `.team/config.json`。

| status | 含义 |
|--------|------|
| `working` | 本进程里队友线程正在工作 |
| `idle` | 本进程里队友线程在等待 inbox |
| `offline` | config 里有队友，但当前进程没有线程 |
| `shutdown` | 队友收到退出请求后停止 |

程序重启后旧线程消失，代码会把遗留的 `idle/working` 改成 `offline`。要让队友继续处理 inbox，需要再次 `spawn_teammate` 唤回同名队友。

5. 主 Agent 区分两种调度。

```text
dispatch_subagent = 临时派一次差，办完即散
spawn_teammate = 固定班底，可持续协作
```

完整代码: [code/step10_agent_team.py](../code/step10_agent_team.py)

## 变更内容

| 组件 | 之前 (s09) | 之后 (s10) |
|------|------------|------------|
| 子代理 | `dispatch_subagent` 临时派差 | 继续保留 |
| 固定成员 | 无 | `spawn_teammate` 创建持久队友 |
| 通信 | 子代理只回传一次总结 | 文件 inbox 支持 lead 与队友通信 |
| 状态 | 无持久团队状态 | `.team/config.json` 记录成员状态 |
| CLI | 普通对话 | 新增 `/team` 和 `/inbox` |

## 试一试

```sh
python build-agent-example/code/step10_agent_team.py
```

可以输入：

```text
皇上要组一个小队：alice 做 coder，bob 做 reviewer。先让 alice 写一个 hello.py，再让 bob 等 alice 回禀后检查。
```

也可以输入：

```text
/team
/inbox
```

观察 `.team/config.json` 和 `.team/inbox/*.jsonl`，能看到固定队友、状态和消息流动。
