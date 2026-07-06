# s02: 循环但无记忆 (Loop without Memory)

`s01 > [ s02 ] > s03 > s04 | s05 > s06 > s07 | s08 > s09 > s10 > s11 > s12 > s13`

> *"加上 while True"* —— 让对话能连续, 但模型还不会记得上一句。
>
> **Harness 层**: 最朴素的对话循环。

## 问题

s01 一问一答完就退出。要让用户能持续聊, 至少要包一层 `while True`。但仅此还不够 —— 每轮都是独立请求, 模型会"失忆", 你必须意识到这是 LLM 的本质属性。

## 解决方案

```
+--------+      +---------+
|  User  | <--> |   LLM   |   ← 每轮全新调用
| input  |      |  call   |     模型不知道上一轮说了什么
+--------+      +---------+
       (loop forever; messages 数组只有当前这一句)
```

## 工作原理

```python
while True:
    user_input = input("你: ")
    message = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": user_input}]   # ← 只有当前这一句
    )
    for block in message.content:
        if block.type == "text":
            print(f"[Agent回答]: {block.text}")
```

留意 `messages=` 里只塞了**当前**这一条 —— 没有 history 累积。

完整代码: [code/step02_loop_no_memory.py](../code/step02_loop_no_memory.py)

## 变更内容

| 组件   | 之前 (s01) | 之后 (s02)   |
|--------|------------|--------------|
| 循环   | 无         | `while True` |
| 多轮   | 否         | 是           |
| 记忆   | 无         | 仍然无       |

## 试一试

```sh
python build-agent-example/code/step02_loop_no_memory.py
```

试这两句, 看模型如何"失忆":

1. `我叫张三`
2. `我叫什么名字?`

第二句模型答不上来 —— 因为它根本没见过第一句。s03 修这个问题。
