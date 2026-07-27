# mc-han

mc-han 是一个 Minecraft 整合包自动汉化工具。目标是让普通玩家和开服者选择本地整合包目录、输入自己的 API Key，然后自动扫描、翻译、检查、构建并安装可用的汉化包。

## 当前阶段已实现

- `mc-han scan <modpack_dir>`
- `mc-han preview <csv>`
- `mc-han review <csv>`
- `mc-han translate <modpack_dir>`
- `mc-han check <csv_or_output_dir>`
- `mc-han build <modpack_dir>`
- `mc-han install <modpack_dir>`
- `mc-han status <csv>`
- `mc-han rollback <csv>`
- `mc-han config show|save|clear`
- `mc-han all <modpack_dir>`
- `mc-han gui`
- `mc-han-gui`
- 生成 `scan_report.txt`
- 扫描 `mods/*.jar` 内的：
  - `assets/**/ae2guide/**/*.md`
  - `assets/**/guides/**/*.md`
  - `assets/**/patchouli_books/**/en_us/**/*.json`
  - `assets/**/modonomicon/books/**/en_us/**/*.json`
- 扫描 `config/ftbquests/quests/lang/en_us`、`en_us.json`、`en_us.snbt`、`en_us.lang` 等任务书语言文件
- 扫描 `config/ftbquests/quests/**/*.snbt` 中的 `title`、`subtitle`、`description`
- 扫描 jar / KubeJS / resourcepacks 中的 `assets/**/lang/en_us.json`
- GUI 工作流生成 `.mc-han/extracted_texts.jsonl`、`.mc-han/extracted_texts.csv`、`.mc-han/translations.sqlite`
- CLI 备用工作流仍支持生成 `extracted_texts.csv`
- 默认跳过 `item.*`、`block.*`、`entity.*`、`fluid.*` 等基础名称 key；用户开启后会以 `中文名 (English Original)` 格式翻译并保留英文原名
- Markdown 抽取会跳过代码块
- `translate` 支持 mock 离线测试和 OpenAI 兼容接口
- 真实 Provider 默认需要 `--limit` 试译或 `--confirm-cost` 明确确认，避免误花 API 费用
- 翻译阶段有终端/GUI 进度显示，可区分已翻译、缓存/复用、API 新翻译、失败、剩余和预计剩余时间
- 翻译按估算 token 自动分批，支持稳定/平衡/快速三种速度模式，长文本单独成批，短文本自动合并
- 支持并发翻译 1/2/3，默认并发 1；SQLite 缓存写入带锁，停止后继续不会重复翻译已完成条目
- 翻译缓存默认写入整合包 `.mc-han` 共享缓存，并支持同轮重复文本复用、同 Provider 跨模型复用、SQLite 断点续传
- 支持 `custom` OpenAI 兼容 Provider，可填写自定义 Base URL、模型和 API Key
- 可选本地保存 Provider、模型、Base URL、API Key、翻译速度、并发和名称翻译设置
- 可生成 HTML 翻译审阅报告，方便真实 API 小批量试译后人工确认
- 翻译前自动创建 CSV checkpoint，试译不满意可以回滚
- GUI 可分别生成客户端资源包、服务端任务包和完整安装包，不直接修改 `mods/*.jar`
- GUI 支持安装预演、安装 manifest 和按最近备份回滚
- 提供可双击运行的 Tkinter 桌面 GUI MVP

## 安装开发环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
```

也可以不安装包，直接用 `PYTHONPATH` 运行：

```powershell
$env:PYTHONPATH = "src"
python -m mc_han scan "D:\Minecraft\MyModpack"
```

## 使用

### 普通玩家：从 GUI 开始

Windows Release 解压后，普通玩家直接双击：

```text
mc-han-gui.exe
```

GUI 第一阶段主流程：

1. 选择整合包目录。
2. 选择 Provider，填写 API Key、模型名和 Base URL。
3. 选择翻译速度：平衡是默认值；先保持并发 1，确认稳定后再尝试 2 或 3。
4. 如需翻译物品/方块/实体/流体名称，在高级选项里开启“翻译物品/方块/实体/流体名称，并保留英文原名”。
5. 点击“测试API”确认配置可用。
6. 点击“扫描”。
7. 点击“试翻译10条”，在实时翻译表格查看原文/译文对照。
8. 满意后点击“继续翻译”。
9. 需要临时停下时点击“暂停”，继续时点击“恢复”。
10. 点击“生成客户端资源包”或“生成服务端任务包”。
11. 需要完整安装目录时点击“生成完整安装包”。
12. 点击“安装预演”查看将要安装和覆盖的文件。
13. 点击“安装”会先检查输出并备份已有文件。
14. 需要撤回时点击“回滚安装”，会按最近一次安装清单恢复。

GUI 状态保存在整合包目录下：

```text
<modpack_dir>\.mc-han\
  extracted_texts.jsonl
  extracted_texts.csv
  translations.sqlite
  translation_cache.jsonl
  logs\
  output\
  backups\
```

试翻译后关闭软件也没关系，再打开同一个整合包会继续使用 `.mc-han` 里的状态。已经翻译的条目不会重复请求 API。

GUI 生成的输出：

```text
<modpack_dir>\.mc-han\output\
  mc-han-client-resourcepack\
    pack.mcmeta
    assets\...
    README_CLIENT.txt

  mc-han-server-pack\
    config\ftbquests\quests
    README_SERVER.txt

  mc-han-complete-install\
    install_cn_pack.bat
    config\...
    resourcepacks\mc-han-cn\
    README_ALL.txt
```

多人服提示：服务端需要安装 server pack，每个玩家建议安装 client resourcepack。PCL2 开局域网或 FRP 穿透时，开服玩家也相当于服务器；Windows/PCL2 开服端建议添加 JVM 参数 `-Dfile.encoding=UTF-8`。

以下 CLI 内容是备用入口，主要用于开发和排错。

### 检测整合包

在扫描或翻译前，可以先只读检测所选目录是否像 Minecraft 整合包，并查看版本、Loader、模组数量和可汉化内容：

```powershell
mc-han inspect "D:\Minecraft\MyModpack"
mc-han inspect "D:\Minecraft\MyModpack" --json
```

检测只读取实例元数据和 JAR entry 名称，不会修改 `mods/*.jar`，也不会扫描或解压指南正文。

### 扫描

```powershell
mc-han scan "D:\Minecraft\MyModpack"
```

默认输出：

```text
D:\Minecraft\MyModpack\extracted_texts.csv
D:\Minecraft\MyModpack\scan_report.txt
```

也可以指定输出位置：

```powershell
mc-han scan "D:\Minecraft\MyModpack" --output "D:\Minecraft\MyModpack\mc-han-extracted.csv"
```

此时报告会写到同目录：

```text
D:\Minecraft\MyModpack\scan_report.txt
```

CSV 字段：

```text
id, source_type, container, file_path, key_path, original, translation, note
```

其中 `translation` 第一阶段留空，后续 `translate` 命令会写入翻译缓存并参与构建。

`scan_report.txt` 会记录：

- 扫描耗时
- 抽取总数
- 各来源抽取数量
- jar 数量和坏 jar 数量
- FTB Quests lang / SNBT 文件数量
- 支持范围内 jar 指南/书本候选文件数量
- 当前发现但尚未进入第一阶段抽取范围的 lang 文件数量

### 预览

扫描后建议先预览 CSV，确认工具抽取的是说明文本，而不是物品/方块名称：

```powershell
mc-han preview "D:\Minecraft\MyModpack\extracted_texts.csv" --limit 30
```

只看未翻译项：

```powershell
mc-han preview "D:\Minecraft\MyModpack\extracted_texts.csv" --untranslated-only
```

只看已翻译项：

```powershell
mc-han preview "D:\Minecraft\MyModpack\extracted_texts.csv" --translated-only
```

### 审阅报告

真实 API 试译后，可以生成本地 HTML 审阅报告，对照原文/译文并查看格式检查问题：

```powershell
mc-han review "D:\Minecraft\MyModpack\extracted_texts.csv"
```

默认输出：

```text
D:\Minecraft\MyModpack\translation_review.html
```

只生成有问题的条目：

```powershell
mc-han review "D:\Minecraft\MyModpack\extracted_texts.csv" --issues-only
```

输出全部条目：

```powershell
mc-han review "D:\Minecraft\MyModpack\extracted_texts.csv" --limit -1
```

### 翻译

离线测试可以先用 mock provider，不需要 API Key：

```powershell
mc-han translate "D:\Minecraft\MyModpack" --provider mock
```

真实翻译使用 OpenAI 兼容接口。API Key 默认只从命令行参数或环境变量读取，不会写入缓存或报告：

```powershell
$env:OPENAI_API_KEY = "sk-..."
mc-han translate "D:\Minecraft\MyModpack" --provider openai --model "你的模型名" --limit 20
```

真实 Provider 默认要求设置 `--limit`，建议先试译 20 个未缓存的唯一文本/API 调用，预览和检查没问题后再扩大范围。

确认要翻译全部待翻译文本时，显式加入：

```powershell
mc-han translate "D:\Minecraft\MyModpack" --provider openai --model "你的模型名" --confirm-cost
```

翻译速度模式：

```powershell
# 稳定：约 10~20 条/批，适合任务书和长指南
mc-han translate "D:\Minecraft\MyModpack" --provider deepseek --model "deepseek-chat" --speed-mode safe --limit 20

# 平衡：约 30~60 条/批，默认模式
mc-han translate "D:\Minecraft\MyModpack" --provider deepseek --model "deepseek-chat" --speed-mode balanced --limit 20

# 快速：约 80~150 条/批，适合短文本；格式检查失败会自动拆小重试
mc-han translate "D:\Minecraft\MyModpack" --provider deepseek --model "deepseek-chat" --speed-mode fast --limit 20
```

并发翻译默认是 1，最稳。Provider 和网络稳定后可以尝试 2 或 3：

```powershell
mc-han translate "D:\Minecraft\MyModpack" --provider deepseek --model "deepseek-chat" --speed-mode balanced --concurrency 2 --limit 100
```

高级覆盖参数：

```powershell
mc-han translate "D:\Minecraft\MyModpack" --provider deepseek --model "deepseek-chat" --max-batch-items 60 --max-input-tokens 6200 --max-output-tokens 8200 --limit 100
```

通常不需要手动设置这些高级参数；工具会按估算 token 自动合并短文本、拆出长文本，并保留安全余量。`--batch-size` 仍可用，但现在只是 `--max-batch-items` 的旧别名。

### 物品/方块/实体名称翻译

默认情况下，`item.*`、`block.*`、`entity.*`、`fluid.*`、`biome.*`、`effect.*`、`enchantment.*` 等基础名称仍会跳过，保证 JEI 搜索和物品对应最安全。

如果需要汉化名称，同时保留英文原名，可以在 GUI 高级选项中开启：

```text
翻译物品/方块/实体/流体名称，并保留英文原名
```

开启后，名称译文必须使用固定格式：

```text
中文名 (English Original)
```

例如：

```text
工程师万用表 (Engineer’s Multimeter)
采矿钻头 (Mining Drill)
量子计算机 (Quantum Computer)
空间塔柱 (Spatial Pylon)
扳手 (Wrench)
```

CLI 扫描时可以显式开启：

```powershell
mc-han scan "D:\Minecraft\MyModpack" --translate-names
mc-han all "D:\Minecraft\MyModpack" --provider deepseek --model "deepseek-chat" --translate-names --limit 20
```

也可以保存为本地配置：

```powershell
mc-han config save --translate-names --name-translation-format "{zh} ({en})"
```

质量检查会验证 `lang_name` 行是否保留英文原名。名称翻译只写入客户端资源包；生成完整安装包时，README 会提醒多人联机建议所有玩家安装同一个客户端资源包，否则不同玩家看到的物品/方块/实体名称可能不一致。

如果在已经扫描/翻译过一部分内容之后再开启或关闭名称翻译，GUI 会提示扫描范围已改变，需要点击“重新扫描”。重新扫描只会刷新 `extracted_texts.csv/jsonl` 的提取清单，不会删除 `.mc-han/translations.sqlite` 或 `translation_cache.jsonl`。同一条任务书/指南书文本会保留旧译文或继续从缓存复用；新增的 `lang_name` 名称条目会作为 missing 条目等待翻译。

重新扫描后，GUI 统计区会显示：

```text
item.* / block.* / entity.* / fluid.*
```

如果已开启名称翻译但 `item.* + block.* + entity.*` 仍为 0，GUI 会显示红色错误，提示检查是否读取到了 `mods/*.jar/assets/**/lang/en_us.json`。

每次 `translate` 写入已有 CSV 前会自动创建 checkpoint：

```text
<csv所在目录>\.mc-han-checkpoints\
```

查看当前翻译进度：

```powershell
mc-han status "D:\Minecraft\MyModpack\extracted_texts.csv"
```

试译不满意时，回滚到最近一次 checkpoint：

```powershell
mc-han rollback "D:\Minecraft\MyModpack\extracted_texts.csv"
```

回滚后可以调整 Provider、模型、Base URL 或术语策略，再重新试译。

支持的 Provider 预设：

```text
openai       OPENAI_API_KEY       https://api.openai.com/v1
deepseek     DEEPSEEK_API_KEY     https://api.deepseek.com
siliconflow  SILICONFLOW_API_KEY  https://api.siliconflow.cn/v1
openrouter   OPENROUTER_API_KEY   https://openrouter.ai/api/v1
dashscope    DASHSCOPE_API_KEY    https://dashscope.aliyuncs.com/compatible-mode/v1
qwen         DASHSCOPE_API_KEY    https://dashscope.aliyuncs.com/compatible-mode/v1
```

如果厂商端点变化，可以覆盖：

```powershell
mc-han translate "D:\Minecraft\MyModpack" --provider openai --base-url "https://example.com/v1" --model "model-name" --api-key "..."
```

如果要使用自定义 OpenAI 兼容接口：

```powershell
mc-han translate "D:\Minecraft\MyModpack" --provider custom --base-url "https://example.com/v1" --model "model-name" --api-key "..." --limit 20
```

如果想保存常用 Provider、模型、Base URL、API Key、速度模式和并发，可以显式保存本地配置：

```powershell
mc-han config save --provider deepseek --model "deepseek-chat" --api-key "sk-..." --speed-mode balanced --concurrency 1 --translate-names --name-translation-format "{zh} ({en})"
mc-han config show
```

之后使用：

```powershell
mc-han translate "D:\Minecraft\MyModpack" --use-config
mc-han all "D:\Minecraft\MyModpack" --use-config
```

也可以在翻译时直接保存当前设置：

```powershell
mc-han translate "D:\Minecraft\MyModpack" --provider deepseek --model "deepseek-chat" --api-key "sk-..." --speed-mode balanced --concurrency 1 --limit 20 --save-config
```

清除本地配置：

```powershell
mc-han config clear
```

Windows 默认配置路径：

```text
%APPDATA%\mc-han\config.json
```

配置文件只保存在本机。保存 API Key 是显式 opt-in 行为；如果不想落盘，继续使用 `--api-key` 或环境变量即可。

翻译缓存默认写入：

```text
<modpack_dir>\.mc-han\translation_cache.jsonl
```

缓存只包含 provider、model、原文、译文和时间，不保存 API Key。缓存会对原文做轻量规范化，复用同一轮重复文本，并允许同一个 Provider 下不同模型复用已有译文，例如 `deepseek-chat` 和后续 DeepSeek 模型名之间的重复文本。

### 检查

检查 CSV：

```powershell
mc-han check "D:\Minecraft\MyModpack\extracted_texts.csv"
```

检查构建目录：

```powershell
mc-han check "D:\Minecraft\MyModpack\mc-han-build"
```

会生成：

```text
汉化检查报告.txt
```

### 构建

```powershell
mc-han build "D:\Minecraft\MyModpack"
```

默认输出：

```text
D:\Minecraft\MyModpack\mc-han-build\
  resourcepacks\mc-han-cn\
  config\ftbquests\quests\
  build_report.txt
```

`build` 不会修改 `mods/*.jar`，也不会覆盖原始 `config`。后续 `install` 命令会在自动备份后再安装。

### 安装

预演安装，不写入整合包：

```powershell
mc-han install "D:\Minecraft\MyModpack" --dry-run
```

会生成：

```text
install_plan.txt
```

正式安装：

```powershell
mc-han install "D:\Minecraft\MyModpack"
```

默认读取：

```text
D:\Minecraft\MyModpack\mc-han-build
```

安装前会先检查构建目录。如果质量检查存在 error，会拒绝安装。

安装时会：

- 复制 `resourcepacks\mc-han-cn` 到整合包的 `resourcepacks`
- 复制 `config` 覆盖文件到整合包目录
- 将已有目标文件备份到 `<modpack_dir>\.mc-han\backups\<时间戳>`
- 写入 `install_manifest.json`，用于回滚

多人服务器提示：

- 客户端需要安装资源包
- 如果生成了 FTB Quests/config 覆盖文件，服务端也需要安装对应 config

回滚最近一次安装：

```powershell
mc-han install "D:\Minecraft\MyModpack" --rollback
```

### 图形界面

```powershell
mc-han gui
```

GUI 可以完成：

- 选择整合包目录和输出目录
- 选择 Provider
- 输入 API Key、模型名和 Base URL
- 使用自定义 OpenAI 兼容 Provider
- 选择翻译速度：稳定、平衡、快速
- 选择并发 1、2 或 3，并在高级设置中覆盖 batch_size / token_limit
- 可选翻译物品/方块/实体/流体名称，格式固定为 `中文名 (English Original)`
- 测试 API 连接
- 保存、加载、清除本地 Provider/API Key/模型/速度/名称翻译配置
- 扫描、试翻译 10 条、继续翻译、暂停、恢复、停止
- 显示扫描数量、原文/译文对照、已翻译、缓存/复用、API 新翻译、失败、剩余和预计剩余时间
- 实时显示当前正在翻译的 text_id、文件路径、原文、译文和状态
- 最近完成表格保留最近 200 条，支持全部、正在翻译、最近完成、失败、疑似问题、当前文件、当前批次筛选
- 选中表格行后可查看完整原文/译文，并可标记通过、标记需要重翻、编辑译文、重新翻译选中项
- 生成客户端资源包、服务端任务包、完整安装包
- 生成审阅报告、检查输出、安装预演、安装、回滚安装
- 翻译阶段显示实际进度、缓存/复用数量、失败数量、剩余数量、预计剩余时间和 API 批次
- 真实 Provider 会默认建议先点“试翻译10条”

GUI 只有点击“保存配置”时才会把 API Key 写入本机配置文件；不点击保存时，关闭窗口后输入框中的 Key 会消失。

### 一键流程

```powershell
mc-han all "D:\Minecraft\MyModpack" --provider mock
```

真实翻译：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
mc-han all "D:\Minecraft\MyModpack" --provider deepseek --model "deepseek-chat" --limit 20
```

确认全量一键生成：

```powershell
mc-han all "D:\Minecraft\MyModpack" --provider deepseek --model "deepseek-chat" --confirm-cost
```

## 测试

```powershell
python -m pytest
```

## Windows Release 打包

开发者在 Windows 上可以打包普通用户可直接运行的版本：

```powershell
python -m pip install -e .[release]
python tools\build_windows_release.py
```

输出：

```text
release\mc-han-0.6.1-windows\
  mc-han-gui.exe
  mc-han-cli.exe
  README.md
  使用说明.txt

release\mc-han-0.6.1-windows.zip
release\mc-han-0.6.1-windows.zip.sha256.txt
```

普通用户优先双击 `mc-han-gui.exe`。高级用户或排错时使用 `mc-han-cli.exe`。

## 后续阶段

后续会继续实现：

- PySide6 多页面 GUI 重构
- 更细的术语表和翻译审阅/局部重翻工作流
