# Aurora CLI 1.0：初始化工作区

Aurora CLI 1.0 使用 `aurora init <目录>` 创建本地工作区。运行前需要 Python 3.11 或更高版本，并确保目标目录不存在或为空。该命令会创建 `aurora.yaml` 和 `sources/` 目录，但不会访问网络。

示例：`aurora init demo-workspace`。成功时终端显示 `Workspace ready`，并返回退出码 0。用户可运行 `aurora status --workspace demo-workspace` 检查配置；正常输出包含 `state: READY`。

如果目标目录已有文件，命令返回退出码 2 并显示 `Directory is not empty`。此时应选择新的空目录，或先人工确认并移动原有文件；工具不会自动删除目录内容。若 `aurora.yaml` 被手工修改后无法解析，可从版本控制恢复该文件，再重新运行状态检查。
