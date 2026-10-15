# 通讯助手

<img src="docs/assets/icon.svg" alt="通讯助手图标" width="88">

原生 macOS Telegram 通讯工作台：多账号同时连接、消息提醒、本地聊天归档，以及用户明确配置的自动回复。

当前发布 **0.4.1 测试版（构建 14）**，修复隔离探测的连接等待及演示原消息提示，保留多任务、任务结果、所有账号和准备与恢复向导。**0.4.1 尚未开展独立 72 小时观察**；本机工作应用仍为 0.4.0 构建 13，原账号和自动回复继续运行，其观察预计 2026-10-18 12:06（北京时间）结束，不能作为 0.4.1 的通过证据。公开仓库：[Aronlele/communications-assistant](https://github.com/Aronlele/communications-assistant)。安装包：[0.4.1 测试版下载](https://github.com/Aronlele/communications-assistant/releases/tag/v0.4.1)。产品展示页：[在线查看](https://aronlele.github.io/communications-assistant/)，源码位于 `docs/`；发布方式见 [发布说明](RELEASING.md)。仓库和安装包均可公开访问，无需 GitHub 登录。

## 安装与使用

推荐从 Releases 下载 Apple Silicon 的 **PKG 安装向导**，双击后按“继续 → 安装”完成安装，再从“应用程序”打开“通讯助手”。系统可能要求管理员验证。安装器只替换应用，保留原有账号、归档、规则与账本；升级前请退出正在运行的应用。DMG/ZIP 保留为备用。

普通使用不需要 Python、Docker 或终端。当前仍为测试版：PKG 未签名、未公证，应用临时签名，内置官方 TEST ONLY API；正式 API、签名公证及干净 Mac 安装验收完成前，暂不面向普通用户正式推广。仅使用测试 API 不能作为正式发行方案。

1. 登录 Telegram 并确认本人身份。内部包使用此前已授权的官方 TEST ONLY 配置；正式产品 API 尚未申请成功。源码不包含 API 配置。
2. 测试系统通知，按需开启来源语音、正文预览和静音时段。
3. 选择归档会话，搜索、查看上下文、导出或本地备份。
4. 在“任务管理”新建任务，选择对象，配置匹配模板和回复；保存并验证样例后，核对身份再开启。
5. 左下角切换或添加账号，其他已登录账号独立运行。关窗仍运行，退出停止全部账号。

[详细使用说明](macos/README.md) · [产品设计](design/product-design.md) · [架构说明](design/architecture.md) · [验收范围](design/acceptance.md) · [后续计划](ROADMAP.md)

[公开推广前产品评估与优先级](design/public-release-review.md) 区分已实现能力、发行阻塞与后续体验改进。

## 功能与边界

- 多账号：独立登录、身份确认、头像、归档、提醒、设备显示字段、自定义 API 和自动化账本。没有头像时显示姓名首字。
- 提醒：入站私聊与提及，系统通知和语音；会话覆盖、静音时段、10 秒合并、通知点击定位。
- 归档：选择性保存文字及可选图片，分页补录、编辑删除状态、中文搜索、日期与发送者筛选、TXT/JSON 导出、旧记录导入、本地备份恢复。
- 自动回复：编号关键词模式；或用户填写提示，全文/包含匹配后发送固定单行内容。每账号可配置多个对象及最多 50 个任务；支持可保存的受限占位符提取模板、固定回复、优先级和独立时段。同一来源消息跨任务只提交一次。不支持群聊自动回复、自由正则或脚本。
- 工作台：所有账号总览、首次准备与恢复向导；任务结果按任务和状态筛选、分页及原消息定位。
- 结果：发送前事务落盘、永久去重、结果不明不重试；编号模式匹配同编号回执。固定回复缺少可关联回执格式时只记“已发送，登记未核实”。
- 运行：菜单栏、登录启动、独立暂停、重连、有限次数引擎恢复、可选防闲置睡眠。在线状态请求为默认关闭的实验功能，不向收藏夹发删消息，不承诺永久在线。

重启或身份恢复后，需要分别确认账号，再在恢复向导核对并恢复选中的原已开启任务；不补发重启前的旧提示。睡眠、合盖、退出和断网期间不能实时执行。备份未加密，不含登录会话和 API 凭据。

本轮已通过 205 项 Python 回归、42 项原生状态检查，另验收延迟启动、隔离界面、打包引擎和安装包。工作账号未切换到 0.4.1，其真实升级与完整 72 小时仍待单独验收，详见[验收记录](design/acceptance.md)。发布附件提供 PKG、DMG、ZIP、中文使用说明和 SHA256。

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

GitHub Actions 在提交/PR 时跑回归、源码边界、展示页检查及 Apple Silicon 开发包构建，包括 PKG 载荷与安装范围验证。发布工作流手动触发，要求 main 的对应版本标签；可连接包需维护者单独配置仓库 Secret，源码库不会自动上传本机 API 配置。当前测试 Release 来自本地验证后的包，详见 [RELEASING.md](RELEASING.md)。

## 仓库内容与数据

`macos/Sources` 为原生界面和系统适配，`macos/engine` 为消息引擎与 SQLite，`macos/tests` 为产品测试，`design/` 保存设计与架构，`docs/` 保存静态展示页。根目录 Python、Docker 和 Windows 语音脚本为参考实现，见 [参考项目说明](design/reference-project.md)。

仓库不包含真实聊天、图片、登录会话、数据库、API Hash、Token、个人验收输出或构建缓存。真实资料保存在本机应用数据目录，下载源码不能恢复登录。

本项目暂未指定开源许可证；参考代码来源许可尚待核实，不对外重新授权。第三方组件遵循各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
