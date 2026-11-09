# 本机部署与运维

正式实例 `vsa-local` 运行 S3-12，入口为 [127.0.0.1:58080](http://127.0.0.1:58080/)。部署使用 Docker Desktop 的 `desktop-linux` 上下文。Web 仅发布在本机，API、Worker、PostgreSQL 和 RustFS 通过 Docker 私网通信。

## 正式更新入口

在仓库根目录打开 PowerShell。确认应用已运行，上传、排队、模型处理和 OSS 清理均已结束，再执行。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\Update-VsaLocal.ps1
~~~

入口使用本机的 `release/s3-12-local` 交付包和现有私密配置路径。交付包由 Git 忽略，需要单独提供。更新器核对配方、镜像和 11 份迁移指纹，暂时关闭维护门禁，按需替换 API、Worker 和 Web，再恢复原维护状态。PostgreSQL、RustFS 和五个正式数据卷继续使用既有资源。变更根目录 `.env` 后使用同一命令应用。需要预查入口与文件路径时可加 `-WhatIf`。

## 状态与凭据

~~~powershell
docker --context desktop-linux ps --filter 'label=com.docker.compose.project=vsa-local'
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:58080/api/health/ready
~~~

就绪检查正常为 `200`，维护关闭时业务拒绝访问。Windows 定时巡检和桌面通知处于禁用状态。维护开关、故障收口和恢复规则见 [WP07-A Runbook](../docs/CR-WP07A_FORMAL_RUNBOOK.md)。当前配方位于 S3-12 交付包的 `deploy/`，WP07-A 文档中的配方路径和镜像表用于核对当时的验收记录。

仓库根 `.env` 供 Worker 读取模型和 OSS 配置，数据库和 RustFS 凭据留在已有的仓库外 `compose.env`。更新入口从正式 API 容器的 Compose 标签解析后者；必要时可用 `-PrivateEnvPath` 指定原文件。两个文件都由本机负责人保管。

## 验证与保留

交付包的 `README.md` 说明如何启动隔离实例，`manifest.json` 记录候选镜像、镜像归档和迁移 SHA-256，`verification/` 保存合成验证结果。正式长视频的实际验收情况见[实施状态](../docs/IMPLEMENTATION_PLAN.md#当前交付状态)。

软件包用于重建应用。业务原片、摘要、账号和删除控制保存在正式卷，按[删除与维护设计](../docs/CR-WP01_DELETION_OPERATIONS.md)管理。历史副本和受保护证据按原保留规则处置。

[千问与 OSS 操作指南](../docs/S3_11_USER_GUIDE.md) · [历史部署说明](../docs/archive/s3/DEPLOYMENT_BEFORE_S3_12.md)
