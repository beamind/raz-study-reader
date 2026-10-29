# 独立级别资源包

正式网页与 Android APK 默认只包含阅读器程序，不再内置 O、P、Q 等书籍资源。每个级别使用独立 `.razpack` 文件，电脑网页与 Android 使用同一种文件。资源包内容的使用与分发须取得相应授权，不属于阅读器的非商业软件许可范围，详见 [NOTICE](../NOTICE.md)。

## 使用

1. 安装阅读器或打开正式网页。
2. 在书架点击“管理资源包”，选择“导入资源包”，选中对应级别的 `.razpack` 文件。
3. 导入完成后书架自动刷新。一个包包含该级别的封面、原书页面、讲义、原声与已有习题。
4. 更新时再次导入同一级别的新包。程序先完整校验再切换，文件损坏、空间不足或中断不会覆盖原包。
5. 在资源包管理中移除级别可释放内容占用。阅读位置、历史作答、错题和未完成练习独立保存；重新导入后可恢复。只有对应题库确实改变时，旧的未完成轮次才需要重开，历史记录仍保留。

浏览器资源使用 OPFS 保存到磁盘，使用新版 Chrome/Edge，通过 HTTPS 或 localhost 打开。导入时申请持久化并检查存储配额；持久化是否获准由浏览器决定。**资源属于当前网站地址、端口及浏览器配置，换地址或换浏览器不会自动迁移。** 清除网站数据也会清除资源及本机学习记录，请保留原始资源包并定期导出学习记录。正式网页在程序文件缓存完毕后可断网重新打开，资源管理页显示离线准备状态。开发服务器不提供离线冷启动。

Android 通过系统文件选择器导入，使用应用内部文件目录存储，不申请整盘访问权限。请保留足够空间：导入时需要临时 ZIP 副本及新版本内容，更新期间原版本也暂时保留。程序更新不清除资源与记录；卸载应用或清除应用数据会删除它们。

## 构建

```powershell
# 制作或修改讲义/习题后先更新并验证运行内容
npm run content:build

# 生成所有已启用级别；也可指定 --level Q
npm run packs:build
npm run packs:build -- --level Q

# 正式网页：不含书籍资源，带离线程序缓存
npm run build
npm run preview

# Android：同样使用不含书籍的程序，生成后再导入资源包
npm run android:build
```

资源包输出在 `artifacts/resource-packs/RAZ-{级别}-{版本}.razpack`。调试 APK 通过 `npm run android:build` 生成；正式 APK 通过 `npm run android:release` 输出到 `artifacts/smart-raz-reader-1.0.0-release.apk`，签名配置见 [Android 正式版](android-release.md)。这些本地二进制产物不提交 Git。

`npm run dev` 首次启动创建空书架，也可读取用户自行准备的本地制作内容。需要原来的整库网页构建可运行 `npm run build:bundled`；Android 构建脚本会拒绝将该整库版本打进 APK。Android 每次清理应用构建产物，避免增量 APK 的 ZIP 空洞残留已移除的素材。Gradle 依赖缓存继续复用。

## 包格式与加载

- 容器为 ZIP_STORED，扩展名 `.razpack`；不接受任意 ZIP，也不使用 ZIP64、加密或二次压缩。WebP/MP3 已经压缩。
- `pack.json` 声明 `raz-reader-pack/1.0`、阅读器协议版本、级别、级别版本、书架与习题目录、文件大小和 SHA-256。
- 内容仅含被书籍引用的 `book.json`、封面、页面图及播放音频，不包含 `_source`、版权 PDF、原始制作音频或未引用素材。
- 版本由本级内容决定，不依赖全库版本号。Q 级改变不会迫使用户重新导入 P 级。
- 当前协议最多 60,000 个内容文件，内容合计不超过 3 GiB；单个 JSON 不超过 4 MiB，其他单文件不超过 64 MiB。Web 读取 ZIP 中央目录及单文件切片，每次最多对一个有界文件计算摘要与写盘；Android 使用 64 KiB 缓冲流复制和计算摘要。不会把整个资源包载入 JS 内存。
- 导入验证路径边界、重复文件、文件清单、摘要、大小、级别/书籍/页面身份、讲义结构、习题结构及资源引用。SHA-256 用于检查内容完整性，不提供发行者身份认证。
- 新内容写入不可变的独立目录，校验完成后原子替换已安装目录索引。失败时清理临时版本；下次启动清理未被索引引用的版本，回收中断导入或已移除包的文件。
- 多个网页窗口使用不同版本时，旧窗口通过共享版本锁保留仍在使用的文件，关闭后可在下次启动回收。Android 同时回收进程中断遗留的临时 ZIP。
- 网页图片及音频按需从本地文件生成 Blob URL，切换或卸载对应组件时释放。Android 通过 Capacitor 的本地文件 URL 按需读取。讲义对象继续使用常规 8 本的 LRU 缓存，当前阅读及本轮练习持有的讲义临时保留。
- 未安装书籍的学习记录不会被当前书架过滤掉。混合复习若缺少所需书籍，会暂停并提示导入对应包。

## 验证

```powershell
python -m pip install -r requirements-dev.txt
npm run test:python
npx playwright install chromium
npm test
```

本仓库的测试只使用 `tools/synthetic_content.py` 中自行编写的例句、生成的图片和静音音频，覆盖导入、练习、更新、损坏与不兼容场景。测试资源输出到被 Git 忽略的 `artifacts/fixture-content/` 和 `artifacts/pack-fixtures/`，不进入生产构建。原制作库中依赖真实教材的逐册验收用例不在本仓库中。

参考：[OPFS 存储规则](https://developer.mozilla.org/en-US/docs/Web/API/File_System_API/Origin_private_file_system)、[原子文件写入语义](https://developer.mozilla.org/en-US/docs/Web/API/FileSystemFileHandle/createWritable)、[Service Worker 离线缓存](https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API/Using_Service_Workers)。
