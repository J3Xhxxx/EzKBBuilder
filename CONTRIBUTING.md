# Contributing to EzKBBuilder

欢迎使用中文或英文提交 Issue 和 Pull Request。不同领域的真实使用问题、可核对来源的检索用例、安装问题和小范围修复都很有帮助。

## 本地开发

使用 Python 3.11+ 创建虚拟环境后安装：

```sh
python -m pip install setuptools wheel -e .
python -m unittest discover -s tests -p 'test_*.py' -v
ezkb-builder --workspace .demo-workspace --provider mock demo
```

离线测试不需要密钥。安装包测试应实际运行；缺少构建工具导致的跳过不算安装验证通过。前端修改另运行 `node --check src/knowledge_pipeline/web/static/app.js`，并在浏览器检查相关交互。

真实 API 测试是显式操作，会产生服务商用量。不要在 CI 中添加个人密钥或自动运行付费测试。跨领域批量测试方法见 [本地检索与批量验收](docs/本地检索与批量验收.md)。

## 提交问题或修改

- 描述 Python / 操作系统版本、复现步骤、预期与实际结果；提供最小、脱敏的任务和来源示例。
- 修复行为问题时增加能复现问题的回归测试；文案修改不需要凑测试。
- 不绕过人工审核、内容版本绑定、来源边界或有限修复预算。模板只声明数据，不执行代码。
- 声称提高质量时报告数据范围、首测和复测结果、失败样本与评判方法；不能把 `answerable=true` 当成回答正确。
- 不提交 `.env`、工作区、下载的第三方资料、生成卡片、模型调用回执或未经授权的数据。自写示例应明确虚构范围。

Pull Request 请写清解决的问题、变化后的行为和实际验证结果。提交贡献表示你有权按项目 MIT 许可证提供这些改动。第三方内容必须保留其适用的授权与归属说明。
