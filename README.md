# Video Summary App

Windows 本机视频摘要应用。支持本地上传和 Bilibili BV 播放页导入，提供中英文摘要、章节和 Markdown 下载。本地视频可在结果页播放和跳转章节，Bilibili 章节使用纯文本时间戳。

处理默认使用 Qwen3.8-Omni-Flash。阶段 B 已实现可选的 DeepSeek-V4.1-Flash，提交时固定所选模型，重试沿用。B-5 两条真实素材与 B-6 近两小时 SSE 复测均已隔离验收。正式实例中 DeepSeek 是常驻可选模型，默认仍为 Qwen。样本覆盖和仍待验证的边界见[阶段 B 验证记录](docs/E2E_TEST_PLAN.md#阶段-b-验证状态)。


## 从源码构建与隔离验证

在 Windows 安装 Docker Desktop（Linux 容器）、Python 3.11、Node.js 22.12+（含 npm）及 Chrome 或 Edge。从仓库根目录运行：

~~~powershell
python backend/tools/verify_source_build.py
~~~

首次构建从公开镜像仓库和依赖源获取所需组件。脚本通过前端锁文件在临时目录生成 Web 产物，并重新构建 API、Worker 和 Web 镜像。独立验证无需本机交付包、已有 `dist/` 或 `.env`。浏览器安装在其他位置时，可通过 `UI_TEST_BROWSER` 指定可执行文件。

脚本分别启动内联媒体与模拟 OSS 两个临时项目。每个项目有独立数据库、网络和卷，Web 仅绑定本机临时端口。测试检查 Stage C 的 001～014 迁移账本及冻结字节、维护门禁、跨用户访问隔离、合成视频的中英文摘要、Markdown 下载、播放、取消和删除。验证使用模拟千问与 OSS，覆盖本地上传，并回收测试容器、网络及卷。Bilibili 下载器部署和隔离验证见 [Stage C 运维](deploy/README.md#stage-c-第二阶段发布演练)。源码镜像保留为唯一的 `vsa-source-*` 本地标签，便于复核。

仅构建前端时，可在 `frontend/` 运行 `npm ci` 和 `npm run build`。如需保留本机已验收的 `dist/`，请在独立检出中运行。模型配置模板见 [.env.example](.env.example)。

## 已有本机实例

正式 `vsa-local` 运行 Stage C 014，于 2026-11-26 开放普通任务和新 Bilibili 导入。入口为 [127.0.0.1:58080](http://127.0.0.1:58080/)。正式运维使用单独保管的 014 交付包与私密配置。

- [正式部署状态与 014 运维](deploy/README.md)
- [千问与 OSS 操作指南](docs/guides/QWEN_OSS.md)
- [产品、架构与测试文档](docs/README.md)

## 授权说明

本仓库目前未指定项目许可证。第三方依赖适用各自的许可证。
