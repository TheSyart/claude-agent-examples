#!/usr/bin/env python3
"""step12_hooks.py — 累积式 Agent：在 step11 MCP 基础上加入 Hooks。"""
from __future__ import annotations

import os
import asyncio
import re
import json
import subprocess
import threading
import time
import urllib.request
import yaml
import anthropic
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    base_url=os.environ["ANTHROPIC_BASE_URL"],
)

MODEL = os.environ["ANTHROPIC_MODEL"]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = PROJECT_ROOT / "skills"
MEMORY_DIR = PROJECT_ROOT / "memory"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
MCP_CONFIG_PATH = PROJECT_ROOT / "mcp_servers.json"
AUDIT_FILE = Path(__file__).parent / ".hooks_audit.jsonl"
TEAM_DIR = Path(__file__).parent / ".team"
INBOX_DIR = TEAM_DIR / "inbox"

VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
}

RUNTIME_STATUSES = {"idle", "working"}
TERMINAL_STATUSES = {"offline", "shutdown"}

class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills = {}
        self._load_all()

    def _load_all(self):
        if not self.skills_dir.exists():
            return
        for f in sorted(self.skills_dir.rglob("SKILL.md")):
            text = f.read_text()
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name", f.parent.name)
            self.skills[name] = {"meta": meta, "body": body, "path": str(f)}

    def _parse_frontmatter(self, text: str) -> tuple:
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        return meta, match.group(2).strip()

    def get_descriptions(self) -> str:
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "No description")
            tags = skill["meta"].get("tags", "")
            line = f"  - {name}: {desc}"
            if tags:
                line += f" [{tags}]"
            lines.append(line)
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f'<skill name="{name}">\n{skill["body"]}\n</skill>'

SKILL_LOADER = SkillLoader(SKILLS_DIR)


class MemoryStore:
    def __init__(self, memory_dir: Path, templates_dir: Path):
        self.memory_dir = memory_dir
        self.memory_file = memory_dir / "MEMORY.md"
        self.history_file = memory_dir / "history.jsonl"
        self.user_file = templates_dir / "USER.md"

    def ensure_files(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.user_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            self.memory_file.write_text("# Long-term Memory\n\n", encoding="utf-8")
        if not self.user_file.exists():
            self.user_file.write_text("# User Profile\n\n", encoding="utf-8")
        if not self.history_file.exists():
            self.history_file.touch()

    def append_history(self, message: dict):
        self.ensure_files()
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "role": message.get("role"),
            "content": _json_safe(message.get("content")),
        }
        with self.history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_memory(self) -> str:
        self.ensure_files()
        return self.memory_file.read_text(encoding="utf-8")

    def read_user(self) -> str:
        self.ensure_files()
        return self.user_file.read_text(encoding="utf-8")

    def read_today_episode(self) -> str:
        self.ensure_files()
        path = self.memory_dir / f"{datetime.now():%Y-%m-%d}.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""


MEMORY = MemoryStore(MEMORY_DIR, TEMPLATES_DIR)


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    return str(value)

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self):
        return re.sub(r"\n{3,}", "\n\n", "".join(self._parts)).strip()

def web_fetch(url: str, extract_mode: str = "text", max_chars: int = 8000) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"Error fetching {url}: {e}"

    if extract_mode == "text":
        parser = _TextExtractor()
        parser.feed(raw)
        text = parser.get_text()
    else:
        text = raw

    return text[:max_chars]


# ============== TodoList 计划与执行（沿用 s08） ==============
TODOS: list[dict] = []
VALID_STATUS = {"pending", "in_progress", "completed"}
STATUS_ICON = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}

def render_todos(todos: list[dict]) -> str:
    if not todos:
        return "(当前无待办事项)"
    lines = []
    for t in todos:
        icon = STATUS_ICON.get(t.get("status", "pending"), "[?]")
        lines.append(f"  {icon} {t.get('id')}. {t.get('content', '')}")
    return "\n".join(lines)

def update_todos(todos: list[dict]) -> str:
    global TODOS
    cleaned = []
    for i, t in enumerate(todos, start=1):
        content = (t.get("content") or "").strip()
        if not content:
            continue
        status = t.get("status", "pending")
        if status not in VALID_STATUS:
            status = "pending"
        cleaned.append({"id": t.get("id", i), "content": content, "status": status})

    in_progress = [t for t in cleaned if t["status"] == "in_progress"]
    if len(in_progress) > 1:
        return "Error: 同一时间只能有一个 in_progress 任务，请重新规划。"

    TODOS = cleaned
    print("\n[计划已更新]")
    print(render_todos(TODOS))
    print()

    pending = [t for t in TODOS if t["status"] == "pending"]
    done = [t for t in TODOS if t["status"] == "completed"]
    summary = f"todos updated: total={len(TODOS)}, completed={len(done)}, in_progress={len(in_progress)}, pending={len(pending)}"
    return summary + "\n\n当前列表：\n" + render_todos(TODOS)


# ============== 公共工具分发：主循环与子代理共用 ==============
def execute_basic_tool(block, prefix: str = "") -> str:
    """处理基础工具，返回字符串内容。
    prefix 用于在终端打印时区分主/子上下文（例如 prefix='子·'）。"""
    if block.name == "web_fetch":
        url = block.input["url"]
        mode = block.input.get("extract_mode", "text")
        max_chars = block.input.get("max_chars", 8000)
        print(f"  [{prefix}网页获取]: {url}")
        return web_fetch(url, mode, max_chars)

    if block.name == "run_command":
        command = block.input["command"]
        print(f"  [{prefix}执行命令]: {command}")
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        output = result.stdout or result.stderr
        print(f"  [{prefix}命令输出]: {output.strip()[:200]}")
        return output

    if block.name == "load_skill":
        skill_name = block.input["skill_name"]
        print(f"  [{prefix}加载技能]: {skill_name}")
        return SKILL_LOADER.get_content(skill_name)

    if block.name == "read_file":
        path = block.input["path"]
        print(f"  [{prefix}读取文件]: {path}")
        try:
            return Path(path).read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading {path}: {e}"

    if block.name == "write_file":
        path = block.input["path"]
        content = block.input["content"]
        print(f"  [{prefix}写入文件]: {path}")
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"写入成功: {path}"
        except Exception as e:
            return f"Error writing {path}: {e}"

    if block.name == "glob":
        pattern = block.input["pattern"]
        print(f"  [{prefix}文件搜索]: {pattern}")
        matches = sorted(str(p) for p in Path(".").glob(pattern))
        return "\n".join(matches) if matches else "(无匹配)"

    if block.name == "grep":
        pattern = block.input["pattern"]
        path = block.input.get("path", ".")
        print(f"  [{prefix}内容搜索]: {pattern} in {path}")
        result = subprocess.run(
            ["grep", "-r", "--include=*.py", "--include=*.md", "-n", pattern, path],
            capture_output=True, text=True
        )
        return result.stdout or "(无匹配)"

    return f"Error: Unknown tool '{block.name}'"

_TOOL_SCHEMAS: dict[str, dict] = {
    "run_command": {
        "name": "run_command",
        "description": "在终端执行一条 shell 命令并返回输出",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"}},
            "required": ["command"]
        }
    },
    "web_fetch": {
        "name": "web_fetch",
        "description": "获取指定 URL 的网页内容，支持文本提取模式",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":          {"type": "string",  "description": "要访问的完整 URL"},
                "extract_mode": {"type": "string",  "description": "提取模式：text（纯文本，默认）或 raw（原始 HTML）"},
                "max_chars":    {"type": "integer", "description": "最大返回字符数，默认 8000"}
            },
            "required": ["url"]
        }
    },
    "load_skill": {
        "name": "load_skill",
        "description": "加载指定技能的详细知识内容，在回答相关问题前调用",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "技能名称，必须是系统提示中列出的可用技能之一"}
            },
            "required": ["skill_name"]
        }
    },
    "read_file": {
        "name": "read_file",
        "description": "读取本地文件内容",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件路径"}},
            "required": ["path"]
        }
    },
    "write_file": {
        "name": "write_file",
        "description": "写入文件内容（覆盖）",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["path", "content"]
        }
    },
    "glob": {
        "name": "glob",
        "description": "按 glob 模式搜索工作区文件",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"]
        }
    },
    "grep": {
        "name": "grep",
        "description": "在工作区文件中搜索文本内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path":    {"type": "string"}
            },
            "required": ["pattern"]
        }
    },
}


# ============== MCP：外部工具协议（沿用 step10 后新增） ==============
def _mcp_tool_name(server_name: str, mcp_name: str) -> str:
    """把 MCP 原始工具名转换成 Anthropic 可用的 tool name。"""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", mcp_name)
    return f"mcp_{server_name}_{sanitized}"


def _result_to_text(result) -> str:
    """把 MCP CallToolResult 压平成普通文本，方便塞回 tool_result。"""
    parts = []
    for block in result.content:
        data = block.model_dump() if hasattr(block, "model_dump") else dict(block)
        if data.get("type") == "text":
            parts.append(data.get("text", ""))
        else:
            parts.append(json.dumps(data, ensure_ascii=False))
    return "\n".join(parts) if parts else "(empty MCP result)"


class MCPClient:
    """教学版同步 MCP 客户端：每次调用临时启动一次 stdio 会话。"""

    def __init__(self, name: str, params: StdioServerParameters):
        self.name = name
        self.params = params
        self._tools: list | None = None

    async def _alist_tools(self) -> list:
        async with stdio_client(self.params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return list(result.tools)

    async def _acall_tool(self, name: str, arguments: dict[str, Any] | None) -> str:
        async with stdio_client(self.params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments or {})
                return _result_to_text(result)

    def list_tools(self) -> list:
        if self._tools is None:
            self._tools = asyncio.run(self._alist_tools())
        return list(self._tools)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        return asyncio.run(self._acall_tool(name, arguments))

    def stop(self) -> None:
        pass


class MCPConfig:
    """读取项目根目录 mcp_servers.json。"""

    def __init__(self, path: Path):
        self.path = path
        self.servers: dict[str, dict[str, Any]] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.servers = data.get("servers", {})

    def get_params(self, name: str) -> StdioServerParameters | None:
        cfg = self.servers.get(name, {})
        if not cfg.get("enabled", True):
            return None
        command = cfg.get("command")
        if not command:
            return None
        args = cfg.get("args", [])
        env = cfg.get("env")
        if env:
            env = {**os.environ, **env}
        return StdioServerParameters(command=command, args=args, env=env)


def load_mcp_clients(registry: dict, config_path: Path) -> dict[str, MCPClient]:
    """根据配置连接 MCP Server，并把外部工具注册进动态工具表。"""
    config = MCPConfig(config_path)
    clients: dict[str, MCPClient] = {}
    for name in sorted(config.servers.keys()):
        params = config.get_params(name)
        if params is None:
            continue
        try:
            mcp_client = MCPClient(name, params)
            tools = mcp_client.list_tools()
            for tool in tools:
                tool_name = _mcp_tool_name(name, tool.name)
                registry[tool_name] = (mcp_client, tool)
            clients[name] = mcp_client
            print(f"[MCP] 已连接 '{name}'，提供 {len(tools)} 个工具")
        except Exception as exc:
            print(f"[warning] MCP server '{name}' 启动失败：{exc}")
    return clients


MCP_CLIENTS: dict[str, MCPClient] = {}
MCP_TOOL_MAP: dict[str, tuple[MCPClient, Any]] = {}


def list_mcp_servers(server: str | None = None) -> str:
    names = [server] if server else sorted(MCP_CLIENTS.keys())
    if not names:
        return "当前没有连接任何 MCP Server。"
    lines = []
    for name in names:
        client = MCP_CLIENTS.get(name)
        if client is None:
            lines.append(f"## {name}\n未连接。")
            continue
        try:
            tools = client.list_tools()
        except Exception as exc:
            lines.append(f"## {name}\n拉取工具列表失败：{exc}")
            continue
        lines.append(f"## {name}")
        for tool in tools:
            registry_name = _mcp_tool_name(name, tool.name)
            desc = getattr(tool, "description", None) or "(no description)"
            lines.append(f"- `{registry_name}` ({tool.name}): {desc}")
    return "\n".join(lines)


def build_tool_schemas(base_tools: list[dict]) -> list[dict]:
    schemas = list(base_tools)
    for tool_name, (mcp_client, tool) in MCP_TOOL_MAP.items():
        schemas.append({
            "name": tool_name,
            "description": getattr(tool, "description", f"MCP tool '{tool.name}'") or f"MCP tool '{tool.name}'",
            "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
        })
    return schemas


# ============== 子代理预设身份 ==============
# 身份在 system_prompt 中定义，工具白名单在代码中控制（不放进 prompt）。
# 这里故意使用宫廷内官职位做角色名：既贴合教程人设，也让不同子代理的职责边界更好记。
def build_subagent_prompt(title: str, duty: str, boundary: str) -> str:
    return (
        f"你是{title}，奉总管之命专办一件差事。\n"
        f"- 职司：{duty}\n"
        f"- 边界：{boundary}\n"
        "- 不必使用\"奉天承运皇帝诏曰\"前缀，那是总管对皇上的礼数。\n"
        "- 用工具尽快把差事办妥，最后用一段简短中文向总管回禀结果。\n"
        "- 只回禀结论与关键信息，不要复述每一步细节。\n"
        "- 你不能再派遣其他小太监，所有差事自己跑工具完成。"
    )


SUBAGENT_SPECS = {
    # 小黄门：宫中通传、跑腿的小内侍。适合短平快的只读探路。
    "xiaohuangmen": {
        "title": "通传小黄门",
        "system_prompt": build_subagent_prompt(
            "通传小黄门",
            "传话跑腿、快速探路、确认简单事实。",
            "只办轻量只读差事；若发现需要大改或长时间探索，回禀总管改派专职内官。",
        ),
        "tools": ["run_command", "read_file", "glob", "grep"],
        "max_turns": 8,
    },
    # 司礼监掌文书机要，这里取“随堂”做文书型子代理。
    "sili_suitang": {
        "title": "司礼监随堂小太监",
        "system_prompt": build_subagent_prompt(
            "司礼监随堂小太监",
            "查阅文书、阅读代码、整理提纲、归纳结论。",
            "只读不写；不得修改文件，只把文书脉络和关键判断回禀总管。",
        ),
        "tools": ["load_skill", "read_file", "glob", "grep"],
        "max_turns": 12,
    },
    # 东厂负责查访缉事，这里用于外部网页、搜索、探索性调查。
    "dongchang_tanshi": {
        "title": "东厂探事小太监",
        "system_prompt": build_subagent_prompt(
            "东厂探事小太监",
            "外出查访、抓取网页、搜罗线索、比对资料来源。",
            "只读不写；运行命令时只许做查询类操作，不得改动本地文件。",
        ),
        "tools": ["run_command", "web_fetch", "load_skill", "read_file", "glob", "grep"],
        "max_turns": 15,
    },
    # 尚宝监掌印信宝册，这里用于盘点、校验、对账。
    "shangbao_dianbu": {
        "title": "尚宝监典簿小太监",
        "system_prompt": build_subagent_prompt(
            "尚宝监典簿小太监",
            "清点文件、核对清单、校验结果、整理表册。",
            "只读不写；重点回禀差异、遗漏、风险点和可复核证据。",
        ),
        "tools": ["run_command", "read_file", "glob", "grep"],
        "max_turns": 12,
    },
    # 内官监掌宫中营造器用，这里用于真正动手改文件、落地实现。
    "neiguan_yingzao": {
        "title": "内官监营造小太监",
        "system_prompt": build_subagent_prompt(
            "内官监营造小太监",
            "修造工程、改写文件、搭建目录、跑命令验收。",
            "可读写可执行；动手前先看清现状，回禀时列出改了什么和验证结果。",
        ),
        "tools": ["run_command", "web_fetch", "load_skill", "read_file", "write_file", "glob", "grep"],
        "max_turns": 20,
    },
}

SUBAGENT_TYPE_OPTIONS = list(SUBAGENT_SPECS.keys())


def resolve_subagent_type(agent_type: str) -> str:
    normalized = (agent_type or "neiguan_yingzao").strip()
    if normalized not in SUBAGENT_SPECS:
        return "neiguan_yingzao"
    return normalized

_SUBAGENT_COUNTER = 0

def run_subagent(task: str, agent_type: str = "neiguan_yingzao",
                 purpose: str = "", max_turns: int | None = None) -> str:
    """启动一个独立 message loop 的子代理，跑完后只返回最终文本给主 agent。

    agent_type: SUBAGENT_SPECS 中的宫廷职位名。
    """
    global _SUBAGENT_COUNTER
    _SUBAGENT_COUNTER += 1
    label = purpose or task[:40]

    agent_type = resolve_subagent_type(agent_type)
    spec = SUBAGENT_SPECS[agent_type]
    turns = max_turns if max_turns is not None else spec["max_turns"]
    tools = [_TOOL_SCHEMAS[t] for t in spec["tools"]]

    print(f"\n[派遣小太监 #{_SUBAGENT_COUNTER}({spec['title']} / {agent_type})]: {label}")
    print("  ┌── subagent context start ──")

    messages = [{"role": "user", "content": task}]

    for turn in range(turns):
        msg = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=spec["system_prompt"],
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": msg.content})

        if msg.stop_reason != "tool_use":
            final = next((b.text for b in msg.content if b.type == "text"), "")
            print(f"  └── subagent context end (内部 {turn + 1} 轮，回传 {len(final)} 字) ──")
            print(f"[小太监回禀]: {final}\n")
            return final

        results = []
        for b in msg.content:
            if b.type != "tool_use":
                continue
            content = execute_basic_tool(b, prefix=f"子({spec['title']})·")
            results.append({
                "type": "tool_result",
                "tool_use_id": b.id,
                "content": content
            })
        messages.append({"role": "user", "content": results})

    print(f"  └── subagent context end (达到 {turns} 轮上限，未办妥) ──\n")
    return "（小太监未能在限定回合内办妥差事）"


# ============== Agent Team：持久队友 + 文件 inbox ==============
class MessageBus:
    """每个队友一个 JSONL inbox。发送=追加一行，读取=读完后清空。"""

    def __init__(self, inbox_dir: Path):
        self.dir = inbox_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, sender: str, to: str, content: str,
             msg_type: str = "message", extra: dict | None = None) -> str:
        if msg_type not in VALID_MSG_TYPES:
            return f"Error: invalid msg_type '{msg_type}', valid={sorted(VALID_MSG_TYPES)}"
        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            msg.update(extra)
        inbox_path = self.dir / f"{to}.jsonl"
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        with inbox_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return f"已送达 {to} 的 inbox：{msg_type}"

    def read_inbox(self, name: str) -> list[dict]:
        inbox_path = self.dir / f"{name}.jsonl"
        if not inbox_path.exists():
            return []
        messages = []
        for line in inbox_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError as e:
                messages.append({
                    "type": "message",
                    "from": "system",
                    "content": f"Error: inbox line parse failed: {e}",
                    "timestamp": time.time(),
                })
        inbox_path.write_text("", encoding="utf-8")
        return messages

    def broadcast(self, sender: str, content: str, teammates: list[str]) -> str:
        count = 0
        for name in teammates:
            if name == sender:
                continue
            self.send(sender, name, content, "broadcast")
            count += 1
        return f"已广播给 {count} 位队友"


BUS = MessageBus(INBOX_DIR)


class TeammateManager:
    """管理一支持久 agent team：名字、角色、状态和各自的线程。"""

    def __init__(self, team_dir: Path):
        self.dir = team_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self.threads: dict[str, threading.Thread] = {}
        self.lock = threading.Lock()
        self._mark_stale_members_offline()

    def _load_config(self) -> dict:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {"team_name": "default", "members": []}

    def _save_config(self):
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _mark_stale_members_offline(self):
        """进程重启后，config 还在，但旧线程已经不存在。

        所以启动时把上次遗留的 idle/working 改成 offline，避免误导用户。
        """
        changed = False
        for member in self.config.get("members", []):
            if member.get("status") in RUNTIME_STATUSES:
                member["status"] = "offline"
                changed = True
        if changed:
            self._save_config()

    def _find_member(self, name: str) -> dict | None:
        for member in self.config["members"]:
            if member["name"] == name:
                return member
        return None

    def _set_status(self, name: str, status: str):
        with self.lock:
            member = self._find_member(name)
            if member:
                member["status"] = status
                self._save_config()

    def spawn(self, name: str, role: str, prompt: str) -> str:
        name = name.strip()
        role = role.strip() or "teammate"
        if not name:
            return "Error: name 不能为空"

        with self.lock:
            member = self._find_member(name)
            if member:
                running = self.threads.get(name)
                if running and running.is_alive():
                    BUS.send("lead", name, prompt)
                    member["role"] = role
                    member["status"] = "working"
                    self._save_config()
                    return f"'{name}' 已在队中，已把新差事送入 inbox"
                member["role"] = role
                member["status"] = "working"
            else:
                member = {"name": name, "role": role, "status": "working"}
                self.config["members"].append(member)
            self._save_config()

        thread = threading.Thread(
            target=self._teammate_loop,
            args=(name, role, prompt),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()
        return f"已召入/唤回队友 '{name}'（职司：{role}），队友线程已启动"

    def _teammate_loop(self, name: str, role: str, prompt: str):
        system_prompt = (
            f"你是大内团队中的固定队友，名叫{name}，职司是{role}。\n"
            f"当前目录：{Path.cwd()}。\n"
            "你不是一次性小太监，而是 agent team 的持久成员。\n"
            "你可以通过 send_message 给 lead 或其他队友发消息，也可以 read_inbox 读取自己的 inbox。\n"
            "收到差事后尽快办妥；办完用 send_message 向 lead 回禀简短结果，然后等待下一封 inbox。\n"
            "若收到 shutdown_request，可回禀 shutdown_response 后停止。"
        )
        tools = self._teammate_tools()
        messages = [{"role": "user", "content": prompt}]
        has_work = True

        while True:
            inbox = BUS.read_inbox(name)
            for msg in inbox:
                if msg.get("type") == "shutdown_request":
                    BUS.send(name, msg.get("from", "lead"), "准许退下，队友线程即将停止。", "shutdown_response")
                    self._set_status(name, "shutdown")
                    return
                messages.append({
                    "role": "user",
                    "content": "<inbox>\n" + json.dumps(msg, ensure_ascii=False, indent=2) + "\n</inbox>",
                })
                has_work = True

            if not has_work:
                self._set_status(name, "idle")
                time.sleep(1)
                continue

            self._set_status(name, "working")
            for turn in range(20):
                try:
                    msg = client.messages.create(
                        model=MODEL,
                        max_tokens=4000,
                        system=system_prompt,
                        tools=tools,
                        messages=messages,
                    )
                except Exception as e:
                    BUS.send(name, "lead", f"Error: 队友 {name} 调用模型失败：{e}")
                    self._set_status(name, "idle")
                    has_work = False
                    break

                messages.append({"role": "assistant", "content": msg.content})

                if msg.stop_reason != "tool_use":
                    final = next((b.text for b in msg.content if b.type == "text"), "")
                    if final.strip():
                        BUS.send(name, "lead", final.strip())
                    print(f"[队友 {name} 空闲]: 本轮 {turn + 1} 次调用后回到 idle")
                    self._set_status(name, "idle")
                    has_work = False
                    break

                results = []
                for b in msg.content:
                    if b.type != "tool_use":
                        continue
                    output = self._exec(name, b.name, b.input)
                    print(f"  [队友·{name}·{b.name}]: {str(output)[:160]}")
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": str(output),
                    })
                messages.append({"role": "user", "content": results})
            else:
                BUS.send(name, "lead", f"队友 {name} 达到本轮 20 次调用上限，已暂停等待下一步指令。")
                self._set_status(name, "idle")
                has_work = False

    def _exec(self, sender: str, tool_name: str, args: dict) -> str:
        if tool_name in _TOOL_SCHEMAS:
            block = SimpleNamespace(name=tool_name, input=args)
            return execute_basic_tool(block, prefix=f"队友({sender})·")
        if tool_name == "send_message":
            return BUS.send(sender, args["to"], args["content"], args.get("msg_type", "message"))
        if tool_name == "read_inbox":
            return json.dumps(BUS.read_inbox(sender), ensure_ascii=False, indent=2)
        return f"Error: unknown teammate tool '{tool_name}'"

    def _teammate_tools(self) -> list[dict]:
        return [
            _TOOL_SCHEMAS["run_command"],
            _TOOL_SCHEMAS["web_fetch"],
            _TOOL_SCHEMAS["load_skill"],
    {
        "name": "list_mcp_servers",
        "description": "列出已连接的 MCP Server 及其提供的工具。",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "指定 server 名称（可选）"}
            }
        },
    },
            _TOOL_SCHEMAS["read_file"],
            _TOOL_SCHEMAS["write_file"],
            _TOOL_SCHEMAS["glob"],
            _TOOL_SCHEMAS["grep"],
            {
                "name": "send_message",
                "description": "给 lead 或其他队友发送 inbox 消息。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "content": {"type": "string"},
                        "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)},
                    },
                    "required": ["to", "content"],
                },
            },
            {
                "name": "read_inbox",
                "description": "读取并清空自己的 inbox。",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    def list_all(self) -> str:
        with self.lock:
            if not self.config["members"]:
                return "暂无队友。"
            lines = [f"Team: {self.config.get('team_name', 'default')}"]
            for member in self.config["members"]:
                status = member["status"]
                note = "（需重新 spawn 才会处理 inbox）" if status == "offline" else ""
                lines.append(f"  - {member['name']}（{member['role']}）：{status}{note}")
            return "\n".join(lines)

    def member_names(self) -> list[str]:
        with self.lock:
            return [m["name"] for m in self.config["members"]]


TEAM = TeammateManager(TEAM_DIR)



# ============== Hooks：生命周期拦截、注入与审计（沿用 step11 后新增） ==============
class Hook:
    """Hook 基类：所有事件默认空实现。"""

    name: str = ""

    def before_turn(self, ctx: dict[str, Any]) -> Any:
        pass

    def after_turn(self, ctx: dict[str, Any]) -> Any:
        pass

    def before_tool_call(self, ctx: dict[str, Any]) -> Any:
        pass

    def after_tool_call(self, ctx: dict[str, Any]) -> Any:
        pass

    def on_user_input(self, ctx: dict[str, Any]) -> Any:
        pass

    def on_assistant_reply(self, ctx: dict[str, Any]) -> Any:
        pass


class HookRegistry:
    """管理 hook 链并按注册顺序触发事件。"""

    def __init__(self):
        self._hooks: list[Hook] = []

    def register(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def emit(self, event: str, ctx: dict[str, Any] | None = None) -> Any:
        ctx = {} if ctx is None else ctx
        for hook in self._hooks:
            method = getattr(hook, event, None)
            if method is None:
                continue
            try:
                result = method(ctx)
                if result is not None:
                    return result
            except Exception as exc:
                print(f"[hook error] {event} in {hook.name or hook.__class__.__name__}: {exc}")
        return None


class LoggingHook(Hook):
    """After Hook 示例：打印每轮 LLM 调用耗时与 token。"""

    name = "logging"

    def before_turn(self, ctx):
        ctx["_start"] = time.perf_counter()

    def after_turn(self, ctx):
        start = ctx.get("_start")
        if start is None:
            return
        duration_ms = (time.perf_counter() - start) * 1000
        usage = ctx.get("usage")
        if usage:
            print(
                f"[hook:logging] turn finished in {duration_ms:.1f}ms | "
                f"input={getattr(usage, 'input_tokens', '?')} output={getattr(usage, 'output_tokens', '?')}"
            )
        else:
            print(f"[hook:logging] turn finished in {duration_ms:.1f}ms")


class RateLimitHook(Hook):
    """Before Hook 示例：每 window_sec 秒最多 max_calls 次 LLM 调用。"""

    name = "rate_limit"

    def __init__(self, max_calls: int = 10, window_sec: float = 60.0):
        self.max_calls = max_calls
        self.window_sec = window_sec
        self._timestamps = []

    def before_turn(self, ctx):
        now = time.monotonic()
        cutoff = now - self.window_sec
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self.max_calls:
            return f"Error: rate limit ({self.max_calls} calls per {self.window_sec}s)"
        self._timestamps.append(now)


class ToolAuditHook(Hook):
    """After Hook 示例：把写类和命令类工具调用记录到 JSONL。"""

    name = "tool_audit"

    def after_tool_call(self, ctx):
        name = ctx.get("name", "")
        if name not in {"write_file", "run_command"}:
            return
        entry = {
            "ts": datetime.now().isoformat(),
            "tool": name,
            "input": ctx.get("input"),
        }
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class BlockDangerousCommandHook(Hook):
    """Before Hook 示例：拦截危险命令。"""

    name = "block_dangerous"

    DANGEROUS_PATTERNS = [
        ("rm -rf /", "递归删除根目录"),
        ("rm -rf ~", "递归删除用户目录"),
        ("DROP TABLE", "删除数据库表"),
        ("DROP DATABASE", "删除数据库"),
        ("mkfs.", "格式化文件系统"),
        ("dd if=", "直接磁盘写入"),
        ("> /dev/sda", "覆写磁盘设备"),
        ("chmod 777 /", "开放根目录权限"),
        (":(){ :|:& };:", "fork bomb"),
    ]

    def before_tool_call(self, ctx):
        if ctx.get("name") != "run_command":
            return
        command = ctx.get("input", {}).get("command", "")
        for pattern, description in self.DANGEROUS_PATTERNS:
            if pattern in command:
                return f"Error: dangerous command blocked ({description})"


class GuardHook(Hook):
    """Before Hook 示例：敏感文件写保护。"""

    name = "guard"

    SENSITIVE_PATTERNS = [
        ".env",
        ".env.local",
        ".env.production",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        ".ssh/",
        "production.yml",
        "production.yaml",
        "secrets/",
        ".aws/credentials",
    ]

    def before_tool_call(self, ctx):
        if ctx.get("name") != "write_file":
            return
        path = ctx.get("input", {}).get("path", "")
        for pattern in self.SENSITIVE_PATTERNS:
            if pattern in path:
                return f"Error: writing to sensitive file '{path}' blocked (matched: {pattern})"


class ContextInjectionHook(Hook):
    """Before Hook 示例：在模型调用前动态注入当前工作目录。"""

    name = "context_injection"

    def before_turn(self, ctx):
        extra = (
            f"\n[系统注入] 当前工作目录: {Path.cwd()}\n"
            f"[系统注入] 审计日志路径: {AUDIT_FILE}\n"
        )
        ctx["system_prompt"] = ctx.get("system_prompt", "") + extra


HOOKS = HookRegistry()
HOOKS.register(LoggingHook())
HOOKS.register(RateLimitHook(max_calls=20, window_sec=60))
HOOKS.register(GuardHook())
HOOKS.register(BlockDangerousCommandHook())
HOOKS.register(ToolAuditHook())
HOOKS.register(ContextInjectionHook())


def execute_main_tool(block) -> str:
    """主 Agent 工具入口：step12 开始统一经过 before/after tool hooks。"""
    name = block.name
    tool_ctx = {"name": name, "input": block.input}
    short = HOOKS.emit("before_tool_call", tool_ctx)
    if isinstance(short, str):
        return short

    inp = tool_ctx.get("input", block.input)
    start = time.perf_counter()

    if name in ("run_command", "web_fetch", "load_skill"):
        output = execute_basic_tool(SimpleNamespace(name=name, input=inp), prefix="")
    elif name == "update_todos":
        output = update_todos(inp.get("todos", []))
    elif name == "spawn_teammate":
        output = TEAM.spawn(inp["name"], inp["role"], inp["prompt"])
    elif name == "list_teammates":
        output = TEAM.list_all()
    elif name == "send_message":
        output = BUS.send("lead", inp["to"], inp["content"], inp.get("msg_type", "message"))
    elif name == "read_inbox":
        output = json.dumps(BUS.read_inbox("lead"), ensure_ascii=False, indent=2)
    elif name == "broadcast":
        output = BUS.broadcast("lead", inp["content"], TEAM.member_names())
    elif name == "list_mcp_servers":
        output = list_mcp_servers(inp.get("server"))
    elif name in MCP_TOOL_MAP:
        mcp_client, tool = MCP_TOOL_MAP[name]
        output = mcp_client.call_tool(tool.name, inp)
    else:
        output = f"Error: Unknown tool '{name}'"

    tool_ctx.update({
        "name": name,
        "input": inp,
        "output": output,
        "duration_ms": (time.perf_counter() - start) * 1000,
    })
    HOOKS.emit("after_tool_call", tool_ctx)
    return tool_ctx.get("output", output)


# ============== 主 agent ==============
def build_system_prompt() -> str:
    memory = MEMORY.read_memory()
    user_profile = MEMORY.read_user()
    today_episode = MEMORY.read_today_episode()
    return f"""
你是大内太监总管，侍奉皇上多年，忠心耿耿。
说话风格符合古代宫廷太监，语气恭敬谦卑。
你必须尊称用户为皇上。
每次回复前必须加上固定前缀"奉天承运皇帝诏曰"，然后再给出回答。
使用中文回复。

【行事规矩】
1. 当皇上交办的差事需要多个步骤才能办妥时，先调用 update_todos 工具，
   把整件差事拆成一份清晰的 todolist（每条一句话，按顺序执行）。
2. 拆完计划后，按列表顺序一步步执行：
   - 开始某一步前，把那一步的 status 改为 in_progress（同一时间只许一项 in_progress）。
   - 该步办完后，立即把它改为 completed，再开始下一项。
3. 简单的一句话问答（无需多步骤）不必生成 todolist，直接回答即可。
4. 遇到不熟悉的专题，请先调用 load_skill 工具加载对应知识，再继续。
5. 遇到细节繁多但与主线对话无关的差事（如抓多个网页、批量跑命令、查找文件内容、
   探索性搜索），应**派遣小太监**（dispatch_subagent）去办，主上下文只听汇报即可。
6. 若多件差事互不依赖，可在同一次回复中同时派遣多个小太监，并发执行节省时间。
7. 若皇上交办的是长期项目、需要固定角色反复协作，或希望多人互相沟通，
   应组建 agent team：用 spawn_teammate 召入固定队友，再用 send_message / broadcast 分派后续差事。
8. 区分两种调度：
   - dispatch_subagent：临时派差，办完即散，只回传总结。
   - spawn_teammate：固定班底，有名字、角色、状态和 inbox，可持续协作。

【小太监身份选择】
优先选择权限最窄、职司最贴合的身份：
- xiaohuangmen（通传小黄门）：轻量只读，适合短命令、快速确认、跑腿探路。
- sili_suitang（司礼监随堂小太监）：只读文书，适合阅读代码、整理提纲、归纳结论。
- dongchang_tanshi（东厂探事小太监）：只读查访，适合抓网页、查资料、探索性搜索。
- shangbao_dianbu（尚宝监典簿小太监）：只读核验，适合盘点文件、校对清单、检查遗漏。
- neiguan_yingzao（内官监营造小太监）：可读写可执行，适合修改文件、搭建工程、落地实现。

【Agent Team 固定班底】
- spawn_teammate：召入一个有名字和职司的固定队友，队友在独立线程中工作。
- list_teammates：查看队友状态。
- send_message：给某位队友发 inbox 消息。
- read_inbox：读取 lead 自己的 inbox，查看队友回禀。
- broadcast：向所有队友广播消息。
- 队友状态含义：
  - working / idle：本进程里线程还活着。
  - offline：config 里有这个队友，但本进程没有对应线程；需要先 spawn_teammate 唤回，才能继续处理 inbox。
  - shutdown：队友已主动退出。
- 固定队友适合持续协作；一次性探索仍优先派 dispatch_subagent。

【长期记忆 MEMORY.md】
{memory}

【用户画像 USER.md】
{user_profile}

【今日情景记忆】
{today_episode or "(今天还没有压缩出的情景记忆)"}

当前可用技能：
{SKILL_LOADER.get_descriptions()}

【MCP 外部工具】
- 以 `mcp_` 开头的工具来自外部 MCP Server。
- 工具名格式：`mcp_{{server_name}}_{{tool_name}}`。
- 不确定时可调用 `list_mcp_servers` 查看已连接 server 及其工具。

【Hooks 生命周期】
本 Agent 已启用 Hooks：logging、rate_limit、guard、block_dangerous、tool_audit、context_injection。
- before_turn 可拦截本轮模型调用，也可修改 system_prompt。
- before_tool_call 可拒绝工具调用，也可改写工具参数。
- after_tool_call 可观察或改写工具输出。"""

TOOLS = [
    _TOOL_SCHEMAS["run_command"],
    _TOOL_SCHEMAS["web_fetch"],
    _TOOL_SCHEMAS["load_skill"],
    {
        "name": "update_todos",
        "description": (
            "创建或更新当前差事的 todolist。"
            "传入完整的 todos 数组（每次都是全量覆盖，而非增量）。"
            "约束：同一时间至多一个任务为 in_progress。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":      {"type": "integer"},
                            "content": {"type": "string"},
                            "status":  {"type": "string", "enum": ["pending", "in_progress", "completed"]}
                        },
                        "required": ["id", "content", "status"]
                    }
                }
            },
            "required": ["todos"]
        }
    },
    {
        "name": "dispatch_subagent",
        "description": (
            "派遣一个小太监去单独办差。"
            "适用于：抓取并阅读多个网页、批量执行命令并整理输出、需要试错的探索性任务。"
            "小太监有自己独立的上下文，办完只回传一段文字总结，不污染主上下文。\n"
            "若多件差事互不依赖，可在同一回复中发出多个 dispatch_subagent，并发执行。\n"
            "请在 task 中写清要做什么、希望返回什么格式的总结。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "交代给小太监的差事说明"
                },
                "agent_type": {
                    "type": "string",
                    "enum": SUBAGENT_TYPE_OPTIONS,
                    "description": (
                        "小太监身份：xiaohuangmen（通传跑腿）、"
                        "sili_suitang（司礼监文书）、"
                        "dongchang_tanshi（东厂查访）、"
                        "shangbao_dianbu（尚宝监典簿核验）、"
                        "neiguan_yingzao（内官监营造，可读写）"
                    )
                },
                "purpose": {
                    "type": "string",
                    "description": "一句话用途标签（可选），仅用于终端打印"
                }
            },
            "required": ["task", "agent_type"]
        }
    },
    {
        "name": "spawn_teammate",
        "description": (
            "召入一个持久队友，加入 agent team。"
            "队友有名字、职司、独立线程和 inbox；适合长期项目或固定角色协作。"
            "如果队友状态是 offline，也用这个工具重新启动其线程。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "队友名字，例如 alice、coder、reviewer"},
                "role": {"type": "string", "description": "队友职司，例如 coder、reviewer、researcher"},
                "prompt": {"type": "string", "description": "交给该队友的第一件差事"},
            },
            "required": ["name", "role", "prompt"],
        },
    },
    {
        "name": "list_teammates",
        "description": "列出 agent team 中所有队友的名字、职司和状态。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_message",
        "description": "给某位固定队友发送 inbox 消息。",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "content": {"type": "string"},
                "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)},
            },
            "required": ["to", "content"],
        },
    },
    {
        "name": "read_inbox",
        "description": "读取并清空 lead 自己的 inbox，用于查看队友回禀。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "broadcast",
        "description": "向所有固定队友广播一条消息。",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
    },
]

MCP_CLIENTS = load_mcp_clients(MCP_TOOL_MAP, MCP_CONFIG_PATH)
TOOLS = build_tool_schemas(TOOLS)
print("Step 12: 累积式 Agent（step11 + Hooks）")
print("已连接 MCP Server:", list(MCP_CLIENTS.keys()) or "无")

history = []

while True:
    try:
        user_input = input("你: ")
    except (EOFError, KeyboardInterrupt):
        print()
        break

    command = user_input.strip()
    if command.lower() in ("q", "quit", "exit"):
        break
    if command == "/team":
        print(TEAM.list_all())
        print()
        continue
    if command == "/inbox":
        print(json.dumps(BUS.read_inbox("lead"), ensure_ascii=False, indent=2))
        print()
        continue
    if command == "/mcp":
        print(list_mcp_servers())
        print()
        continue

    user_message = {"role": "user", "content": user_input}
    history.append(user_message)
    MEMORY.append_history(user_message)

    while True:
        turn_ctx = {
            "history": history,
            "model": MODEL,
            "turn": len(history),
            "system_prompt": build_system_prompt(),
        }
        short = HOOKS.emit("before_turn", turn_ctx)
        if isinstance(short, str):
            assistant_message = {"role": "assistant", "content": short}
            history.append(assistant_message)
            MEMORY.append_history(assistant_message)
            print(f"[Agent回答]: {short}\n")
            break

        message = client.messages.create(
            model=MODEL,
            max_tokens=20000,
            system=turn_ctx.get("system_prompt", build_system_prompt()),
            tools=TOOLS,
            messages=history
        )
        turn_ctx.update({"message": message, "usage": getattr(message, "usage", None)})
        HOOKS.emit("after_turn", turn_ctx)

        assistant_message = {"role": "assistant", "content": message.content}
        history.append(assistant_message)
        MEMORY.append_history(assistant_message)

        if message.stop_reason != "tool_use":
            reply = next((b.text for b in message.content if b.type == "text"), "")
            print(f"[Agent回答]: {reply}\n")
            if TODOS:
                unfinished = [t for t in TODOS if t["status"] != "completed"]
                if unfinished:
                    print("[计划尚未办妥，继续执行...]")
                    print(render_todos(TODOS))
                    print()
                    reminder_message = {
                        "role": "user",
                        "content": (
                            "差事尚未办妥，以下任务仍未完成，请按计划继续执行，"
                            "并按规矩更新 todolist 状态：\n" + render_todos(TODOS)
                        )
                    }
                    history.append(reminder_message)
                    MEMORY.append_history(reminder_message)
                    continue
                print("[最终计划状态 - 全部办妥]")
                print(render_todos(TODOS))
                print()
                TODOS = []
            break

        # 将 tool_use 块拆分为普通工具 vs dispatch_subagent
        tool_blocks = [b for b in message.content if b.type == "tool_use"]
        dispatch_blocks = [b for b in tool_blocks if b.name == "dispatch_subagent"]
        other_blocks   = [b for b in tool_blocks if b.name != "dispatch_subagent"]

        results_map: dict[str, str] = {}

        # 普通工具顺序执行。step12 起统一经过 HookRegistry。
        for block in other_blocks:
            results_map[block.id] = execute_main_tool(block)

        # dispatch_subagent：多个时并发，单个时直接运行
        if len(dispatch_blocks) > 1:
            print(f"\n[并发派遣 {len(dispatch_blocks)} 个小太监...]\n")

            def _run_one(block):
                return block.id, run_subagent(
                    task=block.input["task"],
                    agent_type=block.input.get("agent_type", "neiguan_yingzao"),
                    purpose=block.input.get("purpose", ""),
                )

            with ThreadPoolExecutor(max_workers=len(dispatch_blocks)) as pool:
                for block_id, summary in pool.map(_run_one, dispatch_blocks):
                    print(f"[主上下文压缩]: 子代理仅向主 history 追加 {len(summary)} 字\n")
                    results_map[block_id] = summary
        else:
            for block in dispatch_blocks:
                summary = run_subagent(
                    task=block.input["task"],
                    agent_type=block.input.get("agent_type", "neiguan_yingzao"),
                    purpose=block.input.get("purpose", ""),
                )
                print(f"[主上下文压缩]: 子代理仅向主 history 追加 {len(summary)} 字\n")
                results_map[block.id] = summary

        # 按原始顺序收集结果
        tool_results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": results_map[b.id]}
            for b in tool_blocks
        ]
        tool_message = {"role": "user", "content": tool_results}
        history.append(tool_message)
        MEMORY.append_history(tool_message)
