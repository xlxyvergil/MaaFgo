# 助战礼装扩展包发布素材

推送 `v*` tag 时，`.github/workflows/install.yml` 生成独立附件
`MaaFGO-SupportCE-DLC-<tag>.zip`，与 MXU ZIP、Android APK 一起上传到同一个
GitHub Release。PR 和手动工作流也构建 DLC，供下载 Actions artifact 验证。
此目录只供构建使用，不复制进 MXU 或 APK，也不安装到开发者的 config。

## 本地生成

在仓库根目录运行，Python 3.10 或更高版本即可，不需要设备或 MaaFramework：

```powershell
python -m pip install -r extras/support-ce/requirements.txt
python tools/build_support_extension.py --version v2.0.26 --check-missing extras/support-ce/missing.json --output build/support-ce/MaaFGO-SupportCE-DLC-v2.0.26.zip
python tools/build_support_extension.py --verify build/support-ce/MaaFGO-SupportCE-DLC-v2.0.26.zip
python -m unittest discover -s tests -p test_support_extension_generator.py -v
```

`--dry-run` 只清点；`--strict` 在存在任何缺图时停止。发布采用
`--check-missing`：缺图必须与已审阅的 `missing.json` 完全一致，否则停止。
每次构建报告所选数量、排除数量、缺图名单、ZIP 大小和 SHA-256。

## 素材与维护

- `sources/`：从游戏解包的真实 `150×68 RGBA EquipFaces` 原图按运行清单 ID
  精确筛选，文件名为 `f_<equipId>0.png`；初始收录 1,110 张，23,492,809 字节。
  2026-11-16按Atlas的准确`equipFace`声明新增No.2719「利休＆駒姫ストラップ」
  （`9312650`）的150×68 RGBA原图，来源为
  `https://static.atlasacademy.io/JP/EquipFaces/f_93126500.png`；该新增项不再属于缺图，
  原有8项已审阅缺图保持。原始下载SHA-256及验证见本轮资源更新回执。
- `limit-star.png`：真实 20×20 RGBA 满破星标。
- `builtin.json`：47 种现有内置助战礼装的中文文件名到源图文件名映射，沿用本地
  礼装流水线已核验的映射，用于排除内置礼装。新增内置模板时须同步该映射。
- `missing.json`：本次 8 张缺少真实助战源图的礼装，保留准确 ID、名称和预期文件名。
  它们不会出现在扩展包中。不能用 Atlas Faces、仓库头像或占位图代替。

补礼装时，更新 `agent/custom/equip_list.json` 及其助战候选，将新增的真实
EquipFaces 源图复制到 `sources/`；已经补齐的缺图从 `missing.json` 移除。
确实缺少源图时，核实 ID、名称后登记缺图，不能为了让构建通过而跳过检查。
删除运行条目或改为内置时，移除不再使用的源图。发布前运行上述命令核验。

生成器沿用正式识别配方：BILINEAR 缩放为 158×72，居中裁到 158×44，非满破
使用 FASTOCTREE 256 色无抖动，满破转 RGBA 并在 (131, 22) 贴星标。
ZIP 使用稳定顺序、固定时间戳，每张图片登记 SHA-256；输出前校验 CRC、文件集合、
图片尺寸、模式及哈希。同一 Python/Pillow/zlib 环境中，相同输入和版本生成相同 ZIP。

## 下载和安装

GitHub 对 Release 附件特殊字符可能重命名，公开文件名统一使用 ASCII 并包含 DLC；
ZIP 内的中文名称及 `满破/`、`非满破/` 目录通过 UTF-8 保留。
ZIP 根目录直接是 `manifest.json` 和两种状态目录，没有额外外层文件夹。

用户在 Release 的 Assets 下载该 DLC ZIP，无需解压。在编队编辑器中依次点击
“选择概念礼装”→“安装礼装扩展”→“选择扩展包 ZIP”完成安装。
新增助战识别能力不改变 `important` 或编辑器的“常用礼装”分类。
