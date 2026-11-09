# CR-WP02：请求结构与隔离验证交付

**日期：** 2026-11-04
**依据：** 已正式验收的WP01 L1.0及用户“现在开始WP02”启动授权。
**状态：** 本包实现与隔离验证完成，提交产品负责人验收；**不代表已验收，不启动WP03**。
**基线：** implementation，HEAD `96b093afed1ef0cb36685ba46325d52c805d612c`，既有文档改动及历史删除保留，无Git暂存/提交/推送。

## 1. 交付与范围

新增[010迁移](../../../backend/app/migrations/010_cr_support_expand.sql)：
- 单support_requests表；UUIDv4、task/user复合RESTRICT外键、三scope、有限UTC时间、严格86400秒、关闭字段一致性。
- 单未关闭请求唯一、队列和任务创建时间索引；到期未关闭行仍占槽，WP03事务先关闭再创建。
- 请求身份/归属/范围/创建和期限不可改，已关闭不能重开或改写；tasks.support_version非负默认0，不变或严格+1的数据库保护。
- 两个普通invoker触发器，不使用SECURITY DEFINER；不自动增业务版本、不撤旧授权/转码、不授生产角色，不建011/crypto/receipt/epoch/清理影子。

本包只提供结构及事务基础。CREATE/REGENERATE服务、409→GET、HTTP身份/CSRF、内容实时24h/范围鉴权等仍由WP03实现；旧API正式失效和业务库权限切换留WP07。

## 2. 实际文件清单

| 责任 | 文件 |
| --- | --- |
| Coding | [010_cr_support_expand.sql](../../../backend/app/migrations/010_cr_support_expand.sql) |
| Testing | [cr_wp02_fixtures.py](../../../backend/tests/cr_wp02_fixtures.py)、[test_cr_support_schema.py](../../../backend/tests/test_cr_support_schema.py)、[test_cr_migrations.py](../../../backend/tests/test_cr_migrations.py)、[test_cr_roles.py](../../../backend/tests/test_cr_roles.py) |
| Main | 本报告及[工作包计划](CR-001_IMPLEMENTATION_PLAN.md)的授权/派发/交接状态 |

001～009、migrate.py、旧test_migrations.py、models.py、现有API/Worker/UI/配置均未改；原S3-10证据保留。无其他新增实现文件。

## 3. 独立测试证据

专用postgres:17-alpine实例，实际PostgreSQL **17.11**；本机回环随机端口，tmpfs数据，无业务卷/旧用户数据。仅合成账户/任务/JSON/权限，显式TEST_DATABASE_URL，不读取.env或业务DATABASE_URL。角色为集群级资源，因此使用专用实例，不能仅以schema隔离代替。

| 执行 | 结果 | 范围 |
| --- | --- | --- |
| 首轮新增测试 | 39 passed / 14.88s | 初始独立验证，不累计到最终数量。 |
| 最终新增测试 | **42 passed / 16.15s，无skip** | 约束/关闭/版本、DST/UUID冲突、多用户/双连接、升级保持/校验、失败重放/并发迁移、两role。 |
| 原只读test_migrations.py | **10 passed / 0.51s** | 原迁移行为在新010存在时兼容。 |
| 角色定点复测 | 1 passed / 0.72s | 新增prosecdef=false断言后的复核，属于42项内重复验证，不额外计数。 |

共**52个不同测试通过**。未发现SQL缺陷，010首次交付至最终测试SHA保持不变。

关键oracle：
- 空库和001～009旧合成库升级，涵盖USER/ADMIN、五任务状态、多purpose/双scope、自授权、配额及合成SQL内容；除新增support_version=0外旧行逐表快照一致，新请求为空，不转码或终止。
- 旧九份SQL冻结SHA与schema_migrations登记一致；临时副本篡改拒绝，不改真实旧资产/登记。
- autocommit逐文件与已有外层事务保存点、失败后可重放、双迁移者只登记一次。
- 真PG deferred constraint trigger在COMMIT拒绝，旧关闭/新请求/任务版本/审计整体回滚；不以仅mock入口异常代替。
- 真实双连接唯一/CAS及多用户并发；FK错配、UUID冲突、关闭不可逆、版本回退/跳号/NULL、DST日历日非86400秒均拒绝。
- 合成maintenance兼owner可迁移/重连，runtime无superuser/DDL/角色管理/SET ROLE逃逸；旧task_consents仅清退DELETE和定位列读取，目的授权查询/创建/更新拒绝；两个guard函数prosecdef=false且非owner更新保护有效。

复现入口（需重新获准建立同类专用实例后注入合成TEST_DATABASE_URL、WP02_ISOLATED_INSTANCE和WP02_TEST_PORT，原实例已回收）：

```powershell
$env:PYTHONPATH = 'backend'
& backend\.venv\Scripts\python.exe -m pytest backend/tests/test_cr_support_schema.py backend/tests/test_cr_migrations.py backend/tests/test_cr_roles.py -q --tb=short
& backend\.venv\Scripts\python.exe -m pytest backend/tests/test_migrations.py -q --tb=short
& backend\.venv\Scripts\python.exe -m pytest backend/tests/test_cr_roles.py -q --tb=short
```

Testing结束后核对UUID测试schema和合成maint/run角色均为空；Main确认CR-WP02容器标签后停止唯一专用容器，--rm及tmpfs随之回收，复核无该容器。未停止业务容器，未删除任何业务卷。

## 4. 派发与审核交接

Main在派发前冻结L1.0字段/三辅助索引/不可变及版本触发器、010范围和隔离边界。

| 任务 | 输入/文件责任 | 交接 |
| --- | --- | --- |
| /root/wp02_coding，CR-WP02-Coding | WP01 L1.0 A/B/契约、计划§3、AC；唯一010写权，不写测试/旧SQL。 | 交付SQL/约束说明与SHA，未自行迁业务库；Main阅读SQL，未发现契约偏差。 |
| /root/wp02_testing，CR-WP02-Testing | 独立读AC/测试规格§7及L1.0；仅四个新测试/夹具。 | 在Main建立的专用实例独立执行、报告52不同测试；补真实COMMIT、DST、多用户和非提权断言，无SQL缺陷，无改Coding文件。 |
| Main/root | 计划/契约、隔离实例/资源、变更范围与证据审核。 | 提供冻结旧九SQL哈希，核验测试方法/SQL/资源回收；不以Coding自检或Agent完成代用户验收。 |

各派发明确ID、输入版本、模块/可写路径、冻结数据/状态、必须任务、禁止事项、CR-AC08/09/10/19/20及T25/26约束部分/T29/32、交付证据、依赖/交接。无并写同文件，无固定第四安全Agent；WP03/04/07按已批准计划定向复核。

## 5. 资产指纹与后续限制

| 资产 | SHA-256 |
| --- | --- |
| 010_cr_support_expand.sql | df119b7fbf4150c480f7bef96c559d777e74082bef6c1de31d3b80ba9cd05d47 |
| cr_wp02_fixtures.py | ffb2311cbe5bcacc27e0092ee4f7100f28cbd7c6bdf08dae63a7037acee1bb4f |
| test_cr_migrations.py | 8cee4c78fe7dd05429776c9ea845ca91fa58363b8b23da30ba383869ef85858f |
| test_cr_roles.py | 192649d00df510e0b0ace9b69a09b86d9b1b3ab78880a94a030ea2aabab58274 |
| test_cr_support_schema.py | 79be45655b09ab5f51591c9c3b31b4e203e73c1a5c36f0330f99ab1da013e4bb |

本包测试只证明DB约束/事务和合成权限基础，不能证明尚未实现的HTTP授权、实时内容访问、Worker停止、恢复门禁或真实旧binary/连接切换。未运行全量后端/浏览器/模型测试，未调用真实Qwen或产生模型费用。

010在现有app.migrate扫描目录；对业务连接运行迁移命令会读取候选010，本轮没有这样执行。真实结构/权限及旧授权切换须WP07专项批准；本包不触及011或M3。后续应用过的010保持checksum冻结，不修改登记规避；结构变化按批准流程处理。

当前无未解决实现缺陷或超范围变更。Main认为具备申请WP02验收条件，最终验收由产品负责人决定；WP03另行授权。

## 6. 最终完整性核对

2026-11-04 Main验证：仅白名单内7个文件（新增010/4测试/本报告，修改计划）；其余105份Git可见既有文件哈希不变，S3-10所列73份受保护源码/配置全部匹配，001～009原文不变。两份本包文档的13处本地链接/锚点、新文件空白格式与Git diff --check通过，无范围外修改。测试专用容器回收已核验，无真实库迁移、M3、视频/备份清理或付费模型调用。上述文档/指纹证据不替代后续业务验收。
