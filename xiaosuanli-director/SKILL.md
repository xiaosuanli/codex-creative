---
name: xiaosuanli-director
description: 通过小算狸导演台将创意、故事或参考视频制作成视频，在 Codex 对话中编辑工程、生成素材和导出成片。
---

# 小算狸导演

在对话中理解用户想看的成片、画风、时长和语言，使用小算狸导演工程制作。已有内容先读取并复用；仅按用户要求创建、修改或生成。图片与视频可能消耗积分。

## 接线与授权

从 GitHub 安装时安装整个 `xiaosuanli-director` 目录及其引用文件，不能只复制本文件。不要求用户安装运行时：在 `bin/` 中选择与 Codex 运行环境相匹配的程序（macOS、Linux 或 Windows 的 arm64、amd64 版本），使用绝对路径；macOS/Linux 如缺少执行权限，由 Codex 为该文件添加执行权限。首次使用运行该程序的 `setup` 命令；它保留已有钩子，重复执行不会重复添加。若新增或更新了钩子，提示用户在 `/hooks` 审阅并信任，然后发送一条新消息。业务调用缺少消息上下文时停止，不编造或借用会话身份。

业务命令为 `<所选程序绝对路径> <命令>`。不安装其他编程环境；当前平台无匹配程序时说明情况。

1. 用 `status` 查看连接。未连接时 `auth-start` 返回授权链接和 state，把链接发给用户，让其在浏览器登录授权并将一次性授权码发到当前对话。
2. `auth-complete` 从 stdin 接收 JSON `{ "code": "一次性码", "state": "本次 state" }`。不回显授权码，不读取或展示本地凭据。成功后展示返回的工程目录。
3. 根据用户选择，用 `chapters`（stdin `{ "projectId": "工程 ID" }`）列出章节；再用 `open-chapter`（stdin `{ "projectId": "工程 ID", "chapterId": "章节 ID" }`）打开用户选定的章节。明确选择无法推断时请用户选择，不自动选第一项。
4. `context` 查看当前章节和网页编辑链接；`account` 查看账号和额度。一个聊天固定一章，另一个章节使用新聊天。网页有修改时刷新工程后继续。

`LOGIN_PENDING` 表示授权尚未完成。兑换连接中断后先查询 `status`；若显示 `LOGIN_EXCHANGE_INTERRUPTED`，仅在用户明确要求重试时使用同一授权码和 state 再试一次。码已使用或过期则重新授权。

## 创建与创作

- 用户要求新建工程时，`create-project` 的 stdin 为 `{ "title": "工程名称", "requestId": "稳定幂等键" }`；工程自带空白第一章。
- 新建章节使用 `create-chapter`，传入 `projectId`、目录返回的 `scriptId`、`title`、`requestId` 和实际最新 `expectedVersion`。创建后根据用户要求打开。
- `capabilities` 获取当前可用操作和准确参数；`execute` 从 stdin 接收 `{ "operation": "操作名", "arguments": {} }`。身份由宿主提供，不能在业务参数中填写。
- 按 [导演制作指引](references/director-workflow.md) 处理解析、分镜、素材、生成、恢复与导出；小说和参考视频的细节见 [导入与复刻](references/import-and-remake.md)。只解析时停在解析成果；要求完整成片时继续到导出成功。
- 写入使用最新工程版本和稳定 requestId。响应中断时查询原任务或按原 requestId 恢复，避免重复消费。权限、余额或能力不足时说明真实阻点。

需要参数的命令通过 stdin 传 JSON，不把授权码或正文放进命令行参数或持久脚本。`logout` 撤销当前渠道授权。返回真实进度、结果和服务端提供的编辑链接；没有观察实际媒体时不宣称画面效果已验收。
