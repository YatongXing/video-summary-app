# Video Summary App

Video Summary App 在 Windows 上导入本地视频或经安全校验的 HTTPS 直达视频，处理任务并提供双语摘要、JSON 导出和视频播放。当前交付使用离线模型替身。

截至 2026-11-07，CR-002 和新版 WP06 已获负责人验收。WP07-A 已完成全新 Windows Docker 部署的隔离验证，正式实例尚未创建、发布或开放。实际 API 和 Web 验证镜像已随测试资源回收，正式创建前需按冻结版本重建、隔离复验并保留候选镜像。

- [现行产品契约](docs/CR-002.md)：USER 和 ADMIN 均可从个人入口查看自己的内容。管理员后台仅显示白名单元数据，按 Task ID 搜索。
- [正式本地部署方案](docs/CR-WP07A_LOCAL_DEPLOYMENT_PLAN.md)和[执行清单](docs/CR-WP07A_FORMAL_RUNBOOK.md)：使用新的数据库、私有对象存储和独立删除控制卷。创建、发布到 localhost 和开门分阶段审批。
- [阶段状态与待办](docs/IMPLEMENTATION_PLAN.md#当前交付状态)和[文档索引](docs/README.md)：区分当前规范、验证证据、历史归档及后续工作。

旧 manual 和 WP06 容器、全部 13 个历史卷均已按批次回收。最后一次清理盘点为 0 个容器、0 个卷，旧 manual 专用网络及既有镜像保留。S3-11 真实 Qwen 接入及计费、S3-12 部署完善仍待各自授权。 [处置记录](docs/CR-WP07A_FORMAL_RUNBOOK.md#2026-11-07-剩余六卷最终处置)
