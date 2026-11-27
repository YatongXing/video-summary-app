# Video Summary App

在 Windows 本机上传视频，按章节阅读中英文摘要，点击时间戳回看原片，再将结果下载为 Markdown。完整的 Stage C 014 部署还支持导入 Bilibili BV 播放页，链接来源的结果提供文字时间戳。

## 第一次使用

**先打开[从这里开始](docs/GETTING_STARTED.md)**。指南从下载源码写到第一次登录、上传视频和查看结果，先完成第 1 节的环境检查，再从下表选择构建路线。

| 你手头有什么 | 选哪条路 |
| --- | --- |
| 想修改源码或检查构建结果，可以安装开发工具 | [从源码部署](docs/GETTING_STARTED.md#路线-a--用本机-node-构建前端)。在本机构建前端，用项目的 Dockerfile 构建应用服务。 |
| 只想在本机运行，已安装 Docker Desktop | [Docker 部署](docs/GETTING_STARTED.md#路线-b--在-docker-内构建前端)。按指南准备镜像，再用 Compose 启动。GitHub 的 014 Release 提供源码 ZIP。 |

第一次安装 Docker、配置 WSL 2 或选择 Node.js 版本时，可以先完成[依赖安装与自检](docs/DEPENDENCIES.md)。首次使用建议从一段短的 H.264 MP4 开始。离线模式适合检查流程；要根据视频内容生成真实摘要，请按指南为 Worker 配置模型服务。默认模型为 Qwen3.8-Omni-Flash，完整部署也支持可选的 DeepSeek-V4.1-Flash。

## 已有实例与开发验证

- 已有正式 014 实例的负责人可直接查阅[部署、健康检查与恢复](deploy/README.md)。首次安装使用独立项目名和端口。
- 开发者可以从仓库根目录运行下面的命令，重新构建 API、Worker 和 Web 并执行隔离验收。脚本使用模拟模型和 OSS，验证本地上传、播放、双语导出与删除，随后回收两个测试项目的容器、网络和卷。构建生成的镜像标签会打印在终端，供复核使用。

~~~powershell
py -3.11 backend/tools/verify_source_build.py
~~~

[014 源码下载](https://github.com/YatongXing/video-summary-app/releases/tag/v1.1.0) · [产品、架构与测试文档](docs/README.md) · [各阶段实际验证范围](docs/E2E_TEST_PLAN.md)

## 授权说明

本仓库目前未指定项目许可证。第三方依赖适用各自的许可证。
