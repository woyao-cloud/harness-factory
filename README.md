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

