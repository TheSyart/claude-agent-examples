# s01: 单次 LLM 调用 (One-shot Call)

`[ s01 ] > s02 > s03 > s04 | s05 > s06 > s07 | s08 > s09 > s10 > s11 > s12 > s13`

> *"先把电话打通"* —— 让 Python 能调起 Claude, 拿到一段回答。
>
> **API 层**: 一次请求 → 一次响应。

## 问题

要让 LLM 帮你做事, 第一步是能调起来。光读官方文档不够 —— 你需要一个最小可跑的 Python 文件: 输入一句话, 拿到一段回答。在这之前, 任何"agent"都谈不上。

## 解决方案

```
+--------+      +---------+      +-----------+
|  User  | ---> |   LLM   | ---> |   Print   |
| input  |      |  call   |      |   reply   |
+--------+      +---------+      +-----------+
                  (one shot — no loop, no memory)
```

## 工作原理

1. 加载环境变量, 构造 Anthropic 客户端。

```python
load_dotenv()
client = anthropic.Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    base_url=os.environ["ANTHROPIC_BASE_URL"],
)
```

2. 读一行用户输入。

```python
user_input = input("你: ")
```

3. 调用 Messages API, 模型返回 `content` 数组 (含 text block)。

```python
message = client.messages.create(
    model="qwen3.6-35b-a3b-ud-mlx",
    max_tokens=1000,
    messages=[{"role": "user", "content": user_input}]
)
```

4. 取出 text 打印, 程序结束。

```python
for block in message.content:
    if block.type == "text":
        print(f"[Agent回答]: {block.text}")
```

完整代码: [code/step01_single_call.py](../code/step01_single_call.py)

## 变更内容

| 组件      | 之前 | 之后                              |
|-----------|------|-----------------------------------|
| API 客户端 | 无   | `anthropic.Anthropic(...)`        |
| 调用      | 无   | `client.messages.create(...)`     |
| 循环      | 无   | 无 (一次性)                       |
| 记忆      | 无   | 无                                |

## 试一试

```sh
python build-agent-example/code/step01_single_call.py
```

- `你好`
- `用一句话解释什么是 LLM`

跑完一次程序就退出 —— 你想接着问第二轮? 这正是 s02 要解决的问题。
