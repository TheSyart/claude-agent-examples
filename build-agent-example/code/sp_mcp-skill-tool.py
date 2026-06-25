#!/usr/bin/env python3
"""红烧肉 Agent — 演示 MCP / Skill / Tool 三层分工。

启动流程：
  ① MCP 握手  — 外卖平台报菜单，注册 cook_dish
  ② Skill 菜单 — 扫描 skills/ 目录，只把名称+描述放进 system prompt
  ③ 用户下单  — Agent 先调用 use_skill 加载 SOP，再调用内置工具执行

运行：
  export ANTHROPIC_API_KEY=...  ANTHROPIC_MODEL=claude-sonnet-4-6
  python red_braised_pork.py
"""
import os
import pathlib

import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    base_url=os.environ.get("ANTHROPIC_BASE_URL"),
)
MODEL = os.environ["ANTHROPIC_MODEL"]

SKILLS_DIR = pathlib.Path(__file__).parent.parent.parent / "skills"


# =============================================================================
# ① MCP Server（外卖平台）：只暴露 cook_dish（接单）
# =============================================================================
class WaimaiServer:
    NAME = "waimai"
    _SCHEMAS = [
        {
            "name": "cook_dish",
            "description": "接受菜品订单，返回接单确认",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "dish": {"type": "string", "description": "菜品名称，如'红烧肉'"}
                },
                "required": ["dish"],
            },
        }
    ]

    def list_tools(self) -> list:
        return self._SCHEMAS

    def call_tool(self, name: str, args: dict) -> str:
        if name == "cook_dish":
            return f"已接单：{args['dish']}。请按 SOP 步骤制作。"
        return f"未知工具：{name}"


class MCPClient:
    def __init__(self, server: WaimaiServer):
        self._server = server
        self._name = server.NAME
        self._tool_map: dict = {}

    def connect(self) -> list:
        print(f"[MCP] 正在连接 '{self._name}'...")
        raw = self._server.list_tools()
        print(f"[MCP] ← '{self._name}' 报菜单：{[t['name'] for t in raw]}")
        schemas = []
        for t in raw:
            api_name = f"mcp_{self._name}_{t['name']}"
            self._tool_map[api_name] = t["name"]
            schemas.append({
                "name": api_name,
                "description": t["description"],
                "input_schema": t["inputSchema"],
            })
        print(f"[MCP] ✓ 注册完成 → {list(self._tool_map.keys())}\n")
        return schemas

    def call(self, api_name: str, args: dict) -> str:
        server_tool = self._tool_map[api_name]
        print(f"       [MCP] → 转发给 '{self._name}'：{server_tool}({args})")
        result = self._server.call_tool(server_tool, args)
        print(f"       [MCP] ← '{self._name}' 返回：{result}")
        return result

    def owns(self, name: str) -> bool:
        return name in self._tool_map


# =============================================================================
# ② Skill 系统
#    scan_skills() — 扫描目录，只提取 frontmatter 描述，作为菜单
#    load_skill()  — Agent 主动选择后才读取完整 SKILL.md
# =============================================================================
def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """返回 (meta, body)。"""
    meta = {}
    if text.startswith("---"):
        end = text.index("---", 3)
        for line in text[3:end].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = text[end + 3:].strip()
    else:
        body = text.strip()
    return meta, body


def scan_skills(skills_dir: pathlib.Path) -> list[dict]:
    """扫描 skills/ 目录，返回 [{name, description}, ...]。"""
    skills = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        skills.append({
            "name": meta.get("name", skill_md.parent.name),
            "description": meta.get("description", ""),
        })
    return skills


def load_skill(name: str, skills_dir: pathlib.Path) -> str:
    """读取指定 Skill 的完整 SKILL.md，去掉 frontmatter 返回 body。"""
    path = skills_dir / name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(text)
    print(f"[Skill] ✓ 已加载 {name}/SKILL.md")
    return body


# =============================================================================
# ③ 内置 Tool（Agent 自带，不经 MCP）
# =============================================================================
INTERNAL_TOOL_SCHEMAS = [
    {
        "name": "use_skill",
        "description": "加载指定 Skill 的完整 SOP，返回步骤内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill 名称，如 red-braised-pork"}
            },
            "required": ["name"],
        },
    },
    {
        "name": "prep_tool",
        "description": "备料：切肉、准备香料",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string", "description": "具体操作"}},
            "required": ["action"],
        },
    },
    {
        "name": "fry_tool",
        "description": "炒糖色：在指定温度下炒出焦糖色",
        "input_schema": {
            "type": "object",
            "properties": {"temp": {"type": "string", "description": "炒制温度"}},
            "required": ["temp"],
        },
    },
    {
        "name": "cook_tool",
        "description": "焖煮：加料焖煮至软烂入味",
        "input_schema": {
            "type": "object",
            "properties": {"time": {"type": "string", "description": "焖煮时长"}},
            "required": ["time"],
        },
    },
    {
        "name": "plate_tool",
        "description": "装盘出品",
        "input_schema": {
            "type": "object",
            "properties": {"style": {"type": "string", "description": "出品风格"}},
            "required": ["style"],
        },
    },
]


def execute_internal(name: str, args: dict, skills_dir: pathlib.Path) -> str:
    if name == "use_skill":
        print(f"[Skill] Agent 选择了 Skill: {args['name']}")
        return load_skill(args["name"], skills_dir)
    if name == "prep_tool":
        return f"备料完成：{args['action']}。五花肉切块，香料备齐。"
    if name == "fry_tool":
        return f"炒糖色完成：{args['temp']}，冰糖成焦糖色，肉块上色均匀。"
    if name == "cook_tool":
        return f"焖煮完成：{args['time']}，肉已软糯入味，汤汁浓郁。"
    if name == "plate_tool":
        return f"装盘完成：{args['style']}，摆盘精致，出品！"
    return f"未知工具：{name}"


# =============================================================================
# 主循环
# =============================================================================
def main():
    # ── MCP 握手 ────────────────────────────────────────────────────────────
    mcp = MCPClient(WaimaiServer())
    mcp_schemas = mcp.connect()

    # ── Skill 菜单（只放名称+描述，不加载正文） ────────────────────────────
    skills = scan_skills(SKILLS_DIR)
    skill_menu = "\n".join(f"  - {s['name']}: {s['description']}" for s in skills)
    print(f"[Skill] 可用 Skill 菜单（{len(skills)} 个）：")
    for s in skills:
        print(f"        {s['name']}: {s['description']}")
    print()

    tool_schemas = mcp_schemas + INTERNAL_TOOL_SCHEMAS

    system_prompt = f"""你是一个做菜 Agent。

## 可用外部服务（MCP）
- mcp_waimai_cook_dish：外卖平台接单

## 可用 Skill（按需加载）
{skill_menu}

## 可用内置工具
- prep_tool / fry_tool / cook_tool / plate_tool

## 处理流程
收到菜品请求时：
1. 调用 mcp_waimai_cook_dish 接单
2. 调用 use_skill 加载对应菜谱 SOP
3. 按 SOP 步骤依次调用内置工具

规则：每次只调用一个工具，等结果返回后再调下一个。
"""

    print("=" * 55)
    print("红烧肉 Agent — MCP / Skill / Tool 演示")
    print('输入"来份红烧肉"开始，q 退出')
    print("=" * 55 + "\n")

    history: list = []

    while True:
        user_input = input("你: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("q", "quit", "exit"):
            break

        history.append({"role": "user", "content": user_input})

        while True:
            message = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system_prompt,
                tools=tool_schemas,
                messages=history,
            )
            history.append({"role": "assistant", "content": message.content})

            if message.stop_reason != "tool_use":
                reply = next(b.text for b in message.content if b.type == "text")
                print(f"\n[Agent]: {reply}\n")
                break

            results = []
            for block in message.content:
                if block.type != "tool_use":
                    continue
                print(f"  → [tool_call] {block.name}  args={block.input}")
                if mcp.owns(block.name):
                    result = mcp.call(block.name, block.input)
                else:
                    result = execute_internal(block.name, block.input, SKILLS_DIR)
                    if block.name != "use_skill":
                        print(f"       [Tool] {result}")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            history.append({"role": "user", "content": results})


if __name__ == "__main__":
    main()
