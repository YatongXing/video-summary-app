# Video Summary App 文档索引

[项目首页](../README.md)

**截至 2026-11-07**，CR-002 及新版 WP06 已验收，WP07-A 已完成合成环境的部署与安全验证。正式本机部署仍在审批点。查看各包实际进度、后续授权和历史资源状态。 [阶段状态](IMPLEMENTATION_PLAN.md#当前交付状态)

## 现行规范与操作入口

| 入口 | 用途 |
| --- | --- |
| [CR-002 最终契约](CR-002.md) | 现行内容权限、管理员元数据及搜索、前端和反馈。替代关系以此为准。 |
| [产品需求](PRD.md) · [范围](SCOPE.md) · [用户故事](USER_STORIES.md) | 产品行为、输入、额度、队列、输出和保留期限。 |
| [架构](ARCHITECTURE.md) · [删除与维护设计](CR-WP01_DELETION_OPERATIONS.md) | 私有存储、Worker、删除栅栏、保留和恢复。删除兼容条款需结合 CR-002 阅读。 |
| [验收标准](AC.md) · [E2E](E2E_TEST_PLAN.md) · [CR-002 测试规格](CR-002_TEST_PLAN.md) | 需求级验收和回归范围。实测结果见下方证据。 |
| [WP07-A 部署方案](CR-WP07A_LOCAL_DEPLOYMENT_PLAN.md) · [正式执行清单](CR-WP07A_FORMAL_RUNBOOK.md) | Windows Docker Desktop Linux 容器的首次部署。执行清单记录创建、发布、开门、恢复和历史资源处置。 |
| [实施计划](IMPLEMENTATION_PLAN.md) · [质量评估](QUALITY_EVALUATION.md) · [Git 策略](GIT_STRATEGY.md) | WP/S3 顺序、模型质量方法及版本管理。 |

旧 [S3 部署说明](../deploy/README.md) 与 [旧 Windows 本地指南](archive/s3/LOCAL_TEST_GUIDE.md) 留存当时的命令和测试口径。正式部署从 WP07-A 执行清单进入。

## 架构决策与历史归档

已退役文档按 S3、CR、WP 归档。旧路径映射和移动前后指纹见[归档索引](archive/README.md)。带日期的状态描述对应当时的交付。

| 记录 | 当前效力 |
| --- | --- |
| [ADR-001 模块和队列](adr/ADR-001-modular-app-queue-worker.md) · [ADR-002 调度越过](adr/ADR-002-conditional-bypass.md) | 技术栈、队列职责、有限越过规则继续有效。 |
| [ADR-003 评测关联删除](adr/ADR-003-linked-evaluation-cleanup.md) · [ADR-004 架构基线](adr/ADR-004-approved-mvp-architecture-baseline.md) · [ADR-005 CR-001](adr/ADR-005-cr-001-deletion-troubleshooting.md) | 保留审批链。关联删除、隔离、重试和保留继续有效。旧内容备份、人工清理界面与排障授权的替代关系见 ADR-005 和 CR-002。 |
| [CR-001 决策](archive/cr/CR-001.md) · [实施计划](archive/cr/CR-001_IMPLEMENTATION_PLAN.md) · [测试规格](archive/cr/CR-001_TEST_PLAN.md) | CR-001 历史及仍有效的删除、维护决策。排障请求流程已经退役。 |
| [WP01 共享契约](archive/wp/CR-WP01_CONTRACT.md) · [数据/API](archive/wp/CR-WP01_DATA_API_DESIGN.md) · [切换回退设计](archive/wp/CR-WP01_CUTOVER_ROLLBACK.md) · [验收和评审归并](archive/wp/CR-WP01_REVIEW_REPORT.md) | 已验收的 L1.0 历史设计。旧 API 方案及旧实例切换计划不作为当前执行入口。 |
| [WP02](archive/wp/CR-WP02_VALIDATION_REPORT.md) · [WP03](archive/wp/CR-WP03_VALIDATION_REPORT.md) · [WP04](archive/wp/CR-WP04_VALIDATION_REPORT.md) · [WP05](archive/wp/CR-WP05_VALIDATION_REPORT.md) | 原阶段的迁移、测试和定向安全复核。WP04 删除物理验证仍是现行保障的证据。 |
| [原 WP06 报告](archive/wp/CR-WP06_VALIDATION_REPORT.md) · [原人工验收指南](archive/wp/CR-WP06_MANUAL_ACCEPTANCE.md) · [原始证据](baseline/cr-wp06.json) | CR-001 集成快照和旧环境记录。 |
| [S3-10 回归及容量报告](archive/s3/REGRESSION_BASELINE.md) · [原始基线](baseline/local-baseline.json) | 变更前本机离线回归、故障测试和短时容量记录。 |

## 最新验收和部署证据

| 阶段 | 已保留的证据 |
| --- | --- |
| CR-002 初轮 | [实施与安全交接](archive/cr/CR-002_IMPLEMENTATION_REPORT.md) · [独立测试](archive/cr/CR-002_TESTING_REPORT.md) · [原始证据](baseline/cr-002-testing.json) |
| 新版 WP06 | [系统验证](archive/wp/CR-002_WP06_VALIDATION_REPORT.md) · [人工指南和退役记录](archive/wp/CR-002_WP06_MANUAL_ACCEPTANCE.md) · [最终验收调整](archive/cr/CR-002_ACCEPTANCE_ADJUSTMENTS.md) · [补充独立测试](archive/cr/CR-002_ACCEPTANCE_ADJUSTMENTS_TESTING.md) · [调整原始证据](baseline/cr-002-acceptance-testing.json) |
| WP07 准备 | [只读准备审核](archive/wp/CR-WP07_PREPARATION_REVIEW.md) · [选路前方案](archive/wp/CR-WP07_EXECUTION_PLAN.md) · [原始盘点](baseline/cr-wp07-preparation.json)。执行路径后来确定为 WP07-A 首次部署。 |
| WP07-A 隔离验证 | [实施报告](CR-WP07A_IMPLEMENTATION_REPORT.md) · [测试报告](CR-WP07A_TESTING_REPORT.md) · [安全复核](CR-WP07A_SECURITY_REVIEW.md) · [冻结契约](CR-WP07A_IMPLEMENTATION_CONTRACT.md) · [测试规格](CR-WP07A_TEST_SPEC.md) · [原始测试记录](baseline/cr-wp07a-testing.json) · [当时的指纹](baseline/cr-wp07a-fingerprints.json) |

原始 JSON、截图和指纹保存在 [baseline/](baseline/)。指纹记录的是对应交付时的版本。历史报告中的“未授权”“待验收”或资源数量是当时记录，当前状态以实施计划和正式执行清单为准。

## 文档清理状态

WP01 验收时明确批准删除的四份中间稿已不在工作区。保留的决策、反例及删除名单见 [WP01 评审归并](archive/wp/CR-WP01_REVIEW_REPORT.md#5-验收后文档清理)。本轮移动归档文件并更新引用，删除候选等待独立审批。

[删除候选与保留条件](archive/README.md#待审批删除候选)集中维护在归档索引。
