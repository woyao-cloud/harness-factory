# HarnessFactory — 系统详细设计 v1.0

> 基于代码实现逆向生成的系统设计文档
> 版本：1.0 | 更新日期：2026-04-25

---

## 目录

1. [项目概述](#1-项目概述)
2. [架构总览](#2-架构总览)
3. [分层详解](#3-分层详解)
   - [3.1 类型层 — runtime/types.py](#31-类型层--runtimetypespy)
   - [3.2 事件总线 — runtime/message_bus.py](#32-事件总线--runtimemessage_buspy)
   - [3.3 安全门 — runtime/security.py](#33-安全门--runtimesecuritypy)
   - [3.4 LLM 提供者 — runtime/llm_provider.py](#34-llm-提供者--runtimellm_providerpy)
   - [3.5 会话管理 — runtime/session.py](#35-会话管理--runtimesessionpy)
   - [3.6 管道引擎 — runtime/pipeline.py](#36-管道引擎--runtimepipelinepy)
   - [3.7 流式传输 — runtime/streaming.py](#37-流式传输--runtimestreamingpy)
   - [3.8 错误恢复 — runtime/recovery.py](#38-错误恢复--runtimerecoverypy)
   - [3.9 检查点持久化 — runtime/checkpoint.py](#39-检查点持久化--runtimecheckpointpy)
   - [3.10 运行时编排 — runtime/harness.py](#310-运行时编排--runtimeharnesspy)
   - [3.11 工具注册表 — tool_registry/](#311-工具注册表--tool_registry)
   - [3.12 上下文管理器 — context_manager/](#312-上下文管理器--context_manager)
   - [3.13 提供者工厂 — harnesses/provider.py](#313-提供者工厂--harnessesproviderpy)
   - [3.14 Harness 工厂 — harnesses/research.py + base.py](#314-harness-工厂--harnessesresearchpy--basepy)
   - [3.15 CLI 入口 — harnesses/cli.py](#315-cli-入口--harnessesclipy)
4. [数据流](#4-数据流)
5. [核心序列](#5-核心序列)
6. [配置与默认值](#6-配置与默认值)
7. [扩展指南](#7-扩展指南)
8. [测试策略](#8-测试策略)

---

## 1. 项目概述

**HarnessFactory** 是一个领域特定的 AI 代理框架，用于构建和运行 "harness"——围绕 LLM 预配置的交互循环，配备领域工具、系统提示和安全策略。

### 核心特性

| 特性 | 说明 |
|------|------|
| **多 Provider** | Anthropic、OpenAI、Ollama（OpenAI 兼容）无缝切换 |
| **插件式工具** | 文件读写、Web 搜索、代码搜索等，可自由组合 |
| **分层上下文** | 三级上下文管理（L1 内存 / L2 SQLite / L3 远程），自动压缩 |
| **安全策略链** | 可组合的策略链控制工具调用审批 |
| **错误恢复** | 指数退避重试 + 断路器 + 错误分类 |
| **检查点持久化** | 自动保存/恢复会话状态，支持崩溃恢复 |
| **三种管道模式** | 交互式、自动、流式，适应不同场景 |
| **事件驱动** | 基于 MessageBus 的解耦发布/订阅架构 |

### 项目结构

```
agentsFactory/
├── pyproject.toml              # 项目元数据 + 构建配置
├── harnesses/                  # 领域工厂 + CLI
│   ├── __init__.py             #   公共 API 导出
│   ├── __main__.py             #   python -m harnesses 入口
│   ├── cli.py                  #   Click CLI 定义
│   ├── provider.py             #   Provider 注册表 + 工厂
│   ├── research.py             #   ResearchHarness 工厂
│   └── base.py                 #   工厂共享工具函数
├── runtime/                    # 核心运行时
│   ├── __init__.py             #   公共 API 导出
│   ├── types.py                #   类型定义（数据类 + 枚举）
│   ├── message_bus.py          #   事件发布/订阅
│   ├── security.py             #   安全审批链
│   ├── llm_provider.py         #   LLM 提供者实现
│   ├── session.py              #   会话生命周期
│   ├── pipeline.py             #   管道引擎（交互式/自动）
│   ├── streaming.py            #   流式传输
│   ├── recovery.py             #   错误恢复（重试 + 断路器）
│   ├── checkpoint.py           #   检查点持久化
│   └── harness.py              #   HarnessRuntime 编排器
├── tool_registry/              # 工具定义 + 执行
│   ├── __init__.py             #   公共 API 导出
│   ├── base.py                 #   BaseTool 抽象 + ToolResult
│   ├── registry.py             #   ToolRegistry 中心注册表
│   ├── file_tools.py           #   文件工具（read/write/edit/glob/grep）
│   └── web_tools.py            #   Web 工具（search/fetch）
├── context_manager/            # 上下文窗口管理
│   ├── __init__.py             #   公共 API 导出
│   ├── types.py                #   上下文类型定义
│   ├── store.py                #   L2 SQLite + L3 分层存储
│   ├── compressor.py           #   压缩引擎（评分 + 策略 + 应用）
│   ├── dependency.py           #   依赖检测（文件路径/行号/关键词）
│   ├── restorer.py             #   内容恢复 + 补充上下文注入
│   └── manager.py              #   ContextManager 编排器
└── tests/                      # 测试套件
    ├── conftest.py
    ├── test_provider.py
    ├── test_harnesses.py
    ├── test_harness_cli.py
    ├── test_runtime_core.py
    ├── test_runtime_advanced.py
    ├── test_tool_registry.py
    └── test_context_manager.py
```

---

## 2. 架构总览

### 分层架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        领域工厂层 (Domain Factory)                     │
│  harnesses/cli.py → harnesses/provider.py → harnesses/research.py    │
│  提供者注册 + 工厂方法 + CLI 参数解析 + 领域特定 Harness 装配         │
├──────────────────────────────────────────────────────────────────────┤
│                         工具注册表层 (Tool Registry)                   │
│  tool_registry/registry.py → base.py → file_tools.py, web_tools.py  │
│  工具定义、注册、安全执行、Harness 集成安装                            │
├──────────────────────────────────────────────────────────────────────┤
│                       核心运行时层 (Runtime Core)                      │
│  runtime/harness.py → pipeline.py → session.py → llm_provider.py    │
│  工作流编排 → REPL 循环策略 → 会话生命周期 → LLM 推理调用              │
├──────────────────────────────────────────────────────────────────────┤
│                   横切支撑层 (Cross-cutting Support)                   │
│  runtime/message_bus.py  事件驱动通信                                 │
│  runtime/security.py     安全审批链                                    │
│  runtime/recovery.py     错误恢复（重试 + 断路器）                      │
│  runtime/checkpoint.py   检查点持久化                                   │
│  runtime/streaming.py    流式传输                                       │
├──────────────────────────────────────────────────────────────────────┤
│                      上下文管理层 (Context Management)                 │
│  context_manager/manager.py → compressor.py → store.py               │
│  → dependency.py → restorer.py                                       │
│  三级存储、自动压缩、依赖追踪、内容恢复                                  │
├──────────────────────────────────────────────────────────────────────┤
│                         类型定义层 (Types)                             │
│  runtime/types.py + context_manager/types.py                         │
│  所有数据类、枚举、协议的权威定义                                       │
└──────────────────────────────────────────────────────────────────────┘
```

### 依赖倒置原则

所有核心运行时组件依赖 **类型抽象**（Protocol / 数据类），而非具体实现：

| 抽象 | 实现 | 注入方式 |
|------|------|----------|
| `LLMProvider` (Protocol) | `AnthropicProvider` / `OpenAIProvider` / `MockProvider` | 工厂方法 |
| `Pipeline` (ABC) | `InteractivePipeline` / `AutoPipeline` / `StreamPipeline` | 策略选择 |
| `RemoteStore` (Protocol) | 可选的 L3 远程存储 | 构造参数 |
| `StreamProvider` (Protocol) | `MockStreamProvider` | 运行时检测 |

---

## 3. 分层详解

### 3.1 类型层 — `runtime/types.py`

**职责：** 运行时范围内所有枚举和数据类的权威定义，零外部依赖。

#### 枚举

| 枚举 | 值 | 用途 |
|------|-----|------|
| `SessionState` | CREATED, ACTIVE, PAUSED, CLOSED | 会话生命周期状态机 |
| `PipelinePhase` | IDLE → PARSING_INPUT → BEFORE_INFERENCE → INFERENCE → ... → CLOSED | 管道相位跟踪 |
| `PipelineEvent` | "session.started", "user.input", "before.inference", ... (15 个事件) | 消息总线事件类型 |

#### 核心数据类

```
InferenceConfig
├── model: str = "deepseek-v4-flash:cloud"          # 模型名称
├── max_tokens: int = 4096                           # 最大输出令牌
├── temperature: float = 0.7                         # 采样温度
├── stop_sequences: tuple[str, ...] = ()             # 停止序列
├── system_prompt: str = ""                          # 系统提示
├── thinking: bool = False                           # 扩展思考
└── thinking_budget: int | None = None               # 思考预算

InferenceResult
├── text: str = ""                                   # 文本输出
├── tool_calls: tuple[ToolCall, ...] = ()            # 工具调用
├── stop_reason: str = "end_turn"                    # 停止原因
├── usage: Usage                                     # 令牌使用
└── model: str = ""                                  # 实际使用模型

HarnessSpec (Harness 蓝图)
├── name: str                                         # 名称
├── version: str = "0.1.0"                            # 版本
├── description: str = ""                             # 描述
├── tools: tuple[ToolDefinition, ...] = ()            # 工具定义
├── system_prompt_template: str = ""                  # 系统提示模板
├── pipeline_strategy: str = "interactive"            # 管道策略
├── default_model: str = "deepseek-v4-flash:cloud"    # 默认模型
├── config: dict                                      # 扩展配置
└── metadata: dict                                    # 元数据

ToolDefinition (暴露给 LLM 的工具描述)
├── name: str
├── description: str
├── parameters: tuple[ToolParam, ...]
├── to_openai_tool() -> dict                          # OpenAI 格式
└── to_anthropic_tool() -> dict                       # Anthropic 格式

ToolCall (运行时工具调用包装器)
├── id: str                                           # 唯一 ID
├── tool_name: str                                    # 工具名
├── params: dict                                      # 参数
├── status: str = "pending"                           # pending/approved/denied/completed/failed
├── result: Any                                       # 执行结果
└── error: str | None                                 # 错误信息

StepResult (管道步骤结果)
├── text: str                                         # 输出文本
├── tool_calls: tuple[ToolCall, ...]
├── turn_complete: bool                               # 轮次完成标记
├── session_complete: bool                            # 会话完成标记
├── error: str | None
└── supplemental_context: str | None                  # 补充上下文
```

### 3.2 事件总线 — `runtime/message_bus.py`

**职责：** 轻量级进程内事件发布/订阅，解耦管道组件。

```python
class MessageBus:
    _subscribers: dict[PipelineEvent, list[EventHandler]]
    _history: list[EventPayload]
```

**核心方法：**

| 方法 | 签名 | 说明 |
|------|------|------|
| `on` | `(event: PipelineEvent, handler: EventHandler)` | 订阅事件 |
| `off` | `(event: PipelineEvent, handler: EventHandler)` | 取消订阅 |
| `emit` | `(event: PipelineEvent, phase: PipelinePhase, **data)` | 发出事件，单个 handler 异常不影响其他 handler |
| `history` | 属性 | 返回所有历史事件的元组 |
| `last_event` | `(event_type=None)` | 获取最近事件，可按类型过滤 |

**设计要点：**
- 同步广播（非队列），适合进程内场景
- 错误隔离：单个 handler 抛异常不影响其他 handler
- 历史记录可追踪，用于调试和检查点

### 3.3 安全门 — `runtime/security.py`

**职责：** 工具调用审批的策略链。

```python
PolicyFunc = Callable[[ToolCall], ApprovalResult | None]
# 返回 None = 不表态（传递给链中下一个策略）
```

#### 内置策略

| 策略 | 行为 |
|------|------|
| `allow_read_only` | 只读工具（read/list/grep/glob/search/fetch/view）→ ALLOW；可变工具（write/edit/delete/rm/create/exec/bash）→ DENY；其他 → 不表态 |
| `allow_path_prefix(prefixes)` | 工具参数中的路径以允许前缀开头 → ALLOW |
| `deny_commands(patterns)` | 参数匹配正则表达式 → DENY |

#### SecurityGate 审批流程

```
approve(tool_call):
  1. 遍历 _policies 链
  2. 第一个返回非 None 的 ApprovalResult 获胜
  3. 全部不表态 → 检查 require_approval_for + default_decision
  4. 策略异常 → 捕获并返回 DENY
```

### 3.4 LLM 提供者 — `runtime/llm_provider.py`

**职责：** LLM 推理抽象和三个实现。

#### 协议

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        config: InferenceConfig,
    ) -> InferenceResult: ...
```

#### 实现对比

| 实现 | SDK | 模型映射 | 特性 |
|------|-----|----------|------|
| `AnthropicProvider` | `anthropic.AsyncAnthropic` | `claude-sonnet-4-20250514` / `claude-opus-4-20250514` / `claude-haiku-4-20250514` + 短别名 | 工具、系统提示、停止序列、扩展思考 |
| `OpenAIProvider` | `openai.AsyncOpenAI` | `gpt-4o` / `gpt-4o-mini` / `o3-mini` + 自定义模型透传 | 自定义 `base_url`（Ollama 兼容）、工具、停止序列 |
| `MockProvider` | 无 | `"mock"` | 确定性响应列表、调用计数追踪 |

#### OpenAIProvider 构造

```python
def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
    self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
```

`base_url` 参数使其兼容 Ollama（`http://localhost:11434/v1`）等 OpenAI 兼容服务。

#### 关键设计决策

- Anthropic 格式与 OpenAI 格式的差异在 Provider 内部封装
- 工具定义转换由 `ToolDefinition.to_anthropic_tool()` / `to_openai_tool()` 处理
- 系统提示：Anthropic 用独立 `system` 参数，OpenAI 合并到消息列表
- `MODEL_MAP` 提供短别名 → 全名的映射，未知名称直接透传

### 3.5 会话管理 — `runtime/session.py`

**职责：** 会话生命周期 + 消息记录（集成 ContextManager）。

```python
class SessionConfig:
    session_id: str = ""
    total_budget: int = 64000
    context_manager: ContextManager | None = None
    store_path: str = ""
    metadata: dict = {}
```

#### 生命周期状态机

```
CREATED → start() → ACTIVE → pause() → PAUSED → resume() → ACTIVE
                                           PAUSED → close() → CLOSED
                      ACTIVE → close() → CLOSED
```

#### 消息记录

| 方法 | 消息类型 | 说明 |
|------|----------|------|
| `record_user_input(input)` | USER_INPUT | 创建消息 + 递增 turn_count + 发出 USER_INPUT 事件 |
| `record_assistant_text(text)` | MODEL_REPLY | 记录模型回复 |
| `record_tool_result(name, result, error)` | TOOL_RESULT | 记录工具结果，结果截断至 10000 字符 |

### 3.6 管道引擎 — `runtime/pipeline.py`

**职责：** REPL 循环的核心引擎。

```python
class PipelineConfig:
    max_tool_rounds: int = 25
    inference_config: InferenceConfig = field(default_factory=InferenceConfig)
    tool_as_messages: bool = True
```

#### Pipeline 抽象基类

```python
class Pipeline(ABC):
    _tool_defs: dict[str, ToolDefinition]     # 工具定义注册表
    _tool_executors: dict[str, ToolExecutorFn] # 工具执行器注册表
    _session: Session
    _llm: LLMProvider
    _bus: MessageBus
    _security: SecurityGate
```

#### 核心工具循环 `_tool_loop(result)`

```
1. 解析 result.tool_calls
2. 遍历每个 tool_call：
   a. SecurityGate.approve() → 拒绝则发出 TOOL_DENIED 事件
   b. ContextManager.record_tool_call() → 依赖检测
   c. 查找 executor → 不存在则记录错误
   d. 执行（支持 sync/async）
   e. Session.record_tool_result()
   f. 用工具结果重新调用 LLM
3. 重复直到无 tool_calls 或达到 max_tool_rounds
```

#### 管道策略（策略模式）

| 策略 | 类 | 行为 |
|------|-----|------|
| **交互式** | `InteractivePipeline` | Input → Inference → Tool Loop → Output。每次推理前调用 `safe_point_before_inference()` |
| **自动** | `AutoPipeline` | 自主循环直到模型产生纯文本回复（无 tool_calls）。最大 50 轮 |
| **流式** | `StreamPipeline` | （位于 streaming.py）实时逐 token 流式传输 |

### 3.7 流式传输 — `runtime/streaming.py`

**职责：** 实时逐 token 输出。

```python
class StreamEvent:
    type: StreamType  # TEXT / TOOL_CALL_BEGIN / TOOL_CALL_END / DONE / ERROR
    data: Any = None
    index: int = 0
```

| 组件 | 职责 |
|------|------|
| `StreamBuffer` | 事件累积 → `InferenceResult`。支持 `partial_text` 实时显示 |
| `StreamProvider` (Protocol) | 流式推理接口 `stream_complete() → AsyncIterator[StreamEvent]` |
| `MockStreamProvider` | 确定性测试模拟 |
| `StreamPipeline` | 流式管道策略。检测 LLM 是否实现 `StreamProvider`，是则使用 `_stream_turn()` |

### 3.8 错误恢复 — `runtime/recovery.py`

**职责：** 分层错误恢复机制。

#### 三层恢复架构

```
PipelineRecovery
├── ErrorClassifier     # 错误分类（RETRYABLE / FATAL / DEGRADE / UNKNOWN）
├── RetryPolicy         # 指数退避 + 随机抖动
└── CircuitBreaker      # 三态断路器（CLOSED → OPEN → HALF_OPEN）
```

#### ErrorClassifier

| 分类方式 | 优先级 | 说明 |
|----------|--------|------|
| 谓词函数 | 最高 | 自定义匹配逻辑 |
| 类型规则 | 中 | `ConnectionError` / `TimeoutError` → RETRYABLE |
| 字符串启发式 | 低 | "rate limit" / "unauthorized" → 相应分类 |

#### RetryPolicy

```
delay(attempt) = min(base_delay * multiplier^attempt, max_delay) + random_jitter
```

#### CircuitBreaker 状态机

```
CLOSED ──(threshold 次失败)──→ OPEN ──(reset_timeout 已过)──→ HALF_OPEN
  ↑                                                            │
  └──────────(half_open_max 次成功)───────────────────────────┘
```

### 3.9 检查点持久化 — `runtime/checkpoint.py`

**职责：** 会话检查点保存/恢复。

```python
class Checkpoint:
    version: int = CHECKPOINT_VERSION  # = 1
    session_id: str
    created_at: str
    turn_count: int
    messages: tuple[dict, ...]
    harness_spec: dict | None = None
    config: dict | None = None
    store_path: str = ""
    metadata: dict = {}
```

| 组件 | 职责 |
|------|------|
| `CheckpointStore` | 文件系统 JSON 持久化。目录布局：`{save_dir}/{session_id}/checkpoint_{timestamp}.json` + `meta.json` |
| `AutoSaveManager` | 绑定到 `PIPELINE_TURN` / `SESSION_CLOSED` 事件的自动保存。可配置保存间隔 |

### 3.10 运行时编排 — `runtime/harness.py`

**职责：** 核心编排器，组装所有组件。

```python
class HarnessRuntime:
    _spec: HarnessSpec
    _cfg: HarnessConfig
    _bus: MessageBus
    _security: SecurityGate
    _session: Session
    _llm: LLMProvider
    _pipeline: Pipeline
    _recovery: PipelineRecovery | None
    _checkpoint_store: CheckpointStore | None
    _auto_save: AutoSaveManager | None
    _closed: bool
```

#### 生命周期

```
__init__() → 初始化组件 → 选择管道策略 → 注册工具 → 绑定事件
    → start() → turn() → turn() → ... → close()
```

#### HarnessConfig

```python
class HarnessConfig:
    session_budget: int = 64000
    max_tool_rounds: int = 25
    security_default_decision: str = "allow"
    security_require_approval: tuple[str, ...] = ("bash", "exec")
    pipeline_strategy: str = "interactive"
    enable_auto_save: bool = False
    checkpoint_interval: int = 30
    metadata: dict = {}
```

#### turn() 方法流程

```
turn(user_input):
  1. [可选] recovery.call_with_recovery() 包装
  2. Session.record_user_input()
  3. Pipeline.turn() → 返回 StepResult
  4. 更新检查点 / 触发自动保存
  5. 返回 StepResult
```

#### restore() 类方法

从检查点重建运行时：
1. 加载最新检查点
2. 创建新运行时（禁用自动保存避免双重写入）
3. 使用存储的路径重建 ContextManager
4. 从检查点数据恢复消息

### 3.11 工具注册表 — `tool_registry/`

#### 分层设计

```
tool_registry/__init__.py    # 公共 API 导出
tool_registry/base.py        # BaseTool 抽象 + ToolResult
tool_registry/registry.py    # ToolRegistry 中心注册表
tool_registry/file_tools.py  # 5 个文件工具
tool_registry/web_tools.py   # 2 个 Web 工具
```

#### BaseTool 协议

```python
class BaseTool(ABC):
    name: str = ""
    description: str = ""
    parameters: tuple[ParamSpec, ...] = ()

    @property
    def definition() -> ToolDefinition        # 转换为运行时 ToolDefinition

    @abstractmethod
    async execute(**params) -> ToolResult     # 执行工具

    async safe_execute(**params) -> str       # 错误安全的字符串包装
```

#### ToolResult

| 方法 | 说明 |
|------|------|
| `ToolResult.ok(text, data)` | 成功结果 |
| `ToolResult.err(error)` | 错误结果 |
| `__bool__` | 返回 `success` 字段 |

#### 内置工具

| 工具 | 名称 | 参数 | 特性 |
|------|------|------|------|
| `ReadTool` | `read` | file_path, offset?, limit? | 行范围读取，编码检测（UTF-8 → latin-1） |
| `WriteTool` | `write` | file_path, content | 自动创建父目录，500KB 大小限制 |
| `EditTool` | `edit` | file_path, old_string, new_string | 首次匹配替换，报告更改次数 |
| `GlobTool` | `glob` | pattern, path?, max_results? | 递归 glob，上限 500 结果 |
| `GrepTool` | `grep` | pattern, path?, glob?, max_results?, context?, case_sensitive? | 正则搜索，二进制过滤，上下文行，上限 200 结果 |
| `WebSearchTool` | `web_search` | query, max_results?, engine? | HTTP 后端自动检测 |
| `WebFetchTool` | `web_fetch` | url, prompt?, max_bytes? | URL 方案验证，2MB 上限 |

**安全机制：** 所有文件工具通过 `_safe_path()` 防止路径遍历攻击。

#### ToolRegistry 集成

```python
registry = ToolRegistry()
registry.register(ReadTool(allowed_roots=("/safe/path",)))
registry.install_all(harness)
# 或选择性安装：
registry.install(harness, names=["read", "grep"])
```

`install_all()` 使用 `BaseTool.safe_execute` 作为执行器注册到 `HarnessRuntime` 管道。

### 3.12 上下文管理器 — `context_manager/`

#### 三级存储架构

```
L1 (内存)
├── 活动消息列表（正在使用的上下文）
├── 支持预算检查（warn 65% / compress 80% / critical 92% / hard_limit 98%）
└── 压缩后，部分消息降级到 L2

L2 (SQLite)
├── 表：compressed_records + anchors
├── WAL 模式 + NORMAL 同步
└── 按 session_id + anchor_id 索引

L3 (远程，可选)
├── RemoteStore Protocol
└── 写入时扇出（L2 + L3），读取时优先 L3
```

#### ContextManager 架构

```
context_manager/manager.py      # ContextManager 编排器
context_manager/types.py        # 上下文类型定义
context_manager/store.py        # L2 SQLite + L3 存储
context_manager/compressor.py   # 压缩引擎
context_manager/dependency.py   # 依赖检测
context_manager/restorer.py     # 内容恢复
```

#### 压缩引擎

**压缩级别：**

| 级别 | 策略 | 说明 |
|------|------|------|
| LEVEL_0_PRESERVE | 保留 | 不处理 |
| LEVEL_1_TRUNCATE | 截断 | 保留 N 行/N 字符，添加 `[anchor: ...]` 前缀 |
| LEVEL_2_SUMMARIZE | 摘要 | `[Compressed: TYPE, Nt, M lines]` 标签替换 |
| LEVEL_3_DISCARD | 丢弃 | 从 L1 完全移除 |
| LEVEL_4_STRUCTURED | 结构化 | 保留结构化格式 |

**可压缩性评分因子：**

| 因子 | 权重 | 说明 |
|------|------|------|
| age | 0.25 | 时间衰减，标准化至 1 小时 |
| size | 0.30 | 相对最大消息的大小 |
| type_bonus | 0.25 | 类型奖金（日志 0.9 / USER_INPUT 0.1） |
| tool_result_size | 0.20 | 工具结果大小奖金，标准化至 2000 token |

**预算触发器阈值：**

| 触发器 | 比率 | 行为 |
|--------|------|------|
| BUDGET_WARN | 65% | 启动异步预压缩 |
| BUDGET_COMPRESS | 80% | 执行压缩，目标回退到 65% |
| BUDGET_CRITICAL | 92% | 强制压缩，目标回退到 80% |
| HARD_LIMIT | 98% | 紧急压缩 |

#### 依赖检测与恢复

工具调用后，`DependencyDetector` 沿三个维度检查是否引用了已压缩内容：

1. **文件路径匹配** — 检查工具参数中的路径是否匹配已压缩内容的 `source_path`
2. **行号匹配** — 检查参数中的行号引用
3. **关键术语匹配** — 将参数与锚点元数据中的 `key_terms` 匹配

匹配触发 `Restorer` 从 L2/L3 恢复内容，作为 `<supplemental_context>` XML 块注入提示。

#### ContextManager 生命周期

```
add_message(msg)
  → 追加到 L1
  → _check_budget()
      → BUDGET_WARN: 提交异步预压缩计划
      → BUDGET_COMPRESS: 同步压缩，目标 → warn_ratio
      → BUDGET_CRITICAL: 强制压缩，目标 → compress_ratio
      → HARD_LIMIT: 紧急压缩

safe_point_before_inference()
  → 收集异步压缩计划
  → 返回挂起的补充上下文

safe_point_after_tool(tool_call)
  → DependencyDetector.detect() → 匹配活动锚点
  → Restorer.restore() → 恢复内容
  → 队列补充片段到 pending_supplemental

build_prompt()
  → 如果 pending_supplemental 非空:
      → 在最新用户消息前注入 system 角色消息
```

### 3.13 提供者工厂 — `harnesses/provider.py`

**职责：** Provider 注册表 + 工厂方法。

```python
@dataclass(frozen=True)
class ProviderInfo:
    name: str             # 提供者名称
    description: str      # 描述
    env_key: str          # 环境变量名
    model_default: str    # 默认模型

_providers: dict[str, tuple[ProviderInfo, Callable[[str], Any] | None]]
```

#### 内置 Provider

| 名称 | 环境变量 | 默认模型 | 工厂实现 |
|------|----------|----------|----------|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` | `AnthropicProvider(api_key=key)` |
| `openai` | `OPENAI_API_KEY` | `gpt-4o` | `OpenAIProvider(api_key=key)` |
| `ollama` | `OLLAMA_API_KEY` | `deepseek-v4-flash:cloud` | `OpenAIProvider(api_key=key or "ollama", base_url="http://localhost:11434/v1")` |

#### create_provider 流程

```
create_provider(name, api_key=None):
  1. 查找 _providers[name]
  2. 不存在 → raise ValueError("Unknown provider...")
  3. 解析 API key: api_key 参数 > 环境变量
  4. 无 key → raise ValueError("...API key...")
  5. 调用工厂函数 → 返回 LLMProvider
```

### 3.14 Harness 工厂 — `harnesses/research.py` + `base.py`

**职责：** 创建预配置的 `HarnessRuntime` 实例。

#### create_research_harness 流程

```
1. 创建 ToolRegistry
2. ensure_dir(research_root) → 解析并创建目录
3. 注册 6 个工具：
   - ReadTool(allowed_roots=[root])
   - WriteTool(allowed_roots=[root])
   - GlobTool(allowed_roots=[root])
   - GrepTool(allowed_roots=[root])
   - WebSearchTool()
   - WebFetchTool()
4. 构建 HarnessSpec：
   - name="research-harness", version="0.1.0"
   - tools=registry.definitions()
   - system_prompt_template=RESEARCH_PROMPT
   - pipeline_strategy="interactive"
   - default_model=model
5. 构建 HarnessConfig：
   - security_default_decision="ask"
   - security_require_approval=("write", "edit")
6. 创建 HarnessRuntime(spec, llm, config, ...)
7. registry.install_all(harness) → 安装真实工具执行器
8. 添加 read_only_path_policy 安全策略
```

#### 系统提示词结构

包含四部分：
1. **能力清单** — 列出所有可用工具及其用途
2. **工作指南** — 研究流程规范（先搜索 → 结构化摘要 → 追踪引用）
3. **输出风格** — Markdown 格式，TL;DR / 方法论 / 关键发现 / 局限性 / 链接

#### base.py 共享工具

| 函数 | 说明 |
|------|------|
| `install_file_tools(registry, allowed_roots)` | 批量安装文件工具 |
| `install_web_tools(registry)` | 批量安装 Web 工具 |
| `read_only_path_policy(allowed_roots)` | 生成只读安全策略元组 |
| `ensure_dir(path)` | 解析并创建目录 |

### 3.15 CLI 入口 — `harnesses/cli.py`

**职责：** Click 命令行接口。

#### CLI 选项

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `query` (参数) | 必填（单次模式） | 用户查询 |
| `--provider` | `ollama` | LLM 提供者 |
| `--api-key` | 无 | API 密钥覆盖 |
| `--model` | 无 | 模型名称覆盖 |
| `--research-root` | `./research` | 工作区目录 |
| `--repl` | False | 交互式 REPL 模式 |
| `--mock` | False | 使用 MockProvider |

#### 执行流程

```
research 命令:
  1. --mock 模式 → MockProvider
  2. 否则 → create_provider(provider, api_key)
  3. create_research_harness(llm, model, research_root)
  4. harness.start()
  5. REPL 模式 → _run_repl() 循环
  6. 单次模式 → harness.turn(UserInput(query))
  7. harness.close()
```

---

## 4. 数据流

### 完整请求流

```
用户输入 "Find papers on AI"
    │
    ▼
CLI (harnesses/cli.py)
    │ 解析参数 → create_provider("ollama") → OpenAIProvider(base_url="http://localhost:11434/v1")
    │            → create_research_harness(llm, model)
    │
    ▼
HarnessRuntime.turn(UserInput)
    │
    ├─ Session.record_user_input()
    │   ├─ ContextManager.add_message() → L1 追加 + 预算检查
    │   └─ MessageBus.emit(USER_INPUT)
    │
    ├─ [Recovery] 可选 recovery.call_with_recovery()
    │
    ├─ Pipeline.turn()
    │   ├─ Session 构建消息列表
    │   ├─ safe_point_before_inference() → 收集异步压缩 + 补充上下文
    │   ├─ LLMProvider.complete(messages, tools, config)
    │   │   └─ POST /v1/chat/completions → 模型推理
    │   ├─ Session.record_assistant_text()
    │   │
    │   └─ _tool_loop(result) [循环直到无 tool_calls]
    │       │
    │       ├─ SecurityGate.approve(tool_call)
    │       │   ├─ allow_read_only → ALLOW/DENY
    │       │   └─ allow_path_prefix → ALLOW/DENY
    │       │
    │       ├─ ContextManager.record_tool_call()
    │       │   └─ DependencyDetector.detect() → 恢复补充上下文
    │       │
    │       ├─ Executor(**params)
    │       │   ├─ ReadTool / WriteTool / ...
    │       │   └─ 返回 ToolResult
    │       │
    │       ├─ Session.record_tool_result()
    │       └─ LLMProvider.complete() [重新推理]
    │
    ├─ [AutoSave] 事件触发自动保存
    │
    └─ 返回 StepResult
```

---

## 5. 核心序列

### 5.1 启动序列

```
CLI                    HarnessRuntime           Pipeline            Session         ContextManager
 │                          │                      │                   │                  │
 │──create_research_harness→│                      │                   │                  │
 │                          │──_build_pipeline()──→│                   │                  │
 │                          │                      │                   │                  │
 │──start()────────────────→│                      │                   │                  │
 │                          │──session.start()─────│──────────────────→│                  │
 │                          │                      │                   │──add_message()──→│
 │                          │                      │                   │──emit()          │
 │                          │                      │                   │                  │
 │◄──ready──────────────────│                      │                   │                  │
```

### 5.2 工具执行序列（含安全审批）

```
Pipeline                    SecurityGate           Executor         Session       ContextManager
    │                            │                    │                │               │
    │──_tool_loop(result)        │                    │                │               │
    │                            │                    │                │               │
    │──approve(tool_call)───────→│                    │                │               │
    │◄──ApprovalResult───────────│                    │                │               │
    │                            │                    │                │               │
    │──record_tool_call()───────│────────────────────│───────────────→│               │
    │                            │                    │                │──detect()────→│
    │                            │                    │                │               │
    │──executor(**params)───────│────────────────────→│                │               │
    │                            │                    │──execute()     │               │
    │◄──ToolResult──────────────│────────────────────←│                │               │
    │                            │                    │                │               │
    │──record_tool_result()─────│────────────────────│───────────────→│               │
    │                            │                    │                │               │
    │──LLM.complete() [重试]     │                    │                │               │
```

### 5.3 上下文压缩序列

```
ContextManager                  Compressor              Store
    │                               │                     │
    │──add_message()                │                     │
    │──_check_budget()              │                     │
    │   (ratio > 0.80)              │                     │
    │──_compress()─────────────────→│                     │
    │                               │──score(messages)    │
    │                               │──create_plan()      │
    │                               │──apply_plan()       │
    │◄──ApplyResult────────────────←│                     │
    │                               │                     │
    │──persist records/anchors─────│────────────────────→│
    │                               │                     │──SQLite INSERT
    │──替换 L1 消息                  │                     │
```

---

## 6. 配置与默认值

### 6.1 编译时默认值

| 位置 | 键 | 默认值 |
|------|-----|--------|
| `types.py:InferenceConfig` | model | `deepseek-v4-flash:cloud` |
| | max_tokens | 4096 |
| | temperature | 0.7 |
| `types.py:HarnessSpec` | default_model | `deepseek-v4-flash:cloud` |
| | pipeline_strategy | `interactive` |
| `harness.py:HarnessConfig` | session_budget | 64000 |
| | max_tool_rounds | 25 |
| | security_default_decision | `allow` |
| `pipeline.py:PipelineConfig` | max_tool_rounds | 25 |
| `cli.py:CLI` | --provider | `ollama` |
| | --research-root | `./research` |
| `provider.py:ProviderInfo` | ollama model_default | `deepseek-v4-flash:cloud` |
| | anthropic model_default | `claude-sonnet-4-20250514` |
| | openai model_default | `gpt-4o` |
| `session.py:SessionConfig` | total_budget | 64000 |
| `context_manager:ManagerConfig` | total_budget | 64000 |
| | warn_ratio | 0.65 |
| | compress_ratio | 0.80 |
| | critical_ratio | 0.92 |
| `checkpoint.py:CheckpointConfig` | auto_save_interval | 30 (秒) |
| | max_checkpoints_per_session | 10 |

### 6.2 环境变量

| 变量 | 用途 |
|------|------|
| `ANTHROPIC_API_KEY` | Anthropic Provider |
| `OPENAI_API_KEY` | OpenAI Provider |
| `OLLAMA_API_KEY` | Ollama Provider（Ollama 本地运行时忽略） |

### 6.3 优先级

**API 密钥：** `--api-key` 参数 > 环境变量

**模型选择：** `--model` CLI 参数 > ProviderInfo.model_default > InferenceConfig 默认

---

## 7. 扩展指南

### 7.1 添加新 Provider

```python
from harnesses.provider import register_provider, ProviderInfo
from runtime.llm_provider import OpenAIProvider

register_provider(
    ProviderInfo(
        name="my_provider",
        description="My custom LLM provider",
        env_key="MY_PROVIDER_API_KEY",
        model_default="my-default-model",
    ),
    lambda key: OpenAIProvider(api_key=key, base_url="http://localhost:8080/v1"),
)

# 使用
llm = create_provider("my_provider", api_key="sk-...")
```

如果要实现全新 Provider（非 OpenAI 兼容），在 `runtime/llm_provider.py` 中实现 `LLMProvider` 协议：

```python
class MyCustomProvider:
    MODEL_MAP = {"my-model": "my-model-v1"}

    def __init__(self, api_key: str) -> None:
        self._client = MySDK(api_key=api_key)

    async def complete(
        self, messages: list[dict], tools: list[dict], config: InferenceConfig
    ) -> InferenceResult:
        # 实现推理逻辑
        ...
```

### 7.2 添加新 Harness

```python
# harnesses/my_harness.py
from runtime.harness import HarnessConfig, HarnessRuntime
from runtime.types import HarnessSpec
from tool_registry import ToolRegistry

def create_coding_harness(
    llm=None,
    model: str = "deepseek-v4-flash:cloud",
) -> HarnessRuntime:
    registry = ToolRegistry()
    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(GrepTool())

    spec = HarnessSpec(
        name="coding-harness",
        version="0.1.0",
        description="Code assistant with file tools",
        tools=tuple(registry.definitions()),
        system_prompt_template="You are a coding assistant...",
        default_model=model,
    )

    config = HarnessConfig(
        security_default_decision="ask",
        security_require_approval=("write", "edit"),
        pipeline_strategy="interactive",
    )

    harness = HarnessRuntime(spec=spec, llm=llm, config=config)
    registry.install_all(harness)
    return harness
```

### 7.3 添加新工具

```python
from tool_registry.base import BaseTool, ToolResult, ParamSpec

class WeatherTool(BaseTool):
    name = "get_weather"
    description = "Get current weather for a city"
    parameters = (
        ParamSpec(name="city", type="string", description="City name", required=True),
    )

    async def execute(self, city: str, **kwargs) -> ToolResult:
        try:
            # 调用天气 API
            result = f"Weather in {city}: 22°C, sunny"
            return ToolResult.ok(result)
        except Exception as e:
            return ToolResult.err(str(e))
```

### 7.4 添加新安全策略

```python
from runtime.security import ApprovalResult, Decision

def deny_high_cost_operations(tool_call):
    """拒绝高成本操作"""
    if tool_call.tool_name == "web_search" and tool_call.params.get("max_results", 0) > 100:
        return ApprovalResult(
            decision=Decision.DENY,
            reason="Too many search results would be expensive",
            policy_name="deny_high_cost",
        )
    return None  # 不表态

# 使用
harness.add_security_policy(deny_high_cost_operations)
```

---

## 8. 测试策略

### 测试文件与覆盖范围

| 测试文件 | 覆盖范围 | 测试数 |
|----------|----------|--------|
| `test_provider.py` | 提供者注册表、工厂、API 密钥解析、边界条件 | ~12 |
| `test_harnesses.py` | ResearchHarness 集成：创建、工具、安全、边缘情况 | ~10 |
| `test_harness_cli.py` | CLI 参数解析、REPL、错误处理、模块执行 | ~10 |
| `test_runtime_core.py` | 类型、MessageBus、Session、Security、MockProvider | ~30 |
| `test_runtime_advanced.py` | Pipeline、Streaming、Recovery、Checkpoint、HarnessRuntime | ~40 |
| `test_tool_registry.py` | 所有工具、注册表、集成端到端 | ~25 |
| `test_context_manager.py` | 所有上下文组件、完整生命周期、集成 | ~40 |

### 测试模式

1. **单元测试** — 测试单个类/函数，Mock 外部依赖
2. **集成测试** — 测试组件间交互（如 Pipeline + Session + Security）
3. **端到端测试** — MockProvider 模拟完整请求流
4. **边界测试** — 空输入、大文件、关闭后调用、双重关闭、未知 provider

### 运行测试

```bash
# 运行全部测试
python -m pytest tests/

# 运行单个测试文件
python -m pytest tests/test_provider.py -v

# 运行特定测试类
python -m pytest tests/test_harnesses.py::TestResearchHarness -v

# 带覆盖率
python -m pytest tests/ --cov=harnesses --cov=runtime --cov=tool_registry --cov=context_manager
```
