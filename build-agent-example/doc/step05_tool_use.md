# s05: 工具调用 (Tool Use) —— 真正的 Agent 循环

`s01 > s02 > s03 > s04 | [ s05 ] > s06 > s07 | s08 > s09 > s10 > s11 > s12 > s13`

> *"One loop & Bash is all you need"* —— 一个工具 + 一个循环 = 一个 Agent。
>
> **Harness 层**: tool_use 循环 —— 模型与真实世界的第一道连接。

## 问题

前 4 步, 模型只会"说话"。要让它读文件、跑测试、看报错, 必须给它**工具**, 并在它返回 `tool_use` 时, 把执行结果回灌进去, 让它再决定下一步。手动粘结果不可持续, **循环**就是解。

## 解决方案

```
+--------+      +-------+      +---------+
|  User  | ---> |  LLM  | ---> |  Tool   |
| prompt |      |       |      | execute |
+--------+      +---+---+      +----+----+
                    ^                |
                    |   tool_result  |
                    +----------------+
              (loop while stop_reason == "tool_use")
```

一个退出条件控制整个流程: 模型不再调工具, 循环就停。

## 工作原理

1. 声明工具 schema:

```python
TOOLS = [{
    "name": "run_command",
    "description": "在终端执行一条 shell 命令并返回输出",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"}},
        "required": ["command"]
    }
}]
```

2. 内层循环: 模型若返回 `tool_use`, 执行并把结果作为 user 消息回灌:

```python
while True:
    message = client.messages.create(
        model=MODEL, system=SYSTEM_PROMPT,
        tools=TOOLS, messages=history,
    )
    history.append({"role": "assistant", "content": message.content})

    if message.stop_reason != "tool_use":
        break    # ← 退出条件: 模型不再调工具

    tool_results = []
    for block in message.content:
        if block.type == "tool_use":
            output = run_command(block.input["command"])
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
    history.append({"role": "user", "content": tool_results})
```

不到 30 行, 这就是一个完整 Agent。**后面所有章节都在这个循环上叠加机制 —— 循环本身始终不变。**

完整代码: [code/step05_tool_use.py](../code/step05_tool_use.py)

## 变更内容

| 组件          | 之前 (s04)   | 之后 (s05)                          |
|---------------|--------------|-------------------------------------|
| 工具          | 无           | `run_command`                       |
| API 参数      | system+msgs  | + `tools`                           |
| 控制流        | 单次调用     | `while stop_reason == "tool_use"`   |
| 模型能做什么  | 说话         | 说话 + 操作系统                     |

## 试一试

```sh
python build-agent-example/code/step05_tool_use.py
```

试这些 prompt:

1. `帮朕看一下当前目录下都有些什么文件`
2. `检查一下当前 git 分支`
3. `创建一个 hello.py 写入 print("hi") 然后运行它`
4. `帮朕统计 build-agent-example/code 下每个 .py 文件的行数`

观察 `[执行命令]: ...` 与 `[命令输出]: ...` —— 这就是 agent 在和真实世界对话。
