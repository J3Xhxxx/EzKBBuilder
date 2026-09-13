# EzKBBuilder

[中文文档](README.md) · [MIT License](LICENSE) · [Tests](https://github.com/J3Xhxxx/EzKBBuilder/actions/workflows/test.yml)

Build source-grounded knowledge cards for retrieval and question answering. EzKBBuilder takes local text or allowlisted web sources through generation, validation, model audit, bounded repair, human review, and export. A card preserves the subject, conditions, exceptions, and references needed to answer questions independently of the original document.

**v0.1.0 is an early release for local, single-user use.** The web interface is currently in Chinese. It has no account authentication, tenant isolation, or large-scale vector database. Keep the workbench on localhost.

## What works

- Configurable YAML profiles and versioned Generate / Audit / Repair prompts.
- A generic knowledge-card profile with required scope and knowledge sections; optional explanation, examples, applications, and boundaries.
- Chat Completions-compatible APIs, FastGPT App adapters, and a deterministic offline mock.
- Source snapshots and hashes, immutable document versions, persisted events, and interrupted-task recovery.
- Human approval bound to the current document version; Markdown, JSON, and ZIP exports; manual publication and acceptance records.
- Local embedding search and cited answers using `Qwen/Qwen3-Embedding-8B` by default. Separate approved and draft-test indexes, version filtering, and embedding caching.

External knowledge-platform ingestion remains manual. Model audit and valid citation IDs do not prove factual correctness; review generated content against its sources.

## Quick start

Requires Python 3.11+. Clone and install from source; this project is not currently published to PyPI.

```sh
git clone https://github.com/J3Xhxxx/EzKBBuilder.git
cd EzKBBuilder
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
ezkb-builder --workspace .knowledge-workspace serve --source-root . --open-browser
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m knowledge_pipeline --workspace .knowledge-workspace serve --source-root . --open-browser
```

Open `http://127.0.0.1:8770/`. Add `--port 8771` after `serve` if needed. Windows users can also run the included `启动知识Pipeline工作台.bat` launcher. The compatible `knowledge-pipeline` command, Python module `knowledge_pipeline`, and `KP_` configuration keys remain available.

The default mock needs no API key. It extracts source text to demonstrate the workflow; it does not demonstrate real model quality. Run the packaged offline example with:

```sh
ezkb-builder --workspace .demo-workspace --provider mock demo
```

## Connect an API

Copy `.env.example` to `.env` in the directory from which you start the workbench, and set:

```dotenv
KP_PROVIDER=openai-compatible
KP_API_BASE=https://api.example.com/v1
KP_API_KEY=replace-me
KP_MODEL=your-model
KP_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
```

Use the exact model names supported by your provider. Embeddings reuse the chat endpoint base and key unless `KP_EMBEDDING_API_BASE` / `KP_EMBEDDING_API_KEY` override them. Restart after editing configuration. Real generation, indexing, and answers incur provider usage. Never commit `.env` or share logs containing credentials or private sources.

## Generate and search

Prepare a UTF-8 Markdown/TXT source and a YAML specification:

```yaml
document_id: CARD-PRODUCT-001
title: Product return policy
profile_id: knowledge_card
audience: Customer-support knowledge base
objective: Answer return-window and eligibility questions using the supplied policy.
```

```sh
ezkb-builder --workspace .knowledge-workspace run --spec spec.yaml --source policy.md --source-root .
ezkb-builder --workspace .knowledge-workspace index --sandbox
ezkb-builder --workspace .knowledge-workspace search "When is a return eligible?" --sandbox --answer
```

Use the web workbench to review the full card and approve it or return it with a reason. Approval enables export. The sandbox allows testing pending cards without approving them; omit `--sandbox` to use the approved index. Rebuild the index after adding or revising cards.

## Evidence and development

The [50-card evaluation](docs/50张跨领域知识库测试报告.md) covers materials simulation, PLC automation, PostgreSQL, Python data processing, and statistical modeling. Final regression results: 97/100 Top-1, 100/100 Top-3, 50/50 reviewed answer checks, and 20/20 unanswerable checks. The first held-out answer run was 28/30; the final result follows fixes and is not an unseen test. Answers were checked by an agent against frozen references, without independent domain experts. These results do not establish universal accuracy.

The repository includes [aggregate metrics](docs/benchmarks/benchmark50-summary.json) and rerunnable scripts, not downloaded third-party sources, generated cards, private workspaces, or API receipts. See the [benchmark instructions](docs/本地检索与批量验收.md) for reproduction and paid API usage details. New runs require new semantic review.

```sh
python -m pip install setuptools wheel -e .
python -m unittest discover -s tests -p 'test_*.py' -v
```

See [CONTRIBUTING](CONTRIBUTING.md), [SECURITY](SECURITY.md), and [CHANGELOG](CHANGELOG.md). Code is MIT-licensed; third-party source material retains its own terms.
