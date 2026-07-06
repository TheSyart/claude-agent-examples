# s07: 记忆系统 (Memory)

`s01 > s02 > s03 > s04 | s05 > s06 > [ s07 ] | s08 > s09 > s10 > s11 > s12 > s13`

> *"把短期对话沉淀成长期记忆"* —— history 只能撑住眼前几轮，真正的 Agent 需要把重要信息写回文件，下次再注入 prompt。
>
> **记忆层**: Raw History + Working Memory + Episodic Memory + Core Memory + User Profile。

## 问题

s03 的 `history` 是短期记忆：每轮把 messages 全部发回模型。它能让模型记住刚说过的话，但有两个明显副作用：

- 聊得越久，messages 越长，token 成本线性增长；
- 程序一关，内存里的 `history` 就没了；
- 用户偏好、长期项目状态、关键事实不应该永远混在完整聊天记录里。

s07 要把记忆从“内存里的 list”升级成“可持久化、可压缩、可注入”的系统。

## 解决方案

本步骤保留 s06 的 skill 能力，同时新增四类文件：

| 文件 | 作用 |
|------|------|
| `memory/history.jsonl` | 原始对话流水，每条 user/assistant/tool_result 都追加一行 |
| `memory/YYYY-MM-DD.md` | 情景记忆，压缩旧对话后追加到当天日志 |
| `memory/MEMORY.md` | 长期核心记忆，每轮注入 system prompt |
| `templates/USER.md` | 用户画像和稳定偏好，每轮注入 system prompt |

主循环仍然只保留最近一段 `history` 给模型；旧对话达到阈值后交给 compact prompt 整理成三份输出：

```xml
<episode>追加到今天情景记忆</episode>
<updated_memory>完整的新 MEMORY.md</updated_memory>
<updated_user>完整的新 USER.md</updated_user>
```

## 工作原理

### 1. 每轮追加 raw history

```python
user_message = {"role": "user", "content": user_input}
history.append(user_message)
MEMORY.append_history(user_message)
```

Raw history 不参与每次 prompt 全量回灌，只作为可追溯流水保存。

### 2. 每轮动态构建 system prompt

```python
def build_system_prompt():
    return f"""
    【长期记忆 MEMORY.md】
    {MEMORY.read_memory()}

    【用户画像 USER.md】
    {MEMORY.read_user()}

    【今日情景记忆】
    {MEMORY.read_today_episode()}
    """
```

这一步让长期记忆真正影响模型，而不是只躺在文件里。

### 3. 旧 history 压缩成记忆

```python
if len(history) > COMPACT_AFTER_MESSAGES:
    old_messages = history[:-RECENT_MESSAGES]
    recent_messages = history[-RECENT_MESSAGES:]
    # old_messages -> compact_prompt.md -> episode / memory / user
    history = recent_messages
```

教学版用消息数量阈值触发压缩，方便观察；完整系统可以改成 token 阈值。

完整代码: [code/step07_memory_system.py](../code/step07_memory_system.py)

## 变更内容

| 组件 | 之前 (s06) | 之后 (s07) |
|------|------------|------------|
| skill | 可加载知识文件 | 继续保留 |
| history | 只在内存里增长 | 同时追加到 `memory/history.jsonl` |
| system prompt | 固定人设 + skill 列表 | 动态注入 MEMORY / USER / 今日 episode |
| 长期记忆 | 无 | compact 后写入 `MEMORY.md` 和 `USER.md` |

## 试一试

```sh
python build-agent-example/code/step07_memory_system.py
```

可以先说：

```text
以后回答代码问题时，先给我最小可运行版本，再解释关键点。
```

多聊几轮后观察：

```sh
tail -n 5 memory/history.jsonl
cat memory/MEMORY.md
cat templates/USER.md
```
