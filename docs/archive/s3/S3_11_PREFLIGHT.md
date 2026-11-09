# S3-11 千问音视频接入准备

2026-11-08。无网络自检及首次真实 API 单请求测试已由负责人验收。验收范围为千问 AI 平台按量端点上的 3 秒合成视频。

## 现有配置与兼容性

- 当前 Git 的模型接口为 `ModelAdapter`，接收抽样帧和音轨，返回候选结果、请求 ID、用量和版本。Worker 只选择 FakeModel，`MODEL_CALLS_ENABLED=true` 会被拒绝。仅填写 Key 无法启用真实模型。首个探针独立验证服务协议与单段素材，完整生产适配仍需另行设计，覆盖双语结果、Schema 校验、取消与租约、错误分类和重复扣费风险。
- 仓库 `.env` 的 Key 当前为空，端点和模型匹配千问 AI 平台按量服务。该文件受 Git 忽略，Python 的 `os.environ` 只读取进程环境。正式实例使用 E 盘冻结 Compose 配方和仓库外的专用 `--env-file`，正式 Worker 没有千问变量或 `/srv/.env`。Web 构建上下文采用白名单，后端上下文排除 `.env` 和 `.env.*`，运行镜像仅复制 app 和依赖清单。
- 指定模型页的 SDK 示例使用 DashScope 百炼域名。当前仓库按量配置使用 `https://maas.qianwenaiapi.com/compatible-mode/v1`，应配千问 AI 平台按量 Key。百炼工作空间的地域端点与 Key，以及 Token Plan 的专属 Key，需单独匹配。本次探针固定使用仓库对应的千问 AI 平台按量端点。[模型页](https://www.qianwenai.com/models/qwen3.8-omni-flash#built-in-tools) · [千问 API Key](https://platform.qianwenai.com/docs/api-reference/preparation/api-key) · [百炼服务地址](https://help.aliyun.com/zh/model-studio/qwen-omni)
- 使用 OpenAI 兼容 `POST /chat/completions`。用户消息可含 `video_url` 和文本，音频独立输入使用 `input_audio`，视频文件可同时承载画面和音轨。探针按千问本地文件示例准备短 MP4 的 Base64 Data URL。该示例展示同系列的另一个型号，确切型号与按量路由组合须首测。Responses 使用独立的 `input_video`、`input_audio` 结构。指定型号仅输出文本，首测使用 SSE、`include_usage`，读取可能单独出现的用量尾帧。模型页的内置搜索属于 Responses，首测关闭搜索、推理并省略工具。[千问音视频文件理解](https://platform.qianwenai.com/docs/developer-guides/speech/multimodal-speech) · [Chat API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat) · [Responses API](https://platform.qianwenai.com/docs/api-reference/chat/openai-responses)
- 千问指南列该型号音频最长 3 小时、视频最长 2 小时，模型页列 1M 上下文及约 991K 非思考输入、131K 输出。百炼指南列该型号公网视频 URL 上限 2 GB，Base64 编码后小于 10 MB。百炼额度和限制只作为跨平台对照，项目配置所用按量账号的 2 GB、2 小时与实际 MIME/编解码接受范围仍待核实。首次 3 秒合成片段只覆盖短视频内联传输。[千问模型页](https://www.qianwenai.com/models/qwen3.8-omni-flash) · [千问音视频指南](https://platform.qianwenai.com/docs/developer-guides/speech/multimodal-speech) · [百炼输入限制](https://help.aliyun.com/zh/model-studio/qwen-omni)

## 零请求验证

在已验收 API 镜像上启动只读、无网络的临时容器，无正式卷和供应商凭据。生成并检查 3 秒 H.264/AAC 合成 MP4（蓝底和 440 Hz 合成音，22,890 字节），并运行 FakeModel 双语契约自检。探针本地通过请求构造、文本分片组装、用量尾帧、费用估算、缺用量、截断和服务错误自检。非交互式 `--live` 在网络前拒绝。无真实或付费请求。

工具、无密钥配置、合成视频与验证 JSON 原位于 Work 工作区的 `s3-11-prep`。这些材料已移入本机仓库 `docs/archive/work-private/s3-12-2026-11-09` 的加密归档，恢复方法见 [S3-12 清理报告](S3_12_CLEANUP_REPORT.md#c-盘-work-工作区)。恢复后可按下方历史步骤查阅。

## 首次真实 API 测试结果

负责人在本机交互终端完成单次请求并确认验收。以下结果来自负责人提交的脱敏探针截图。

| 项目 | 结果 |
| --- | --- |
| 请求 ID | chatcmpl-c2355161-f8f1-9035-fc8262fc55f5 |
| 输入 / 输出 / 缓存输入 Token | 270 / 21 / 0 |
| 探针费用估算 | ¥0.0002727，控制台实际账单待核对 |
| 端到端探针耗时 | 1.31 秒 |
| 检查 | JSON 结构通过，识别蓝底画面与合成音，实际发出 1 个请求 |

这次通过覆盖短合成视频内联输入、流式文本 JSON 和用量回传。正式 Worker 接入、2 小时 / 2 GB 兼容性、15 个授权样本的质量评估及账号账单仍待单独安排。S3-12 等负责人另行决定。

> 历史探针验收记录。文中的配置、审批方案和 Work 路径描述当时状态。当前使用见 [S3-11 操作指南](../../S3_11_USER_GUIDE.md)。

## 首次受控测试方案（验收留档）

现行 [实施计划](../../IMPLEMENTATION_PLAN.md) 和 [架构](../../ARCHITECTURE.md) 已要求逐批批准目的、外发字段、次数、预算、失败计费和真实素材边界。[质量评估](../../QUALITY_EVALUATION.md) 的 15 个授权视频仍属后续批次。本次补齐以下最小方案，批准范围由负责人决定。

| 项目 | 首测建议 |
| --- | --- |
| 批次 | 仅一次请求，自动重试 0 次。验证合成视频内联输入、文本 JSON、SSE 用量、耗时和账单。输出上限 256 Token。 |
| 外发 | 仅已校验 SHA-256 的 3 秒合成 MP4 及固定英文提示。Key 只用于固定服务商 HTTPS 的 Authorization 请求头。使用内联媒体，保持本机 Web 和私有桶的网络边界。 |
| 费用 | 千问 AI 按量模型页列输入 ¥0.8、输出 ¥2.7、缓存命中输入 ¥0.1，单位均为每百万 Token。以 5,000 输入和 256 输出作预算示例，约 ¥0.0047。视频画面与音频用量以服务商实测为准。建议先在控制台核对该账号计价和可执行的支出限制，再决定是否批准不超过 ¥1 的单请求预算。探针限制次数与输出量，服务商硬停付能力及失败费用仍待核实。[千问模型定价](https://www.qianwenai.com/models/qwen3.8-omni-flash#pricing) |
| 数据与凭据 | 独立按量测试 Key，通过交互终端隐藏输入。结果只输出请求 ID、用量、估算、耗时、结构与合成内容检查标志。服务商会收到外发字节。真实素材前确认实际账号、地域、日志与留存条款和删除能力，并单独批准。 |
| 停止 | 响应非 200、超时、中断、缺用量、缺终止标记、异常工具或推理、JSON 无效、识别标志为假、账单异常时停止复核。超时或失败仍可能计费，核对控制台后再决定是否重试。 |

### 操作步骤

1. 在 `s3-11-prep` 运行 `python qwen_probe.py --dry-run`，核对固定端点、模型、媒体哈希和输出限制。
2. 负责人确认预算、账号及单次批次后，在自己的交互式终端运行 `python qwen_probe.py --live`。键入 `SEND ONE` 后，在隐藏提示中填写匹配的千问 AI 平台按量 Key。`probe-config.json` 固定端点、模型、媒体哈希与交互式 Key 来源。
3. 保存脱敏输出，核对控制台用量和账单。请提供请求结果、HTTP 状态、用量及费用，保留 Key 和完整响应在本机受控范围。根据结果再决定 S3-11 下一批和 S3-12。
