import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    base_url=os.environ.get("ANTHROPIC_BASE_URL"),
)
MODEL = os.environ["ANTHROPIC_MODEL"]

ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "skills"
MEMORY_DIR = ROOT / "memory"
TEMPLATES_DIR = ROOT / "templates"
COMPACT_PROMPT_PATH = TEMPLATES_DIR / "agent" / "compact_prompt.md"

RECENT_MESSAGES = 10
COMPACT_AFTER_MESSAGES = int(os.environ.get("AGENT_MEMORY_COMPACT_AFTER", "18"))


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

    def write_memory(self, text: str):
        self.ensure_files()
        self.memory_file.write_text(text.strip() + "\n", encoding="utf-8")

    def read_user(self) -> str:
        self.ensure_files()
        return self.user_file.read_text(encoding="utf-8")

    def write_user(self, text: str):
        self.ensure_files()
        self.user_file.write_text(text.strip() + "\n", encoding="utf-8")

    def today_episode_file(self) -> Path:
        return self.memory_dir / f"{datetime.now():%Y-%m-%d}.md"

    def read_today_episode(self) -> str:
        self.ensure_files()
        path = self.today_episode_file()
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def append_episode(self, text: str):
        self.ensure_files()
        with self.today_episode_file().open("a", encoding="utf-8") as f:
            f.write("\n" + text.strip() + "\n")


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


def _messages_to_text(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = json.dumps(_json_safe(msg.get("content")), ensure_ascii=False)
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _extract_tag(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


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

遇到不熟悉的专题时，请先调用 load_skill 工具加载对应的知识，再给出回答。

【长期记忆 MEMORY.md】
{memory}

【用户画像 USER.md】
{user_profile}

【今日情景记忆】
{today_episode or "(今天还没有压缩出的情景记忆)"}

当前可用技能：
{SKILL_LOADER.get_descriptions()}"""


def compact_history(history: list[dict]) -> list[dict]:
    if len(history) <= COMPACT_AFTER_MESSAGES:
        return history

    old_messages = history[:-RECENT_MESSAGES]
    recent_messages = history[-RECENT_MESSAGES:]
    if not old_messages:
        return history

    prompt_template = COMPACT_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = prompt_template.format(
        old_conversation=_messages_to_text(old_messages),
        current_memory=MEMORY.read_memory(),
        current_user=MEMORY.read_user(),
        today_episode=MEMORY.read_today_episode(),
        now_hhmm=datetime.now().strftime("%H:%M"),
    )

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=3000,
            system="你是记忆整理员。请严格按要求输出 XML，不要输出额外解释。",
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in message.content if b.type == "text"), "")
    except Exception as exc:
        print(f"[记忆压缩失败，保留完整 history]: {exc}")
        return history

    episode = _extract_tag(text, "episode")
    updated_memory = _extract_tag(text, "updated_memory")
    updated_user = _extract_tag(text, "updated_user")

    if episode:
        MEMORY.append_episode(episode)
    if updated_memory:
        MEMORY.write_memory(updated_memory)
    if updated_user:
        MEMORY.write_user(updated_user)

    print(f"[记忆已压缩]: old={len(old_messages)} recent={len(recent_messages)}")
    return recent_messages


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


TOOLS = [
    {
        "name": "run_command",
        "description": "在终端执行一条 shell 命令并返回输出",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"}
            },
            "required": ["command"]
        }
    },
    {
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
    {
        "name": "load_skill",
        "description": "加载指定技能的详细知识内容，在回答相关问题前调用",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "技能名称，必须是系统提示中列出的可用技能之一"
                }
            },
            "required": ["skill_name"]
        }
    }
]


def execute_tool(block) -> str:
    if block.name == "web_fetch":
        url = block.input["url"]
        mode = block.input.get("extract_mode", "text")
        max_chars = block.input.get("max_chars", 8000)
        print(f"[网页获取]: {url}")
        return web_fetch(url, mode, max_chars)

    if block.name == "run_command":
        command = block.input["command"]
        print(f"[执行命令]: {command}")
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        output = result.stdout or result.stderr
        print(f"[命令输出]: {output}")
        return output

    if block.name == "load_skill":
        skill_name = block.input["skill_name"]
        print(f"[加载技能]: {skill_name}")
        return SKILL_LOADER.get_content(skill_name)

    return f"Error: Unknown tool '{block.name}'"


history = []

while True:
    user_input = input("你: ")
    user_message = {"role": "user", "content": user_input}
    history.append(user_message)
    MEMORY.append_history(user_message)

    while True:
        message = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=build_system_prompt(),
            tools=TOOLS,
            messages=history
        )

        assistant_message = {"role": "assistant", "content": message.content}
        history.append(assistant_message)
        MEMORY.append_history(assistant_message)

        if message.stop_reason != "tool_use":
            reply = next(b.text for b in message.content if b.type == "text")
            print(f"[Agent回答]: {reply}\n")
            history = compact_history(history)
            break

        tool_results = []
        for block in message.content:
            if block.type != "tool_use":
                continue
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": execute_tool(block)
            })

        tool_message = {"role": "user", "content": tool_results}
        history.append(tool_message)
        MEMORY.append_history(tool_message)
