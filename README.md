# EzKBBuilder

[![tests](https://github.com/J3Xhxxx/EzKBBuilder/actions/workflows/test.yml/badge.svg)](https://github.com/J3Xhxxx/EzKBBuilder/actions/workflows/test.yml) · [English](README.en.md) · [MIT License](LICENSE)

EzKBBuilder 把来源资料整理为供知识库检索与问答使用的知识卡。每张卡围绕一个主题，保留可独立理解、可核对来源、能回答相关问题的知识正文；可以承载业务规则、产品知识、常见问答、概念和方法。读者可读是底线，入库质量还要求主体明确、适用条件完整，脱离原文后仍不会误解。默认结构不要求行业编号，也不要求每张卡都有操作、验证或回滚流程。

生成流程为：来源整理 → 模型生成 → 本地结构与引用校验 → 模型审计 → 有限修复 → 人工审核 → 导出 → 人工发布登记 → 发布后验收。当前版本面向本地单用户。

**v0.1.0 是可使用、可扩展的早期版本。** Web 界面目前为中文，默认仅监听本机；未提供账户认证、多人权限或大规模向量库。适合个人与小规模知识库试用，尚未定位为可直接部署到公网的服务。

现在也可在工作台“检索与问答验证”中建立本地向量索引，实际检查召回和带引用的回答。默认嵌入模型为 `Qwen/Qwen3-Embedding-8B`，复用现有 API 地址与密钥；待审核卡可以进入独立测试索引。配置、操作和批量测试命令见 [本地检索与批量验收](docs/本地检索与批量验收.md)。

已完成 50 张跨领域卡片的真实 API 测试：本轮 100 个问题 Top-3 召回 100%，修复后 50 个问答与 20 个无答案问题通过代理逐条核对。样本范围、首测失败和复测口径见 [测试报告](docs/50张跨领域知识库测试报告.md)；该结果不代表所有领域或外部平台的准确率。

## 一张知识库知识卡包含什么

默认模板为 `knowledge_card`，当前模板版本为 `1.1`，沿用已有段落键，只有两个必填段落：

| 段落 | 是否必填 | 用途 |
|---|---|---|
| 主题与范围 `summary` | 是 | 说明知识主题、适用对象与范围，保留来源提供的版本或时间限定。 |
| 知识正文 `key_points` | 是 | 写出可独立理解、得到来源支持、足以回答相关问题的事实、规则或方法，并就近保留条件与例外。 |
| 详细解释 `explanation` | 否 | 必要的原理、关系、区别或背景。 |
| 示例 `examples` | 否 | 来源已有、能帮助理解的例子。 |
| 使用规则与方法 `application` | 否 | 来源支持的使用规则、处理方法或应用场景。 |
| 适用边界 `boundaries` | 否 | 来源明确给出的条件、例外或限制。 |

选填段落由内容决定；不适用或没有依据时直接省略，不留空标题，不写“暂无”“不适用”，也不为填满模板编造步骤或例子。不强制六节或科普式简短字数。概念卡可以是定义与区别，方法卡可以说明做法，FAQ 可以在知识正文中使用问答。领域、卡片类型和标签只是可选元数据。

正文应明确实体、产品或业务对象，保留来源提供的版本、时间、适用条件及来源引用，不凭空补齐资料未说明的信息。会改变结论的条件与例外应紧邻相应事实，不能只放在远处的“适用边界”中，以免下游切片后丢失限制。独立成片的内容应让检索系统找到相关知识，并让回答者据此回答问题；具体召回与回答效果仍需在目标知识平台验证。

另有 `product_docs` 专用模板，适合需要固定前置条件、操作步骤和排错章节的产品使用文档。普通知识卡使用默认模板即可。

## 快速启动

需要 Python 3.11 或更高版本。先获取源码：

```sh
git clone https://github.com/J3Xhxxx/EzKBBuilder.git
cd EzKBBuilder
```

Windows 可以双击：

```text
启动知识Pipeline工作台.bat
```

脚本会创建 `.venv`、安装 `requirements.txt`，然后打开 `http://127.0.0.1:8770/`。默认使用 `mock`，不需要密钥。工作台默认演示“星桥图书室借阅常见问答”，使用 `examples/knowledge_card_demo/library_faq_spec.yaml` 与 `library_faq_source.md`；这是虚构业务场景，仅用于演示。

`mock` 只用于检查按钮、状态、引用、审核与导出流程：它确定性地摘录来源，并明确标注没有进行模型归纳。它不代表真实 AI 的内容整理或审计质量。需要生成可用卡片时，应配置真实模型 API 并人工审核结果。

Windows PowerShell 手动安装并启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -B -m knowledge_pipeline --workspace .knowledge-workspace serve --source-root . --open-browser
```

Linux / macOS：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
ezkb-builder --workspace .knowledge-workspace serve --source-root . --open-browser
```

源码安装后可使用 `ezkb-builder` 或兼容命令 `knowledge-pipeline`。Python 模块名保留为 `knowledge_pipeline`；环境变量继续使用 `KP_` 前缀。本项目尚未发布到 PyPI。默认端口被占用时，在 `serve` 后增加 `--port 8771`。

CLI 提供随安装包分发的最小离线演示“保存可核对的资料笔记”，安装 wheel 后也能在项目外运行：

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -B -m knowledge_pipeline --workspace .demo-workspace --provider mock demo
```

源码目录中可在 `demo` 后追加 `--example-root examples/knowledge_card_demo`，选择附加的“同义词与近义词”示例。此 CLI 入口与工作台默认业务 FAQ 示例分开。

## 换一个领域生成知识卡

准备 UTF-8 Markdown/TXT 资料，以及一个 YAML 或 JSON 任务描述。例如：

```yaml
document_id: CARD-LIBRARY-001
title: 星桥图书室借阅常见问答
profile_id: knowledge_card
audience: 面向星桥图书室借阅问题的知识库检索与问答
objective: 依据来源回答开放时间、借阅额度、借期与续借条件；未说明的信息明确标注未知。
metadata:
  domain: 社区服务
  card_type: faq
  tags: [借阅, 图书室]
```

`document_id` 是项目内的唯一编号；自己起一个英文、数字、点、下划线或连字符组成的编号即可。`profile_id` 不写时默认使用 `knowledge_card`。`audience`、`objective` 和 `metadata` 可省略，但填写使用场景、目标用户和待回答的问题通常有助于模型组织知识。`card_type` 可按需写 `concept`、`fact`、`method` 或 `faq`，它不切换为另一套流程。

生成命令：

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -B -m knowledge_pipeline --workspace .knowledge-workspace run `
  --spec examples/knowledge_card_demo/library_faq_spec.yaml `
  --source examples/knowledge_card_demo/library_faq_source.md `
  --source-root .
```

换领域时，修改标题、目标和来源即可继续使用同一个 `knowledge_card` 模板。仓库还提供日常方法“用清单整理短途出行物品”，可运行：

```powershell
.\.venv\Scripts\python.exe -B -m knowledge_pipeline --workspace .knowledge-workspace run `
  --spec examples/knowledge_card_demo/daily_method_spec.yaml `
  --source examples/knowledge_card_demo/daily_method_source.md `
  --source-root .
```

这些本地示例来源都是项目自写并标明范围的演示资料，没有虚构外部引文。实际使用时应换成自己认可的来源；只有一个标题、没有资料，不能保证生成的事实可靠。

网页来源接受 HTTPS，需显式加入域名白名单：

```powershell
.\.venv\Scripts\python.exe -B -m knowledge_pipeline --workspace .knowledge-workspace run `
  --spec spec.yaml `
  --source-url https://docs.example.com/guide `
  --allow-host docs.example.com
```

本地来源限制在 `--source-root` 内。网页采集检查协议、域名、重定向、公网地址、内容类型和大小。来源正文仅作为资料，不得改变模型的生成与审计规则。

## 连接模型 API

在项目根目录（启动脚本所在目录）将 `.env.example` 复制为 `.env`，填写兼容 Chat Completions 的接口。双击启动脚本会读取这个文件；命令行启动则读取当前工作目录的 `.env`，已有系统环境变量优先。不要把密钥发到聊天、截图或提交到仓库。

```dotenv
KP_PROVIDER=openai-compatible
KP_API_BASE=https://api.example.com/v1
KP_API_KEY=replace-me
KP_MODEL=your-model
KP_VERIFY_TLS=true
KP_SUPPORTS_JSON_SCHEMA=false
```

`KP_API_BASE` 填服务商的 API 地址，`KP_API_KEY` 填密钥，`KP_MODEL` 填服务商提供的精确模型 ID。仅域名会补上 `/v1/chat/completions`；已有 `/v1` 或其他 API 前缀时仅追加 `/chat/completions`；完整接口地址也可直接填写。

保存后关闭原工作台进程，重新双击启动脚本。页面顶部显示 `openai-compatible` 就表示已选择真实 API；显示 `mock` 仍是演示模式。选择真实 API 不代表连接已验证，提交一张卡片后才能检查服务商是否接受地址、密钥和模型。三个角色默认共用 `KP_MODEL`，暂时不用配置角色覆盖字段。先保留 `KP_SUPPORTS_JSON_SCHEMA=false`。

`KP_GENERATE_MODEL`、`KP_AUDIT_MODEL`、`KP_REPAIR_MODEL` 可分别覆盖默认模型。服务明确支持 JSON Schema 时才把 `KP_SUPPORTS_JSON_SCHEMA` 设为 `true`。

也支持分别连接 FastGPT App：

```dotenv
KP_PROVIDER=fastgpt
KP_FASTGPT_BASE_URL=https://fastgpt.example.com
KP_FASTGPT_API_KEY=replace-me
KP_FASTGPT_GENERATE_APP_ID=
KP_FASTGPT_AUDIT_APP_ID=
KP_FASTGPT_REPAIR_APP_ID=
```

不同 App 使用不同密钥时，填写对应的 `KP_FASTGPT_*_API_KEY`。远端 App 的 Prompt 也应与所选模板一致。密钥从环境变量或未提交的 `.env` 读取，TLS 校验默认开启。

## 审核、导出与验收

工作台定时同步真实任务事件。模型审计通过后，人工审核区展示全文和当前版本；通过才能导出，退回需要填写原因。导出格式包括 Markdown、JSON 和 ZIP。完成外部知识平台的发布后，再登记发布记录。

知识卡发布后检查四项：来源支撑、主题相关、表达清楚、检索可用。人工审核还应检查正文是否自包含、实体与版本是否明确、关键条件是否随事实保留、引用是否足以核对。检索可用需要在实际知识平台提问，检查召回片段及基于片段的回答准确性。保存验收时会明确记录 PASS 或 FAIL，未通过项必须填写原因；没有真实检索时不能因为演示流程跑通就认定检索通过。本项目提供本地向量检索与带引用问答；导入外部知识平台仍需人工操作，并在该平台另行验收。

运行数据默认位于 `.knowledge-workspace/`，可通过 `--workspace` 指定：

- `pipeline.sqlite3`：任务、事件、版本、审核、发布与验收记录。
- `documents/`：每个不可变版本的 Markdown；结构化正文保存在 SQLite，导出时另生成 JSON。
- `bundles/`：任务实际使用的来源快照。
- `exports/`：批准后生成的交付文件。
- `retrieval.sqlite3` / `retrieval-sandbox.sqlite3`：正式 / 独立测试索引、向量缓存及版本信息。

## 自定义结构

多数领域可以直接使用 `knowledge_card`，只更换任务与资料。确实需要固定业务章节时，再复制 `src/knowledge_pipeline/profiles/knowledge_card/`，修改 `profile.yaml` 和 Generate、Audit、Repair 三份 Prompt。每个段落用 `required: true/false` 声明必填或选填；通过 `--profile-root <目录>` 加载额外模板。

模板配置只声明结构、Prompt、修复上限和验收项，不执行 Python 代码。未来可添加独立 SOP 模板；知识生成服务本身不执行卡片中的动作。

## 验证

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py" -v
```

测试检查结构合同、选填段落、版本与审核绑定、来源边界、Provider 请求、进程中断恢复、向量校验、检索版本隔离和 wheel 安装。打包测试需要安装 `setuptools wheel`，也可用 `KP_TEST_BUILD_PYTHON` 指定已有构建解释器；缺少构建工具时该组会跳过。CI 安装构建工具并运行完整离线测试，不需要 API 密钥。

配置本项目 `.env` 的真实 API 后，可显式运行小样本测试。每个样本最多调用模型 6 次（含重试），记录接口报告的 token 用量与耗时；结果保留在待人工审核，不会自动批准或发布：

```powershell
.\.venv\Scripts\python.exe -B tools/live_smoke.py --case language
.\.venv\Scripts\python.exe -B tools/live_smoke.py --case daily
.\.venv\Scripts\python.exe -B tools/live_smoke.py --case library
.\.venv\Scripts\python.exe -B tools/live_smoke.py --case astronomy
```

前三个样本使用本地演示资料；`astronomy` 从白名单内的 NASA HTTPS 网页采集资料并生成中文卡。运行记录位于 `output/live-smoke/`，卡片在 `.env` 指定的工作区（默认 `.knowledge-workspace/`）。验证错误拦截可运行 `--case language --fault-probe`：以本地故意写错的候选替代生成阶段，调用真实 API 审计、修复和重审，结果单独保存在 `.knowledge-workspace-live-probes/`。

早期小样本的内容整理测试见 [历史测试报告](docs/真实API与可用性测试报告-2026-09-12.md)。该报告当时未测试召回与回答；后续 50 张跨领域测试已覆盖本地检索，最新结果与局限见 [50 张测试报告](docs/50张跨领域知识库测试报告.md)。详细设计见 [架构说明](docs/架构说明.md)。

知识库验收流程见 [知识库入库验收](docs/知识库入库验收.md)，已准备 [10 条业务检索与问答用例](examples/knowledge_card_demo/library_faq_queries.yaml)。它们覆盖同义问法、续借条件、未知信息及对象范围，当前为待执行用例，不是检索成绩。

## 参与与许可

欢迎提交可复现问题、不同领域的验收用例、模板和修复，见 [贡献指南](CONTRIBUTING.md)。部署边界与漏洞报告方式见 [安全说明](SECURITY.md)，版本变化见 [更新记录](CHANGELOG.md)。项目代码采用 [MIT 许可证](LICENSE)；下载的第三方资料及其生成内容仍需按来源的授权条件使用，代码许可证不替代资料许可。
