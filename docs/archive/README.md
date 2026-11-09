# 历史文档归档

历史验收结论保留，跨文件链接按新位置调整。现行规范及 WP07-A 正式部署见[文档索引](../README.md)。[原始证据和交付指纹](../baseline/)仍在原位，旧报告中的字面路径记录当时位置，可通过下表定位。

历史归档统一存放在 `docs/archive/`，按类别分目录。`release/` 只存放正式交付包。

## 文件移动映射

### S3

| 原路径 | 归档位置 |
| --- | --- |
| docs/LOCAL_TEST_GUIDE.md | [s3/LOCAL_TEST_GUIDE.md](s3/LOCAL_TEST_GUIDE.md) |
| docs/REGRESSION_BASELINE.md | [s3/REGRESSION_BASELINE.md](s3/REGRESSION_BASELINE.md) |
| docs/S3_11_PREFLIGHT.md | [s3/S3_11_PREFLIGHT.md](s3/S3_11_PREFLIGHT.md) |
| deploy/README.md（S3-12 前） | [s3/DEPLOYMENT_BEFORE_S3_12.md](s3/DEPLOYMENT_BEFORE_S3_12.md) |
| release/work-archive/s3-12-2026-11-09/CLEANUP_REPORT.md | [S3-12 清理与剩余文件报告](s3/S3_12_CLEANUP_REPORT.md) |
| deploy/Apply-S3-11.ps1 | [S3-11 退役入口](s3/retired-deploy/Apply-S3-11.ps1) |
| deploy/compose.s3-11.yaml | [S3-11 退役配方](s3/retired-deploy/compose.s3-11.yaml) |

S3-11 两份部署文件仅供查阅。入口依赖已移除的设置索引。现行更新见 [S3-12 本机运维](../../deploy/README.md)。

### 本机私有归档

S3-12 的 DPAPI 加密包、`manifest.json` 和 `Restore-WorkArchive.ps1` 已从 `release/work-archive/s3-12-2026-11-09` 整组迁至 `docs/archive/work-private/s3-12-2026-11-09`。三份文件的 SHA-256 在迁移前后逐一匹配，已从新位置实际恢复并核对 247 个文件和 35 个目录。私有归档目录由 Git 忽略。清理报告复制时校验一致，当时更新路径引用后、发布脱敏前的 SHA-256 为 59ae58eb2b4d33886da9f4c529b54cb4790f9372e0aa41d3d7836b247ecf90cf。恢复说明见 [S3-12 清理报告](s3/S3_12_CLEANUP_REPORT.md#c-盘-work-工作区)。

### CR

| 原路径 | 归档位置 |
| --- | --- |
| docs/CR-001.md | [cr/CR-001.md](cr/CR-001.md) |
| docs/CR-001_IMPLEMENTATION_PLAN.md | [cr/CR-001_IMPLEMENTATION_PLAN.md](cr/CR-001_IMPLEMENTATION_PLAN.md) |
| docs/CR-001_TEST_PLAN.md | [cr/CR-001_TEST_PLAN.md](cr/CR-001_TEST_PLAN.md) |
| docs/CR-002_IMPLEMENTATION_REPORT.md | [cr/CR-002_IMPLEMENTATION_REPORT.md](cr/CR-002_IMPLEMENTATION_REPORT.md) |
| docs/CR-002_TESTING_REPORT.md | [cr/CR-002_TESTING_REPORT.md](cr/CR-002_TESTING_REPORT.md) |
| docs/CR-002_ACCEPTANCE_ADJUSTMENTS.md | [cr/CR-002_ACCEPTANCE_ADJUSTMENTS.md](cr/CR-002_ACCEPTANCE_ADJUSTMENTS.md) |
| docs/CR-002_ACCEPTANCE_ADJUSTMENTS_TESTING.md | [cr/CR-002_ACCEPTANCE_ADJUSTMENTS_TESTING.md](cr/CR-002_ACCEPTANCE_ADJUSTMENTS_TESTING.md) |

### WP（归入 CR）

下表 15 个文件由 `docs/archive/wp/` 移至 `docs/archive/cr/`，文件名保持原样。

| 原路径 | 归档位置 |
| --- | --- |
| docs/CR-WP01_CONTRACT.md | [wp/CR-WP01_CONTRACT.md](cr/CR-WP01_CONTRACT.md) |
| docs/CR-WP01_DATA_API_DESIGN.md | [wp/CR-WP01_DATA_API_DESIGN.md](cr/CR-WP01_DATA_API_DESIGN.md) |
| docs/CR-WP01_CUTOVER_ROLLBACK.md | [wp/CR-WP01_CUTOVER_ROLLBACK.md](cr/CR-WP01_CUTOVER_ROLLBACK.md) |
| docs/CR-WP01_REVIEW_REPORT.md | [wp/CR-WP01_REVIEW_REPORT.md](cr/CR-WP01_REVIEW_REPORT.md) |
| docs/CR-WP02_VALIDATION_REPORT.md | [wp/CR-WP02_VALIDATION_REPORT.md](cr/CR-WP02_VALIDATION_REPORT.md) |
| docs/CR-WP03_VALIDATION_REPORT.md | [wp/CR-WP03_VALIDATION_REPORT.md](cr/CR-WP03_VALIDATION_REPORT.md) |
| docs/CR-WP04_VALIDATION_REPORT.md | [wp/CR-WP04_VALIDATION_REPORT.md](cr/CR-WP04_VALIDATION_REPORT.md) |
| docs/CR-WP05_VALIDATION_REPORT.md | [wp/CR-WP05_VALIDATION_REPORT.md](cr/CR-WP05_VALIDATION_REPORT.md) |
| docs/CR-WP06_VALIDATION_REPORT.md | [wp/CR-WP06_VALIDATION_REPORT.md](cr/CR-WP06_VALIDATION_REPORT.md) |
| docs/CR-WP06_MANUAL_ACCEPTANCE.md | [wp/CR-WP06_MANUAL_ACCEPTANCE.md](cr/CR-WP06_MANUAL_ACCEPTANCE.md) |
| docs/CR-002_WP06_VALIDATION_REPORT.md | [wp/CR-002_WP06_VALIDATION_REPORT.md](cr/CR-002_WP06_VALIDATION_REPORT.md) |
| docs/CR-002_WP06_MANUAL_ACCEPTANCE.md | [wp/CR-002_WP06_MANUAL_ACCEPTANCE.md](cr/CR-002_WP06_MANUAL_ACCEPTANCE.md) |
| docs/CR-WP07_PREPARATION_REVIEW.md | [wp/CR-WP07_PREPARATION_REVIEW.md](cr/CR-WP07_PREPARATION_REVIEW.md) |
| docs/CR-WP07_EXECUTION_PLAN.md | [wp/CR-WP07_EXECUTION_PLAN.md](cr/CR-WP07_EXECUTION_PLAN.md) |
| docs/CR-WP07A_IMPLEMENTATION_CONTRACT.md | [wp/CR-WP07A_IMPLEMENTATION_CONTRACT.md](cr/CR-WP07A_IMPLEMENTATION_CONTRACT.md) |

WP07-A 实施审批契约仅适用于 2026-11-06 的实现和隔离验证阶段。移动前后原始 SHA-256 均为 6186df41a04e12d19e2a08e66cf5243c7ba585d34642f6c955cbc802f7cf4e81。现行部署与维护见[部署移交](../WP07A_DEPLOYMENT_HANDOFF.md)。既有 WP07 准备审核与选路前方案继续留在本目录。

### 发布证据脱敏

`docs/baseline/` 的 `cr-002-testing.json`、`cr-002-acceptance-testing.json`、`cr-wp07-preparation.json` 和 `cr-wp07a-before.json` 仅替换了本机路径和个人地址。原始字节保存在本机 Git 忽略的 `docs/archive/work-private/release-review-originals/`，供核对历史指纹。

## 待审批删除候选

| 文件 | 删除条件 |
| --- | --- |
| [原 WP06 人工验收指南](cr/CR-WP06_MANUAL_ACCEPTANCE.md) | 旧入口已经退役，可申请移除。该文件仍承载专用状态、样本、人工检查步骤和精确清理顺序。删除前须确认这些历史信息由验收报告和原始记录承接，或明确接受舍弃，并处理历史入链。 |
| [选路前 WP07 执行方案](cr/CR-WP07_EXECUTION_PLAN.md) | 路径 A 已接管现行执行入口。旧实例切换、目标选择和回退边界仍有追溯价值。确认保留所需对照并更新准备审核入链后，可再申请删除。 |
| [旧 Windows 本地指南](s3/LOCAL_TEST_GUIDE.md) | 启停命令与旧部署说明重叠，仍含 S3-10 人工验收方法、合成样本复现和 S3-11 准备记录。整理独有内容及链接后，可申请缩减或删除。 |

## 上一轮 Git 差异记录

移动 23 份，新增本索引，实际删除 0 份。归档文件只改 Markdown 链接目标。原路径中 9 份是已跟踪文件，14 份是未跟踪文件。由于保持暂存区原状，Git 状态将已跟踪旧路径显示为 D，新归档路径显示为 ??。以上映射用于核对移动。

<details>
<summary>移动前后 SHA-256</summary>

```text
docs/LOCAL_TEST_GUIDE.md
  before 8fac7e0fffba39bf26e135a00c35c8c891c7e2408d63f104d2cf947eb36debf2
  after  b1b21b0450bc72d98bec2e482527c47387a2ed413e6d78543c2205f5c659cb04
docs/REGRESSION_BASELINE.md
  before 0eb2693b80bf8e9bffe575f6ab59bd4a80625264297636ced469bc7b3ae7df6f
  after  23e9fb319bf376add4a12886e6e8f6b67b513914f9e0a9e7f5cf8d5b90e27d5b
docs/CR-001.md
  before db698f116adf0c39dfd69136f9280b8a978ee32818b39fb227963b970a7f44c9
  after  4fbc253bce8c328e5e8393553af27ce7ac0561dc1f9766147b9614d5fea1bd42
docs/CR-001_IMPLEMENTATION_PLAN.md
  before 14e34f24348097c803382849dcf7f849bbf597163384bf308e65314244a8177c
  after  b68afb382f0b58ac96d2a14553820aa05ae1c0140b55826aed173da8c253a1ae
docs/CR-001_TEST_PLAN.md
  before 06a14e5a8033123f009f17f20b03220f43e932e4516184fae9bcc13e6eaf8ece
  after  894eb45fdc2b4a1a54dad2f1097837f2b5aaba5dce979ac379d4a6e2bb98ac0a
docs/CR-002_IMPLEMENTATION_REPORT.md
  before 195b61aa2d0e93e8e62fc4ca7d0c39d4be1a27381f943ae377bfe0851fc4631a
  after  15ac185a6734cd81111cd587836aa0590772ab651bbf70d3e0ee1b4e22018ab1
docs/CR-002_TESTING_REPORT.md
  before c53100c0d78a2836fb7385d1753580aea7e2515cb506385200b362c608f130a9
  after  c53100c0d78a2836fb7385d1753580aea7e2515cb506385200b362c608f130a9
docs/CR-002_ACCEPTANCE_ADJUSTMENTS.md
  before ad090a12e4aedf96c33dbce41179f63c86d5684cc8379b3ed6d9d44d594d334f
  after  233ef4737eb7efd9318b7131be48b6cf084f8ee8e0959bbc725b47874a6099c9
docs/CR-002_ACCEPTANCE_ADJUSTMENTS_TESTING.md
  before ad67617d99221e34e2cf43ac259d3ea37ff629cad1057bcc7adb984ee465b405
  after  ad67617d99221e34e2cf43ac259d3ea37ff629cad1057bcc7adb984ee465b405
docs/CR-WP01_CONTRACT.md
  before 0196cca184c886ea6999096f207bd8cd98bee597061798c005b0481c11aa3277
  after  d8c50e6273e1d4ebc0beeece7ebc216cf14d8ad37e30a2b28501aa48e1700983
docs/CR-WP01_DATA_API_DESIGN.md
  before ba64acb2a2eea3dfb3cbabdb5a7f71919e86ebb378d4f763d6cc2d8f776efa68
  after  b56c32092cc8120c97f052b0914adb9f5be280f9a242c61aa682676f39dda9fe
docs/CR-WP01_CUTOVER_ROLLBACK.md
  before de074a1595d07878ad610f2c1459d946d6811bdba79062ff29a2d0f0a2aab6e7
  after  21acc157aa5657f8972fa84e2e18ce91039cf10d37f619a5743c9a2ec1ca1a39
docs/CR-WP01_REVIEW_REPORT.md
  before 1c6c3dfb4be988b934b2ff58973f4156ea0b2edeeae577d6a75479327df9fc6b
  after  a6e2c0534f54c75ad918e9c62b503b966f55aca08ae6e7066e320730809eb73c
docs/CR-WP02_VALIDATION_REPORT.md
  before 094abba2a08a9fc4f49418615de24caccc835f9905f04cfac3a26c8cae2cafc7
  after  35c8809674910cde24893bea5f91a55a270fe6a7e14b3755b69fd50c51c70d84
docs/CR-WP03_VALIDATION_REPORT.md
  before ebb484ddeade284d74d89b3fece653e8941c111760b633f398aa506a2f0d7612
  after  be32cb95eec90e371b9f9ad7eeb6676579d5719541d47c1eb7cd1730a4200c8d
docs/CR-WP04_VALIDATION_REPORT.md
  before 7a3da72ffed06fa88b0ae084798e6dd5d313cb439cfc7d0ce367055f8187fe6d
  after  dd8689f9859600d65e33ba4a1b3003e82fa6a6ec82af210fcfc98f7d36be5a2c
docs/CR-WP05_VALIDATION_REPORT.md
  before 366b3e8d4419c1bce869363a9ebaac080ed75f4d76dc827df1361db433422af7
  after  1f87a3960be2a60e827f6467fccb82ea43c441885988d33fd881195bc2f3be86
docs/CR-WP06_VALIDATION_REPORT.md
  before cb5a5a9d55bfeeb4121a5adc8f077c80c90ec44248e57f1ca373e1639e5cdb82
  after  3a1e34056af0758421b1fd0d92142419e3cfcd0f046242e16accccf0364cd893
docs/CR-WP06_MANUAL_ACCEPTANCE.md
  before e895854b37180fcc9cbb0f41ee8431fb3f5cb7eecbb6f02834f99477317e698d
  after  acc6cd7fddf342a4a4fcaf9386527caf09113f08186d80c641bca0b3034abe13
docs/CR-002_WP06_VALIDATION_REPORT.md
  before 5ee98cd56037191c50d7e6629a036f91ace1f0ec9f5f89265d035a2651839a43
  after  e154501c469f72126be905bfd44bc354f89a82bf698375b3b461b6025c239a99
docs/CR-002_WP06_MANUAL_ACCEPTANCE.md
  before 25e53833d2648cda179fec96a733eeff1e5f16ac997207082514bee33d78e7a2
  after  c3a92524b2c3ed56e83aaac1f3795da6d628365478ae0bd278c14413d01ccc30
docs/CR-WP07_PREPARATION_REVIEW.md
  before 924a005bcc39f8d66d2aa6c92d5d7130c21a486517fa90a2726ede58d7091e72
  after  8bab72a771f030bac6b69b23c335e06588b900d89c2844de531a947e6cd387f9
docs/CR-WP07_EXECUTION_PLAN.md
  before 376177e317e9509b09cacc1609da062488aacc5d0ce8cb041b75558d793b9742
  after  e16a8b20f725eae9011c7cc40dc17f844e1b59ca635c5f81e63441d44187a1d5
```

</details>
