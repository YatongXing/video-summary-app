# CR-WP07-A 独立安全复核

日期：2026-11-06。独立复核任务 `wp07a_resume_security` 仅审源码和测试断言，未操作Docker、Git、实际数据或秘密。Main将其原结论及所审版本登记于此；动态验证另见独立Testing报告。

**结论：对最终复核的G-01～G-03范围，未发现仍未解决的具体阻断项。此结论不等于正式实例准入授权。**

| 核查点 | 发现与复核结果 |
|---|---|
| G01 已进入的业务事务 | 取消、重试、发布、搜索计数、清理确认/完成在最后状态处理后、事务提交前复核门禁。复核涵盖门禁关闭导致回滚及必要清退定位不被遗漏。 |
| G01 自动与离线重放 | 自动重放及journal裁剪仍需门禁；裁剪在入口及unlink前检查。严格模式离线check/restore通过维护DSN验证真实表owner和匹配的已有控制身份，并在同一连接执行。OPEN在重放提交后写入；正常运行无HTTP、ADMIN或普通运行身份开门旁路。 |
| G01 启动和内容流 | Worker在启动重放遇维护关门时进入已有等待循环，不把该状态当致命启动失败；真实journal错误仍拒业务。API检查状态、请求/响应边界和流后续块，I/O中止保留资源释放。 |
| G02 同密码隔离缺口 | 不同角色若复用同一解码密码，runtime持有的密码可登录维护/初始角色。初始化配置现先拒绝重复解码秘密，再发生任何写入。新空库/新桶、001～011、非root与维护owner分离由独立环境验证。 |
| G03 巡检超时与地址绑定 | 从多个独立超时累加改为30秒单轮预算，子探测按剩余预算，循环扣除本轮耗时；超时记录固定分类并保持后续巡检。HTTP按实际已验证的127.0.0.1绑定连接，localhost不能转去同端口的其他IPv6服务。S3以运行身份做有界head_bucket，不读对象正文。 |
| 通知、旧实例与范围 | 只写本地测试通知，无真实收件人、外部通知和计划任务安装。新模板独立资源及端口，不授权正式创建或开放。旧资源保留及实际网络绑定由Main/Testing记录。 |

复核边界为代码定义的请求、续流、事务提交和I/O检查点；已送达字节与已开始的单次系统调用不作可追回保证。正式通知、实际目标资源和开放授权仍须负责人决定。

## 复核版本 SHA-256

- `backend/app/maintenance.py`：`0df0adbeae0591f40c7e82eb3c663613c23f27ca97a67eb5fb46bd4037738088`
- `backend/app/deletion_control.py`：`9b078d26197c2007a3dbde55220e346912ca3caed8c44d5fa474ffd654ae6f0c`
- `backend/app/worker.py`：`d757f1362a6c4e4ebc6679e18ee697234e8308194dbc2453e4eddf656ea440e2`
- `backend/app/tasks.py`：`21258bb44dcebafd4590bec32f03f3de504e711847893953611f4f8865c23c6d`
- `backend/app/auth.py`：`66c000536125ea32cd6b437fcf5c21821439cfd1833ed90a2fba986e3036fec7`
- `backend/app/cleanup.py`：`45bcb1a366b433d2668bd6b75235315f7d6847380fa5f278d389a91119eba1f9`
- `backend/app/deployment.py`：`40e74ff025d895d183b106bb66da172d7748369eefdac09afeb69a57d47da20d`
- `backend/tools/wp07_monitor.py`：`62a27df4cda572b718dac6d599cf4df54159d25e5d2ef288813be278b124cfe4`
- `deploy/compose.wp07.yaml`：`e44683998b7421e8def837d3de79bcc9ec0c9ed090ca5c119a126bf652965e6e`
- `deploy/wp07_web.py`：`891b2bad2445865546812cd65464a99efc40e91c5aa6080b82989545a1eab230`
- `backend/app/ingest.py`：`78a2112d295c38e6515c42f876bc9d7c3ee874ae97fab87516ad01109fdefbfe`

复核末次 API 启动修补：启动重放只捕获 MaintenanceClosed，保留存活检查；其余 JournalError 继续使启动失败。正常自动重放与 HTTP 门禁未增加例外。

## 音频 I/O 定点复核（2026-11-07）

`backend/app/processing.py` SHA-256：`36a57f4cf2fe02acd969a658bd78e12da5a03e4f77a6cfbf3de7787811685af1`。移除 WP07-A 新增的重复租约刷新后，打开、读取前后和进入上下文仍检查可信维护状态、数据库部署身份和本地取消。打开后的检查失败或读取异常均关闭句柄；原适配器逐块检查、Worker 心跳和队列提交前后的所有权/代次检查仍有效。独立复核未发现阻断项；本段仅为只读源码证据。
