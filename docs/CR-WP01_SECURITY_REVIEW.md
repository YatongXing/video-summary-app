# CR-WP01：独立安全审查与风险记录

**日期：** 2026-10-30  
**来源：** 独立安全Sub-agent /root/wp01_security（CR-WP01-S3），主Agent按原发现和交叉审核回传整合。  
**性质：** 仅文档/源码安全审查，未运行测试、扫描服务或迁移，未读取真实秘密/视频，无产品验收权限。

## 1. 输入、独立性与威胁边界

S3独立读取CR-001、PRD/AC、ADR-005、架构/工作包/测试规格、C0.1及auth/results/cleanup/deletion_control/processing/preprocess/queue/worker、迁移器、账户停用、对象存储、Compose示例和测试。只读，无写路径；设计输出由主Agent整合，不以S1自称完成视为安全通过。C0.2等整合稿再独立复核，具体参数仍待产品批准。

保护边界：普通用户/管理员通过受支持API不能越权，应用误组装候选/并发/故障/恢复不能重建权限或误删其他任务。可信应用原登记是正常归属权威；不声称抵御任意改DB+宿主root、未隔离启动整机旧快照、已经下载的副本或供应商即时擦除。

## 2. 首轮问题与处理

| ID | 严重度/反例 | 主Agent落实位置/第二轮结论 |
| --- | --- | --- |
| S3-F01 | 高：旧binary不读版本标志，继续生成purpose授权 | D§3/4/5停旧服务/连接、退役旧role、新最小runtime、路由410、恢复epoch/授权终止；最终复核设计关闭。 |
| S3-F02 | 高：父持锁但Popen关FD，父死子活仍生产 | C§3稳定task SH/EX和pass_fds，父不提前LOCK_UN，先排空旧Worker；实际FFmpeg行为留WP04硬门槛。 |
| S3-F03 | 高：旧cleaner claim过期仍在I/O | C§4任务EX覆盖物理I/O，未拿锁不重领，不只凭token；二审基本落实。 |
| S3-F04 | 高：namespace/当前bucket不能证明归属 | C§4/8可信immutable backend/bucket/key/version，错误/矛盾停止，NoSuchBucket不作成功；正常旧登记可核实回填。 |
| S3-F05 | 高：nonce复用/相同真key换ID/恢复旧计数 | AB§1/3独立nonce预留、真实key身份、AAD、恢复换真key；补key注册和生成/退役锁，S3消息确认必要且锁图可行。 |
| S3-F06 | 高：入口Principal和事务旧now放行 | AB§4/7源锁后新语句/真实clock、最终/块复核、actor不反锁，明确在途字节边界；二审基本落实。 |
| S3-F07 | 中：码进URL/错误/SQL日志；失败计数回滚 | AB§5/8 POST body、统一脱敏、no-store、持久独立失败计数；二审基本落实。 |
| S3-F08 | 中：危险CLI或日志失明 | C§5～7不收path/key/bucket/force，独立health和持续信号；二审补F11明确事务/角色。 |
| S3-F09 | 中：online冒充overall、90天删控制 | C§4/8、D§6受控BACKUP与unknown分层，未完成控制单独保留；二审基本落实。 |
| S3-F10 | 中：重新生成/幂等伤新授权 | AB§2/4/5显式operation、expectedID、终止重放409、精确ID删除；二审基本落实。 |
| S3-F11 | 中：retry审计非原子/身份模糊 | C0.3§7受限ops role、回执优先、source→task→job、调度/revision/回执/事件同事务，写失败全回滚且无I/O；S3最终定点确认设计关闭。 |
| S3-F12 | 中：M3被误藏进普通migrate自动切换 | D0.2§2单独维护操作，expected部署/协议/revision与显式授权；010起仅扩结构；待最终复核。 |

“二审基本落实/设计关闭”均不是实现验证，不表示产品批准、测试通过或可以启动WP02。最终复核记录在评审报告统一登记。

## 3. 必须保留的安全条件

- AES-GCM每次加密新nonce，预留独立提交且业务失败不回收；登记至key销毁，退役key fingerprint/身份配置保留，防同真key换ID。nonce ledger无个人/内容，不是视频备份。
- 生成最终key SHARE锁至提交，退役key UPDATE后新语句读refs且不反锁source；nonce独立事务不持锁跨入业务。key变DECRYPT_ONLY后在途预留可完成但不续TTL，RETIRED拒绝。
- 旧初始化role可能高权限/owner：先建立验证独立管理通道再NOLOGIN/断连接，新runtime不读写legacy同意；新版本标志不能约束旧程序。
- 码解析/list/内容每次管理员会话/角色/范围/任务/请求/epoch复核，码和request_id不替代认证；旧同意API拒绝。
- 任务锁文件私有、稳定inode、位于独立控制目录，子进程保留同一FD；新版本协议不能证明不合作旧进程已经停，WP07先排空。
- 存储归属用可信登记及固定后端身份，不简单匹配prefix；原originals/user/importUUID不含task仍可按可信登记核实，不能机械拒绝全部旧资产。
- 部署者root拥有应用外控制力，不冒用普通管理员Cookie。ops环境与正常服务秘密隔离，CLI无任意目标/强制完成，未知/危险只能专项核实修复。
- 任何恢复必须隔离、重放独立删除、终止会话和新旧授权、换真实key/epoch/旧角色，不能先开放再补清理。

## 4. 尚待实现验证和产品审批的风险

1. Linux Docker命名卷flock、实际FFmpeg传FD、子进程崩溃和inode稳定性必须双容器验证；不满足停止该部分设计落地，不能假装有物理停止证明。
2. 最小PG runtime/ops/迁移权限和旧连接撤销需真实隔离角色测试，尤其当前初始化role是否兼owner/唯一维护入口，本轮未查真实DB。
3. 密钥只读注入/轮换/丢失恢复、随机码格式/碰撞窗口、nonce预留与恢复新key需故障验证；本轮未生成或读取真key。
4. Docker健康/本机巡检能否实际被部署者发现须S3-12演示；未部署观察器不能称自动通知。
5. 历史备份/版本/自动快照尚未清点，未批准不删；受控BACKUP未清overall未完成，unknown单列。
6. 源内容无主动备份，升级/故障损失可能不可恢复；Git无内容检查点不替代内容备份，不承诺整机任意回滚后的权限安全。
7. 终态hash有限保留、160位概率边界、在途256KiB块不可追回、容器OS主体不等于可证明Windows逐人身份须在技术选择中明示。

## 5. 独立核验的技术依据

- [Python3.11 subprocess](https://docs.python.org/3.11/library/subprocess.html)：Popen close_fds/pass_fds。
- [Linux flock](https://www.man7.org/linux/man-pages/man2/flock.2.html)：open-file-description继承/关闭，合作锁语义。
- [cryptography AESGCM](https://cryptography.io/en/stable/hazmat/primitives/aead/)：同key nonce不得复用、AAD认证。

仅查询官方公开技术资料，无项目秘密或媒体外发，无模型/付费请求。

## 6. S3最终交接结论

S3最终只读核对C0.3/AB0.3/C0.3/D0.2，确认F01～12落实为可检查设计约束，全部“设计修订关闭”，未发现剩余安全设计阻塞；参数/单部署者身份边界仍待产品审批，真实权限/进程/加密/恢复测试未执行。D§3按建议明确写key仅禁新生成、已有请求依其解密key及epoch，避免误停全部support。此结论不等于产品验收或WP02权限。
