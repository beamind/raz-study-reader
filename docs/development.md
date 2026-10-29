# 开发与构建

面向开发者的环境、构建和验证说明。普通使用方法见 [项目首页](../README.md)。

应用版本保持 **1.0.0**，正式发布前不因本地构建自动升版。

## 本地运行

需要 Node.js 22.12+ 与 Python 3.11+。开发和生产构建都不需要教材。

```powershell
npm ci
npm run dev
```

开发地址为 `http://127.0.0.1:5173`。首次启动自动建立空目录索引，不下载资源。`content/` 是被 Git 忽略的本地目录；如需制作自己的内容，相关工具只处理用户自行提供的素材。

构建网页：

```powershell
npm run build
npm run preview
```

生产构建始终输出空书架，即使本地制作目录有书籍，也不会自动打包进去。浏览器资源导入需新版 Chrome/Edge，通过 HTTPS 或 localhost 使用。资源保存在当前网站的本地存储中，详情见 [资源包说明](resource-packs.md) 与 [隐私说明](privacy.md)。

## Android

需要 JDK 21+、Android SDK Platform 36 和 Build Tools 36.1.0。

```powershell
npm run android:build
# 正式签名版还需自行准备私有签名配置
npm run android:release
```

最低 Android 7.0。应用 ID 保持 `com.beamind.smartraz`，以保持现有本地安装的兼容性。私钥、密码、SDK 本地路径与构建缓存均不在仓库中；安装及签名配置见 [Android 构建说明](android-release.md)。

## 验证

```powershell
python -m pip install -r requirements-dev.txt
npm run test:python
npx playwright install chromium
npm test
```

测试使用自行编写的例句、生成的图片和静音音频，不依赖 RAZ 文件。生成的测试资源只保存在被忽略的 `artifacts/` 中，不会进入生产网页或 APK。本机已安装 Chrome 时，可设置 `$env:PLAYWRIGHT_CHANNEL = 'chrome'` 运行浏览器测试。

`npm test` 会生成原创测试包、构建空书架，并运行资源包导入、损坏拒绝、更新、离线读取、练习记录、缓存和许可证检查。PDF、OCR、转写等可选内容制作工具另有 `requirements-reader-pdf.txt`、`requirements-reader-ocr.txt` 及对应脚本帮助；普通阅读器开发不需要它们。

## 更新首页截图

首页使用真实运行界面，展示文字、封面和插图为本项目原创演示内容，不引用 RAZ 教材。演示包的音频是用于验证播放能力的静音数据，截图不代表附送朗读教材。

```powershell
python -m pip install -r requirements-dev.txt
npm run build
python tools/create_readme_demo.py
# 使用已安装的 Chrome，也可不设置此项而使用 Playwright Chromium
$env:PLAYWRIGHT_CHANNEL = 'chrome'
node tools/capture_readme_screenshots.mjs
```

脚本启动临时预览服务（端口 4183），以全新的浏览器配置导入演示包并截图，完成后关闭服务；不会使用或改写个人书架、资源包和学习记录。演示包生成在被 Git 忽略的 `artifacts/readme-demo/`，只有截图保存到 `docs/screenshots/`。重新截图后请逐张检查文字、按钮、图片和排版。
