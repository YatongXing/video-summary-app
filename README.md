# Video Summary App

Windows 本机视频摘要应用。支持本地文件和经安全校验的 HTTPS 直达视频，提供中英文摘要、JSON 导出和视频播放。

## 从源码构建与隔离验证

在 Windows 安装 Docker Desktop（Linux 容器）、Python 3.11、Node.js 22.12+（含 npm）及 Chrome 或 Edge。从仓库根目录运行：

~~~powershell
python backend/tools/verify_source_build.py
~~~

首次构建从公开镜像仓库和依赖源获取所需组件。脚本通过前端锁文件在临时目录生成 Web 产物，并重新构建 API、Worker 和 Web 镜像。独立验证无需本机交付包、已有 `dist/` 或 `.env`。浏览器安装在其他位置时，可通过 `UI_TEST_BROWSER` 指定可执行文件。

脚本分别启动内联媒体与模拟 OSS 两个临时项目。每个项目有独立数据库、网络和卷，Web 仅绑定本机临时端口。测试检查 001～011 迁移、维护门禁、跨用户访问隔离、合成视频的中英文摘要、JSON 导出、播放、取消和删除。验证使用模拟千问与 OSS，并回收测试容器、网络及卷。源码镜像保留为唯一的 `vsa-source-*` 本地标签，便于复核。

仅构建前端时，可在 `frontend/` 运行 `npm ci` 和 `npm run build`。如需保留本机已验收的 `dist/`，请在独立检出中运行。模型配置模板见 [.env.example](.env.example)。

## 已有本机实例

已部署的 S3-12 实例使用 [127.0.0.1:58080](http://127.0.0.1:58080/)，支持真实千问和私有 OSS。正式更新需要本机已有实例及单独保管的 S3-12 交付包。

- [正式更新与运维](deploy/README.md)
- [千问与 OSS 操作指南](docs/guides/QWEN_OSS.md)
- [产品、架构与测试文档](docs/README.md)

## 授权说明

本仓库目前未指定项目许可证。第三方依赖适用各自的许可证。
