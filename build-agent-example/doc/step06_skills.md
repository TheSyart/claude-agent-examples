# s06: 多工具 + 动态技能 (Skills)

`s01 > s02 > s03 > s04 | s05 > [ s06 ] > s07 | s08 > s09 > s10 > s11 > s12 > s13`

> *"按需取知识"* —— 不把所有知识塞 system prompt, 让模型自己 load。
>
> **Prompt 层**: 知识从静态变动态。

## 问题

system prompt 越塞越长, 会出现两个问题:

1. **token 浪费** —— 大部分知识在当前任务里用不到。
2. **注意力稀释** —— 长 prompt 让关键指令被忽视, 模型表现下降。

应该有个机制: **列出有哪些技能** (一行简介), 让模型遇到相关问题时**主动调工具**取出详情。

## 解决方案

```
SKILL files (磁盘)
   ├─ frontmatter (name, description, tags)  →  system prompt (常驻, 一行)
   └─ body (Markdown 正文)                    →  load_skill 工具按需取
```

技能存为 `skills/<name>/SKILL.md`, frontmatter 写元数据。启动时只把简介拼进 system prompt, 正文留在磁盘。模型通过 `load_skill(skill_name)` 工具按需加载。

## 工作原理

1. **SkillLoader** 启动时扫描所有 SKILL.md, 解析 yaml frontmatter:

```python
class SkillLoader:
    def _load_all(self):
        for f in sorted(self.skills_dir.rglob("SKILL.md")):
            meta, body = self._parse_frontmatter(f.read_text())
            self.skills[meta["name"]] = {"meta": meta, "body": body}
```

2. **system prompt 只列简介**:

```
当前可用技能：
  - palace_etiquette: 宫廷礼仪规范 [ceremony]
  - imperial_history: 历代帝王轶事 [knowledge]
```

3. **三个工具协作**:

| 工具         | 作用                                  |
|--------------|---------------------------------------|
| `run_command`| 执行 shell                            |
| `web_fetch`  | 抓网页 (text 提取 / raw HTML 两种模式)|
| `load_skill` | 按 name 取技能正文                    |

4. **主循环按 `block.name` 分发** (从这步开始, 工具不止一个):

```python
for block in message.content:
    if block.type != "tool_use":
        continue
    if block.name == "web_fetch":
        content = web_fetch(block.input["url"], ...)
    elif block.name == "run_command":
        content = subprocess.run(block.input["command"], ...).stdout
    elif block.name == "load_skill":
        content = SKILL_LOADER.get_content(block.input["skill_name"])
    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
```

完整代码: [code/step06_skills.py](../code/step06_skills.py)

## 变更内容

| 组件        | 之前 (s05)         | 之后 (s06)                       |
|-------------|--------------------|----------------------------------|
| 工具数      | 1                  | 3                                |
| 知识管理    | 全塞 system prompt | 元数据常驻 + 正文按需加载        |
| 网页能力    | 无                 | `web_fetch` (text/raw)           |
| 工具分发    | 单一 if            | 多分支 (`block.name == ...`)     |

## 试一试

```sh
python build-agent-example/code/step06_skills.py
```

- `帮朕抓一下 https://example.com 的纯文本`
- 在 `build-agent-example/code/skills/foo/SKILL.md` 写一份知识 (带 frontmatter), 重启程序, 问相关问题, 看模型是否主动调 `load_skill`。

技能让 Agent 能“按需加载知识”。下一步 s07 会把短期 history 沉淀到 `memory/`，让 Agent 有长期记忆。
