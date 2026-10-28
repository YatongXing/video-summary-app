# Video Summary App 文档索引

**日期：** 2026-10-28  
**本地仓库：** 当前镜像目录下 `video-summary-app/`；Git 远端为 `https://github.com/YatongXing/video-summary-app.git`，默认分支 `main`。  
**恢复状态：** 远端 `main` 提交 `68cdcbb` 的旧版 Step 1 文档已克隆，七份 Step 1 文档随后按 2026-10-28 新决定在本地定点更新；原镜像中未推送的较新本地文件无法逐字恢复，Step 2 文件依据用户对话中的已批准决定重建。

## 阅读顺序与权威范围

1. 先读镜像根目录的 [AGENTS.md](../../AGENTS.md)。镜像 `sources/` 是只读参考，当前为空。
2. 产品行为先读现行本地 [PRD.md](PRD.md)；[CURRENT_DECISION_BASELINE.md](CURRENT_DECISION_BASELINE.md)保留远端旧版与后续决定的来源差异。
3. 架构工作读 [STEP2_ARCHITECTURE_REPLAN.md](STEP2_ARCHITECTURE_REPLAN.md) 和三份 ADR。重建报告是审查稿；ADR 记录此前已批准的方向。
4. 测试/质量工作再按需读对应 AC、E2E 和质量评测计划，不要求每次加载全部文件。

| 文档 | 职责与状态 |
| --- | --- |
| [PRD.md](PRD.md)、[USER_STORIES.md](USER_STORIES.md)、[SCOPE.md](SCOPE.md)、[AC.md](AC.md)、[E2E_TEST_PLAN.md](E2E_TEST_PLAN.md)、[QUALITY_EVALUATION.md](QUALITY_EVALUATION.md)、[STEP1_CHECKLIST.md](STEP1_CHECKLIST.md) | 现行本地 Step 1 需求及验证文档；已同步 2h/2GB、双输入、条件越过、身份与删除运营等新决定。 |
| [CURRENT_DECISION_BASELINE.md](CURRENT_DECISION_BASELINE.md) | 记录从远端旧版到当前需求的决定来源与差异，并列出现行技术/环境信息。 |
| [STEP2_ARCHITECTURE_REPLAN.md](STEP2_ARCHITECTURE_REPLAN.md) | 合并重建 Step 2.1 驱动因素、2.2 QA 场景、2.3/2.4 方案比较、2.5 候选技术基线和验证/批准计划；供用户审查。 |
| [ADR-001](adr/ADR-001-modular-app-queue-worker.md) | 已批准模块化应用、持久队列与独立 Worker；文件依据对话重建。 |
| [ADR-002](adr/ADR-002-conditional-bypass.md) | 已批准唯一条件的队首越过及同用户顺序；公平参数未批准；文件依据对话重建。 |
| [ADR-003](adr/ADR-003-linked-evaluation-cleanup.md) | 已批准评测资料反向关联及随源任务删除；文件依据对话重建。 |
| [GIT_STRATEGY.md](GIT_STRATEGY.md) | 从远端恢复的旧版分支用途记录。 |

## 当前阶段

Step 2 的分析和技术默认已重建为**审查稿**。方案 B 有条件接受；Qwen3.8-Omni-Flash 是 MVP 指定模型；最终 Step 2.5 基线还需验证和用户批准。完整 2h/2GB 媒体经限时签名 URL 被模型读取的可行性、实际账号条款、受控公网入口与本地容量仍待验证。MVP 计费测试按批准批次执行；正式运行须先配置预算与限额。真实视频外发还需完成数据条款核验。

本轮更新了七份 Step 1 文档，未改变远端历史。仅本机的 `.env` 已创建，密钥字段仍为空；仓库根目录 [.env.example](../.env.example)是可分享的无密钥示例，[.gitignore](../.gitignore)排除真实 `.env`。E 盘 `video-summary-app-log` 目录已创建供未来 App 日志使用；服务商侧审计日志不在此目录。
