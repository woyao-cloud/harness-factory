# HarnessFactory 使用指南

## 目录

1. [概述](#1-概述)
2. [快速开始](#2-快速开始)
3. [架构总览](#3-架构总览)
4. [HarnessRuntime — 核心编排器](#4-harnessruntime--核心编排器)
5. [Harness 工厂](#5-harness-工厂)
6. [ToolRegistry — 工具注册与执行](#6-toolregistry--工具注册与执行)
7. [SecurityGate — 安全策略链](#7-securitygate--安全策略链)
8. [Pipeline — REPL 循环策略](#8-pipeline--repl-循环策略)
9. [LLM Provider — 模型抽象层](#9-llm-provider--模型抽象层)
10. [ContextManager — 上下文生命周期](#10-contextmanager--上下文生命周期)
11. [错误恢复与熔断](#11-错误恢复与熔断)
12. [流式输出](#12-流式输出)
13. [检查点与持久化](#13-检查点与持久化)
14. [自定义 Harness](#14-自定义-harness)
15. [API 速查表](#15-api-速查表)

---

## 1. 概述

**HarnessFactory** 是一个元架构框架，将 Claude Code 的内部模式抽象为可复用的构建块。核心思想是：**不同领域的 AI 助手（ResearchHarness、DataHarness、CodeHarness 等）共享相同的底层运行时，只是装配了不同的工具、提示词和安全策略**。

### 核心特性

| 特性 | 说明 |
|------|------|
| 即用型 Harness | `create_research_harness()` 等工厂函数一键创建完整运行时 |
| 工具注册中心 | 统一的 `ToolRegistry`，支持安装到任意 Harness |
| 安全策略链 | 基于策略链的细粒度权限控制 (ALLOW / DENY / ASK) |
| 多种 Pipeline | 交互式、自动、流式三种 REPL 策略 |
| 模型无关 | 支持 Anthropic Claude、OpenAI GPT，以及 Mock 测试 |
| 上下文管理 | 自动压缩、依赖检测、补充上下文的完整上下文生命周期 |
| 错误恢复 | 指数退避重试 + 熔断器 |
| 检查点 | 会话持久化与崩溃恢复 |

---

## 2. 快速开始

### 安装依赖

```bash
pip install anthropic  # Claude 模型
# 或
pip install openai     # GPT 模型
```

### 使用 ResearchHarness

```python
import asyncio
from harnesses import create_research_harness
from runtime.types import UserInput

async def main():
    harness = create_research_harness(
        research_root="./my_papers",
        model="claude-sonnet-4-20250514",
    )
    harness.start()

    result = await harness.turn(UserInput(
        text="Find recent papers on LLM agent architectures"
    ))
    print(result.text)

    harness.close()

asyncio.run(main())
```

### 指定 LLM Provider

```python
from runtime.llm_provider import AnthropicProvider
from harnesses import create_research_harness

harness = create_research_harness(
    llm=AnthropicProvider(api_key="sk-..."),
    model="claude-sonnet-4-20250514",
)
```

### 运行测试

```bash
python -m pytest tests/ -v
```

---

## 3. 架构总览

```
┌─────────────────────────────────────────────────────┐
│                     HarnessRuntime                    │
│  ┌───────────┐ ┌─────────┐ ┌──────────────────────┐ │
│  │  Session   │ │ Pipeline │ │    SecurityGate     │ │
│  │  (会话)    │ │ (REPL)  │ │    (安全策略链)      │ │
│  └─────┬─────┘ └────┬────┘ └──────────┬───────────┘ │
│        │            │                 │              │
│  ┌─────┴────────────┴─────────────────┴───────────┐  │
│  │              ToolRegistry (工具中心)            │  │
│  │  Read / Write / Glob / Grep / WebSearch / ...  │  │
│  └────────────────────────────────────────────────┘  │
│        │                                              │
│  ┌─────┴────────────────────────────────────────┐     │
│  │           ContextManager                      │     │
│  │  L1: 活跃消息 → L2: 压缩记录 → L3: 存储      │     │
│  └──────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────┘
```

### 组件职责

| 组件 | 职责 |
|------|------|
| `HarnessRuntime` | 主编排器，组装所有子组件并管理生命周期 |
| `Session` | 会话状态管理，消息记录，turn 计数 |
| `Pipeline` | REPL 循环实现（交互式/自动/流式） |
| `SecurityGate` | 工具调用审批策略链 |
| `ToolRegistry` | 工具注册、发现、执行 |
| `ContextManager` | 上下文压缩、依赖检测、补充注入 |
| `MessageBus` | 事件发布/订阅，解耦管道与工具 |

### 数据流

```
UserInput → Session 记录 → Pipeline.turn()
  → ContextManager.safe_point() [可能触发压缩]
  → LLM.complete() [调用模型]
  → 检测 ToolCall → SecurityGate.approve()
  → ToolRegistry.execute() [执行工具]
  → 循环直到模型停止调用工具
  → 返回 StepResult
```

---

## 4. HarnessRuntime — 核心编排器

`HarnessRuntime` 是框架的入口点，负责组装所有子组件。

### 生命周期

```python
harness = HarnessRuntime(spec, llm=llm)  # 创建
harness.start()                           # 启动会话
result = await harness.turn(user_input)   # 处理输入
result = await harness.run(user_input)    # turn 的同义词
harness.close()                           # 关闭会话
```

### 创建方式

**方式一：使用工厂函数（推荐）**

```python
from harnesses import create_research_harness

harness = create_research_harness(research_root="./papers")
```

**方式二：直接构造**

```python
from runtime import HarnessRuntime, HarnessConfig
from runtime.types import HarnessSpec

spec = HarnessSpec(
    name="my-harness",
    description="Custom assistant",
    tools=(tool_def_1, tool_def_2),
    system_prompt_template="You are a helpful assistant.",
)

harness = HarnessRuntime(
    spec=spec,
    llm=my_llm_provider,
    config=HarnessConfig(max_tool_rounds=50),
)
```

### HarnessConfig

```python
@dataclass(frozen=True)
class HarnessConfig:
    session_budget: int = 64000           # 会话 token 预算
    max_tool_rounds: int = 25             # 每轮最大工具调用次数
    security_default_decision: str = "allow"  # 安全默认决策
    security_require_approval: tuple = ("bash", "exec")  # 需审批的工具
    pipeline_strategy: str = "interactive"    # interactive / auto / stream
    enable_auto_save: bool = False            # 自动检查点
    checkpoint_interval: int = 30             # 检查点间隔(秒)
```

### 事件订阅

```python
from runtime.types import PipelineEvent

def on_error(payload):
    print(f"Error: {payload.data}")

harness.on(PipelineEvent.ERROR, on_error)
harness.off(PipelineEvent.ERROR, on_error)  # 取消订阅
```

---

## 5. Harness 工厂

工厂函数是创建预配置 Harness 的推荐方式。每个工厂封装了特定领域的工具选择、系统提示词、安全策略和管道配置。

### ResearchHarness

```python
from harnesses import create_research_harness

harness = create_research_harness(
    research_root="./research",           # 论文工作目录
    llm=AnthropicProvider(),              # LLM 提供者
    model="claude-sonnet-4-20250514",     # 模型名称
    recovery=PipelineRecovery(),          # 错误恢复（可选）
    checkpoint_store=CheckpointStore(),   # 检查点（可选）
    max_tool_rounds=25,                   # 最大工具调用轮数
    enable_auto_save=False,               # 自动保存
)
```

#### 注册的工具

| 工具 | 用途 | 安全策略 |
|------|------|---------|
| `read` | 读取本地论文文件 | ALLOW |
| `write` | 保存笔记和摘要 | ASK（需确认） |
| `glob` | 查找论文文件 | ALLOW |
| `grep` | 在论文中搜索关键词 | ALLOW |
| `web_search` | 搜索学术论文 | ALLOW |
| `web_fetch` | 获取论文摘要/全文 | ALLOW |

#### 系统提示词

ResearchHarness 内置了结构化摘要提示词，指导 LLM 按以下格式输出论文分析：

```markdown
## Paper Title (Year)

**TL;DR:** One-sentence summary.

**Methodology:** Key approach and techniques.

**Key Findings:** Bullet-point list of main results.

**Limitations:** Methodological concerns or scope constraints.

**Links:** [URLs or file paths]
```

### 创建更多 Harness（后续）

按相同模式添加新的工厂函数：

```python
def create_code_harness(...) -> HarnessRuntime: ...
def create_data_harness(...) -> HarnessRuntime: ...
```

---

## 6. ToolRegistry — 工具注册与执行

`ToolRegistry` 是工具的中心注册中心。每个工具是 `BaseTool` 的子类。

### 内置工具

| 工具 | 类 | 参数 | 说明 |
|------|-----|------|------|
| Read | `ReadTool` | `file_path`, `offset?`, `limit?` | 读取文件，支持行范围 |
| Write | `WriteTool` | `file_path`, `content` | 写入文件，有大小限制 |
| Edit | `EditTool` | `file_path`, `old_string`, `new_string` | 编辑文件，替换文本 |
| Glob | `GlobTool` | `pattern`, `path?`, `max_results?` | 按 glob 模式搜索文件 |
| Grep | `GrepTool` | `pattern`, `path?`, `glob?`, `context?`, `case_sensitive?` | 按正则搜索文件内容 |
| WebSearch | `WebSearchTool` | `query`, `max_results?` | 网络搜索 |
| WebFetch | `WebFetchTool` | `url`, `prompt?` | 抓取网页内容 |

### 基本用法

```python
from tool_registry import ToolRegistry
from tool_registry.file_tools import ReadTool, GlobTool

registry = ToolRegistry()
registry.register(ReadTool(allowed_roots=("./workdir",)))
registry.register(GlobTool(allowed_roots=("./workdir",)))

# 异步执行
result = await registry.execute("read", file_path="./workdir/doc.txt")
if result:
    print(result.text)
else:
    print(f"Error: {result.error}")

# 安全执行（返回字符串，异常安全）
text = await registry.safe_execute("read", file_path="./workdir/doc.txt")
```

### 安装到 Harness

```python
# 安装所有工具
registry.install_all(harness)

# 或选择性安装
registry.install(harness, names=["read", "grep"])
```

`install_all()` 会为每个工具注册两样东西：
1. **ToolDefinition** — 工具描述（供 LLM 函数调用使用）
2. **executor** — 实际执行函数（`tool.safe_execute`，自动异常处理）

### 自定义工具

```python
from tool_registry import BaseTool, ParamSpec, ToolResult

class MyCustomTool(BaseTool):
    name = "my_tool"
    description = "Does something useful"
    parameters = (
        ParamSpec("input", description="The input value"),
        ParamSpec("option", description="An option", required=False),
    )

    async def execute(self, input: str, option: str | None = None) -> ToolResult:
        try:
            result = do_something(input, option)
            return ToolResult.ok(text=f"Result: {result}", data=result)
        except Exception as e:
            return ToolResult.err(str(e))

registry.register(MyCustomTool())
```

---

## 7. SecurityGate — 安全策略链

`SecurityGate` 使用策略链模式评估每个工具调用。链中第一个返回非 `None` 的策略决定最终结果。如果所有策略都返回 `None`，使用默认决策。

### 决策类型

```python
from runtime.security import Decision

Decision.ALLOW  # 允许执行
Decision.DENY   # 拒绝执行
Decision.ASK    # 需要用户确认
```

### 内置策略

```python
from runtime.security import allow_read_only, allow_path_prefix, deny_commands

# 1. 读写分离：只读工具 ALLOW，变更工具 DENY
gate.add_policy(allow_read_only)

# 2. 路径白名单：只允许特定目录下的文件操作
gate.add_policy(allow_path_prefix(("/safe/dir",)))

# 3. 命令黑名单：拒绝匹配正则模式的操作
gate.add_policy(deny_commands(("rm -rf", "drop table")))
```

### 策略执行顺序

```python
gate = SecurityGate(config=SecurityConfig(default_decision=Decision.ASK))

# 策略按添加顺序执行
gate.add_policy(policy_a)  # 第一优先级
gate.add_policy(policy_b)  # 仅在 policy_a 返回 None 时执行
gate.add_policy(policy_c)  # 仅当前面都返回 None 时执行

# 所有策略都返回 None → 使用 SecurityConfig 的默认决策
# 工具名称在 require_approval_for 中 → ASK
# 否则 → ALLOW
```

### 审计日志

```python
result = gate.approve(tool_call)
print(result.decision, result.reason, result.policy_name)

# 查看所有审批记录
for entry in gate.audit_log:
    print(f"{entry.policy_name}: {entry.decision} - {entry.reason}")
```

---

## 8. Pipeline — REPL 循环策略

Pipeline 实现"输入 → LLM 推理 → 工具执行 → 推理继续"的主循环。

### 三种策略

```python
from runtime.pipeline import InteractivePipeline, AutoPipeline
from runtime.streaming import StreamPipeline
```

| 策略 | 类 | 行为 |
|------|-----|------|
| `interactive` | `InteractivePipeline` | 每次 turn 等待用户输入，适合对话场景 |
| `auto` | `AutoPipeline` | 自动执行直到产生纯文本回复，适合自主任务 |
| `stream` | `StreamPipeline` | 与 interactive 相同，但令牌实时推送 |

### InteractivePipeline（默认）

标准 REPL：用户输入 → 推理 → 工具循环 → 输出。

```python
result = await pipeline.turn(UserInput(text="What is X?"))
# StepResult(text="...", turn_complete=True)
```

### AutoPipeline

自主模式：用户提供一个目标，模型自动循环直到完成。

```python
pipe = AutoPipeline(config, session, llm, bus, security)
result = await pipe.turn(UserInput(text="Research topic X and save summary"))
# session_complete = True 当模型停止调用工具
```

### StreamPipeline

实时流式输出，适用于需要逐令牌显示的 UI。

```python
from runtime.streaming import MockStreamProvider, StreamPipeline

# 收集流事件
events = []
def on_event(event):
    events.append(event)

pipe = StreamPipeline(config, session, llm, bus, security)
pipe.on(PipelineEvent.PIPELINE_STEP, on_event)

result = await pipe.turn(UserInput(text="Explain X"))
# events 包含 TEXT、TOOL_CALL_BEGIN、TOOL_CALL_END、DONE
```

---

## 9. LLM Provider — 模型抽象层

```python
from runtime.llm_provider import (
    AnthropicProvider,   # Claude
    OpenAIProvider,      # GPT
    MockProvider,        # 测试用
)
```

### AnthropicProvider

```python
from runtime.llm_provider import AnthropicProvider

llm = AnthropicProvider(api_key="sk-ant-...")
# 或从环境变量 ANTHROPIC_API_KEY 读取
llm = AnthropicProvider()
```

支持的模型别名：

| 别名 | 实际 ID |
|------|---------|
| `claude-sonnet-4` | `claude-sonnet-4-20250514` |
| `claude-opus-4` | `claude-opus-4-20250514` |
| `claude-haiku-4` | `claude-haiku-4-20250514` |

### MockProvider

测试时使用，返回可预测的响应：

```python
from runtime.llm_provider import MockProvider
from runtime.types import InferenceResult, ToolCall

mock = MockProvider()

# 自定义响应序列
mock.add_response(InferenceResult(
    text="I'll search for that.",
    tool_calls=(ToolCall(tool_name="web_search", params={"query": "latest AI papers"}),),
))
mock.add_response(InferenceResult(
    text="Here are the results...",
))

# 默认行为：回显最后一条消息
result = await mock.complete(
    messages=[{"role": "user", "content": "hello"}],
    tools=[],
    config=InferenceConfig(),
)
# result.text == "[mock] received: hello"
```

### 自定义 Provider

实现 `LLMProvider` 协议：

```python
from runtime.llm_provider import LLMProvider
from runtime.types import InferenceConfig, InferenceResult

class MyProvider:
    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        config: InferenceConfig,
    ) -> InferenceResult:
        # 调用自定义模型
        ...
        return InferenceResult(text=response_text)
```

由于使用 `Protocol`，不需要继承——只需实现接口。

---

## 10. ContextManager — 上下文生命周期

`ContextManager` 管理三层上下文存储，自动压缩历史消息，并在需要时恢复。

### 三层架构

```
L1 (活跃消息) → 当前推理循环中的工作集，保存在内存
L2 (压缩记录) → 被压缩的历史消息，保存在 SQLite
L3 (文件仓库) → 原始文件内容引用
```

### 配置

```python
from context_manager import ContextManager, ManagerConfig

cm = ContextManager(
    ManagerConfig(
        total_budget=64000,      # L1 token 预算
        session_id="sess_001",   # 会话 ID
        warn_ratio=0.65,         # 65% 发出警告
        compress_ratio=0.80,     # 80% 触发压缩
        hard_limit_ratio=0.98,   # 98% 硬限制
        async_pre_compress=True, # 异步预压缩
    )
)
```

### 压缩策略

| 策略 | 级别 | 行为 |
|------|------|------|
| `TruncateStrategy` | `LINE_TRUNCATE` | 截断长行 |
| `SummarizeStrategy` | `SUMMARIZE` | LLM 生成摘要 |
| `DiscardStrategy` | `DISCARD` | 丢弃低价值消息 |

### 依赖检测

当 LLM 调用工具时，`DependencyDetector` 自动分析参数中的文件引用和行号引用，建立锚点。后续如果这些依赖被压缩，`Restorer` 会在下一轮推理前通过 `supplemental_context` 自动注入相关片段。

```python
# 工具调用 → 自动检测依赖
cm.record_tool_call("read", {"file_path": "src/main.py", "offset": 10, "limit": 20})

# 推理前 → 安全点，可能触发恢复
supplemental = cm.safe_point_before_inference()
# supplemental 包含需要恢复的代码片段
```

---

## 11. 错误恢复与熔断

### ErrorClassifier

对异常进行分类以决定恢复策略：

```python
from runtime.recovery import ErrorClassifier, ErrorCategory

classifier = ErrorClassifier()

# 内置规则：
# ConnectionError / TimeoutError → RETRYABLE
# "rate limit" / "429" → RETRYABLE
# "unauthorized" / "403" → FATAL
# "not found" / "404" → DEGRADE

# 自定义规则
classifier.register(ValueError, ErrorCategory.FATAL)
```

### RetryPolicy

指数退避 + 随机抖动：

```python
from runtime.recovery import RetryPolicy, retry

policy = RetryPolicy(
    max_retries=3,
    base_delay=1.0,    # 初始 1s
    max_delay=60.0,    # 封顶 60s
    multiplier=2.0,    # 指数退避
    jitter=0.1,        # ±10% 抖动
)

# 延迟序列示例：1.0s → 2.0s → 4.0s（各加 ±10% 随机抖动）

result = await retry(my_async_fn, policy, classifier, arg1="val1")
```

### CircuitBreaker

三态熔断器：Closed → Open → Half-Open → Closed

```python
from runtime.recovery import CircuitBreaker, CircuitState

breaker = CircuitBreaker(
    name="anthropic_api",
    threshold=5,          # 连续 5 次失败 → OPEN
    reset_timeout=30.0,   # 30s 后尝试 HALF_OPEN
)

async with breaker.protect():
    return await call_llm(prompt)

# 或者在 PipelineRecovery 中使用
```

### PipelineRecovery

整合重试 + 熔断器的一站式恢复层：

```python
from runtime.recovery import PipelineRecovery, RecoveryConfig

recovery = PipelineRecovery(
    RecoveryConfig(
        retry_policy=RetryPolicy(max_retries=3),
        enable_circuit_breakers=True,
        max_consecutive_failures=5,
        fallback_text="I encountered an error. Please try again.",
    )
)

result = await recovery.call_with_recovery(
    "pipeline_turn",     # 组件名称（用于熔断器追踪）
    pipeline.turn,
    user_input=my_input,
)
```

---

## 12. 流式输出

### StreamEvent

```python
from runtime.streaming import StreamEvent, StreamType

# 事件类型
StreamType.TEXT            # 文本片断
StreamType.TOOL_CALL_BEGIN # 工具调用开始
StreamType.TOOL_CALL_END   # 工具调用结束
StreamType.DONE            # 推理完成（含 usage）
StreamType.ERROR           # 错误

# 创建事件
text_evt = StreamEvent.text("Hello")
tool_evt = StreamEvent.tool_call_begin("read", 0)
done_evt = StreamEvent.done(usage=Usage(input_tokens=100, output_tokens=50))
```

### StreamBuffer

收集流事件，最终产出完整的 `InferenceResult`：

```python
from runtime.streaming import StreamBuffer

buf = StreamBuffer()
buf.add(StreamEvent.text("Hello "))
buf.add(StreamEvent.text("world"))
buf.add(StreamEvent.done(usage=Usage(input_tokens=5, output_tokens=2)))

# 最终结果
result = buf.to_result()  # InferenceResult(text="Hello world", ...)
```

### MockStreamProvider

测试流式场景：

```python
from runtime.streaming import MockStreamProvider

provider = MockStreamProvider()
events = []
async for event in provider.stream_complete(messages, tools, config):
    events.append(event)
```

---

## 13. 检查点与持久化

### CheckpointStore

```python
from runtime.checkpoint import CheckpointStore, CheckpointConfig

store = CheckpointStore(
    CheckpointConfig(
        save_dir="./checkpoints",
        auto_save_interval=30,
        max_checkpoints_per_session=10,
    )
)

# 保存检查点
checkpoint = store.save(
    session_id="sess_001",
    turn_count=5,
    messages=[msg1, msg2],
    spec=harness_spec,
    config={"key": "value"},
)

# 加载检查点
cp = store.load_latest("sess_001")
print(cp.turn_count, cp.messages)

# 列表
checkpoints = store.list_checkpoints("sess_001")

# 删除
store.delete_session("sess_001")
```

### AutoSaveManager

通过事件总线自动保存：

```python
manager = AutoSaveManager(store, interval=30)

# 绑定到 pipeline turn 事件
bus.on(PipelineEvent.PIPELINE_TURN, manager.create_handler())
```

### 从检查点恢复

```python
harness = HarnessRuntime.restore(
    spec=spec,
    store=store,
    session_id="sess_001",
    llm=my_llm,
)
harness.start()
result = await harness.turn(user_input)  # 继续对话
```

---

## 14. 自定义 Harness

### 使用工厂辅助函数

从 `harnesses/base.py` 中的共享辅助函数开始：

```python
from runtime.harness import HarnessConfig, HarnessRuntime
from runtime.types import HarnessSpec
from tool_registry import ToolRegistry
from tool_registry.file_tools import ReadTool, GrepTool
from harnesses.base import ensure_dir, read_only_path_policy

MY_PROMPT = """\
You are a code review assistant...

Available tools: ``read``, ``grep``, ``web_search``, ``web_fetch``
"""

def create_code_review_harness(
    workspace_root: str = "./workspace",
    llm=None,
    model: str = "claude-sonnet-4-20250514",
) -> HarnessRuntime:
    registry = ToolRegistry()
    root = ensure_dir(workspace_root)
    allowed = (str(root),)

    # 注册工具
    registry.register(ReadTool(allowed_roots=allowed))
    registry.register(GrepTool(allowed_roots=allowed))

    # 构建规格
    spec = HarnessSpec(
        name="code-review",
        description="Code review assistant",
        tools=tuple(registry.definitions()),
        system_prompt_template=MY_PROMPT,
        default_model=model,
    )

    # 构建运行时
    harness = HarnessRuntime(spec, llm=llm)

    # 安装工具
    registry.install_all(harness)

    # 安全策略
    for policy in read_only_path_policy(allowed):
        harness.add_security_policy(policy)

    return harness
```

### 导出工厂函数

```python
# harnesses/__init__.py
from .research import create_research_harness
from .code_review import create_code_review_harness  # 新增

__all__ = [
    "create_research_harness",
    "create_code_review_harness",
]
```

### 设计模式

创建自定义 Harness 时的常见模式：

1. **工具选择**：只注册领域相关的工具，避免不必要的工具干扰 LLM
2. **系统提示词**：明确告诉 LLM 可用工具及其用途，设定输出格式
3. **安全策略**：读多写少的领域用 `allow_read_only`，需要保护的路径用 `allow_path_prefix`
4. **Pipeline 策略**：对话场景用 `interactive`，自主任务用 `auto`
5. **错误恢复**：网络相关的工具调用建议启用 `PipelineRecovery`

---

## 15. API 速查表

### 核心类型

| 类型 | 模块 | 说明 |
|------|------|------|
| `HarnessSpec` | `runtime.types` | Harness 蓝图（名称、工具、提示词） |
| `HarnessRuntime` | `runtime.harness` | 主编排器 |
| `HarnessConfig` | `runtime.harness` | 运行时配置 |
| `Session` | `runtime.session` | 会话管理 |
| `ToolDefinition` | `runtime.types` | 工具定义（供 LLM 使用） |
| `ToolCall` | `runtime.types` | 工具调用（运行时的结构化表示） |
| `UserInput` | `runtime.types` | 用户输入封装 |
| `StepResult` | `runtime.types` | Pipeline turn 结果 |
| `InferenceResult` | `runtime.types` | LLM 推理结果 |
| `InferenceConfig` | `runtime.types` | 推理参数 |
| `PipelineEvent` | `runtime.types` | 总线事件类型 |
| `EventPayload` | `runtime.types` | 事件负载 |

### 工具系统

| 类 | 模块 | 说明 |
|------|------|------|
| `BaseTool` | `tool_registry.base` | 工具抽象基类 |
| `ToolResult` | `tool_registry.base` | 工具结果（ok/err 模式） |
| `ParamSpec` | `tool_registry.base` | 参数描述 |
| `ToolRegistry` | `tool_registry.registry` | 注册中心 |
| `ReadTool` | `tool_registry.file_tools` | 读取文件 |
| `WriteTool` | `tool_registry.file_tools` | 写入文件 |
| `EditTool` | `tool_registry.file_tools` | 编辑文件 |
| `GlobTool` | `tool_registry.file_tools` | 文件搜索 |
| `GrepTool` | `tool_registry.file_tools` | 内容搜索 |
| `WebSearchTool` | `tool_registry.web_tools` | 网络搜索 |
| `WebFetchTool` | `tool_registry.web_tools` | 网页抓取 |

### 安全

| 函数/类 | 说明 |
|---------|------|
| `SecurityGate(config)` | 策略链审批器 |
| `SecurityConfig(decision, require)` | 安全配置 |
| `Decision.ALLOW/DENY/ASK` | 决策枚举 |
| `allow_read_only` | 内置策略：读写分离 |
| `allow_path_prefix(prefixes)` | 路径白名单策略工厂 |
| `deny_commands(patterns)` | 命令黑名单策略工厂 |

### 错误恢复

| 类 | 说明 |
|------|------|
| `ErrorClassifier` | 异常分类 |
| `RetryPolicy` | 重试策略（指数退避 + 抖动） |
| `retry(fn, policy, **kw)` | 重试执行器 |
| `CircuitBreaker(name)` | 熔断器 |
| `PipelineRecovery(config)` | 综合恢复层 |

### 上下文管理

| 类/函数 | 说明 |
|---------|------|
| `ContextManager(config, store?)` | 上下文编排器 |
| `ManagerConfig(total_budget, ...)` | 管理器配置 |
| `LayeredStore(config)` | SQLite 持久化存储 |
| `AsyncCompressor` | 异步压缩引擎 |
| `DependencyDetector` | 依赖检测器 |
| `Restorer` | 上下文恢复器 |

### 检查点

| 类 | 说明 |
|------|------|
| `Checkpoint` | 可序列化快照 |
| `CheckpointStore(config)` | 文件系统存储 |
| `AutoSaveManager(store, interval)` | 自动保存管理器 |
| `CheckpointConfig(save_dir, ...)` | 检查点配置 |
