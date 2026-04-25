# 
D:\claude-code-project\langchain\agentsFactory

## prompts
 <<<260424#思考一个问题：是否能从claude code 代码中抽象出一些代码或组件，开发出一套HarnessFactory 套件：
	 实现以下功能：1.达成功能最小可以运行系统类比claude code ;2.编写代码只是一种组装方式，不同软件可能有不同组合,比如视频处理harness/图片处理harness;股票处理;论文学习研究；课题研究等不同的harness
	 3.
	 头脑风暴:1.与用户直接交互的app，处于一个动态过程，会被快速升级迭代，HarnessFactory不断升级Harness->促进app 迭代更新
	 <<260424#
     HarnessFactory

## Test
pip install pytest
python -m pytest tests/test_tool_registry.py -v -k "TestReadTool" 
python -m pytest tests/ -v
python -m pytest tests/test_runtime_core.py tests/test_runtime_advanced.py -v
python -m pytest tests/test_context_manager.py -v

## 安装与运行
 1. 模块方式（开发时推荐，不需要安装）

  # 单次查询
  python -m harnesses "Find papers on AI"
 python -m harnesses research "深圳高三二模数学试卷难度分析报告" --provider openai
  # 或带 research 子命令
  python -m harnesses research "Find papers on AI"

  # 交互模式
  python -m harnesses --repl

  # 使用 MockProvider（无需 API key）
  python -m harnesses --mock "test query"

  2. pip 安装方式（安装后可直接使用 research 命令）

  pip install -e .
  research "Find papers on AI"
  research --repl

  选项说明：

  Options:
    --provider TEXT      LLM provider (anthropic, openai)  [default: anthropic]
    --api-key TEXT       API key（不传则从环境变量读取）
    --model TEXT         模型名称覆盖
    --research-root TEXT 研究工作目录  [default: ./research]
    --repl               交互式 REPL 模式
    --mock               使用 MockProvider（无需 API key）
    --help               显示帮助

  如果测试的话直接 python -m harnesses --mock "test query" 就能看到效果。