# 构建与发布

## 本地内部包

先同步版本、变更记录和展示页，执行回归及原生探针。不覆盖正在运行的应用，不把旧版或中断的观察算作新包通过。

```bash
bash scripts/test.sh
bash scripts/test-native.sh
TGA_RELEASE=1 TGA_OUTPUT_ROOT="$PWD/output/release-preview" bash macos/scripts/build.sh
python3 macos/scripts/smoke.py output/release-preview/通讯助手.app/Contents/Resources/Engine/tgengine
codesign --verify --deep --strict output/release-preview/通讯助手.app
hdiutil verify output/release-preview/通讯助手-0.6.1-arm64.dmg
python3 macos/scripts/check_installer.py output/release-preview/CommunicationsAssistant-0.6.1-macOS-arm64.pkg output/release-preview/通讯助手.app
```

API 配置仅用于签名前生成资源，不加入 Git。发布说明须写明 TEST ONLY、临时签名、实际验证环境及待验收范围；正式产品 API 尚未就绪。

构建默认生成 PKG、DMG、ZIP；PKG 是网站的首选入口。已有验收通过的应用可单独运行 `bash macos/scripts/package_installer.sh /绝对路径/通讯助手.app`，随后执行 `check_installer.py` 验证载荷与原应用一致，不需要重签应用或替换正在运行的副本。安装器没有安装脚本，不触碰用户资料、不下载依赖、不自动启动应用；系统安装器负责固定目录与升级检查。当前只生成未签名、未公证的测试安装器。

提交 main，创建与包版本一致的 v0.6.1 标签，在公开 Release 上传 PKG、DMG、ZIP、SHA256SUMS、最新中文使用说明、发布元数据和离线展示页。下载无需 GitHub 登录，标签必须指向实际应用代码。多行发布说明使用文件传入 CLI。

对已有 v0.3.3 补充 PKG 时不移动标签、不重建 DMG/ZIP；保留原 `source_commit`，另记 `installer_commit`、`website_commit` 及实际验证范围。安装交付脚本以该提交为复现依据，不能假称原应用标签已含后补脚本。

## GitHub 工作流

ci.yml 在提交/PR 时跑离线测试、源码和展示页检查及无凭据的开发包构建。release.yml 仅手动运行，要求已有 main 历史内的同版本标签，且与 Info.plist 一致；没有 TELEGRAM_API_CONFIG Secret 时停止生成可连接包。

需要云端生成可连接包的维护者应自行配置仓库 Secret TELEGRAM_API_CONFIG。当前建立仓库过程不自动上传本机 API 配置，也不在 PR 使用发布凭据。

## 展示页

docs 只包含可展示的 HTML/CSS/SVG 和演示截图，下载链接指向公开 Release。在线地址：https://aronlele.github.io/communications-assistant/ 。site-preview.zip 可以离线查看；本地服务：python3 -m http.server 8765 --directory docs --bind 127.0.0.1。

仓库由维护者改为公开，并已确认发布在线展示页。GitHub Pages 使用 Actions 构建，只发布 docs 目录；pages.yml 仅手动触发，不随普通推送自动上线。更新页面后，在 Actions 中运行 Publish product page（main），确认部署成功后再检查在线页面和下载链接。

文档和页面更新不移动已有版本标签，也不重新打包应用。若更新同一 Release 的安装器或离线展示页，需同步相应文件和 SHA256SUMS.txt，并在 release-metadata.json 区分应用、安装器和网站提交；DMG 和 ZIP 的校验值保持不变。

## 升级与回滚

退出应用再替换 .app，不清空用户资料，重启后逐账号确认并重新开启规则。回滚前核对数据库兼容性，未知版本停止发送。业务备份不包含会话或 API 凭据；完整本机升级回退副本包含登录会话，须受保护且不可分享。

## 0.4.0 已验收包同步

本次沿用 `output/macos-0.4.0-candidate/` 中已通过本机升级验收的 PKG、DMG、ZIP，不改应用载荷或校验值，不重启应用。先完成源码/文档/演示截图检查并提交，使用同版本标签指向实际应用代码；再执行：

```bash
python3 scripts/prepare_release.py --source output/macos-0.4.0-candidate --verification-report output/macos-0.4.0-candidate/验收汇总.json --observation-summary output/macos/稳定性验收-0.4.0/正式观察/summary.json
```

发布为测试预发布，安装包中的帮助为构建时快照；最新使用说明独立附在 Release 并与源码同步。仅上传生成的发布附件，不上传本机升级、观察原始报告、运行状态或真实资料。元数据只摘取测试数量和匿名观察起止时间，不能把 summary 的 complete 字段直接当作稳定性通过。确认附件下载与校验值后再部署 Pages，避免页面先指向不存在的附件。

## 0.4.1 独立构建与发布

0.4.1 使用独立输出 `output/macos-0.4.1-candidate/` 完整构建，禁止覆盖正在运行的 `output/macos/通讯助手.app`。先用延迟 6 秒的独立演示引擎复现旧探测错误，再验证新探测、完整回归和新包。发布元数据不传入 0.4.0 的观察 summary，0.4.1 稳定性标记为 not_verified；0.4.0 的工作账号及观察继续运行，自动检查仍针对原版本。新版公开后不自动安装或重启本机应用。

## 0.4.2 即讯与安装权限修复

本版更名“即讯”（原通讯助手）。保留实际磁盘包名 `通讯助手.app`、bundle ID、PKG ID、可执行文件名、钥匙串服务与用户资料路径，确保安装器在原位置覆盖同一组件。显示名与本地化名称为“即讯”；不要仅为更名迁移数据或另装第二份应用。下载附件沿用 `CommunicationsAssistant-<版本>-macOS-arm64` 的稳定命名。

使用独立输出 `output/macos-0.4.2-candidate/` 构建，不替换 `/Applications/通讯助手.app`。包内 API 配置需为 0644，以便 PKG 安装为 root 所有后普通用户可读；私有构建输入仍保持 0600。`check_installer.py` 必须逐项验证实际载荷对普通用户可读、目录可遍历、程序可执行；不可只比较文件字节和签名。

本机实际运行 0.4.1 构建 14（安装权限已本地修复），其 72 小时观察为 2026-10-16 10:29 至预计 2026-10-19 10:29（北京时间），尚未通过。0.4.2 元数据不传入旧版 summary，标记 not_verified。提交、创建新标签并上传新版本；不得覆盖历史 v0.4.1 附件或将旧观察算作新版本通过。

## 0.5.0 发布与本机升级

使用独立输出目录构建，发布前同步使用说明并重新打包，确保包内帮助与功能一致。上传 PKG、DMG、ZIP、使用说明、离线展示页、校验清单和脱敏元数据；校验公开附件后再部署 Pages。

本次用户授权同时升级本机。先记录并停止 0.4.2 观察，再正常退出、完整备份资料和旧应用，通过 PKG 原位安装；新引擎升级 schema 5，逐账号确认并恢复原先已开启的任务，逐字段比对配置，仅允许启用时间及恢复意图字段变化。恢复正常后启动独立 72 小时观察并更新原自动复查，不合并旧版时长。升级备份含本机敏感资料，仅存本机，不上传。旧版不能直接打开 schema 5，回退须同时恢复完整升级前资料。


## 0.6.0发布与保留资料升级

独立发行目录`output/macos-0.6.0-release/`包含最新包内帮助。先完成237项Python、77项原生及实际冻结引擎、安装包载荷/权限/完整性检查。用户本次另行授权升级：结束0.5.0观察，正常退出并完整备份资料、旧应用和偏好，经系统PKG安装后按原启用集合恢复两个账号的两条任务。工作数据库仍schema5，比较配置只允许重新启用时间变化。0.6.0观察从2026-10-16 15:56:06至预计2026-10-19 15:56:06（北京时间），尚未通过；0.5.0的3.518小时不合并。

源码提交及v0.6.0标签与实际应用代码一致，发布元数据仅摘取脱敏检查和本版观察起止时间；不上传完整备份、工作资料、原始升级报告或观察样本。公开附件校验后再手动部署Pages。

## 0.6.1发布与原位升级

独立发行构建包含最新说明；251项Python、82项原生、打包引擎及服务消息冒烟、PKG/DMG/ZIP检查通过后再发布。升级前正常退出，完整备份资料、会话、旧应用与偏好；系统安装器原位安装，按升级前启用集合恢复原任务。旧空记录通过应用已有引擎逐群预览与确认，报告可恢复与不可恢复数量，不另建客户端或发送验收消息。

用户明确取消本轮72小时观察：停止采样并删除自动复查，不启动新版观察，元数据标记cancelled_by_user且未通过，不传入旧版summary。历史观察和计划中断证据保留。仅上传脱敏发布附件，不上传升级备份或真实消息。公开附件核对后再部署Pages。
