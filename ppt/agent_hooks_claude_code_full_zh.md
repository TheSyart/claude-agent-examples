# Agent Hooks 完整知识整理：从基础概念到 Claude Code 项目实践

> 用途：这是一份用于学习、内部分享、制作 PPT 与口播稿的 Markdown 文档。  
> 主题：Agent Hooks / Claude Code Hooks。  
> 更新日期：2026-07-07。  
> 主要来源：Anthropic / Claude Code 官方文档，重点参考 Hooks Guide、Hooks Reference、Agent SDK Hooks、Best Practices、Security Guidance Plugin、Settings、Extend Claude Code 等页面。

---

## 0. 一句话总览

**Hook 是“挂在 Agent 生命周期关键节点上的自动化控制点”。**

在传统软件里，Hook 常用于“某个事件发生前后，自动执行一段逻辑”。在 AI Agent 里，Hook 的作用更关键：它把不可预测的模型行为，接入到可预测、可测试、可审计的工程规则中。

可以把它理解为：

```text
Agent 正在运行
  ↓
某个生命周期事件发生：用户提交 prompt / 工具即将执行 / 文件已被编辑 / 回答即将结束
  ↓
Hook 匹配事件与条件
  ↓
执行脚本、HTTP 请求、MCP 工具、Prompt 判断或子 Agent 检查
  ↓
返回结果：放行 / 拦截 / 询问用户 / 注入上下文 / 记录日志 / 通知外部系统
```

**一句话给听众讲清楚：**

> Prompt 是“告诉 Agent 应该怎么做”，Hook 是“在关键节点保证某些事情一定发生”。

---

## 1. 为什么 Agent 需要 Hooks

### 1.1 Agent 的问题不是“不知道规则”，而是“规则未必每次被执行”

普通 LLM 对话通常依赖自然语言指令，例如：

- “每次修改代码后请运行测试。”
- “不要修改 `.env` 文件。”
- “提交前请检查安全问题。”
- “如果遇到权限变更，请先问我。”

这些规则在轻量场景里够用，但在 Agent 场景里会暴露几个问题：

1. **模型会遗忘或忽略**：上下文太长、任务太复杂、指令不够突出时，模型可能没执行。
2. **执行时机不稳定**：模型可能在完成后才想起要测试，或根本没有测试。
3. **不可审计**：你很难证明“每次都做了这件事”。
4. **不可硬拦截**：自然语言里的“不要编辑 `.env`”不是系统级阻止。
5. **不适合工程治理**：团队规范、安全策略、合规审计需要稳定机制。

Hooks 解决的是：**把“希望 Agent 做”变成“系统在特定时机自动执行”。**

### 1.2 Hooks 在 Agent 架构中的位置

一个典型 Agent Loop 可以抽象成：

```text
User Prompt
  ↓
Model 思考并决定下一步
  ↓
Tool Call：读文件 / 写文件 / 搜索 / 执行命令 / 调 MCP
  ↓
Tool Result 返回
  ↓
Model 根据结果继续或停止
```

Hooks 插入到这个循环的关键节点：

```text
User Prompt
  ├─ UserPromptSubmit Hook：提交前增强、检查、拦截
  ↓
Model
  ↓
PreToolUse Hook：工具执行前拦截、修改参数、审批
  ↓
Tool Execution
  ↓
PostToolUse Hook：工具执行后格式化、检查、注入反馈
  ↓
Stop Hook：Agent 想结束时做质量门禁
```

**核心价值：**

| 价值 | 说明 | 示例 |
|---|---|---|
| 安全控制 | 阻止危险操作 | 拦截写 `.env`、阻止危险 Bash 命令 |
| 质量保障 | 自动执行工程检查 | 保存后格式化、lint、测试 |
| 上下文增强 | 动态注入运行时信息 | 注入当前分支、CI 状态、环境变量说明 |
| 审计治理 | 记录 Agent 行为 | 记录每次 Bash、配置变更、敏感文件访问 |
| 人机协作 | 在敏感点触发确认 | 数据库写入、生产环境操作前问人 |
| 集成系统 | 连接外部服务 | Slack 通知、Webhook、MCP 安全扫描 |

---

## 2. 术语澄清：Agent Hooks、Claude Code Hooks、Agent Hook Handler

“Agent hooks”这个词容易混淆，建议分享时先分三层讲清楚。

### 2.1 泛化意义上的 Agent Hooks

泛化意义上，Agent Hook 是任何 AI Agent 生命周期中的回调机制。

例如：

- agent 启动时执行初始化。
- 工具调用前检查参数。
- 工具调用后清洗输出。
- agent 停止前检查任务是否完整。
- 子 agent 启动或结束时记录状态。

这是一种架构思想，不限定具体产品。

### 2.2 Claude Code Hooks

Claude Code Hooks 是 Claude Code 官方提供的配置化 Hook 机制。

它允许在 Claude Code 生命周期事件发生时，自动运行：

- Shell command
- HTTP endpoint
- MCP tool
- Prompt-based check
- Agent-based check

它通常写在：

- `~/.claude/settings.json`
- `.claude/settings.json`
- `.claude/settings.local.json`
- 插件的 `hooks/hooks.json`
- Skill 或 Agent frontmatter 中

### 2.3 Claude Code 中的 `type: "agent"` Hook Handler

Claude Code Hooks 里还有一种具体 handler 类型叫 `agent`，也就是：

```json
{
  "type": "agent",
  "prompt": "Verify that all unit tests pass. $ARGUMENTS",
  "timeout": 120
}
```

它表示：当 Hook 触发时，启动一个子 Agent 去做多步检查。官方文档标注它仍是 experimental，生产流程优先使用 command hooks。

---

## 3. Hook 与 Prompt、CLAUDE.md、Skills、Subagents、MCP、Plugins 的关系

Claude Code 有多种扩展方式，Hooks 不是替代所有东西，而是解决“事件驱动、自动化、硬约束”的问题。

| 扩展方式 | 解决什么问题 | 触发方式 | 确定性 | 适合场景 |
|---|---|---|---|---|
| `CLAUDE.md` | 每次会话都要知道的项目规则 | 会话启动加载 | 中等，模型解释执行 | 编码规范、架构说明、常用命令 |
| Skill | 可复用知识或工作流 | 用户 `/skill` 或模型判断相关 | 中等，模型执行流程 | 发布流程、代码审查清单、领域文档 |
| Subagent | 隔离上下文执行任务 | 主 Agent 派发 | 中等 | 大量搜索、专项审查、并行任务 |
| MCP | 连接外部工具和数据 | 模型调用工具 | 工具本身确定，调用时机由模型决定 | Jira、数据库、Figma、Slack、浏览器 |
| Hook | 生命周期事件自动执行 | 事件触发 | 高 | 格式化、拦截、审计、通知、质量门禁 |
| Plugin | 打包分发扩展 | 安装启用 | 取决于内部组件 | 团队复用、组织分发 |

### 3.1 一个简单判断规则

```text
如果是“Claude 应该知道” → 放 CLAUDE.md 或 Skill
如果是“Claude 可以调用的外部能力” → 用 MCP
如果是“Claude 不该污染主上下文的子任务” → 用 Subagent
如果是“每次必须自动发生” → 用 Hook
如果是“多个项目都要复用” → 打包成 Plugin
```

### 3.2 Hook vs Skill：最容易混淆的一组

| 维度 | Hook | Skill |
|---|---|---|
| 本质 | 事件触发的外部执行逻辑 | Claude 可读取和执行的说明书 |
| 触发 | 生命周期事件 | 用户命令或模型判断 |
| 是否保证运行 | 是，匹配事件就运行 | 不保证，取决于模型是否使用 |
| 是否占上下文 | 默认不占，除非返回输出 | 描述通常会加载，正文按需加载 |
| 适合 | 格式化、拦截、日志、通知 | 发布流程、审查方法、领域知识 |
| 安全边界 | 可硬拦截 | 更多是软约束 |

**讲解金句：**

> Skill 是“给 Agent 一本操作手册”，Hook 是“在流水线上加一个强制检查点”。

---

## 4. Claude Code Hooks 的核心模型

Claude Code Hooks 可以抽象为四层：

```text
Event：什么时机触发
  ↓
Matcher：触发后是否匹配当前对象
  ↓
Handler：匹配后执行什么
  ↓
Output：执行后如何影响 Claude Code
```

### 4.1 Event：生命周期事件

例如：

- `SessionStart`：会话开始或恢复。
- `UserPromptSubmit`：用户提交 prompt 后，Claude 处理前。
- `PreToolUse`：工具执行前。
- `PermissionRequest`：权限弹窗出现前。
- `PostToolUse`：工具成功执行后。
- `Stop`：Claude 准备结束本轮回答时。
- `Notification`：Claude Code 发出通知时。
- `ConfigChange`：配置文件变化时。

### 4.2 Matcher：事件过滤器

例如只在 Claude 写文件之后运行格式化：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path' | xargs npx prettier --write"
          }
        ]
      }
    ]
  }
}
```

这里的含义是：

```text
事件 = PostToolUse
matcher = Edit|Write
handler = 执行 prettier
```

### 4.3 Handler：实际运行的逻辑

Claude Code 支持五类 handler：

| Handler 类型 | 配置 | 特点 | 适合 |
|---|---|---|---|
| Command hook | `type: "command"` | 执行 shell 命令或脚本 | 最常用；格式化、校验、日志 |
| HTTP hook | `type: "http"` | POST 到 HTTP endpoint | 对接内部服务、Webhook、审计平台 |
| MCP tool hook | `type: "mcp_tool"` | 调已连接 MCP 工具 | 借助 MCP 做扫描或集成 |
| Prompt hook | `type: "prompt"` | 让 Claude 模型做单轮判断 | 需要语义判断但不需要工具 |
| Agent hook | `type: "agent"` | 启动子 Agent 多步检查 | 需要读文件、搜索、运行检查；实验性 |

### 4.4 Output：Hook 对 Agent 的影响

Hook 可以产生不同影响：

- 静默通过。
- 阻止某个动作。
- 允许某个动作并跳过权限提示。
- 修改工具输入。
- 修改工具输出给 Claude 看的内容。
- 向 Claude 注入上下文。
- 向用户显示系统消息。
- 记录日志或发通知。
- 让 Claude 不要停止，继续处理质量问题。

---

## 5. Claude Code 官方 Hook 生命周期事件全景

下面是适合 PPT 展示的分层表。

### 5.1 会话层事件

| 事件 | 触发时机 | 典型用途 |
|---|---|---|
| `Setup` | 以初始化或维护模式启动时 | CI 或脚本环境的一次性准备 |
| `SessionStart` | 会话开始、恢复、清空或压缩后 | 注入动态上下文、准备环境变量 |
| `InstructionsLoaded` | `CLAUDE.md` 或 `.claude/rules/*.md` 加载时 | 记录规则加载、检测规则变化 |
| `SessionEnd` | 会话终止时 | 清理资源、上传审计日志 |

### 5.2 用户输入层事件

| 事件 | 触发时机 | 典型用途 |
|---|---|---|
| `UserPromptSubmit` | 用户 prompt 提交后、模型处理前 | 检查 prompt、注入上下文、拦截危险请求 |
| `UserPromptExpansion` | 用户输入的命令扩展为 prompt 前 | 阻止某些命令扩展、补充上下文 |
| `MessageDisplay` | assistant 文本展示时 | 改变显示内容，例如脱敏或纯文本化；不改变 transcript |

### 5.3 工具调用层事件

| 事件 | 触发时机 | 典型用途 |
|---|---|---|
| `PreToolUse` | 工具调用前 | 拦截危险命令、保护文件、修改参数、审批 |
| `PermissionRequest` | 权限对话框即将出现 | 自动批准窄范围安全操作、拒绝敏感操作 |
| `PermissionDenied` | Auto mode classifier 拒绝工具调用时 | 告诉模型是否可重试 |
| `PostToolUse` | 工具成功执行后 | 格式化、lint、审计、上下文反馈 |
| `PostToolUseFailure` | 工具失败后 | 记录失败、补充修复建议、告警 |
| `PostToolBatch` | 一批并行工具调用完成后 | 对批量结果统一注入上下文 |

### 5.4 Agent / 并行任务层事件

| 事件 | 触发时机 | 典型用途 |
|---|---|---|
| `SubagentStart` | 子 Agent 启动时 | 注入子 Agent 专用上下文 |
| `SubagentStop` | 子 Agent 结束时 | 质量门禁、汇总检查 |
| `TaskCreated` | 创建任务时 | 任务治理、限制任务类型 |
| `TaskCompleted` | 任务完成时 | 结果聚合、通知 |
| `TeammateIdle` | Agent team 队友即将空闲时 | 重新分配任务、要求继续检查 |

### 5.5 状态、配置、环境层事件

| 事件 | 触发时机 | 典型用途 |
|---|---|---|
| `Notification` | Claude Code 发出通知 | 桌面通知、Slack 通知 |
| `ConfigChange` | 配置文件变化 | 审计、阻止未授权配置变更 |
| `CwdChanged` | 工作目录改变 | 重新加载 direnv / devbox / nix 环境 |
| `FileChanged` | 被监听文件变化 | `.envrc` 或 `.env` 变化后重新加载环境 |
| `WorktreeCreate` | 创建 worktree 时 | 自定义隔离工作区策略 |
| `WorktreeRemove` | 删除 worktree 时 | 清理资源 |
| `PreCompact` | 上下文压缩前 | 保存状态、阻止压缩 |
| `PostCompact` | 上下文压缩后 | 记录压缩结果、重新注入关键上下文 |
| `Stop` | Claude 准备结束本轮回答 | 质量门禁：要求测试、总结、清理 |
| `StopFailure` | 因 API 错误结束 | 告警、记录 rate limit / auth 错误 |
| `Elicitation` | MCP server 请求用户输入时 | 审核或自动处理表单请求 |
| `ElicitationResult` | 用户响应 MCP elicitation 后 | 修改、阻止或记录用户响应 |

---

## 6. 配置结构：从 settings.json 开始

### 6.1 最小配置结构

```json
{
  "hooks": {
    "EventName": [
      {
        "matcher": "MatcherPattern",
        "hooks": [
          {
            "type": "command",
            "command": "your-command-here"
          }
        ]
      }
    ]
  }
}
```

三个嵌套层次：

```text
hooks
  └─ EventName：事件，如 PreToolUse
       └─ matcher group：匹配条件，如 Bash、Edit|Write
            └─ hook handler：实际执行，如 command/http/prompt/agent
```

### 6.2 Hook 配置位置与作用域

| 位置 | 作用域 | 是否适合提交到仓库 | 场景 |
|---|---|---|---|
| `~/.claude/settings.json` | 当前用户所有项目 | 否 | 个人通知、个人审计、个人偏好 |
| `.claude/settings.json` | 当前项目 | 是 | 团队共享 hook、项目规范 |
| `.claude/settings.local.json` | 当前项目当前用户 | 否 | 本机调试、实验性 hook、个人路径 |
| Managed settings | 组织范围 | 是，由管理员控制 | 合规、安全策略、强制配置 |
| Plugin `hooks/hooks.json` | 插件启用范围 | 是 | 打包复用 |
| Skill / Agent frontmatter | 组件活跃期间 | 是 | 某个 skill 或 subagent 生命周期内使用 |

**项目实践建议：**

- 团队共享、无机密、路径相对的 hook：放 `.claude/settings.json`。
- 含个人路径、个人 token、个人通知方式：放 `.claude/settings.local.json` 或 `~/.claude/settings.json`。
- 组织强制安全策略：用 Managed settings。
- 多仓库复用：做成 plugin。

### 6.3 matcher 规则

常见写法：

```json
"matcher": "Bash"
"matcher": "Edit|Write"
"matcher": "Edit, Write"
"matcher": "mcp__memory__.*"
"matcher": "^Notebook"
"matcher": "*"
"matcher": ""
```

理解重点：

1. `"*"`、空字符串或省略 matcher：匹配所有。
2. `Edit|Write` 或 `Edit, Write`：匹配多个具体工具名。
3. 包含特殊字符时可作为正则表达式。
4. MCP 工具名通常形如 `mcp__<server>__<tool>`。
5. 如果要匹配一个 MCP server 的所有工具，要用 `mcp__server__.*`，不要只写 `mcp__server`。

### 6.4 `if` 字段：比 matcher 更细的过滤

`matcher` 通常按工具名过滤，`if` 可以按工具和参数进一步过滤。

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "if": "Bash(git *)",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/audit-git.sh",
            "args": []
          }
        ]
      }
    ]
  }
}
```

注意：`if` 是辅助过滤，不建议把它当作唯一安全边界。硬性权限控制仍应配合权限规则、deny rule、sandbox 等机制。

---

## 7. Hook 输入输出协议

### 7.1 输入：stdin 中的 JSON

Command hook 会从 stdin 收到 JSON。以 `PreToolUse` 中 Bash 为例：

```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
  "cwd": "/Users/sarah/myproject",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {
    "command": "npm test"
  },
  "tool_use_id": "toolu_01ABC123"
}
```

常见公共字段：

| 字段 | 含义 |
|---|---|
| `session_id` | 当前 Claude Code 会话 ID |
| `transcript_path` | 会话 transcript 文件路径 |
| `cwd` | 事件触发时工作目录 |
| `permission_mode` | 当前权限模式 |
| `hook_event_name` | 事件名称 |

不同事件会有不同额外字段，例如：

- `UserPromptSubmit` 有 `prompt`。
- `PreToolUse` 有 `tool_name`、`tool_input`、`tool_use_id`。
- `PostToolUse` 有 `tool_response`、`duration_ms`。
- `Notification` 有通知类型与 message。
- `ConfigChange` 有配置来源与文件路径。

### 7.2 输出：exit code 与 stdout / stderr

Command hook 有两种表达方式。

#### 方式 A：Exit code 简单控制

```bash
#!/bin/bash
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

if echo "$COMMAND" | grep -qi "drop table"; then
  echo "Blocked: dropping tables is not allowed" >&2
  exit 2
fi

exit 0
```

| Exit code | 含义 |
|---|---|
| `0` | hook 成功，无反对意见；对 `PreToolUse` 来说不等于自动批准，仍走正常权限流 |
| `2` | 阻止动作；stderr 会作为反馈给 Claude 或用户，取决于事件 |
| 其他 | 通常视为非阻塞错误，继续执行，并写入 debug log |

#### 方式 B：exit 0 + JSON 结构化控制

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Use rg instead of grep for better performance"
  }
}
```

重要规则：

- 要么用 exit code 表达，要么用 JSON 表达，不要混用。
- JSON 只在 exit code 为 `0` 时处理。
- 如果 exit code 是 `2`，stdout 中的 JSON 会被忽略。
- stdout 必须只有 JSON 对象，shell profile 不应额外输出文本。

### 7.3 常见 JSON 输出字段

| 字段 | 作用 |
|---|---|
| `continue` | `false` 时让 Claude 停止处理 |
| `stopReason` | `continue: false` 时给用户看的原因 |
| `suppressOutput` | 隐藏 hook stdout |
| `systemMessage` | 给用户看的系统消息 |
| `terminalSequence` | 触发终端通知、响铃、窗口标题等受限序列 |
| `hookSpecificOutput` | 针对具体事件的决策输出 |
| `additionalContext` | 注入给 Claude 的上下文，通常放在 `hookSpecificOutput` 里 |

### 7.4 `additionalContext`：把运行时事实注入 Claude

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "This file is generated. Edit src/schema.ts and run `bun generate` instead."
  }
}
```

适合注入：

- 当前分支、部署目标、feature flag。
- 当前文件对应的测试命令。
- CI 结果摘要。
- 内部服务返回的 issue 状态。
- 工具执行后的诊断结果。

不适合注入：

- 长篇静态规则；应放 `CLAUDE.md` 或 Skill。
- 大段日志；应先过滤摘要。
- 带 prompt injection 风险的非可信文本。

---

## 8. 决策控制：不同事件能控制什么

Hook 不是每个事件都能“阻止”或“修改”。不同事件能力不同。

| 事件 | 主要控制方式 | 可做什么 |
|---|---|---|
| `PreToolUse` | `hookSpecificOutput.permissionDecision` | `allow`、`deny`、`ask`、`defer`，可 `updatedInput` |
| `PermissionRequest` | `hookSpecificOutput.decision.behavior` | 权限弹窗前自动 allow / deny，可更新权限 |
| `PermissionDenied` | `retry: true` | 告诉模型可重试被 auto mode 拒绝的工具调用 |
| `PostToolUse` | `decision` / `updatedToolOutput` / `additionalContext` | 给 Claude 反馈、替换 Claude 看到的工具结果 |
| `UserPromptSubmit` | `decision` / `additionalContext` | 阻止 prompt 或补充上下文 |
| `UserPromptExpansion` | `decision` / `additionalContext` | 阻止命令展开或补充上下文 |
| `Stop` / `SubagentStop` | `decision: "block"` 或 `additionalContext` | 阻止 Claude 停止，要求继续处理 |
| `ConfigChange` | `decision: "block"` 或 exit 2 | 阻止配置变化生效 |
| `PreCompact` | `decision: "block"` | 阻止上下文压缩 |
| `MessageDisplay` | `displayContent` | 只改变屏幕显示，不改变 transcript 或 Claude 所见 |
| `SessionStart` / `Setup` / `SubagentStart` | `additionalContext` | 只能注入上下文，通常不能阻止 |
| `Notification` / `SessionEnd` / `WorktreeRemove` 等 | 无决策控制 | 适合副作用：日志、通知、清理 |

### 8.1 `PreToolUse` 的四种决策

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Destructive command blocked by hook"
  }
}
```

| 决策 | 含义 |
|---|---|
| `allow` | 跳过交互式权限提示，但仍不能覆盖 deny / ask 权限规则 |
| `deny` | 阻止工具调用，并把原因反馈给 Claude |
| `ask` | 要求用户确认 |
| `defer` | 非交互模式下暂停，供外层 Agent SDK / UI 接管后恢复 |

多个 `PreToolUse` hook 同时返回不同结果时，优先级：

```text
deny > defer > ask > allow
```

### 8.2 `updatedInput`：修改工具参数

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "Redirecting write to sandbox path",
    "updatedInput": {
      "file_path": "/sandbox/project/output.txt",
      "content": "..."
    }
  }
}
```

注意：

- `updatedInput` 替换整个输入对象，应包含未修改字段。
- 通常要配合 `allow` 或 `ask`。
- 对已经执行完的工具不能回滚真实副作用。

### 8.3 `PostToolUse`：只能影响“Claude 接下来看到什么”

`PostToolUse` 发生在工具已成功执行后。它可以：

- 注入上下文。
- 替换 Claude 看到的工具输出。
- 让 Claude 根据反馈继续处理。

但它不能撤销已经发生的副作用。

**经验规则：**

```text
要阻止或修改动作 → 用 PreToolUse
要检查结果并反馈 → 用 PostToolUse
要结束前做质量门禁 → 用 Stop
```

---

## 9. 五类 Hook Handler 的使用策略

### 9.1 Command hooks：默认首选

适合：

- 文件保护。
- 格式化。
- lint。
- 测试。
- 日志。
- 本地环境管理。
- 脚本化检查。

优点：

- 确定性强。
- 易测试。
- 与现有 DevOps 工具兼容。

配置示例：

```json
{
  "type": "command",
  "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/check-style.sh",
  "args": [],
  "timeout": 30
}
```

建议使用 exec form：

```json
{
  "type": "command",
  "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/check-style.sh",
  "args": []
}
```

原因：`args` 形式不会经过 shell tokenization，路径包含空格或特殊字符时更安全。

### 9.2 HTTP hooks：对接内部平台

适合：

- 将 tool call 发送到审计平台。
- 调内部安全策略服务。
- 连接通知系统。
- 集中管理规则。

配置示例：

```json
{
  "type": "http",
  "url": "http://localhost:8080/hooks/pre-tool-use",
  "timeout": 30,
  "headers": {
    "Authorization": "Bearer $MY_TOKEN"
  },
  "allowedEnvVars": ["MY_TOKEN"]
}
```

注意：

- HTTP hooks 通过 POST 发送 hook 输入 JSON。
- 非 2xx、连接失败或超时通常是非阻塞错误。
- 如果要阻止动作，需要返回 2xx + JSON 决策体，而不是依靠 4xx / 5xx。

### 9.3 MCP tool hooks：借助已连接工具

适合：

- 调用安全扫描 MCP。
- 对接知识库或内部服务。
- 用已有 MCP 工具做检查。

示例：

```json
{
  "type": "mcp_tool",
  "server": "my_server",
  "tool": "security_scan",
  "input": {
    "file_path": "${tool_input.file_path}"
  }
}
```

注意：MCP server 必须已经连接。`SessionStart` 或 `Setup` 触发较早，可能早于 MCP server 完成连接。

### 9.4 Prompt hooks：语义判断

适合：

- 需要判断“是否完成任务”。
- 需要检查回答是否覆盖用户要求。
- 需要轻量语义审查，但不需要读文件或跑命令。

示例：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "Evaluate if Claude should stop: $ARGUMENTS. Check if all tasks are complete.",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

Prompt hook 返回格式：

```json
{
  "ok": true,
  "reason": "..."
}
```

或：

```json
{
  "ok": false,
  "reason": "Tests have not been run yet."
}
```

### 9.5 Agent hooks：多步检查，谨慎用于生产

适合：

- 需要读文件、搜索代码、运行测试后再判断。
- 需要一个 verifier 子 Agent。
- 需要比 prompt hook 更强的上下文获取能力。

示例：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "agent",
            "prompt": "Verify that all unit tests pass. Run the test suite and check the results. $ARGUMENTS",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
```

注意：官方将 agent hooks 标为 experimental。生产工作流中，能用 command hook 明确实现的，优先用 command hook。

---

## 10. Async Hooks：长任务不要卡住 Agent

默认情况下，hook 会阻塞 Claude Code，直到执行完成。

对于长任务，例如：

- 测试套件。
- 安全扫描。
- 部署检查。
- 外部 API 调用。

可以使用：

```json
{
  "type": "command",
  "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/run-tests-async.sh",
  "args": [],
  "async": true,
  "timeout": 300
}
```

Async hook 的特点：

| 特点 | 说明 |
|---|---|
| 不阻塞 | Claude 会继续工作 |
| 不能决策 | 不能阻止、修改、审批已经发生的动作 |
| 可注入后续上下文 | 结束后如果输出 `additionalContext`，会在下一轮传给 Claude |
| 适合副作用 | 日志、通知、异步测试、异步扫描 |
| 注意重复触发 | 每次触发会创建独立后台进程 |

**设计规则：**

```text
同步 hook 用于“必须等结果再继续”的检查。
异步 hook 用于“结果可以稍后反馈”的检查。
```

---

## 11. Claude Agent SDK 中的 Hooks

除了 Claude Code 配置文件里的 hooks，Claude Agent SDK 也支持在代码里注册 hook callback。

### 11.1 SDK hooks 的核心概念

在 SDK 中，hooks 是 callback functions：当 agent 发生事件时调用你的代码。

可用于：

- 工具调用前阻止危险操作。
- 记录每次工具调用。
- 改写工具输入或输出。
- 敏感操作前要求人工审批。
- 会话启动和结束时初始化或清理资源。

### 11.2 Python 示例：保护 `.env`

```python
import asyncio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher

async def protect_env_files(input_data, tool_use_id, context):
    file_path = input_data.get("tool_input", {}).get("file_path", "")
    file_name = file_path.split("/")[-1]

    if file_name == ".env":
        return {
            "hookSpecificOutput": {
                "hookEventName": input_data["hook_event_name"],
                "permissionDecision": "deny",
                "permissionDecisionReason": "Cannot modify .env files",
            }
        }

    return {}

async def main():
    options = ClaudeAgentOptions(
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Write|Edit", hooks=[protect_env_files])
            ]
        }
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Update the database configuration")
        async for message in client.receive_response():
            print(message)

asyncio.run(main())
```

### 11.3 Claude Code Hooks vs Agent SDK Hooks

| 维度 | Claude Code settings hooks | Agent SDK callback hooks |
|---|---|---|
| 写在哪里 | JSON settings / plugin / skill frontmatter | Python / TypeScript 代码 |
| 适合谁 | Claude Code 用户、团队项目配置 | 自定义 Agent 应用开发者 |
| Handler | command/http/mcp_tool/prompt/agent | callback function，也可加载 settings shell hooks |
| 输入输出 | stdin/stdout/exit code/JSON | 函数参数与返回对象 |
| 典型用途 | 本地项目自动化、团队规范 | 自定义 UI、Agent 平台、企业应用 |

---

## 12. Claude Code 官方实践案例拆解

下面这些来自 Claude Code 官方 Hooks Guide / Reference / Security Guidance Plugin 的实践，适合做 PPT 的“从简单到高级”案例线。

### 12.1 案例一：桌面通知

**目标：** Claude 需要输入或权限时通知用户，不必一直盯终端。

```json
{
  "hooks": {
    "Notification": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "osascript -e 'display notification \"Claude Code needs your attention\" with title \"Claude Code\"'"
          }
        ]
      }
    ]
  }
}
```

适合讲：最简单 hook，只有副作用，不改变 Agent 行为。

### 12.2 案例二：编辑后自动格式化

**目标：** Claude 每次写入或编辑文件后，自动运行 Prettier。

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path' | xargs npx prettier --write"
          }
        ]
      }
    ]
  }
}
```

适合讲：把“请记得格式化”变成“编辑后必定格式化”。

### 12.3 案例三：阻止修改受保护文件

**目标：** 不允许 Claude 修改 `.env`、`package-lock.json`、`.git/` 等敏感路径。

`.claude/hooks/protect-files.sh`：

```bash
#!/bin/bash
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

PROTECTED_PATTERNS=(".env" "package-lock.json" ".git/")

for pattern in "${PROTECTED_PATTERNS[@]}"; do
  if [[ "$FILE_PATH" == *"$pattern"* ]]; then
    echo "Blocked: $FILE_PATH matches protected pattern '$pattern'" >&2
    exit 2
  fi
done

exit 0
```

`.claude/settings.json`：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/protect-files.sh",
            "args": []
          }
        ]
      }
    ]
  }
}
```

执行：

```bash
chmod +x .claude/hooks/protect-files.sh
```

适合讲：这是 Hooks 的核心价值——硬拦截。

### 12.4 案例四：压缩后重新注入上下文

**目标：** 上下文压缩后，重新提醒 Claude 关键项目事实。

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "echo 'Reminder: use Bun, not npm. Run bun test before committing. Current sprint: auth refactor.'"
          }
        ]
      }
    ]
  }
}
```

适合讲：Hook 不只是拦截，也能动态补充上下文。

### 12.5 案例五：审计配置变化

**目标：** 记录设置或 skill 文件被修改的时间、来源、路径。

```json
{
  "hooks": {
    "ConfigChange": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "jq -c '{timestamp: now | todate, source: .source, file: .file_path}' >> ~/claude-config-audit.log"
          }
        ]
      }
    ]
  }
}
```

适合讲：Hooks 也可以服务合规与审计。

### 12.6 案例六：目录变化后重载环境

**目标：** Claude 执行 `cd` 后，自动让 Bash tool 使用新目录的环境变量。

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "direnv export bash > \"$CLAUDE_ENV_FILE\""
          }
        ]
      }
    ],
    "CwdChanged": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "direnv export bash > \"$CLAUDE_ENV_FILE\""
          }
        ]
      }
    ]
  }
}
```

也可以监听特定文件：

```json
{
  "hooks": {
    "FileChanged": [
      {
        "matcher": ".envrc|.env",
        "hooks": [
          {
            "type": "command",
            "command": "direnv export bash > \"$CLAUDE_ENV_FILE\""
          }
        ]
      }
    ]
  }
}
```

适合讲：Hooks 能把 Agent 接入真实开发环境。

### 12.7 案例七：窄范围自动批准权限

**目标：** 只自动批准 `ExitPlanMode`，不自动批准所有操作。

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": "ExitPlanMode",
        "hooks": [
          {
            "type": "command",
            "command": "echo '{\"hookSpecificOutput\": {\"hookEventName\": \"PermissionRequest\", \"decision\": {\"behavior\": \"allow\"}}}'"
          }
        ]
      }
    ]
  }
}
```

强调：**matcher 要尽可能窄。不要用空 matcher 自动批准所有权限。**

### 12.8 案例八：Security Guidance Plugin

官方 Security Guidance Plugin 是 Hooks 的高级组合实践。

它把安全检查分成三层：

| 层级 | 触发点 | 检查方式 | 特点 |
|---|---|---|---|
| 每次文件编辑 | `PostToolUse` on `Edit` / `Write` / `NotebookEdit` | 快速模式匹配 | 无模型调用、成本低 |
| 每个 turn 结束 | `Stop` | 后台模型 review 当前 turn 的 git diff | 不阻塞回答，发现问题后让 Claude 修复 |
| commit / push 时 | `PostToolUse` on `Bash`，过滤 `git commit` / `git push` | 更深的 agentic review | 读取周边代码，降低误报 |

此外它还使用：

- `SessionStart`：准备 Python 环境。
- `UserPromptSubmit`：捕获工作区 baseline。
- `PostToolUse`：每次编辑后做模式检查。
- `Stop`：回合结束后后台安全审查。
- `PostToolUse` + Bash 过滤：commit / push 时审查。

**这个案例适合 PPT 重点讲：**

> 官方自己的安全插件不是只靠 prompt，而是用 Hooks 把安全审查嵌入 Claude 的工作循环。

---

## 13. 安全边界与最佳实践

### 13.1 Hooks 的最大风险：它们以你的系统用户权限运行

Command hooks 会用当前系统用户权限执行脚本。它们可能访问、修改、删除当前用户能访问的任何文件。

因此，Hooks 不是“安全沙箱”，而是“可编程控制点”。写得好是护栏，写得差就是新的攻击面。

### 13.2 官方安全建议转成检查清单

| 风险 | 建议 |
|---|---|
| 输入不可信 | 永远校验和清洗 hook input |
| Shell 注入 | 变量始终加引号：`"$VAR"` |
| 路径穿越 | 检查 `..`、规范化路径、限制项目根目录 |
| 相对路径不稳定 | 使用 `${CLAUDE_PROJECT_DIR}` 和绝对路径 |
| 敏感文件泄露 | 跳过 `.env`、`.git/`、keys、secrets |
| 自动批准过宽 | matcher 尽可能窄；不要空 matcher approve 所有权限 |
| 日志泄露 | 不要把 secret、token、完整 prompt 原样写日志 |
| Hook 难调试 | 使用 `claude --debug` 或 `--debug-file` |

### 13.3 Hooks 不应替代权限系统

建议组合：

```json
{
  "permissions": {
    "deny": [
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(./secrets/**)",
      "Bash(curl *)"
    ]
  }
}
```

然后再用 hook 做更细粒度的提示、审计、上下文反馈。

**分享时可以这样说：**

> 权限规则负责“红线”，Hook 负责“流程自动化与上下文反馈”。安全场景下二者要叠加，而不是二选一。

---

## 14. Hooks 设计方法论

### 14.1 判断一个需求是否适合 Hook

用五个问题判断：

1. 它是否必须每次都发生？
2. 是否有明确的生命周期触发点？
3. 是否可以用脚本、HTTP 或工具稳定实现？
4. 失败时应该阻止、提醒，还是异步反馈？
5. 是否会引入权限或安全风险？

如果答案是：

```text
必须每次发生 + 触发点明确 + 脚本可实现
```

那它很适合 Hook。

### 14.2 Hook 需求分类

| 类型 | 典型事件 | 是否阻塞 | 示例 |
|---|---|---|---|
| Guardrail | `PreToolUse` | 是 | 阻止写 `.env`、阻止危险 Bash |
| Formatter | `PostToolUse` | 通常否 | prettier、black、go fmt |
| Quality Gate | `Stop` | 可阻塞 | 没跑测试不允许结束 |
| Context Injector | `SessionStart` / `PostToolUse` | 否 | 注入分支、CI、环境状态 |
| Audit Logger | 多事件 | 否 | 记录 Bash、配置变化 |
| Notifier | `Notification` / `SessionEnd` | 否 | Slack、桌面通知 |
| Integration | `HTTP` / `MCP` | 视情况 | 调内部策略平台 |
| Async Checker | `PostToolUse` async | 否 | 后台跑测试、安全扫描 |

### 14.3 Hook 设计的四个层次

```text
L1：个人效率
  通知、格式化、自动记录

L2：项目规范
  保护文件、lint、test、环境加载

L3：团队治理
  配置审计、统一安全规则、共享 .claude/settings.json

L4：平台化
  plugin、managed settings、HTTP policy service、Agent SDK 集成
```

---

## 15. 项目实践：给一个 TypeScript Web 项目搭建 Hooks

假设项目结构：

```text
my-app/
  .claude/
    settings.json
    hooks/
      protect-files.sh
      guard-bash.sh
      format-after-edit.sh
      run-tests-async.sh
      stop-quality-gate.sh
      session-context.sh
  package.json
  src/
```

### 15.1 项目级 `.claude/settings.json`

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "deny": [
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(./secrets/**)",
      "Bash(curl *)"
    ]
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|compact",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/session-context.sh",
            "args": [],
            "timeout": 10
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/protect-files.sh",
            "args": [],
            "timeout": 10
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/guard-bash.sh",
            "args": [],
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/format-after-edit.sh",
            "args": [],
            "timeout": 60
          },
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/run-tests-async.sh",
            "args": [],
            "async": true,
            "timeout": 300
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/stop-quality-gate.sh",
            "args": [],
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

### 15.2 `protect-files.sh`

```bash
#!/bin/bash
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"

# 只处理文件写入类工具
if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# 防止路径穿越
if [[ "$FILE_PATH" == *".."* ]]; then
  echo "Blocked: path traversal is not allowed: $FILE_PATH" >&2
  exit 2
fi

PROTECTED=(
  ".env"
  ".env.local"
  ".git/"
  "secrets/"
  "package-lock.json"
  "pnpm-lock.yaml"
)

for pattern in "${PROTECTED[@]}"; do
  if [[ "$FILE_PATH" == *"$pattern"* ]]; then
    echo "Blocked: protected file pattern '$pattern' matched: $FILE_PATH" >&2
    exit 2
  fi
done

exit 0
```

### 15.3 `guard-bash.sh`

```bash
#!/bin/bash
set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

DENY_PATTERNS=(
  "rm -rf /"
  "sudo "
  "chmod 777"
  "curl "
  "wget "
  "mkfs"
  "dd if="
)

for pattern in "${DENY_PATTERNS[@]}"; do
  if [[ "$COMMAND" == *"$pattern"* ]]; then
    echo "Blocked Bash command: pattern '$pattern' is not allowed." >&2
    exit 2
  fi
done

exit 0
```

说明：这只是示例。生产环境应根据团队实际需求建立 allowlist / denylist，并结合 Claude Code 权限规则与 sandbox。

### 15.4 `format-after-edit.sh`

```bash
#!/bin/bash
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [[ -z "$FILE_PATH" || ! -f "$FILE_PATH" ]]; then
  exit 0
fi

case "$FILE_PATH" in
  *.js|*.jsx|*.ts|*.tsx|*.json|*.md|*.css)
    if command -v npx >/dev/null 2>&1; then
      npx prettier --write "$FILE_PATH" >/dev/null 2>&1 || true
    fi
    ;;
  *)
    ;;
esac

exit 0
```

### 15.5 `run-tests-async.sh`

```bash
#!/bin/bash
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

case "$FILE_PATH" in
  *.ts|*.tsx|*.js|*.jsx)
    ;;
  *)
    exit 0
    ;;
esac

RESULT=$(npm test -- --runInBand 2>&1 || true)

# 限制输出长度，避免塞爆上下文
SUMMARY=$(echo "$RESULT" | tail -80)

jq -nc --arg msg "Async test result after editing $FILE_PATH:\n$SUMMARY" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: $msg
  }
}'
```

### 15.6 `stop-quality-gate.sh`

```bash
#!/bin/bash
set -euo pipefail

# 示例：如果存在 package.json，就提醒 Claude 在结束前确认测试状态。
# 这里用 additionalContext 作为“正常反馈”，而不是错误式 block。

if [[ -f "package.json" ]]; then
  jq -nc '{
    hookSpecificOutput: {
      hookEventName: "Stop",
      additionalContext: "Before finalizing, summarize whether lint/tests were run. If not run, explain why and propose the exact command."
    }
  }'
else
  exit 0
fi
```

如果你希望“没测试就不准停”，可以让脚本检测测试日志或状态文件，并返回：

```json
{
  "decision": "block",
  "reason": "Tests have not been run. Run npm test before stopping."
}
```

### 15.7 `session-context.sh`

```bash
#!/bin/bash
set -euo pipefail

BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
RECENT=$(git log --oneline -5 2>/dev/null || true)

cat <<CTX
Project runtime context:
- Current git branch: $BRANCH
- Preferred package manager: npm
- After code edits, run: npm test
- Recent commits:
$RECENT
CTX
```

### 15.8 初始化命令

```bash
chmod +x .claude/hooks/*.sh
claude
/hooks
```

`/hooks` 可以查看 hook 是否注册成功。

---

## 16. 项目落地路线图

### 阶段一：个人试点

目标：先让个人效率提升。

建议 hook：

- `Notification`：需要输入时通知。
- `PostToolUse`：编辑后格式化。
- `SessionStart`：注入当前项目上下文。

### 阶段二：项目共享

目标：让团队开发规范稳定执行。

建议 hook：

- `PreToolUse`：保护敏感文件。
- `PostToolUse`：lint / format。
- `Stop`：结束前质量门禁。
- `ConfigChange`：审计配置变化。

### 阶段三：安全治理

目标：减少 Agent 引入安全问题。

建议：

- 使用 `permissions.deny` 先设红线。
- 加 `PreToolUse` 拦截危险命令和路径。
- 使用 Security Guidance Plugin 或自研类似分层审查。
- commit / push 前触发安全 review。

### 阶段四：平台化复用

目标：跨项目、跨团队复用。

建议：

- 把通用 hook 打包为 plugin。
- 使用 managed settings 做组织级强制策略。
- 用 HTTP hooks 连接统一策略服务。
- 用 Agent SDK hooks 接入自定义平台或内部 UI。

---

## 17. 常见坑与排查

| 问题 | 常见原因 | 排查方式 |
|---|---|---|
| Hook 没触发 | 事件名大小写错误、matcher 不匹配、配置位置错误 | `/hooks` 查看；开 debug log |
| JSON 输出无效 | stdout 混入其他文本、exit code 非 0、JSON 字段位置错 | 保证 stdout 只有 JSON；用 `jq -n` 生成 |
| `updatedInput` 没生效 | 没放在 `hookSpecificOutput` 下，没配合 `allow` / `ask` | 对照官方 schema |
| 自动批准太多 | matcher 为空或过宽 | 缩窄到具体工具和条件 |
| Hook 太慢 | 同步执行长任务 | 改为 `async: true` 或增加 timeout |
| 路径不稳定 | 使用相对路径 | 用 `${CLAUDE_PROJECT_DIR}` |
| 团队成员环境不同 | hook 依赖本机路径或工具 | 写 README、检测依赖、放 local settings |
| 安全规则被绕过 | 只写 CLAUDE.md，没有 deny rule 或 PreToolUse | 用权限规则 + hook 双层控制 |

Debug 命令：

```bash
claude --debug
claude --debug-file /tmp/claude-debug.log
```

---

## 18. PPT 结构建议：20 页从基础到项目实践

> 下面可以直接拆成 PPT。每页包含：标题、画面建议、要点、口播稿。

### Slide 1：标题页

**标题：** Agent Hooks：让 AI Agent 从“会做”变成“每次都按规则做”  
**画面建议：** Agent loop + hooks checkpoints 示意图。

**要点：**

- Hook 是 AI Agent 的生命周期控制点。
- Claude Code Hooks 是工程化 Agent 的重要机制。
- 分享路线：概念 → 机制 → 官方实践 → 项目落地。

**口播稿：**

大家好，今天我们讲 Agent Hooks。AI Agent 最大的变化不是能回答问题，而是能读代码、改文件、运行命令、调用工具。能力变强之后，我们更需要稳定的工程护栏。Hooks 的作用，就是把规则挂到 Agent 生命周期中，让某些动作在关键节点自动发生，而不是只依赖模型记得。

---

### Slide 2：为什么需要 Hooks

**画面建议：** 左边是 prompt 指令，右边是流水线检查点。

**要点：**

- Prompt 是软约束。
- Hooks 是事件触发的自动执行。
- 适合“每次必须发生”的流程。

**口播稿：**

我们以前会在 prompt 或 CLAUDE.md 里写：修改后运行测试，不要动 `.env`。但这些是软约束，模型可能忘记，也可能因为上下文太长而忽略。Hooks 的价值在于，它不是提醒模型，而是在事件发生时由系统自动运行检查或脚本。所以它更像 CI/CD 里的 pipeline gate。

---

### Slide 3：Agent Loop 中的 Hook 点

**画面建议：** User Prompt → Model → Tool Call → Tool Result → Stop，中间标红 hook。

**要点：**

- Prompt 提交前可检查。
- 工具调用前可拦截。
- 工具调用后可格式化和审计。
- 停止前可做质量门禁。

**口播稿：**

Agent 的工作循环可以简化成用户输入、模型决策、工具调用、工具结果、继续或停止。Hooks 就是插在这些节点上的回调。比如 PreToolUse 在工具执行前触发，所以适合拦截危险命令；PostToolUse 在工具执行后触发，所以适合格式化和记录；Stop 在 Claude 想结束时触发，所以适合问：测试跑了吗？任务真的完成了吗？

---

### Slide 4：Hook 的四层模型

**画面建议：** Event → Matcher → Handler → Output。

**要点：**

- Event：何时触发。
- Matcher：是否匹配。
- Handler：执行什么。
- Output：如何影响 Agent。

**口播稿：**

理解 Claude Code Hooks，只要记住四层：事件、匹配器、处理器、输出。事件决定时机，matcher 决定范围，handler 决定执行什么，output 决定结果是放行、阻止、询问用户，还是注入上下文。

---

### Slide 5：Hooks vs CLAUDE.md / Skills / MCP / Subagents

**画面建议：** 对比表。

**要点：**

- CLAUDE.md：每次都知道。
- Skill：复用知识和工作流。
- MCP：连接外部工具。
- Subagent：隔离上下文。
- Hook：每次自动执行。

**口播稿：**

Claude Code 里有很多扩展方式。CLAUDE.md 是让 Claude 知道项目规则；Skill 是把复用流程变成能力；MCP 是接外部服务；Subagent 是隔离任务；Hook 则是自动化和硬约束。如果一条规则必须每次都执行，就不要只写在 prompt 里，要考虑 Hook。

---

### Slide 6：Claude Code Hooks 的五类 Handler

**画面建议：** 五个卡片：command、http、mcp_tool、prompt、agent。

**要点：**

- command：最常用。
- http：对接内部服务。
- mcp_tool：复用 MCP 工具。
- prompt：单轮语义判断。
- agent：多步 verifier，实验性。

**口播稿：**

Claude Code 不只支持 shell。它支持 command、HTTP、MCP tool、prompt 和 agent 五类 handler。生产中最常用的是 command，因为确定、可测、能复用现有工程工具。Prompt 和 agent hook 适合需要判断的问题，但判断越复杂，成本和不确定性越高。

---

### Slide 7：事件全景

**画面建议：** 按层级展示事件。

**要点：**

- 会话层：SessionStart、SessionEnd。
- 输入层：UserPromptSubmit。
- 工具层：PreToolUse、PostToolUse。
- 结束层：Stop。
- 环境层：ConfigChange、FileChanged。

**口播稿：**

官方事件很多，但不用一次全记住。先掌握四个最常用：SessionStart、PreToolUse、PostToolUse、Stop。SessionStart 用来准备上下文，PreToolUse 做拦截，PostToolUse 做反馈和自动化，Stop 做质量门禁。其他事件是把这种能力扩展到配置、通知、子 Agent、worktree、MCP elicitation 等场景。

---

### Slide 8：配置结构

**画面建议：** JSON 嵌套结构。

**要点：**

- hooks 对象下按事件配置。
- 事件下是 matcher group。
- matcher group 下是 handlers。

**口播稿：**

配置上，Claude Code Hooks 是一个三层 JSON。第一层是事件名，第二层是 matcher，第三层是具体 handler。比如 PostToolUse + Edit|Write + prettier，就是“当 Claude 编辑或写入文件后，自动运行 prettier”。

---

### Slide 9：输入输出协议

**画面建议：** stdin JSON / stdout JSON / exit code。

**要点：**

- 输入从 stdin 传入 JSON。
- exit 0 表示无异议。
- exit 2 表示阻止。
- exit 0 + JSON 表示结构化控制。

**口播稿：**

Command hook 的协议非常 Unix：Claude Code 把事件 JSON 传到 stdin，脚本处理后通过 exit code、stdout、stderr 返回。简单阻止用 exit 2 加 stderr。复杂控制用 exit 0 加 JSON，比如 deny、ask、updatedInput、additionalContext。

---

### Slide 10：PreToolUse 是安全拦截核心

**画面建议：** 工具调用前的红色闸门。

**要点：**

- 工具执行前触发。
- 可 allow / deny / ask / defer。
- 可修改 updatedInput。
- 适合硬约束。

**口播稿：**

PreToolUse 是最重要的安全事件，因为它发生在工具执行前。只要是“不能让它发生”的事情，都优先考虑 PreToolUse。例如不允许改 `.env`、不允许运行危险 Bash、不允许写生产配置，都应该在这里拦截，而不是等执行后再提醒。

---

### Slide 11：PostToolUse 是自动化与反馈核心

**画面建议：** 文件编辑后自动跑格式化、测试、扫描。

**要点：**

- 工具成功后触发。
- 适合格式化、lint、审计。
- 可以向 Claude 注入反馈。
- 不能撤销真实副作用。

**口播稿：**

PostToolUse 适合做编辑后的自动化。比如写文件后格式化，运行 lint，把测试结果反馈给 Claude。要注意，它发生在工具执行后，所以不能防止副作用。如果要阻止动作，还是用 PreToolUse。

---

### Slide 12：Stop Hook 是质量门禁

**画面建议：** Claude 准备结束前出现 Checklist。

**要点：**

- Claude 想结束时触发。
- 可要求继续处理。
- 适合检查测试、总结、未完成事项。

**口播稿：**

Stop Hook 是另一个非常实用的点。很多时候 Claude 改完代码就想总结结束，但团队希望它先确认测试、lint、风险点。Stop Hook 可以在结束前提醒甚至阻止停止，让 Claude 继续完成剩余工作。

---

### Slide 13：官方实践：从通知到格式化

**画面建议：** Notification + Prettier 两个例子。

**要点：**

- Notification：无需盯终端。
- Auto-format：编辑后自动格式化。
- 这是低风险入门组合。

**口播稿：**

官方 guide 的入门案例非常简单：一个是通知，一个是编辑后格式化。通知不改变 Agent 行为，只是提醒人；格式化则把一个重复动作自动化。这两个 hook 风险低，非常适合团队从这里开始试点。

---

### Slide 14：官方实践：保护文件与权限

**画面建议：** `.env` 被红色盾牌保护。

**要点：**

- PreToolUse 阻止敏感文件修改。
- PermissionRequest 可窄范围自动批准。
- 不要广泛 auto-approve。

**口播稿：**

保护文件是展示 Hook 价值最直观的案例。只要 Claude 试图改 `.env`，脚本就 exit 2，动作被取消。另一方面，PermissionRequest 可以帮我们减少低风险确认，例如自动批准 ExitPlanMode。但一定要窄范围匹配，不能把所有权限都自动批准。

---

### Slide 15：官方实践：Security Guidance Plugin

**画面建议：** 三层安全扫描漏斗。

**要点：**

- 每次编辑：模式匹配。
- 每个 turn：后台模型 review diff。
- commit/push：更深 agentic review。
- 全部基于 hooks 集成。

**口播稿：**

Security Guidance Plugin 是官方最好的高级案例。它不是等 PR 再审，而是在 Claude 写代码时就介入。每次编辑做快速模式匹配；每轮结束后台审查 diff；commit 或 push 时做更深的 agentic review。这说明 hooks 可以把安全能力嵌入 Agent 的工作流，而不是事后补救。

---

### Slide 16：安全边界

**画面建议：** Hook 既是护栏也是攻击面。

**要点：**

- Command hook 以用户权限运行。
- 输入必须校验。
- 变量必须加引号。
- 结合 permission deny 和 sandbox。

**口播稿：**

Hooks 很强，但也有风险。Command hook 以你的系统用户权限运行，所以它能访问你能访问的文件。写 hook 时一定要把输入当作不可信，做好路径检查、变量引用、敏感文件保护。安全策略不能只靠 hook，也要结合权限 deny 和 sandbox。

---

### Slide 17：项目实践架构

**画面建议：** `.claude/settings.json` + `.claude/hooks/*.sh` 目录树。

**要点：**

- settings.json 负责注册。
- hooks 目录放脚本。
- 先个人 local，再项目共享。
- 用 `/hooks` 验证。

**口播稿：**

落地时建议把 hook 脚本统一放到 `.claude/hooks/`，项目共享配置放 `.claude/settings.json`。先在 local 配置里试，稳定后再提交到仓库。每次改完用 `/hooks` 查看是否生效，用 debug log 排查问题。

---

### Slide 18：落地路线图

**画面建议：** 四阶段阶梯图。

**要点：**

1. 个人效率。
2. 项目规范。
3. 安全治理。
4. 平台化复用。

**口播稿：**

不要一开始就做复杂平台。先从个人通知和格式化开始，然后把保护文件、lint、Stop gate 放进项目。再往后，把安全审查、审计、权限策略做成团队治理。最后如果多个仓库复用，再做 plugin 或 managed settings。

---

### Slide 19：常见坑

**画面建议：** Debug checklist。

**要点：**

- Hook 不触发：事件名或 matcher。
- JSON 无效：stdout 混入文本。
- Hook 太慢：用 async。
- 自动批准过宽：安全风险。

**口播稿：**

常见问题主要有四类：第一，事件名大小写或 matcher 写错；第二，JSON 输出被其他文本污染；第三，hook 做了长任务导致 Agent 卡住；第四，auto-approve 写得太宽。解决方法就是用 `/hooks` 和 debug log，看清楚到底匹配了什么、执行了什么、返回了什么。

---

### Slide 20：总结页

**画面建议：** 三句话总结。

**要点：**

- Prompt 让 Agent 知道规则。
- Hook 让规则在关键节点执行。
- 项目落地要从低风险自动化到安全治理逐步推进。

**口播稿：**

最后总结三句话。第一，Hooks 是 Agent 工程化的关键机制。第二，Prompt 和 CLAUDE.md 是软约束，Hook 是事件驱动的自动化护栏。第三，Hooks 最好的落地方式是逐步演进：先提升个人效率，再沉淀项目规范，最后进入团队治理和平台化复用。

---

## 19. 10 分钟演示脚本

### 演示目标

让观众看到三个效果：

1. Claude 写文件后自动格式化。
2. Claude 试图改 `.env` 被拦截。
3. Claude 停止前收到质量门禁反馈。

### 演示步骤

#### Step 1：展示目录结构

```bash
tree .claude
```

解释：

```text
settings.json 负责注册 hook。
hooks/*.sh 是实际执行逻辑。
```

#### Step 2：打开 `/hooks`

在 Claude Code 中输入：

```text
/hooks
```

展示：

- `PreToolUse` 有保护文件 hook。
- `PostToolUse` 有格式化和异步测试 hook。
- `Stop` 有质量门禁 hook。

#### Step 3：让 Claude 修改一个 TS 文件

Prompt：

```text
Please add a small utility function to src/utils/demo.ts with intentionally rough formatting.
```

观察：

- Claude 写文件。
- `PostToolUse` 触发 prettier。
- 文件被自动格式化。

#### Step 4：尝试修改 `.env`

Prompt：

```text
Please add DEMO_FLAG=true to .env.
```

观察：

- `PreToolUse` 拦截。
- Claude 收到 blocked reason。
- 它会改用安全方式，例如建议你手动修改或创建 `.env.example`。

#### Step 5：让 Claude 结束任务

Prompt：

```text
Finish the task and summarize what changed.
```

观察：

- `Stop` hook 注入质量门禁反馈。
- Claude 需要说明测试是否运行，或给出未运行原因与命令。

---

## 20. 可复用的 Hooks 选型速查表

| 需求 | 推荐事件 | 推荐 handler | 是否阻塞 | 备注 |
|---|---|---|---|---|
| 保存后格式化 | `PostToolUse` on `Edit|Write` | command | 否 | 最佳入门 |
| 禁止改敏感文件 | `PreToolUse` on `Edit|Write` | command | 是 | 配合 permissions.deny |
| 禁止危险命令 | `PreToolUse` on `Bash` | command | 是 | 尽量 allowlist |
| 运行测试 | `PostToolUse` | async command | 否 | 结果反馈给下一轮 |
| 结束前检查 | `Stop` | command / prompt / agent | 可阻塞 | command 更确定 |
| 注入当前环境 | `SessionStart` / `CwdChanged` | command | 否 | direnv/devbox/nix |
| 通知用户 | `Notification` | command / http | 否 | 桌面/Slack |
| 记录审计 | 多事件 | command / http | 否 | 注意脱敏 |
| 语义判断 | `Stop` / `PreToolUse` | prompt | 可阻塞 | 不适合硬安全 |
| 多步验证 | `Stop` | agent | 可阻塞 | experimental |
| 对接内部平台 | 多事件 | http | 视情况 | 2xx + JSON 才能决策 |

---

## 21. 讲给管理者听的版本

如果听众不是工程师，可以这样讲：

> AI Agent 像一个可以自己操作电脑的实习工程师。Prompt 是你告诉他的工作原则，但他忙起来可能会漏。Hook 就是在工作流程里加自动检查点：改文件后自动格式化，危险操作前自动拦截，任务结束前自动确认测试，关键事件自动留痕。它把 AI 使用从“个人技巧”推进到“可治理的工程流程”。

---

## 22. 讲给工程师听的版本

如果听众是工程师，可以这样讲：

> Claude Code Hooks 是围绕 agent loop 的事件驱动 middleware。事件触发后通过 matcher 过滤，再执行 command/http/mcp/prompt/agent handler。Command hook 通过 stdin 接收 JSON，通过 exit code 或 stdout JSON 返回决策。PreToolUse 是前置拦截，PostToolUse 是后置反馈，Stop 是结束门禁。它最适合把 lint、format、test、安全策略、审计和通知接入 Agent 工作流。

---

## 23. Q&A 备答

### Q1：Hook 会不会让 Claude 变慢？

会，尤其是同步 hook。建议：

- 快速检查同步执行。
- 长任务用 `async: true`。
- 用 matcher 缩小触发范围。
- 用 `if` 避免不必要的脚本启动。

### Q2：能不能用 Hook 自动批准所有权限？

技术上可以配置很宽的 matcher，但不建议。自动批准应该极窄范围，例如只批准 `ExitPlanMode` 或只批准只读工具。广泛 auto-approve 会显著扩大风险。

### Q3：Hook 和 CI 有什么区别？

CI 是事后或提交时检查，Hook 可以在 Agent 工作过程中实时介入。最佳实践是两者结合：Hook 让问题更早暴露，CI 作为最终门禁。

### Q4：Hook 可以保证安全无漏洞吗？

不能。Hook 是防线之一，不是完整安全方案。需要结合权限规则、sandbox、代码审查、CI 扫描、secret scanning、依赖扫描等。

### Q5：Prompt hook 和 command hook 怎么选？

- 规则明确、可脚本化：command hook。
- 需要语义判断：prompt hook。
- 需要读取代码、多步验证：agent hook，但生产中要谨慎。

### Q6：Hook 的输出会污染上下文吗？

默认不会。只有 stdout 被特定事件作为上下文，或你显式返回 `additionalContext` 时，才会进入 Claude 的上下文。

### Q7：Hook 能撤销已经执行的工具吗？

不能。`PostToolUse` 发生在工具执行后，只能影响 Claude 接下来看到什么或做什么。要阻止动作必须用 `PreToolUse`。

---

## 24. 最终总结

Hooks 的核心不是“多跑一个脚本”，而是把 AI Agent 纳入工程流程：

```text
Prompt / CLAUDE.md：定义意图和知识
Hooks：执行确定性动作与硬约束
Permissions / Sandbox：建立安全边界
Skills / Subagents / MCP：扩展能力
Plugins / Managed settings：规模化分发和治理
```

最推荐的落地组合：

```text
第一步：Notification + PostToolUse format
第二步：PreToolUse protect files + Bash guard
第三步：PostToolUse async tests + Stop quality gate
第四步：Security guidance plugin / 自研 HTTP policy hooks
第五步：打包成 plugin 或 managed settings
```

**结论：**

> 当 Agent 能操作真实工程环境时，Hooks 是从“会用 AI”走向“可控地使用 AI”的关键机制。

---

## 25. 官方参考资料

1. Claude Code Hooks Guide：`https://code.claude.com/docs/en/hooks-guide`
2. Claude Code Hooks Reference：`https://code.claude.com/docs/en/hooks`
3. Claude Agent SDK Hooks：`https://code.claude.com/docs/en/agent-sdk/hooks`
4. Claude Code Best Practices：`https://code.claude.com/docs/en/best-practices`
5. Extend Claude Code：`https://code.claude.com/docs/en/features-overview`
6. Claude Code Settings：`https://code.claude.com/docs/en/settings`
7. Claude Code Permissions：`https://code.claude.com/docs/en/permissions`
8. Security Guidance Plugin：`https://code.claude.com/docs/en/security-guidance`
9. Claude Code Skills：`https://code.claude.com/docs/en/skills`
10. Claude Code MCP：`https://code.claude.com/docs/en/mcp`
