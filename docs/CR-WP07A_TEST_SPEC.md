# CR-WP07-A 独立测试与退出规格

本页保存 WP07-A 隔离验证规格。实际通过项、故障接缝及未执行范围见[独立测试报告](CR-WP07A_TESTING_REPORT.md)。正式创建、发布和开门见[执行清单](CR-WP07A_FORMAL_RUNBOOK.md)。

| ID | 必须证明的行为 | 证据层 |
|---|---|---|
| A01 | 新空PG17完整001～011与原SHA；非新空库拒绝误初始化；无旧授权/旧服务失权动作标不适用 | 新专用PG/真实bootstrap |
| A02 | init、NOSUPER owner maint、非owner runtime三身份；API/Worker无init/maint秘密；DDL/CREATE/SET ROLE/表owner负例、维护重连 | catalog/真实连接负例/容器白名单env keys |
| A03 | runtime consent最小清退和support仅关闭清退；INSERT/授权读取拒绝，真实正常任务删除/已有job关闭不被ACL破坏 | PG/HTTP/实际清理 |
| A04 | 新镜像/模板required=true，缺失/false启动预检拒绝；旧WP06/legacy未设flag兼容不影响既有测试 | 配置/独立legacy与strict实例 |
| A05 | 初始CLOSED；信任OPEN才允许业务；缺失/损坏/身份失配/未知state/符号链接fail closed；读取不重建缺失控制 | PG+真实控制卷/负例 |
| A06 | close/open只维护身份、真实DB owner验证，runtime/ADMIN无HTTP/CLI捷径；状态原子持久化，容器/主机代理重启后CLOSED保持 | 真实CLI/重启/HTTP |
| A07 | CLOSED时API业务读写、auth/任务/内容/上传/导出及ready拒绝，live仍可诊断；请求入口后闭门不继续发布/读写 | HTTP与受控I/O接缝 |
| A08 | 视频首响应及HEAD/get/read前后/分块中close停止后续正文，重OPEN可正常Range/seek | 真实HTTP/TCP/Chrome |
| A09 | Worker在CLOSED时不启动重放/领取/心跳写/发布/新转码上传/物理清理；长处理期间closed阻后续I/O且不伪成功退款；OPEN恢复队列 | 双Worker/真实I/O+checkpoint/PG状态 |
| A10 | USER/ADMIN各正常本人上传→处理→双语摘要JSON、视频/seek；跨用户及旧请求/旧API拒绝；metadata明确字段白名单无内容 | 真实PG/私有RustFS/HTTP/Chrome |
| A11 | 已批准前端src/dist逐字节保持；Task ID只名称下，任意字面搜索清空默认，反馈配置、切账号和媒体卸载保持 | SHA/复用浏览器 |
| A12 | 全局N/普通用户约束、FIFO、配额扣退、自动/人工重试/取消/原五态/错误与日志不回归 | 必要完整非模型/真实Worker |
| A13 | 非root UID10001实际运行；staging/work/control卷可写且Linux锁/稳定inode/FD继承/父死子活/N>=2删A保B仍成立 | 新镜像/真实Linux/FFmpeg |
| A14 | 删除意图/栅栏/代次、实际SQL对象工作目录清理、四态版本/退避、journal重放与30天90天/BACKUP不误删 | PG/真实Linux/S3/受控维护 |
| A15 | Docker停止/重启后任务与卷持久；PG/S3/Worker故障恢复、CLOSED不会因重启/依赖失败自行OPEN | 隔离实例实停实启 |
| A16 | 真实运行monitor覆盖API/Worker/DB/S3/cleanup/进度/持续故障，file receiver确实接收故障与恢复，去重不吞持续故障 | 隐藏临时host观察器/文件事件+故障注入 |
| A17 | 无外部通知/真实账号/计划任务安装；正式通知渠道和责任人明示待确认，file receiver只证明测试通知链 | 配置/进程/报告边界 |
| A18 | 所有新资源先验prefix/标签/无冲突；原9容器13卷ID/StartedAt/状态及旧10卷未变；本轮实例回收无残留 | before/afterDocker元数据/产物SHA |

必要隔离故障测试允许停止/重启/删除仅本次创建且已核标签资源、合成控制和合成媒体。不得对旧manual/WP06实例操作。媒体内容均合成，无真实模型费用；不得查阅WP06用户验收期间新增的视频或摘要。

独立Testing先设计/诊断，缺陷报告Main并交对应Coding修复；Security在源码稳定后定向只读审门禁、身份/秘密与monitor资源隔离。每项写通过/失败/未执行与真实/模拟层次，必要完整非模型/真实Linux与首次部署闭环不能用编译替代。正式结果完成后Main核当前白名单差集和前端/SQL/历史冻结，提交发布前剩余审批和新实例执行清单，停止等待正式创建/开放授权。
