# Video Summary App 文档

[项目首页](../README.md)

| 主题 | 现行入口 |
| --- | --- |
| 产品与范围 | [产品需求](PRD.md) · [用户故事](product/USER_STORIES.md) |
| 架构与安全 | [架构](ARCHITECTURE.md) · [删除、清理与维护设计](design/DELETION_OPERATIONS.md) |
| 验收与测试 | [验收标准](AC.md) · [端到端测试计划](E2E_TEST_PLAN.md) · [质量评估](testing/QUALITY_EVALUATION.md) |
| 使用与运维 | [本机部署与运维](../deploy/README.md) · [千问与 OSS 指南](guides/QWEN_OSS.md) |

## 架构决策

- [ADR-001 模块化应用与持久队列](adr/ADR-001-modular-app-queue-worker.md)
- [ADR-002 队列越过条件](adr/ADR-002-conditional-bypass.md)
- [ADR-003 关联评测清理](adr/ADR-003-linked-evaluation-cleanup.md)
- [ADR-004 MVP 架构基线](adr/ADR-004-approved-mvp-architecture-baseline.md)
- [ADR-005 删除、内容访问与备份边界](adr/ADR-005-cr-001-deletion-troubleshooting.md)

## 构建验证指纹

[冻结指纹](baseline/cr-wp07a-fingerprints.json)保留发布时记录的路径和 SHA-256。源码隔离验证读取其中受保护的迁移和前端基线字段。历史路径是当时的证据标识，执行验证只需仓库中的冻结清单与受检源码。
