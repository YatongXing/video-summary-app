# Video Summary App 文档索引

先读镜像根目录的 [AGENTS.md](../../AGENTS.md)，再按任务阅读下表；`sources/` 为只读参考。产品行为由需求和验收文档定义；独立 ADR 是已批准架构决策的权威记录；[ARCHITECTURE.md](ARCHITECTURE.md)区分已批准设计、首轮默认和待验证事项。无需每次加载全部文件。

| 文件 | 用途 |
| --- | --- |
| [PRD.md](PRD.md) | 产品需求、业务规则、输入边界、保留期限和性能目标。 |
| [USER_STORIES.md](USER_STORIES.md)、[SCOPE.md](SCOPE.md) | 用户故事与 MVP 范围。 |
| [AC.md](AC.md)、[E2E_TEST_PLAN.md](E2E_TEST_PLAN.md) | 验收条件与端到端测试场景。 |
| [QUALITY_EVALUATION.md](QUALITY_EVALUATION.md) | 15 个授权视频的人工质量基线方法；正式阈值尚待基线。 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 已批准的 MVP 架构基线、外部模型 API 依据、详细设计与待验证事项。 |
| [ADR-001](adr/ADR-001-modular-app-queue-worker.md)、[ADR-002](adr/ADR-002-conditional-bypass.md)、[ADR-003](adr/ADR-003-linked-evaluation-cleanup.md)、[ADR-004](adr/ADR-004-approved-mvp-architecture-baseline.md) | 已批准的架构决策和首轮实施默认。 |
| [GIT_STRATEGY.md](GIT_STRATEGY.md) | 分支用途和变更管理。 |

MVP 架构基线、HTTPS 直达文件 URL 范围和已有实施默认已获产品负责人批准；Qwen 2 小时/2 GB 实际输入、本地媒体受控回连、容量及故障恢复仍需验证。产品负责人已确认审阅并同意当前模型账号条款。MVP 计费测试仍按批次批准，真实素材外发前需验证媒体访问和网络边界。架构批准不等于功能已实现或性能已达标。仓库仅保存无密钥 [.env.example](../.env.example)，实际 `.env` 由 [.gitignore](../.gitignore) 排除。
