# 构建与发布

## 本地内部包

先同步版本、变更记录和展示页，执行回归及原生探针。不覆盖正在运行的应用，不把旧版或中断的观察算作新包通过。

```bash
bash scripts/test.sh
bash scripts/test-native.sh
TGA_RELEASE=1 TGA_OUTPUT_ROOT="$PWD/output/release-preview" bash macos/scripts/build.sh
python3 macos/scripts/smoke.py output/release-preview/通讯助手.app/Contents/Resources/Engine/tgengine
codesign --verify --deep --strict output/release-preview/通讯助手.app
hdiutil verify output/release-preview/通讯助手-0.3.3-arm64.dmg
```

API 配置仅用于签名前生成资源，不加入 Git。发布说明须写明 TEST ONLY、临时签名、实际验证环境及待验收范围；正式产品 API 尚未就绪。

提交 main，创建与包版本一致的 v0.3.3 标签，在公开 Release 上传 DMG、ZIP、SHA256SUMS 和离线展示页。下载无需 GitHub 登录，标签必须指向实际应用代码。多行发布说明使用文件传入 CLI。

## GitHub 工作流

ci.yml 在提交/PR 时跑离线测试、源码和展示页检查及无凭据的开发包构建。release.yml 仅手动运行，要求已有 main 历史内的同版本标签，且与 Info.plist 一致；没有 TELEGRAM_API_CONFIG Secret 时停止生成可连接包。

需要云端生成可连接包的维护者应自行配置仓库 Secret TELEGRAM_API_CONFIG。当前建立仓库过程不自动上传本机 API 配置，也不在 PR 使用发布凭据。

## 展示页

docs 只包含可展示的 HTML/CSS/SVG 和演示截图，下载链接指向公开 Release。在线地址：https://aronlele.github.io/communications-assistant/ 。site-preview.zip 可以离线查看；本地服务：python3 -m http.server 8765 --directory docs --bind 127.0.0.1。

仓库由维护者改为公开，并已确认发布在线展示页。GitHub Pages 使用 Actions 构建，只发布 docs 目录；pages.yml 仅手动触发，不随普通推送自动上线。更新页面后，在 Actions 中运行 Publish product page（main），确认部署成功后再检查在线页面和下载链接。

文档和页面更新不移动已有版本标签，也不重新打包应用。若更新同一 Release 的离线展示页，需同步 site-preview.zip 和 SHA256SUMS.txt，并在 release-metadata.json 区分应用 source_commit 与 website_commit；DMG 和 ZIP 的校验值保持不变。

## 升级与回滚

退出应用再替换 .app，不清空用户资料，重启后逐账号确认并重新开启规则。回滚前核对数据库兼容性，未知版本停止发送。备份不包含会话或 API 凭据。
