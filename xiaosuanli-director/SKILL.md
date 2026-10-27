---
name: xiaosuanli-director
description: 在 Codex 中通过本地小算狸 MCP 创建、编辑和导出视频，支持故事改编及参考视频创作。
---

# 小算狸导演

先理解用户想看到的成片、画风、时长和语言，再使用小算狸 MCP 创作。已有内容先读取并复用；只按用户要求创建、修改或生成。图片和视频可能消耗积分。

## 安装和连接

从 GitHub 安装时安装整个 `xiaosuanli-director` 目录。优先使用 Codex 宿主提供的 Python 3.9 或更新版本；通过宿主的工作区依赖发现能力寻找，未提供时使用当前环境已有的 Python。无需第三方包；确实找不到时提醒用户安装 Python。

首次安装或 MCP 工具不可用时，按实际环境完成以下步骤：

1. 查看 `codex mcp get xiaosuanli --json`。未配置时执行 `codex mcp add xiaosuanli -- <Python 绝对路径> <本 skill 目录>/scripts/mcp_server.py`。已配置时核对其命令和路径；同名但属于其他服务时说明冲突，不覆盖用户配置。
2. 执行 `<Python 绝对路径> <本 skill 目录>/scripts/director.py setup`，配置消息钩子并保留已有钩子。新增或更新钩子时请用户在 `/hooks` 审阅并信任，然后发送新消息。按 Codex 提示重新加载或重启以连接新 MCP；工具没有出现时说明真实状态。
3. 调用 `xiaosuanli` MCP 的 `status`。未连接时通过 `auth_start` 返回的链接让用户在浏览器登录授权；将用户提供的一次性授权码及返回的 state 传给 `auth_complete`。不回显授权码，不读取或展示长期凭据。
4. 用 `projects` 和 `chapters` 展示工程和章节，用户选定后调用 `open_chapter`。不能代选第一项。用户要求创建时用 `create_project` 或 `create_chapter`，并使用稳定 requestId 和最新版本。

一个聊天固定一章，制作另一章请新建聊天。不同聊天可打开同一章，最新真实用户消息接管；MCP 写操作由宿主钩子自动绑定原消息，不能手填或编造 `_codex_context`。缺少钩子、权限或连接时说明阻点，不能借用其他消息身份。

`LOGIN_PENDING` 表示授权尚未完成。兑换连接中断后先查 `status`；`LOGIN_EXCHANGE_INTERRUPTED` 时仅在用户明确要求重试后使用同一授权码和 state 再试一次。码已使用或过期则重新授权。

## 创作

用 `context` 查看当前章节和网页编辑链接，用 `account` 查看账号和额度。先通过 `capabilities` 获取当前可用操作和准确参数，再用 `execute` 调用对应操作；[导演制作指引](references/director-workflow.md) 中的操作名即 `execute.operation`。小说与参考视频细节见[导入与复刻](references/import-and-remake.md)。只要求解析时停在解析结果；要求完整成片时继续到导出成功。

写入使用最新工程版本和稳定 requestId。响应中断时查询原任务或按原 requestId 恢复，避免重复消费。网页编辑后先刷新工程版本。返回真实进度、结果和编辑链接；没有观察实际媒体时不宣称画面效果已验收。`logout` 撤销当前渠道授权。
