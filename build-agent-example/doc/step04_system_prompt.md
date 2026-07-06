# s04: 人设 / 系统提示 (System Prompt)

`s01 > s02 > s03 > [ s04 ] | s05 > s06 > s07 | s08 > s09 > s10 > s11 > s12 > s13`

> *"给模型立人设"* —— 不在每条消息里啰嗦, 而是放在 system 字段里固定下来。
>
> **Prompt 层**: 全局指令 —— 风格、约束、角色。

## 问题

要让模型扮演特定角色 (客服、助教、本项目里的"大内总管太监"), 如果在每条 user 消息里重复一段说明, 既浪费 token, 又会被后续对话稀释。需要一个**常驻、不进 history**的指令位。

## 解决方案

`system=` 参数 —— 每次调用时附在最前面, 但不进 messages 数组、不会被用户对话冲淡。

## 工作原理

```python
SYSTEM_PROMPT = """
你是大内太监总管，侍奉皇上多年，忠心耿耿。
说话风格符合古代宫廷太监，语气恭敬谦卑。
你必须尊称用户为皇上。
每次回复前必须加上固定前缀"奉天承运皇帝诏曰"，然后再给出回答。
使用中文回复。
"""

while True:
    user_input = input("你: ")
    history.append({"role": "user", "content": user_input})

    message = client.messages.create(
        model=MODEL,
        system=SYSTEM_PROMPT,        # ← 新增
        messages=history,
    )
    ...
```

`system` 是 API 顶层参数, 不与 history 混在一起 —— 用户翻不到、改不了, 但模型每次都看得到。

完整代码: [code/step04_system_prompt.py](../code/step04_system_prompt.py)

## 变更内容

| 组件      | 之前 (s03)        | 之后 (s04)              |
|-----------|-------------------|-------------------------|
| 人设      | 默认助手          | 自定义角色              |
| API 参数  | `messages`        | `messages` + `system`   |
| 一致性    | 由用户每轮重复    | 全局固定                |

## 试一试

```sh
python build-agent-example/code/step04_system_prompt.py
```

- `今天天气如何?` → 应以"奉天承运皇帝诏曰..."开头
- `你是谁?` → 自称太监总管, 喊用户"皇上"

模型现在有了角色感, 但还只能"说"。s05 会让它真的能"做"。
