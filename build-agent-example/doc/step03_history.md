# s03: 上下文记忆 (History)

`s01 > s02 > [ s03 ] > s04 | s05 > s06 > s07 | s08 > s09 > s10 > s11 > s12 > s13`

> *"把消息存起来, 每轮全部回灌"* —— 不是模型在记忆, 是你在喂回去。
>
> **Harness 层**: messages 数组累积 —— LLM 状态机的本质。

## 问题

s02 每轮独立调用, 模型不知道上一句。要让它记得, 必须**每轮把全部历史发回去** —— LLM 本身是无状态的, "记忆"完全由你这一端的 messages 列表撑起来。这是理解所有 agent 的基础: **上下文 = 你手里那个 list, 不是别的**。

## 解决方案

```
turn 1:  [user1]                              → LLM → assistant1
turn 2:  [user1, assistant1, user2]           → LLM → assistant2
turn 3:  [user1, assistant1, user2, ...]      → LLM → assistant3
         └────── 每轮全部回灌 ─────┘
```

## 工作原理

```python
history = []

while True:
    user_input = input("你: ")
    history.append({"role": "user", "content": user_input})

    message = client.messages.create(
        model=MODEL, max_tokens=1000, messages=history,
    )

    reply = next(b.text for b in message.content if b.type == "text")
    print(f"[Agent回答]: {reply}")
    history.append({"role": "assistant", "content": reply})   # ← 关键: 答复也要 append
```

漏掉最后一行 (不 append assistant 的回答), 模型就只记得 user 那一边的对话, 一样不对。

完整代码: [code/step03_history.py](../code/step03_history.py)

## 变更内容

| 组件          | 之前 (s02) | 之后 (s03)             |
|---------------|------------|------------------------|
| messages 来源 | 单条       | 累积 list (`history`)  |
| 记忆          | 无         | 完整对话历史           |
| Token 成本    | 恒定       | 随轮数线性增长         |

## 试一试

```sh
python build-agent-example/code/step03_history.py
```

1. `我叫张三`
2. `我叫什么名字?` ← 这次能答出"张三"

副作用: 越聊 token 越多。后面 s07 会把短期 history 压缩沉淀到长期记忆。
