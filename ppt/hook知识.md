# Agent Hooks 知识纲要

---

## 一、定义与定位

**Agent Hook** 是 Agent Runtime 在特定生命周期事件发生时，自动触发的可插拔处理器。

核心观点：

> Prompt 影响模型如何**决策**；Hook 介入 Runtime 如何**执行**。

模型决定"想做什么"，Runtime 通过 Hook 决定"这个动作是否真的发生"。

Hook 的触发机制是确定性的，但 Hook 内部的判断逻辑未必确定——如果 handler 调用了 LLM 或子 Agent，结果仍然带有概率性。

---

## 二、Hook 所在的位置

Agent 循环抽象：

```
用户输入 → 构建上下文 → 调用模型 → 模型决策（回答/调工具/转交）
→ 执行工具 → 结果回填上下文 → 再次调模型 → 最终回答
```

Hook 插在几乎所有边界上：

```
SessionStart → BeforeAgent → BeforeModel → AfterModel
→ BeforeTool → AfterTool → Handoff/SubAgent → Stop/AfterAgent
```

Hook 属于 Agent Runtime 的**控制平面**，不属于模型的"大脑"（数据平面）：

| 平面 | 职责 |
|------|------|
| 数据平面 | 推理、工具执行、任务完成 |
| 控制平面 | 权限、审计、拦截、重试、策略、生命周期管理 |

---

## 三、统一结构模型

无论框架叫 Hook、Callback、Middleware 还是 Filter，都遵循：

```
Event → Matcher → Handler → Outcome
```

**Event**：Agent 开始/结束、模型调用前后、工具执行前后、子 Agent 创建、上下文压缩、handoff 等。

**Matcher**：筛选哪些事件需要处理（按工具名、正则、环境、Agent 类型等）。

**Context**：Hook 可见的信息——Agent 实例、会话 ID、消息历史、模型名称、工具名/参数/结果、Token 成本、trace ID、重试次数等。

**Handler**：可以是普通函数、Shell 脚本、HTTP 服务、MCP 工具、一次 LLM 判断、一个完整子 Agent，或人工审批流程。

**Outcome**：continue / allow / deny / ask / replace / retry / defer / skip。

形式化表达：

```
H(e, S, x) → (decision, x', effects)
```
- e：生命周期事件
- S：当前运行状态
- x：原始输入
- decision：允许、拒绝、重试等
- x'：修改后的输入或输出
- effects：日志、通知等副作用

---

## 四、Before / After / Around Hook

**Before Hook**——核心操作之前运行，适合参数校验、权限检查、脱敏、阻止危险操作、请求人工确认。

**After Hook**——操作完成后运行，适合日志追踪、格式化输出、检查结果、运行测试、发送通知。

**Around/Wrap Hook**——包裹整个操作，能力最强：可短路、重试、缓存、切换备用模型、修改输入输出。LangChain 的 wrap middleware 明确允许对下层 handler 调用零次、一次或多次。

---

## 五、本项目教学版实现范围

本项目采用精简的类式 Hook API，核心是：

```text
HookRegistry.emit(event, ctx)
```

教学版已实现：

1. **顺序执行**：按注册顺序触发同名事件。
2. **短路返回**：`before_turn` / `before_tool_call` 返回字符串时跳过后续核心操作。
3. **原地改写**：Hook 可修改 `ctx["system_prompt"]`、`ctx["input"]`、`ctx["output"]`。
4. **错误隔离**：单个 Hook 异常会打印 `[hook error]`，不拖垮主循环。
5. **安全隔离**：主 Agent 使用完整 hooks，子代理和队友只使用 `safe_hooks`。

内置教学 hooks：

| Hook | 事件 | 作用 |
|------|------|------|
| `LoggingHook` | `before_turn` / `after_turn` | 打印 LLM 调用耗时与 token |
| `RateLimitHook` | `before_turn` | 限制模型调用频率 |
| `GuardHook` | `before_tool_call` | 阻止写入 `.env`、私钥、生产配置 |
| `BlockDangerousCommandHook` | `before_tool_call` | 阻止 `rm -rf /`、`DROP TABLE` 等危险命令 |
| `ToolAuditHook` | `after_tool_call` | 审计写文件、编辑文件、执行命令等操作 |
| `ContextInjectionHook` | `before_turn` | 动态注入当前工作目录 |

教学版暂不实现配置式 YAML Hook、脚本式 Hook、异步 Hook、人工审批、Agent-Based Stop Hook。它们属于进阶方向，用来讲生产系统时展开。

---

## 六、Hook 能解决的六类问题

1. **可观测性**：每次模型/工具调用的延迟、Token 消耗、成本、错误、handoff 等。
2. **安全与权限**：禁止危险操作（删生产数据、改 .env、外发敏感字段），高风险操作必须人工确认。
3. **上下文工程**：按需动态注入——用户偏好、项目规范、相关文件、会话摘要、长期记忆。
4. **质量门禁**：代码格式化 → 静态检查 → 测试通过 → 才允许 Agent 结束。
5. **可靠性**：超时、指数退避、自动重试、熔断、模型降级、工具 fallback、缓存。
6. **多 Agent 编排**：子 Agent 启停、任务分配、handoff、状态同步、结果汇总。

---

## 七、概念辨析

| 概念 | 区别 |
|------|------|
| **Hook vs Prompt** | Prompt 是给模型看的指令；Hook 是 Runtime 执行的规则。 |
| **Hook vs Tool** | Tool 由模型选择调用；Hook 由系统自动触发。 |
| **Hook vs Guardrail** | Guardrail 描述安全/政策约束；Hook 描述在哪执行逻辑。二者常组合使用。 |
| **Hook vs Callback** | Callback 通常只观察；Hook 可观察、修改、拦截。但实际命名不统一，需看具体能力。 |
| **Hook vs Middleware** | Hook 强调具体插入点；Middleware 强调可组合的处理链和执行顺序。 |
| **Hook vs Webhook** | Webhook 是 HTTP 传输方式；Hook 是生命周期扩展机制。 |
| **Hook vs MCP** | MCP 解决工具发现与调用；Hook 可拦截、审计、阻止 MCP 调用，也可通过 MCP 工具执行检查。 |

判断框架能力不看名称，看三个问题：

1. 能否**修改**输入？
2. 能否**阻止**核心操作？
3. 能否**替换**输出？

---

## 八、主流框架对照

各框架解决的是同一类问题，术语不同：

| 框架 | 术语 | 特点 |
|------|------|------|
| Claude Code | Hooks | 最接近完整控制面，支持 shell/HTTP/MCP/prompt/agent 五种 handler |
| OpenAI | AgentHooks / RunHooks / Guardrails | 分两层（Run vs Agent），Hook 偏观察，阻断靠 guardrails |
| LangChain | Middleware | wrap 型可短路/重试/缓存，before 顺序执行，after 反向执行 |
| Google ADK | Callbacks | 不只是观察，部分节点可返回替代结果跳过原执行 |
| CrewAI | Execution Hooks | before/after LLM 和 tool，before 返回 False 可阻止 |
| Semantic Kernel | Filters | function invocation filter + prompt render filter |

---

## 九、Agent-Based Hook（Claude Code）

三种 handler 层级：

| 类型 | 机制 | 适合 |
|------|------|------|
| Command Hook | 运行确定性脚本 | 路径检查、正则规则、格式化、静态扫描 |
| Prompt Hook | LLM 语义判断 | 模糊语义问题（"是否泄露隐私？"） |
| Agent Hook | 启动有工具的子 Agent | 读 diff、查测试、验证任务是否真正完成 |

关键理解：

> Agent Hook 不是"Hook 变成了 Agent Runtime"，而是"Hook 的 handler 由另一个 Agent 执行"。

触发是确定性的，判断仍可能是概率性的。

**适合语义验收**：任务是否满足需求、测试是否覆盖关键逻辑、回答是否与代码一致、是否存在风险。

**不适合**：精确权限、金额上限、文件后缀检查、SQL 白名单——能用确定性代码判断的，不要交给 LLM。

---

## 十、安全边界警示

1. **观察型 Hook 无法阻止操作**——after-tool 日志发现危险时，操作已发生。权限控制必须放 Before，且同步执行、fail-closed、不可绕过。
2. **异步 Hook 不适合授权**——适合 telemetry/日志/指标，不适合权限/支付审批/数据外发检查。
3. **Matcher 不等于安全策略**——条件过滤只是选择执行哪些 handler，不是不可绕过的授权边界。
4. **LLM Judge 不能当绝对安全边界**——可能被提示注入、忽略隐晦风险、相同输入给出不同判断。

合理的安全架构层级：

```
确定性硬规则 → 权限系统 → 人工审批 → LLM 语义检查
```

不是反过来。

---

## 十一、生产级 Hook 系统十问

1. **Scope**：作用在全局/单次 run/某个 Agent/某个工具/某次会话？
2. **Ordering**：多个 Hook 的执行顺序？冲突时谁优先？修改后的参数是否传给后续？
3. **Failure Policy**：fail-open（日志类）还是 fail-closed（权限类）？
4. **Timeout**：HTTP/LLM/Agent Hook 必须有超时，否则卡死整个 Agent。
5. **Idempotency**：发邮件/扣款/创建工单等操作必须幂等，避免重试导致重复执行。
6. **Reentrancy**：Stop Hook 启动子 Agent → 子 Agent 结束 → 再次触发 Stop Hook → 递归。需 depth 限制、source-agent 标识、once-per-run 控制。
7. **Concurrency**：并行 Hook 可能产生修改冲突、日志乱序、状态竞争。
8. **Permissions**：Hook 自身的权限不能比被保护对象更大，Hook 配置等同于可执行代码。
9. **Observability**：每次执行至少记录 Hook ID、事件类型、trace ID、耗时、决策、修改内容、错误、Token 成本，且日志必须脱敏。
10. **Versioning**：事件 payload 结构变化可能导致权限规则静默失效，需 schema version 和兼容性测试。

---

## 十二、推荐分层建设路径

```
Level 0：可观测性     → 日志、trace、耗时、成本、错误
Level 1：确定性转换   → 参数标准化、脱敏、格式化
Level 2：确定性门禁   → 路径白名单、权限表、金额限制
Level 3：人工审批     → 删数据、发布生产、付款、外发敏感信息
Level 4：语义判断     → LLM 或 Agent Hook 验证任务完成质量
```

好处：可预测、可测试、低成本、小攻击面，语义理解能力按需叠加。

---

## 十三、核心要义

1. Hook 是 Runtime 的生命周期扩展点，不是模型本身的能力。
2. Hook 的自动触发可以确定，但 LLM 或 Agent Hook 的判断仍然具有概率性。
3. 不是所有叫 Hook 或 Callback 的 API 都能修改或阻止执行——看具体能力。
4. 安全边界应优先使用同步、确定性、fail-closed 的机制。
5. Agent-Based Hook 最适合语义验收，不适合替代权限系统。
