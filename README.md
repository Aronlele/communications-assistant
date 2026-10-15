# 通讯助手

<img src="docs/assets/icon.svg" alt="通讯助手图标" width="88">

原生 macOS Telegram 通讯工作台：多账号同时连接、消息提醒、本地聊天归档，以及用户明确配置的自动回复。

当前源码版本 **0.3.2（构建 11）**。公开仓库：[Aronlele/communications-assistant](https://github.com/Aronlele/communications-assistant)。安装包：[测试版 Releases](https://github.com/Aronlele/communications-assistant/releases)。产品展示页：[在线查看](https://aronlele.github.io/communications-assistant/)，源码位于 `docs/`；发布方式见 [发布说明](RELEASING.md)。仓库和安装包均可公开访问，无需 GitHub 登录。

## 安装与使用

从 Releases 下载 Apple Silicon 的 DMG，打开后把“通讯助手”拖入“应用程序”。普通使用不需要 Python、Docker 或终端。安装包目前为内部测试、临时签名，尚未 Developer ID 签名及公证；干净 macOS 14 尚未验收。

1. 登录 Telegram 并确认本人身份。内部包使用此前已授权的官方 TEST ONLY 配置；正式产品 API 尚未申请成功。源码不包含 API 配置。
2. 测试系统通知，按需开启来源语音、正文预览和静音时段。
3. 选择归档会话，搜索、查看上下文、导出或本地备份。
4. 在“自动化”选择对象，配置提示和回复，保存为仅提醒，预览后确认开启。
5. 左下角切换或添加账号，其他已登录账号独立运行。关窗仍运行，退出停止全部账号。

[详细使用说明](macos/README.md) · [产品设计](design/product-design.md) · [架构说明](design/architecture.md) · [验收范围](design/acceptance.md) · [后续计划](ROADMAP.md)

## 功能与边界

- 多账号：独立登录、身份确认、头像、归档、提醒、设备显示字段、自定义 API 和自动化账本。没有头像时显示姓名首字。
- 提醒：入站私聊与提及，系统通知和语音；会话覆盖、静音时段、10 秒合并、通知点击定位。
- 归档：选择性保存文字及可选图片，分页补录、编辑删除状态、中文搜索、日期与发送者筛选、TXT/JSON 导出、旧记录导入、本地备份恢复。
- 自动回复：编号关键词模式；或用户填写提示，全文/包含匹配后发送固定单行内容。机器人与明确允许的普通联系人直接私聊，每账号目前一个对象；不支持群聊自动回复、自由正则、脚本或任意变量提取。
- 结果：发送前事务落盘、永久去重、结果不明不重试；编号模式匹配同编号回执。固定回复缺少可关联回执格式时只记“已发送，登记未核实”。
- 运行：菜单栏、登录启动、独立暂停、重连、有限次数引擎恢复、可选防闲置睡眠。在线状态请求为默认关闭的实验功能，不向收藏夹发删消息，不承诺永久在线。

重启或身份恢复后，需要分别确认账号并重新开启所需规则；不补发重启前的旧提示。睡眠、合盖、退出和断网期间不能实时执行。备份未加密，不含登录会话和 API 凭据。

## 从源码构建

要求 Apple Silicon Mac、Xcode Command Line Tools、Python 3.13 和可访问依赖源的网络。依赖固定于 `macos/requirements-build.txt`。

```bash
git clone https://github.com/Aronlele/communications-assistant.git
cd communications-assistant
bash scripts/test.sh
bash macos/scripts/build.sh
```

没有 API 配置时生成开发包，可浏览独立演示数据，真实登录显示待配置。维护者可把自己的配置放入已忽略的 `macos/Private/TelegramAPI.json`：

```json
{"api_id": 123456, "api_hash": "YOUR_API_HASH"}
```

也可通过 `TGA_API_CONFIG` 指向外部本地文件。`TGA_RELEASE=1` 要求有效配置，否则停止；配置在签名前写入应用资源，不打印、不提交到 Git。打包会完整重建指定输出目录，请使用与正在运行应用不同的 `TGA_OUTPUT_ROOT`。

```bash
TGA_OUTPUT_ROOT="$PWD/output/release-preview" TGA_RELEASE=1 bash macos/scripts/build.sh
python3 macos/scripts/smoke.py output/release-preview/通讯助手.app/Contents/Resources/Engine/tgengine
```

## 测试与发布

`bash scripts/test.sh` 执行引擎、参考解析器及仓库边界检查；`bash scripts/test-native.sh` 运行独立原生状态探针。自动测试使用临时目录和模拟/演示数据，不登录真实 Telegram，不发送真实消息。

GitHub Actions 在提交/PR 时跑回归、源码边界、展示页检查及 Apple Silicon 开发包构建。发布工作流手动触发，要求 main 的对应版本标签；可连接包需维护者单独配置仓库 Secret，源码库不会自动上传本机 API 配置。当前内部 Release 来自本地验证后的包，详见 [RELEASING.md](RELEASING.md)。

## 仓库内容与数据

`macos/Sources` 为原生界面和系统适配，`macos/engine` 为消息引擎与 SQLite，`macos/tests` 为产品测试，`design/` 保存设计与架构，`docs/` 保存静态展示页。根目录 Python、Docker 和 Windows 语音脚本为参考实现，见 [参考项目说明](design/reference-project.md)。

仓库不包含真实聊天、图片、登录会话、数据库、API Hash、Token、个人验收输出或构建缓存。真实资料保存在本机应用数据目录，下载源码不能恢复登录。

本项目暂未指定开源许可证；参考代码来源许可尚待核实，不对外重新授权。第三方组件遵循各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
