# 构建与发布

## 本地内部包

先同步版本、变更记录和展示页，执行回归及原生探针。不覆盖正在运行的应用，不把旧版或中断的观察算作新包通过。

```bash
bash scripts/test.sh
bash scripts/test-native.sh
TGA_RELEASE=1 TGA_OUTPUT_ROOT="$PWD/output/release-preview" bash macos/scripts/build.sh
python3 macos/scripts/smoke.py output/release-preview/通讯助手.app/Contents/Resources/Engine/tgengine
codesign --verify --deep --strict output/release-preview/通讯助手.app
hdiutil verify output/release-preview/通讯助手-0.3.2-arm64.dmg
```

API 配置仅用于签名前生成资源，不加入 Git。发布说明须写明 TEST ONLY、临时签名、实际验证环境及待验收范围；正式产品 API 尚未就绪。

提交 main，创建与包版本一致的 v0.3.2 标签，私有 Release 上传 DMG、ZIP、SHA256SUMS 和离线展示页。下载需要仓库权限，标签必须指向实际代码。多行发布说明使用文件传入 CLI。

## GitHub 工作流

ci.yml 在提交/PR 时跑离线测试、源码和展示页检查及无凭据的开发包构建。release.yml 仅手动运行，要求已有 main 历史内的同版本标签，且与 Info.plist 一致；没有 TELEGRAM_API_CONFIG Secret 时停止生成可连接包。

需要云端生成可连接包的维护者应自行配置仓库 Secret TELEGRAM_API_CONFIG。当前建立仓库过程不自动上传本机 API 配置，也不在 PR 使用发布凭据。

## 展示页

docs 只包含可展示的 HTML/CSS/SVG 和演示截图，下载链接指向私有 Release。site-preview.zip 可以离线查看；本地服务：python3 -m http.server 8765 --directory docs --bind 127.0.0.1。

私有仓库 Pages 通常仍是公开网站，个人私有仓库还要求支持 Pages 的计划。部署前确认受众和账户条件，不能擅自改公开源码。pages.yml 仅手动触发，不随普通推送自动上线。

若计划不支持私有仓库 Pages，保留离线页；另建只含展示页的公开仓库或其他托管需维护者明确选择。

## 升级与回滚

退出应用再替换 .app，不清空用户资料，重启后逐账号确认并重新开启规则。回滚前核对数据库兼容性，未知版本停止发送。备份不包含会话或 API 凭据。
