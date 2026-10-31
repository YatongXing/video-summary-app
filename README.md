# Video Summary App

当前运行实现停在S3-10：本地离线应用与旧基线非模型回归/短时容量证据。已批准[CR-001新文档基线](docs/CR-001.md)，本轮仅更新文档，代码、授权机制及页面尚未切换。后续[工作包](docs/CR-001_IMPLEMENTATION_PLAN.md)须逐包授权；先完成CR新验收/切换，再单独启动S3-11和新范围S3-12。旧测试不证明CR通过。真实Qwen和计费尚未启动。

## 先在 Windows 上运行和验收

请按 **[Windows 11 本地运行与人工验收指南](docs/LOCAL_TEST_GUIDE.md)** 逐步操作：

- Part A：Docker/Node 环境、隔离验收数据、数据库迁移与管理员初始化。
- Part B：打开真实应用，注册登录、上传合成视频、查看离线结果、管理及安全停止/重启。
- Part C：真实 Qwen 的配置与批准准备，**暂不执行**；当前没有仅改环境开关即可使用真实模型的模式。

指南默认使用 `video-summary-app-manual` Docker 项目，复用当前代码及配置，与原 `video-summary-app` 数据卷分离。根目录真实 `.env` 不用于离线启动，请保留且不要提交。

## 其他入口

- [文档索引](docs/README.md)：需求、验收、架构和 ADR。
- [已有部署说明](deploy/README.md)：按工作包整理的运行细节。
- [非模型回归与本机容量基线](docs/REGRESSION_BASELINE.md)：已执行的证据、边界和重跑命令。
- [实施计划](docs/IMPLEMENTATION_PLAN.md)：当前停止于 S3-10，后续工作包须另行授权。

离线替身输出不能证明视频理解质量。不要删除现有数据库或数据卷来解决启动错误；本轮文档不授权模型调用、提交或推送。
