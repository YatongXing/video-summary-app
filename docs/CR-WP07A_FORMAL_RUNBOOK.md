# CR-WP07-A 正式创建与开放清单（待授权，不执行）

WP07-A 隔离验证已完成。本清单用于获批后的全新 Windows Docker 实例。真实模型关闭，发布范围限 IPv4 localhost，所有正式数据和控制卷从空环境初始化。

## 1. 审批后才准备正式参数

拟用项目 `vsa-local`、本地入口 `http://127.0.0.1:58080`、数据库 `video_summary`、私有新桶 `video-summary-private`。负责人确认项目名、空闲端口、镜像及SHA、部署者、一次性初始化/维护/runtime凭据的保管人，以及正式通知渠道、接收人和巡检启动方式。三类数据库密码独立生成，使用便于DSN安全插入的随机十六进制文本；不复用示例值或同一密码。对象存储也使用新凭据。私密env文件放在仓库外，限制Windows文件访问，不打印完整Compose config、DSN或密钥。

从 deploy/wp07.env.example 准备独立参数。最终实际运行的 PostgreSQL 与 RustFS 镜像仍在本机；API 和 Web 测试镜像随隔离资源已回收。正式创建前，先按冻结前后清单复制软件文件和已批准 dist 到专用验证目录，排除实际 env、秘密、用户文件和旧卷。由部署者在该目录重建候选镜像，在独立合成项目复验。可在隔离验证目录运行以下命令。

    python backend/tools/wp07a_compose_verify.py run

该脚本使用新的合成数据库和卷，验证真实配方及浏览器，并只回收本次容器、网络、卷和临时配置。使用验证目录可保留本轮证据原件。记录候选实际运行的 API、Worker、Web、PostgreSQL 和 RustFS 镜像 ID；专门保留通过验证的候选镜像标签。对照本轮证据核查源码、迁移和前端指纹。正式环境的 WP07_APP_IMAGE 和 WP07_WEB_IMAGE 指向已验证且专用的本地标签；启动前后比对实际镜像 ID，禁止用标签或构建成功替代验证。不要把合成标志或合成账号带入正式实例。

## 2. 创建、发布和开门审批

下列PowerShell示例中的env路径需替换为经批准的实际私密路径。正式项目名/端口及资源全部确认为新后执行；不可直接使用样例env创建。用只显示验证结果的 `config --quiet` 检查参数，不回显展开配置。

```powershell
$Wp07ComposeArgs = @('--project-name','vsa-local','--env-file','<已批准的私密文件>','-f','E:\GitHub\video-summary-app\deploy\compose.wp07.yaml')
& docker compose @Wp07ComposeArgs config --quiet
```

1. **启动前核对。** 核对Docker Desktop当前为Linux容器模式，确认项目容器/网络/五卷均不存在，拟发布端口空闲；确认没有对旧10卷和WP06卷的引用。核对非root镜像、Linux命名卷及原001～011校验。只允许发布web到`127.0.0.1`；API/PG/S3不得发布宿主端口。
2. **仅在创建批准后执行。** 依序启动新的DB/S3并等待依赖健康、初始化新卷权限、运行一次性初始化。任何命令非零都停在当步；不得在失败后通过清空、删卷或复用旧数据来让下一步通过。

```powershell
& docker compose @Wp07ComposeArgs up -d --wait --pull never db object-store
& docker compose @Wp07ComposeArgs --profile initialize run --rm --no-deps volume-init
& docker compose @Wp07ComposeArgs --profile initialize run --rm --no-deps initialize
```

3. **检查CLOSED。** 查看无内容迁移清单和实际表owner/runtime权限；确认独立控制身份、producer-ready、CLOSED。旧服务/旧目的授权/旧凭据对新实例不适用，不执行模拟撤权。启动非root API/Worker但不创建web；gate未开时不要用`up --wait`等待业务健康。读`maintenance status`，确认live可诊断、ready与业务受阻，无模型调用。

```powershell
& docker compose @Wp07ComposeArgs up -d --no-deps --no-build --pull never api worker
& docker compose @Wp07ComposeArgs --profile maintenance run --rm --no-deps maintenance python -m app.maintenance status
```

4. **准备巡检。** 在不发布web的CLOSED阶段准备非秘密state和通知配置。正式渠道、接收人、周期和启动方式待负责人确认；本轮文件接收端只验证合成通知。Windows模板不安装任务或发送外部消息。确认实际容器名称后，state 写入 package=CR-WP07-A、id=正式项目名、synthetic_only=false、formal_target_confirmed=true、api_url=实际 IPv4 本机 URL、api_path_prefix=/api，以及 containers 中实际的 api、web、db、s3 和 workers 列表。事件文件和相邻状态文件均放在仓库外；独立事件转发或 Windows 自动启动按审批执行。
5. **等负责人确认localhost发布和OPEN。** 经批准后先在CLOSED下仅启动web，核对本实例标签、容器身份、实际loopback绑定及`/api/health/live`；ready应仍拒绝。把实际web端口和`/api`写入state，确认巡检确实绑定本实例且不会将预期维护误报为可用。通知渠道或控制检查未就绪，保持CLOSED，不开业务。只有再确认OPEN范围后才由独立维护通道开门。若另批内部合成试运行，不用正式用户内容跑故障测试。

```powershell
& docker compose @Wp07ComposeArgs up -d --no-deps --no-build --pull never web
# 核对实际端口、巡检和CLOSED结果；下条需业务开放授权。
& docker compose @Wp07ComposeArgs --profile maintenance run --rm --no-deps maintenance python -m app.maintenance open
```

6. **验收与移交。** 另批应用 ADMIN 创建方式。经许可用合成账号与素材检查 USER/ADMIN 本人正常、跨用户拒绝、无内容元数据、视频 Range/seek、双语、队列/配额/重试及删除；走正常删除流程确认合成素材无残留。移交不含用户内容的基线：源码/迁移/前端及实际镜像指纹、DB/schema/桶/五卷位置、角色归属与非 root UID、门禁/巡检状态、删除清理结果和责任人。角色秘密与业务数据单独保管；基线不包含私密配置、账号令牌、媒体、摘要或数据库/存储备份。S3-11 真实模型继续关闭。

## 3. 失败收口与保留

故障时由受信维护通道置CLOSED并停止已确认的新web；若DB/控制不可用导致CLI失败，先保持该新入口停止，按新实例清单安全排空API/Worker，不以修改文件或无凭据强行open替代。用户数据不自动删除，不通过重启自动OPEN。依赖恢复后复核journal/身份/producer-ready、失败任务与清理位置，处理已启动I/O/FFmpeg，确认可恢复后再申请open。

未知状态不重建空控制；不降级为旧排障实现，不改校验、不导出包含正文的整库备份。新实例只在验证通过且身份/版本匹配时前滚或回到兼容镜像。停止时保留卷，不执行`down --volumes`。历史容器与卷后续处置见本地部署方案，须另行确认归属、范围和方法。


## 4. 历史资源的独立处置清单

| 资源 | 标记与隔离 | 下一阶段另批内容 |
|---|---|---|
| `video-summary-app-manual` 的 db/api/worker/object-store 四容器 | 第一批已按完整 ID 回收四个容器 | 旧 manual 专用网络与既有镜像保留 |
| `video-summary-app-manual_` 与 `video-summary-app_` 两组历史卷 | 先回收四个空暂存或处理卷，后按负责人明确指示逐个删除剩余六个卷 | 两组历史数据卷现已全部移除。见本节最终处置记录 |
| 原 `vsa-wp06-172ff1a984` 五容器及三卷 | 2026-11-07 已按第二批授权回收 | 专用网络与临时素材一并清理，保留停止状态记录 |

此处记录已执行的本机处置范围。网络、镜像及尚未核实的外部副本均按各自范围管理，创建新实例的批准不包含后续历史处置。

### 2026-11-07 处置记录

第一批已按完整 ID 删除四个已退出的 manual 容器。两组历史卷共十个仍在册。

第二批 WP06 处置暂停。专用 state、目录归属标记、五个容器 ID、三个卷、挂载及专用网络标签均已核对。WP06 的 API 日志显示，在三项已验证的合成样本之后还有四次成功导入。人工验收截图中的游戏录像任务完整 ID 与该 API 的访问记录匹配，且未列入已验证合成样本。该任务有 HTTP 202 删除受理记录。先前的只读数据库汇总记录三项清理完成，但没有逐项关联证据可用来确认额外素材的来源、现存副本或删除控制均已收尾。

保留 WP06 五个容器、三个卷、专用网络及临时目录。清理前须确认额外上传的来源、处置权限以及各自的数据保留和删除控制状态。此轮未启动容器、挂载卷或调用专用 stop。

负责人随后确认四次额外上传均可直接处置。 已核对此前的三项删除完成汇总，并检查从该汇总到容器退出的 API 和 Worker 日志，未发现新的成功上传或删除，也未见待处理清理信号。随后执行原 wp06_environment.py stop，退出码为 0。复核确认五个 WP06 容器、三个卷和专用网络已回收，临时目录仅剩 phase 为 stopped 的 state.json。最终 Docker 清单为 0 个容器、10 个历史卷，历史卷元数据及其他网络和镜像与清理前一致。

### 2026-11-07 第三批历史卷处置

Docker 本地卷以完整卷名标识。第三批开始时核对了冻结清单中的 10 个卷，Compose 归属及用途标签均相符，没有容器引用。隔离探测只统计只读挂载内的目录项和控制文件名模式。两组旧 API 和 Worker 镜像的暂存、处理及清理源码中未发现根目录设备或 inode 身份校验。旧服务和数据库均未启动。

| 完整卷名 | 结果 | 依据 |
|---|---|---|
| video-summary-app-manual_input-staging | 已删除 | 无任何目录项，0B，无引用 |
| video-summary-app-manual_worker-processing | 已删除 | 无任何目录项，0B，无引用 |
| video-summary-app_input-staging | 已删除 | 无任何目录项，0B，无引用 |
| video-summary-app_worker-processing | 已删除 | 无任何目录项，0B，无引用 |
| video-summary-app-manual_object-data | 保留 | 非空，数据归属和副本清单未核实 |
| video-summary-app_object-data | 保留 | 非空，数据归属和副本清单未核实 |
| video-summary-app-manual_postgres-data | 保留 | 已初始化，审计期限和删除任务状态未核实 |
| video-summary-app_postgres-data | 保留 | 已初始化，审计期限和删除任务状态未核实 |
| video-summary-app-manual_deletion-control | 保留 | 有部署身份标记和四个符合意图命名规则的记录，尚未与数据库核对完成状态 |
| video-summary-app_deletion-control | 保留 | 只见部署身份标记，与数据库及删除控制的关联仍待核实 |

30 天内容期限按任务创建时间判定，90 天审计期限按事件时间判定。卷创建时间不足以判断其中的数据是否到期，未完成删除控制保留到完成或专项解决。第三批逐个执行四次普通卷删除，最终为 0 个容器、6 个历史卷。保留卷的创建时间和标签均与删除前一致，网络和镜像清单一致。

### 2026-11-07 剩余六卷最终处置

负责人明确要求直接删除剩余六个历史卷，结束此前的数据保留核查。执行前逐项确认了原卷名、创建时间、项目和用途标签、零容器引用。使用普通卷删除命令逐个移除两个项目各自的 object-data、postgres-data 和 deletion-control，共六个卷。

清理后 Docker 清单为 0 个容器、0 个卷。网络和镜像清单与删除前一致，旧 manual 专用网络保留。本次未启动旧数据库、复制业务数据或创建副本。此前待核的审计期限和删除作业状态未被标记为验证通过。
