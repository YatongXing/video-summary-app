# Video Summary App 文档

[项目首页](../README.md)

| 主题 | 现行入口 |
| --- | --- |
| 产品与范围 | [产品需求](PRD.md) · [用户故事](product/USER_STORIES.md) |
| 架构与安全 | [架构](ARCHITECTURE.md) · [删除、清理与维护设计](design/DELETION_OPERATIONS.md) |
| 验收与测试 | [验收标准](AC.md) · [端到端测试计划](E2E_TEST_PLAN.md) · [质量评估](testing/QUALITY_EVALUATION.md) |
| 使用与运维 | [本机部署与运维](../deploy/README.md) · [资源保留与退役规则](../AGENTS.md#本机部署资源保留与退役) · [千问与 OSS 指南](guides/QWEN_OSS.md) |

现行模型行为见[模型流程](ARCHITECTURE.md#4-输入模型与输出流程)，正式 014 配置、维护和回退见[部署指南](../deploy/README.md#stage-c-014-正式运维)。阶段 B 的隔离及真实样本见[历史验证记录](E2E_TEST_PLAN.md#阶段-b-验证状态)。

## 架构决策

- [ADR-001 模块化应用与持久队列](adr/ADR-001-modular-app-queue-worker.md)
- [ADR-002 队列越过条件](adr/ADR-002-conditional-bypass.md)
- [ADR-003 关联评测清理](adr/ADR-003-linked-evaluation-cleanup.md)
- [ADR-004 MVP 架构基线](adr/ADR-004-approved-mvp-architecture-baseline.md)
- [ADR-005 删除、内容访问与备份边界](adr/ADR-005-cr-001-deletion-troubleshooting.md)

## 构建验证指纹

[冻结指纹](baseline/cr-wp07a-fingerprints.json)保留发布时记录的路径和 SHA-256。源码隔离验证读取其中受保护的迁移和前端基线字段。历史路径是当时的证据标识，执行验证只需仓库中的冻结清单与受检源码。

## Stage C

- [正式开放与验证范围](E2E_TEST_PLAN.md#stage-c-014-正式验证记录)。
- [014 迁移与关键运行模块的 SHA-256](baseline/stage-c-014-fingerprints.json)。

- [轻量证据试点验收](E2E_TEST_PLAN.md#stage-c-轻量证据试点) · [部署变化](../deploy/README.md#stage-c-轻量证据试点)。

- Bilibili 导入产品要求见 [PRD](PRD.md#stage-c-结果)，下载边界见 [架构](ARCHITECTURE.md#stage-c-下载边界)，测试与部署见 [E2E 计划](E2E_TEST_PLAN.md#stage-c-第一阶段验收目标)和[部署指引](../deploy/README.md#stage-c-第一阶段部署预备)。
- [第二阶段发布演练](../deploy/README.md#stage-c-第二阶段发布演练)与 [Bilibili 视觉容量、传输与模型批次边界](design/STAGE_C_LIMITS.md)及[正式发布清单](../deploy/README.md#stage-c-正式发布清单)。
