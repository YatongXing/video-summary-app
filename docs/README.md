# Video Summary App 文档索引

**当前状态：** CR-001产品和架构责任边界已批准，本轮仅文档更新；代码仍为S3-10旧实现，所有后续包未启动。新旧冲突以ADR-005列明的替代范围为准，原ADR和S3-10证据保留。

本仓库以本页为导航入口，Agent 协作与授权边界见 [实施计划](IMPLEMENTATION_PLAN.md)，再按任务阅读下表。ChatGPT 镜像中的 `AGENTS.md` 和只读 `sources/` 属于仓库外的会话上下文，迁移后的仓库不再链接不存在的上级文件。产品行为由需求和验收文档定义；独立 ADR 是已批准架构决策的权威记录；[ARCHITECTURE.md](ARCHITECTURE.md)区分已批准设计、首轮默认和待验证事项。无需每次加载全部文件。

| 文件 | 用途 |
| --- | --- |
| [CR-001.md](CR-001.md) | D-01～D-08、CR-D09～16批准登记、替代范围和授权边界。 |
| [CR-001_IMPLEMENTATION_PLAN.md](CR-001_IMPLEMENTATION_PLAN.md) | 独立工作包、依赖、每包准入/验收、Git保存建议及S3-11/12新衔接。 |
| [CR-001_TEST_PLAN.md](CR-001_TEST_PLAN.md) | CR-AC-01～24和新增故障/迁移/人工规格，全部待实施验证。 |
| [ADR-005](adr/ADR-005-cr-001-deletion-troubleshooting.md) | 正式架构变更记录，精确列明旧ADR被替代的部分，不改写历史。 |
| [LOCAL_TEST_GUIDE.md](LOCAL_TEST_GUIDE.md) | Windows 11 逐步运行真实本地应用与人工验收；离线模式、数据隔离和暂不执行的 Qwen 准备。 |
| [PRD.md](PRD.md) | 产品需求、业务规则、输入边界、保留期限和性能目标。 |
| [USER_STORIES.md](USER_STORIES.md)、[SCOPE.md](SCOPE.md) | 用户故事与 MVP 范围。 |
| [AC.md](AC.md)、[E2E_TEST_PLAN.md](E2E_TEST_PLAN.md) | 验收条件与端到端测试场景。 |
| [QUALITY_EVALUATION.md](QUALITY_EVALUATION.md) | 15 个授权视频的人工质量基线方法；正式阈值尚待基线。 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 已批准的 MVP 架构基线、外部模型 API 依据、详细设计与待验证事项。 |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Step 3 活跃任务清单、依赖、Agent 分工与实施门槛；工作状态随交付更新。 |
| [REGRESSION_BASELINE.md](REGRESSION_BASELINE.md) | 非模型回归证据、22 个 E2E 场景追踪、可重跑命令和本机短时容量基线；未验证项明确列出。 |
| [ADR-001](adr/ADR-001-modular-app-queue-worker.md)、[ADR-002](adr/ADR-002-conditional-bypass.md)、[ADR-003](adr/ADR-003-linked-evaluation-cleanup.md)、[ADR-004](adr/ADR-004-approved-mvp-architecture-baseline.md) | 保留原批准历史；当前效力按ADR-005的部分替代清单阅读。 |
| [GIT_STRATEGY.md](GIT_STRATEGY.md) | 分支用途和变更管理。 |

MVP技术栈和HTTPS直达范围继续有效；删除/授权/备份/运营以CR-001和ADR-005更新的边界为准，具体算法、阈值、命令待实施评审；Qwen 2 小时/2 GB 实际输入、本地媒体受控回连、容量及故障恢复仍需验证。产品负责人已确认审阅并同意当前模型账号条款。MVP 计费测试仍按批次批准，真实素材外发前需验证媒体访问和网络边界。架构批准不等于功能已实现或性能已达标。仓库仅保存无密钥 [.env.example](../.env.example)，实际 `.env` 由 [.gitignore](../.gitignore) 排除。
