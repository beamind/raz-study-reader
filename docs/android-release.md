# Android 正式版

当前版本为 **1.0.0**，应用 ID `com.beamind.smartraz`，Android `versionCode` 为 2，最低 Android 7.0 / API 24。正式构建使用独立发布证书，关闭应用调试和 WebView 远程调试，不包含书籍资源。资源包协议沿用 1.0，兼容现有 1.0 格式资源包；本仓库不附带教材资源。

## 安装与首次使用

下载 `smart-raz-reader-1.0.0-release.apk`，在 Android 设备上打开并安装，然后在「管理资源包 → 导入资源包」中选择自己有权使用的 `.razpack` 文件。APK 与资源包分开存放，书页、音频按需读取。网页也可以使用相同格式的资源包。

**从 0.1.0 调试版切换：**旧 APK 使用调试签名，无法被新发布证书直接覆盖。先在旧版导出学习记录并将 JSON 另存到安全位置，保留原始资源包，再卸载旧版、安装正式版、重新导入资源包和学习记录。卸载会删除应用内的资源与记录。

后续正式版保持相同应用 ID 和签名，并递增 `android/app/build.gradle` 的 `versionCode`，即可覆盖升级而保留应用数据。`versionName` 从 `package.json` 自动读取，不再手工维护两个版本字符串。

## 构建

需要 Node.js 22.12+、Python 3.11+、JDK 21+、Android SDK Platform 36 和 Build Tools 36.1.0。源码克隆后可以直接构建空书架，不需要取得或下载 RAZ 原书。

```powershell
npm ci
npm run android:release

# 也可明确指定私有签名配置
npm run android:release -- -SigningConfig 'D:\private\smart-raz-reader\signing.json'

# 开发用调试包
npm run android:build
```

`npm run android:release` 默认读取 `%USERPROFILE%\.android\smart-raz-reader\release-signing\signing.json`。私有 JSON 包含 `storeFile`、`storePassword`、`keyAlias`、`keyPassword` 四个字段；相对 `storeFile` 路径相对于该 JSON 所在目录解析。配置不会提交 Git，也不会打入 APK。构建时仅通过本次进程的环境变量提供给 Gradle，结束后恢复原环境。

首次为自己的发行版本创建签名：

```powershell
pwsh -File tools/create_android_release_key.ps1 `
  -PrivateDirectory "$env:USERPROFILE\.android\smart-raz-reader\release-signing" `
  -JavaHome 'C:\Program Files\Android\Android Studio\jbr'
```

脚本拒绝覆盖已有目录，也拒绝在项目目录内创建私钥。它生成 RSA-3072 / SHA256withRSA、有效期 10,000 天的 PKCS12 证书及随机密码，目录仅允许当前 Windows 账户与 SYSTEM 访问。保留并安全备份整个目录；`release.p12` 与 `signing.json` 均属于私密文件，不能附在 Release 或开源仓库中。Fork 项目应创建自己的签名，不能取得本项目的发布私钥。

构建脚本执行生产空壳构建、许可证收集、Capacitor 同步与 Gradle `:app:clean :app:assembleRelease`；缺少签名时直接失败，不会生成用于分发的未签名 APK。完成后检查 APK 签名、ZIP 对齐和非调试标志，输出到 `artifacts/` 并计算 SHA-256。

可用 `-JavaHome`、`-SdkRoot` 指定本机工具；`-UseChinaMirror` 仅为本次构建优先使用 Maven 镜像。`-SkipWebBuild` 仅在已构建并验证当前生产空壳时使用。发布前检查 `:app:dependencies --configuration releaseRuntimeClasspath`，核对原生组件许可证，并使用 `apksigner verify --verbose --print-certs` 对照发布证书。

## 源码公开与非商业许可范围

阅读器程序与原创工具采用 [PolyForm Noncommercial License 1.0.0](../LICENSE)，具体范围见 [NOTICE](../NOTICE.md)。这是源码公开的非商业许可，不是 OSI 定义的开源许可。一般商业用途不获本协议授权；个人学习、教育机构等允许范围按协议原文判断，超出允许用途需另行获得权利人授权。第三方组件保留自身许可证。APK 内附许可全文与组件声明，可在「阅读设置 → 关于这个版本 → 软件与组件许可证」中离线查看。

公开下载可分发程序 APK、校验值和软件说明。RAZ 的页面、音频、引用原文的讲义与习题不因项目采用非商业许可而成为可自由分发的内容。本仓库不包含教材与制作报告，也未带入原制作仓库的历史。`content/`、资源包、构建产物和签名密钥均被 Git 忽略。

## 验证边界

正式签名和 release 构建表示技术上的发行配置；它不替代 Pad 真机验收，也不表示已上架应用商店。分发前应在目标设备检查：安装、资源包导入、离线冷启动、原书与音频、练习、学习记录导出/导入，以及下一正式版本的覆盖升级。每次实际验证结果记录在相应版本的 `docs/releases/` 下。

正式发布前，应用版本统一保持 1.0.0，Android `versionCode` 保持 2；本地重新构建不自动升版。正式发布后的版本升级再递增版本号与版本码。
