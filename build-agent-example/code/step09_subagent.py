import json
import os
import re
import subprocess
import urllib.request
import yaml
import anthropic
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
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

【小太监身份选择】
优先选择权限最窄、职司最贴合的身份：
- xiaohuangmen（通传小黄门）：轻量只读，适合短命令、快速确认、跑腿探路。
- sili_suitang（司礼监随堂小太监）：只读文书，适合阅读代码、整理提纲、归纳结论。
- dongchang_tanshi（东厂探事小太监）：只读查访，适合抓网页、查资料、探索性搜索。
- shangbao_dianbu（尚宝监典簿小太监）：只读核验，适合盘点文件、校对清单、检查遗漏。
- neiguan_yingzao（内官监营造小太监）：可读写可执行，适合修改文件、搭建工程、落地实现。

【长期记忆 MEMORY.md】
{memory}

【用户画像 USER.md】
{user_profile}

【今日情景记忆】
{today_episode or "(今天还没有压缩出的情景记忆)"}

当前可用技能：
{SKILL_LOADER.get_descriptions()}"""

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
    }
]

history = []

while True:
    user_input = input("你: ")

    user_message = {"role": "user", "content": user_input}
    history.append(user_message)
    MEMORY.append_history(user_message)

    while True:
        message = client.messages.create(
            model=MODEL,
            max_tokens=20000,
            system=build_system_prompt(),
            tools=TOOLS,
            messages=history
        )

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

        # 普通工具顺序执行
        for block in other_blocks:
            if block.name in ("run_command", "web_fetch", "load_skill"):
                results_map[block.id] = execute_basic_tool(block, prefix="")
            elif block.name == "update_todos":
                results_map[block.id] = update_todos(block.input.get("todos", []))
            else:
                results_map[block.id] = f"Error: Unknown tool '{block.name}'"

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
