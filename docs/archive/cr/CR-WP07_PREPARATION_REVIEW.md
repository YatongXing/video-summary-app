# 新版 CR-WP07 准备审核与目标环境清单

日期：2026-11-06。**CR-002及新版WP06已由负责人正式验收，最新验收页面为批准版本。仅WP07准备和切换前审核获授权；正式部署、迁移、失权及开放未授权。**

## 审核结论

准备审核完成，建议停在“目标确认及缺口审批”，**目前不准入正式切换**。

本机只发现5个运行中的WP06合成验收容器；4个旧manual容器已停止，另保留两组历史卷。没有确认任何运行中的旧业务服务，也没有确认正式目标库。不能把“没看到运行旧服务”写成“不存在旧业务数据”，不能把验收实例当生产目标。建议先决定首次部署或指定旧实例接管，再针对目标补齐实证。

另有三个明确的正式开放前条件：持久维护门禁尚未实现；正式运行/维护身份与部署配置尚未落实；实际故障巡检责任人/渠道尚未确认。它们是部署准入条件，不改变已验收的产品页面或权限模型。本轮不修代码、不改配置、不启动这些措施。应用ADMIN与受信部署维护owner是不同身份；owner可直接SQL读取数据，不能把应用权限隔离解释为抵御数据库所有者。

## 一、版本、Git和指纹

- 路径：`E:\GitHub\video-summary-app`；分支`implementation`。
- HEAD：`96b093afed1ef0cb36685ba46325d52c805d612c`。
- 准备报告创建前：53份tracked文件变更（含删除）、49份untracked、暂存区0。逐路径状态保存在[盘点证据](../../baseline/cr-wp07-preparation.json)。历史变更原位保留，没有stash/reset/clean/checkout/add/commit/push。
- 已验收WP02～CR-002内容含大量未提交与未跟踪文件，010/011也未跟踪。HEAD不能单独代表已批准版本；直接按HEAD打包会遗漏关键交付。
- 本次记录101份软件文件SHA，包括后端/前端源码、测试/工具、依赖及部署模板、当前前端dist构建。已批准页面以当前文件与构建指纹为准，未重建或修改页面。
- 001～011本地SQL及33项既有冻结迁移/报告/原始证据/核心删除保障与上一轮冻结记录一致。本轮只读Git时设置GIT_OPTIONAL_LOCKS=0；LF/CRLF提示不作为归一化或改字节的授权。

## 二、实际环境分层

范围：本Windows主机、default和desktop-linux两个本地Docker上下文（返回相同9个容器ID）、运行中的docker-desktop WSL。没有提供或登录远程服务器，其他主机/云资源不在已核实范围。

| 环境 | 当前事实 | 角色与处置边界 |
|---|---|---|
| `vsa-wp06-172ff1a984` | 5容器运行，标签CR-WP06/synthetic_only=true；UI 127.0.0.1:50658、API:50657、S3:50656、PG:50655 | 已批准页面的合成验收环境，原样保留；不是正式目标 |
| `video-summary-app-manual` | db/api/worker/object-store共4容器均exited，PID0，restart policy=no | 历史部署候选，是否承载过业务未知；未启动、未读数据卷、未失权 |
| `video-summary-app_*` | 5个命名卷，无当前关联容器 | 来源/内容/业务属性未知；只登记，不挂载、不删除、不当空卷 |
| 正式业务目标 | 主机/实例、DB、bucket、控制目录均未由负责人指定 | 目标实际迁移、账号、会话、连接、资产状态为未核实 |

### 验收实例的位置

- DB：`vsa-wp06-172ff1a984-db:5432/wp06_test`；`vsa-wp06-172ff1a984-pg`挂至`/var/lib/postgresql/data`。
- S3：`http://vsa-wp06-172ff1a984-s3:9000`，bucket=`wp06-synthetic`；`vsa-wp06-172ff1a984-s3data`挂至`/data`。没有枚举或读取对象。
- 控制/暂存/处理：`vsa-wp06-172ff1a984-control`挂至`/control`，子目录`journal`、`staging`、`processing`。API和两个Worker只读绑定当前`backend/app`。
- 运行软件为`video-summary-app-test:latest`且依赖主机源码绑定；不能直接把这个可变测试标签认定为正式不可变发布镜像。镜像ID、挂载和端口详见JSON。
- MODEL_CALLS_ENABLED=false、offline替身、N=2。验收API/Worker使用测试UID0；正式Dockerfile的appuser及目标卷权限尚需实际部署验证，容器UID也不等于真人维护身份。没有启用真实模型或读取模型Key。

### 历史资源的位置

`video-summary-app-manual`的5卷：postgres-data、object-data、deletion-control、input-staging、worker-processing，均带同名前缀。另一组同用途5卷带`video-summary-app_`前缀。本机共13卷（历史10+验收3），全部只核对Docker元数据。

旧DB卷`video-summary-app-manual_postgres-data`及`video-summary-app_postgres-data`的实际版本、内容和账户权限未知。启动PG即可能执行恢复写入，不以“之后只做SELECT”为理由启动旧库；需要负责人先确认目标及单独授权相应操作。

根目录真实.env存在，但本轮未打开；deploy/.env未发现。旧容器标签指向deploy/.env.example不证明其从未承载业务，也不证明模板等于实际正式配置。

## 三、运行进程、连接与排空检查

- 运行的项目API和双Worker仅属于WP06验收实例；Windows只发现该实例的Python代理，监听50655～50658，未见项目8000/5173/5432/9000旧入口监听。
- 当前容器进程与docker-desktop Linux宿主进程快照未见FFmpeg/ffprobe，Windows亦未见；这是时点快照，不是正式窗口的排空证明。没有发送任何停止信号。
- 4个旧容器停止、PID0，可说明它们当时未在运行；不能证明旧凭据失效、其他主机无服务或卷内无数据。
- 验收PG的pg_stat_activity只采集database/role/state/backend_type等及计数，不读SQL正文。快照仅见本次盘点连接；Worker/API短连接可能在采样间隙发生，不能据此声称无在途请求。
- 正式目标、旧连接池/长事务/其他使用者及旧凭据仍未知。正式窗口必须重新核对容器ID、进程树、连接角色和安全边界，不能按本次PID执行终止。

## 四、迁移、角色与维护检查结果

| 项目 | 实测或源码结论 | 正式准入判断 |
|---|---|---|
| 本地001～011 | 11份SQL SHA冻结匹配 | 可作为候选迁移资产，未执行迁移 |
| 验收库schema_migrations | PG17.11，001～011完整登记且全部SHA与本地相同；查询事务READ ONLY并ROLLBACK | 仅证明验收库；不能推断旧卷或正式目标状态 |
| 正式/停止旧库版本 | 未连接、未启动 | 未核实；不得假定已到009或011 |
| 验收runtime | wp06_runtime非superuser/非owner、无public CREATE、不属于maint；可真实只读连接 | 正式角色仍待指定与核验 |
| 旧consent ACL | runtime禁止全表/目的字段SELECT、INSERT、UPDATE，保留task_id读取和DELETE | 验收最小清退证明，不是正式失权证明 |
| 验收support ACL | runtime仍有SELECT/INSERT/UPDATE | 应在正式授权配置中禁INSERT、缩到必要关闭/清退列；不能全部撤权破坏legacy_support |
| 验收maint | wp06_maint为测试初始化superuser/CREATEROLE/CREATEDB/BYPASSRLS及表owner | 不能作为正式非superuser维护角色隔离证明 |
| Compose模板 | PG_USER同时用于PG初始化和API/Worker DSN | 首次初始化可能令服务继承初始高权限账号，需获批后分开配置，不能直接照抄上线 |
| 维护凭据注入 | 当前验收API/Worker无MAINTENANCE_DATABASE_URL键，维护与运行身份分开使用 | 正式秘密渠道、重连及不继承/SET ROLE/函数旁路仍待核验 |
| 控制身份 | 验收journal identity与DB一致、producer-ready身份一致；loop/I/O进度文件有近期时间 | 仅验收环境有效，不能搬作正式旧生产者排空证明 |
| 持久维护门禁 | 源码/部署未找到maintenance.required的创建、清除及统一检查实现 | 缺口；旧文档是要求，不能宣称现已实现 |
| 维护CLI | status/health/retry及受控事务存在；deletion_control check内部会重放 | 本轮未运行check/restore/retry，避免把有写入的命令当只读审核 |
| 最低外部巡检 | 未发现相关Windows计划任务/服务；验收容器无配置Docker healthcheck/自动重启策略 | 现有ready及CLI不等于实际观察器；正式责任人/渠道/实跑证明待补 |

验收库汇总：旧有效consent=0、未关闭support=0、登记BACKUP=0，清理3个COMPLETED、无清理告警和待清暂存。这不是历史10卷/其他基础设施没有副本的证明。

## 五、风险和准入建议

1. **目标未确认**：先选择首次部署或指定旧系统接管。当前不能执行无目标的迁移、旧授权终止或凭据失权。
2. **门禁缺口**：建议另授最小持久门禁实现/隔离验证；若用部署层持久维护隔离替代须明确批准并证明覆盖重启/流/Worker。不能把producer-ready、身份校验或关闭网页当维护门禁。
3. **正式身份未落地**：另授初始化/维护/运行身份和配置收口，先证明维护重连，再使旧服务身份失权，不能锁死唯一维护通道或影响共享账户的其他系统。
4. **版本未形成发布单元**：已批准的是当前工作树与dist；应按无内容白名单生成不可变发布资料，含未跟踪010/011和新模块；不执行仅HEAD归档或未经授权Git保存。
5. **遗留资产未知**：两组历史卷保持隔离，BACKUP/基础设施快照需清点和专项审批，未知不改成功，不建立新的含内容快照来回退。
6. **业务开放范围**：当前Worker只支持offline/disabled，真实模型尚未授权；WP07不能声称已交付真实模型质量或暗中开启计费功能。

新版[执行与验证方案](CR-WP07_EXECUTION_PLAN.md)列明步骤、所需权限、失败处置和审批点。建议本轮准备审核交付后停止，等待负责人确认目标实例、数据库、操作范围及正式切换/所需补充实现授权。

## 本轮实际操作边界

仅新增本报告、执行方案和脱敏盘点JSON。没有改源码/配置/SQL/已批准页面，没有Git写操作、部署构建、启停重启、迁移、GRANT/REVOKE、密码读取/轮换、会话撤销、对象列举/正文读取、删除重放或历史卷处置。只对已知验收PG执行READ ONLY catalog/汇总查询，未连接未知业务库。本次为Main只读准备审核，不声称新增独立Security实跑或正式环境验收。
