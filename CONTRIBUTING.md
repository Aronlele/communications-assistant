# 开发协作

默认分支 main，功能或修复使用短期分支。先确定验收条件或复现问题，再精准修改；完成相关回归及 git diff --check 后提交 PR，不顺手重构运行正常的模块。

```bash
bash scripts/test.sh
bash scripts/test-native.sh
git diff --check
```

自动测试不接入真实账号。真实发送验收需单独明确账号、对象和内容，不把 Token 或验证码放进 Issue/PR。界面检查使用独立演示环境。

修改版本号需同步 Info.plist、engine/profile.py、使用说明、版本记录和展示页，设置页版本来自包资源。

不得提交 chatlog、output、Private、会话、数据库、真实聊天或凭据。安装包只作为 Release 附件，不加入 Git 历史。原产品 v1.0 保留为基线，新选择写入 design/product-design.md，验证范围写 design/acceptance.md。个人验收证据保留本机。
