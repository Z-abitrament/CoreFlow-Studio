# CoreFlow Studio 用户手册

## 适用范围
本手册描述当前 M17 版本的 CoreFlow Studio，软件版本为 `0.9.0`。当前版本是一个 Windows 优先的桌面工具；M17 新增可复用、可版本化的 Modbus 寄存器列表，同时保留已有模块和打包路径。

当前桌面 UI 采用模块化主界面。主窗口只保留 `Modules` 菜单，并默认直接进入 `Modbus Module` 工作区。可通过菜单切换到 `Filling Module` 灌装模块或 `ASIO/IIS Module`。无界面的模拟器、replay 和导出 smoke 路径仍可通过控制台诊断程序运行，但旧的模拟器 dashboard 不再显示在主窗口中。

## 启动应用
在打包分发目录中双击：

```text
CoreFlowStudio.exe
```

桌面 UI 会直接打开，不会再弹出 PowerShell 或控制台窗口。

如需命令行诊断，请在分发目录中打开 PowerShell，并运行：

```powershell
.\CoreFlowStudioConsole.exe --build-info
.\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\smoke-data
.\CoreFlowStudioConsole.exe --write-register-map-template .\placeholder_modbus.json
.\CoreFlowStudioConsole.exe --write-replay-template .\replay_template.csv
.\CoreFlowStudioConsole.exe --replay-smoke .\replay_template.csv --data-root .\replay-smoke-data
```

如果从源码运行：

```powershell
conda run -n coreflow-studio python -m coreflow --ui
```

## 数据存储
CoreFlow Studio 使用 SQLite 保存结构化运行元数据，并把原始采集、报告、CSV 导出和 manifest 清单保存为文件。

默认数据目录优先级：

1. 如果设置了 `COREFLOW_DATA_ROOT`，优先使用它。
2. `%LOCALAPPDATA%\CoreFlow Studio`。
3. `%APPDATA%\CoreFlow Studio`。
4. 用户 home 目录下的 `.coreflow-studio`。
5. 打包 exe 所在目录下的 `CoreFlowStudioData`。
6. 当前工作目录下的 `CoreFlowStudioData`。
7. 临时目录。

主数据库文件名：

```text
coreflow.sqlite
```

运行 artifacts 存放在：

```text
artifacts/runs/<year>/<month>/<run_id>/
```

## 主窗口区域
主窗口有意只保留 `Modules` 菜单和当前模块工作区。启动时默认显示 `Modbus Module`。

- `Modules > Modbus Module` 会回到 Modbus 主站操作界面。
- `Modules > Filling Module` 会显示手工灌装试验工作区。
- `Modules > ASIO/IIS Module` 会在主窗口中显示 ASIO/IIS 帧流界面。
- 选择另一个模块时，中央工作区会刷新为该模块界面，而不是打开新的顶层模块窗口。
- `Help > Check for Updates...` 会打开软件更新弹窗。首次使用时粘贴
  GitHub Release 的 `latest.json` 地址并点击 `Save URL`；之后按顺序点击
  `Check`、`Download`、`Update and Restart` 即可。目标电脑操作者不需要
  运行 PowerShell 命令。下载的更新包会先按 `latest.json` 中的 SHA-256
  校验；如果有匹配当前版本的小型 patch 包，软件会优先下载 patch，否则回退到完整更新包。校验通过后才由独立 updater 修补或替换安装目录；`%LOCALAPPDATA%\CoreFlow Studio`
  下的用户数据不会被替换。

## Modbus Module
打开 `Modules > Modbus Module`。该模块拥有自己的连接状态、设备档案、连接弹窗、变量映射、`Operations` 菜单、通信数据码显示区和日志。

- 连接前先创建或选择 `Device Profile`。点击 `New Profile` 新建设备档案，点击 `Edit Profile` 修改当前选中的设备档案。`Device ID` 是被测设备的稳定资产 ID，独立于 Modbus RTU 的 Unit ID。不要把 `01` 这类简单从站地址当作设备 ID。Modbus Module 打开时会自动选择最近使用过且仍然存在的设备档案。
- 设备档案保存设备元数据和连接参数，并把 Device ID 绑定到一个明确的寄存器列表 ID 和版本。寄存器列表不与设备、变送器或测量管型号绑定，同一个列表版本可以由多个 Device ID 共用。
- 在设备档案弹窗中，可从 `Register List` 选择已有列表，也可点击 `New List` 填写稳定的列表 ID、显示名称和版本。`Preview Changes` 会汇总新增、删除和修改的寄存器行。修改自定义或旧配置导入列表时只为当前 Device ID 建立新版本；官方列表必须先通过 `New List` 建立副本才能修改。
- 连接前在设备档案弹窗里编辑完整寄存器列表，包括变量名、寄存器类型、地址、字数、数据类型、缩放、单位和是否可写。`Delete` 只删除当前选中的设备档案，不会删除寄存器列表、设备记录或测试记录。
- 客户端更新会安装新的官方寄存器列表版本，但不会静默切换已有 Device ID。已经完成的测试记录继续保留运行时使用的完整列表快照。
- 当前安装包提供官方列表 `krohne-prj-main @ 1.0.0+f0a1b39`。该表直接从对应 DSP 提交的有效 Modbus 地址、位宽、类型和访问权限定义提取，包含测量、参数、通信、线圈、零点监测和 PI 驱动增益。新建设备档案时应明确选择该列表；客户端不会仅根据设备型号自动绑定。
- 选择设备档案后，点击 `Connection...` 打开 Modbus 连接弹窗。端口列表会根据已接入的串口适配器自动发现。插入或拔出 USB 转串口适配器后，可点击 `Refresh Ports` 重新扫描。`Order` 用于选择 32 位数据的字节/字序，例如 `ABCD`、`BADC`、`CDAB` 或 `DCBA`。`Timeout` 和 `Retries` 可用于容忍从机响应较慢或偶发无响应的情况。
- `krohne-prj-main` 包含客户端工作流使用的 `mass_flow`、`mass_rate`、`mass_acc`、`temperature`、`delta_t`、`zero_offset`、`k_factor`、`low_threshold` 和 `zero_calibration_start`，以及 DSP 当前其余有效映射。
- 模块显示精简的 `Live Variables` 表格，用于运行时读取、写入和轮询；寄存器类型、地址、字数、数据类型、缩放、单位和是否可写等配置列会隐藏，因为这些内容属于设备档案。
- 在设备档案弹窗里使用 `Add`、`Delete` 和 `Reset` 维护自定义变量行。保存档案后，地址、类型、缩放、单位、是否可写和行顺序会跟随该设备 ID 保存。
- 可编辑档案映射包含采样变量以及零点校准启动 coil。如需切换映射，请先断开连接再修改。
- `Connect` 只会在连接弹窗中打开所选的 Modbus RTU 串口。保存数据时使用当前设备档案的 `Device ID`，连接弹窗中的 Unit ID 只作为 Modbus 协议地址保存。连接完成后可以手动关闭弹窗，模块窗口会保持已连接状态。
- 连接后可使用每行的 `Read` 主动查询一个变量，并刷新 `Value` 显示列。可写变量可填写 `Write Value` 后点击 `Write`；不可写变量会禁用写入控件。写入仍会经过 write guard 和审计日志。
- 勾选变量行的 `Poll` 后点击 `Start Polling`，会每秒轮询一次选中的变量。每轮轮询会按变量逐个读；同一 Modbus 表内相邻地址会尽量合并成一次读请求。
- 使用 `Operations` 菜单执行 `Variable Sampling`、`Zero Cal`、`K Factor`、`Repeatability`、`Current Device Test Records` 和 `All Test Records`。当前版本隐藏旧的 `K Factor Inputs` 区域；`K Factor` 会打开独立弹窗。
- `Variable Sampling` 会打开独立弹窗，操作者可选择变量、轮询间隔、绘图布局和备注。点击 `Start` 后会打开非模态实时曲线，并持续轮询所选变量直到点击 `Stop`；操作会保存宽表 CSV artifact，记录有单位变量的单位，刷新 Live Variables 表中的最新值，并写入测试记录。
- 通信数据码表会实时显示读写操作的 TX/RX Modbus 数据码。
- `Zero Cal` 会打开独立弹窗，可勾选校准前需要读取的 snapshot 变量。点击 `Save Config` 可把这些勾选项按当前 Device ID 单独保存；不同设备档案互不共享。点击 `Start` 后会先读取已保存或当前勾选的 snapshot 变量以及 `zero_offset` 和 `delta_t`，再通过 write guard 将 `zero_calibration_start` 置 1，等待 3 秒后读取 coil 完成状态，并显示校准前后的 `zero_offset` 和 `delta_t`，供操作员自行判断结果。Live Variables 的 `Value` 列会刷新校准后的值，包括最终的 `zero_calibration_start` coil 状态。
- `K Factor` 会打开独立弹窗，当前启用 Simple 模式，Advanced 模式先保留选项。Simple 模式会像 Zero Cal 一样先采集用户选择的预快照变量，读取配置的流量累积量和当前 K factor，然后通过配置的瞬时流量变量检测一次从非零流量到回零的流量段；流量段结束后等待用户输入标准称重量，计算 `K1`，并可按用户选择写入从机，写入后会再次回读确认。点击 `Save Configuration` 可保存变量对应、轮询间隔和预快照勾选，下次打开 K Factor 时自动恢复；是否写入从机不会被保存。测试记录会保存捕获、计算、可选写入状态以及原始 Modbus 轮询曲线 artifact 引用。
- `Repeatability` 会打开独立弹窗，包含 Three Flow Ranges、Single Flow Range 和保留的 Advanced 模式。主操作弹窗只保留每个 trial 需要改的标准称重量输入；开始第一个 trial 前，点击 `Configuration...` 设置变量对应、轮询间隔、instant-flow 选点秒数、模式、目标流量范围、K Factor 变量、是否保存测试记录、是否记录所有流量采样点、默认 trial 采样变量、操作备注以及前/后共用的一套快照勾选，并可点击 `Save Config` 按当前设备档案保存。不同 Device ID 的重复性配置互不共享，也没有全局重复性配置兜底。保存后的操作备注会显示在 Repeatability 操作弹窗中，该操作下每个完成计算的 trial 都会保存同一段备注。每个 trial 开始时会自动读取用户勾选的快照变量和配置的 K Factor 变量，并在操作状态中更新当前进度，不再弹出读取完成提示；如果启用记录所有流量采样点，点击 `Capture Trial` 后会先让操作者确认本次 trial 的采样/绘图变量，并选择这些变量是叠加在同一张图，还是每个变量单独一张图，然后打开一个独立的非模态时间-数值曲线弹窗，实时更新已采到的 trial 样点，且不阻塞 Repeatability 操作弹窗。点击图线上的采样点，可以查看该点所属 trial、变量、sample index、相对时间、采集时间和值。flow-rate 变量始终采样；操作者为本次 trial 确认的额外变量会在同一个采样周期内读取，并写入同一个宽表 CSV raw artifact。流量开始后会持续轮询，`v1` 会按配置的 flow-start 后秒数从已经采到的实时样点中自动选取，不再额外暂停后主动读取一次。流量段从非零到回零完成后，程序会用同一套快照变量再读取一次作为后快照；随后输入 `Standard Mass` 并点击 `Calculate Trial Error`，程序才会保存该 trial、计算误差 `e = (delta_m - standard_mass) / standard_mass * 100%`，并记录自动读取的原始 K、`v1`、`v_mean`、流量开始/瞬时采样/结束时间戳、前/后快照、原始 Modbus 轮询 artifact，以及启用时的 trial-sample artifact ID、采样点数量和采样变量名。测试记录表中的 trial 时间就是点击 `Capture Trial` 的时间；后续 trial 误差计算并保存完成的时间，以及流量开始/瞬时采样/结束时间，仍在详情指标中保留。未完成 9 次时关闭窗口，已经完成的 trial 仍保留在测试记录中；下次打开会进入新的操作，不恢复上次未完成的弹窗状态。`Calculate Repeatability` 用于选择一个流量点和该流量点下连续 3 次 trial 来计算重复性；该重复性记录在测试记录中的时间就是重复性结果计算并保存完成的时间。三个流量点都选好后，`Calculate Final K` 用选中的 9 次 trial、重复性结果和这些 trial 自动读取的原始 K 计算并保存最终 K 预览，重复点击只覆盖上一次最终 K 预览。基础 9 次完成后可用 `Add Trial` 继续追加 trial。
- `Current Device Test Records` 会打开锁定当前设备档案的测试记录窗口；`All Test Records` 会从 `Operations` 菜单打开所有使用本程序测试过的设备的全局记录窗口。两个窗口都可与校准弹窗同时存在，支持按操作类型筛选，表格包含具体时间，并在参数栏汇总 K factor 写入状态、变量采样点数量或重复性摘要等关键信息，同时允许在记录关联 run 时编辑备注。对于 Variable Sampling 记录和已保存 trial 样点的 repeatability trial，可用 `View Flow Plot` 重新查看保存曲线，用 `View Flow Data` 以表格查看已保存样点，也可用 `Compare Flow Plots` 先勾选具体要比较的 sample artifact，再按第一采样点或有流量前一点对齐比较。图窗内有变量选择表和图布局选择，可单独显示 flow 或某个额外变量，也可将多个变量叠加到同一张图，或切换为每个变量单独一张图；点击图上的采样点会显示该点的具体样本信息。点击 `Export...` 后可先选择操作类型以及可选的开始/结束时间段，再导出方便其他电脑导入的 JSON 测试记录包；点击 `Import...` 可导入兼容包。重复运行记录会跳过；如果不同电脑产生了相同 run ID 但内容不同，导入时会自动使用新的 imported run ID 保留下来。Excel 导出入口先保留到后续版本。

新建设备档案在尚未选择官方或自定义列表时仍显示占位寄存器表模板。应明确选择
`krohne-prj-main` 后再连接对应 DSP；不要把占位表当作发射机文档使用。

## 灌装模块
打开 `Modules > Filling Module`。该模块用于记录手工灌装试验，并计算常规误差、三次试验重复性和关阀提前量。使用本模块不需要也不会打开硬件连接。

### 选择流量计
1. 第一次进入模块时，从 `Device ID` 列表选择流量计，然后点击 `Select`。
2. 如果列表中没有该流量计，点击 `New Device...`，输入稳定的 Device ID 和可选型号，再点击 `Create`。重复 ID 会被拒绝。
3. 后续需要更换流量计时，先结束当前试验组，再点击 `Change Device...`。

Device ID 只代表流量计，不是 Modbus Unit ID、COM 口、控制器 ID 或阀门 ID。从本模块新建的设备会以中性的 `future_adapter` 类型保存；这不表示已经存在真实硬件适配器。

### 设置灌装工况
1. 选择或输入 `Control / valve label`。新的控制器与阀门组合可用 `New Label...` 新建。该标签与流量计 Device ID 相互独立。
2. 如需使用已有修正值，在 `Advance profile` 中选择提前量档案。一个流量计可以保留多个不可变档案，即使流量点和指定质量相同，也可根据标签、工况值、提前量和时间区分。
3. 常规误差和重复性试验选择 `Regular Test`；需要计算关阀提前量时选择 `Calculate Advance`。
4. 填写脉冲频率切换点 `Pulse switch point (Hz)`、每脉冲质量 `Mass per pulse`、质量单位 `Mass unit`、流量点 `Flow point (g/s)`、指定质量 `Specified mass` 和目标质量 `Target mass`。

指定质量是希望最终达到的质量；目标质量是外部控制器使用的关阀阈值；标准秤质量 `Standard mass` 是每次实际灌装后单独录入的最终称量结果。在提前量模式中，设置提前量之前，目标质量会跟随指定质量。所选质量单位适用于全部质量字段，本模块不自动换算单位。

选择某个 Device ID 后，除标准秤质量外的字段会从该设备最后一次已计算的灌装试验恢复；未保存的草稿不会恢复。标准秤质量每次打开都为空。一个试验组完成第一条试验计算后，模式、标签、脉冲参数、单位、流量点、指定质量和目标质量会锁定。

### 计算和追加试验
1. 在 CoreFlow Studio 之外完成一次实际灌装。
2. 将本次标准秤读数填入 `Standard mass`。
3. 点击 `Calculate Current Trial Error`。该条试验会立即保存并出现在表格中，不需要另点保存。
4. 需要继续时点击 `Add Trial`。下一条试验会以空白标准秤质量准备好；完成下一次外部灌装后再计算。

常规误差公式为：

```text
(标准秤质量 - 指定质量) / 指定质量 * 100%
```

分母不是目标质量。正值表示高于指定质量，负值表示低于指定质量，零表示正好一致。这些数值只是计算结果，不代表自动合格或不合格。

### 计算重复性
在 `Regular Test` 模式下，勾选恰好三条已计算且 Trial 编号连续的记录，然后点击 `Calculate Repeatability`。保存的重复性是这三个百分误差的样本标准差。数量不是三条或编号不连续时，系统会拒绝计算。历史记录会保留三个来源 Trial ID 和完整工况快照。

### 计算并设置提前量
1. 在本组第一条试验计算前选择 `Calculate Advance`。
2. 至少计算三条试验，然后勾选用于提前量计算的记录；编号可以不连续。
3. 点击 `Calculate Advance`。计算会立即写入历史，并显示来源试验、平均标准秤质量、指定质量、带符号的提前量和修正目标质量。
4. 确认结果后点击 `Set Advance`，将其保存为可复用提前量档案。

计算公式为：

```text
平均标准秤质量 = 所选标准秤质量的平均值
提前量 = 平均标准秤质量 - 指定质量
修正目标质量 = 指定质量 - 提前量
```

提前量允许为负值，此时修正目标质量会增大。`Set Advance` 是原子操作：创建新的不可变档案、完成旧的提前量试验组，并用修正目标质量创建新的 `Regular Test` 组和空白 Trial 1。当前表格中的旧未修正试验会被清空，不能与修正后的试验混合。再次设置会新增档案，不会覆盖旧档案。

### 历史与结束试验组
点击 `History...` 打开锁定当前 Device ID 的历史记录。四类记录分别是 `Filling Trial`、`Filling Repeatability`、`Filling Advance Calculation` 和 `Filling Advance Profile Set`。选择记录后可查看来源 Trial ID、输入和结果、完整快照、时间、标签与备注。

更换 Device ID、切换当前提前量档案或开始不同工况前，请点击 `End Group`。包含已保存试验的组会标记为完成；空组会取消。关闭应用会丢弃尚未计算的标准秤质量输入，但已经计算的试验仍保存在数据库中。

### 错误说明与使用边界
- 计算或打开历史前必须先选择 Device ID。
- 控制器/阀门标签和质量单位不能为空；数值配置、标准秤质量和修正目标质量必须是有限且大于零的数。
- 如果工况控件已锁定，请先结束当前试验组再修改。
- 如果界面提示保存或 `Set Advance` 失败，不要假定结果已经写入；排除问题后重试。服务会保证 Set Advance 要么全部完成，要么全部回滚。
- 本模块不读取脉冲，也没有脉冲总数；不控制阀门，不写控制器或发射机，也不发送 Modbus、串口或其他协议通信。外部灌装过程和标准秤读数由操作者负责。

## ASIO/IIS Module
打开 `Modules > ASIO/IIS Module`。该模块拥有自己的连接状态，不会创建或连接发射机通道。

- 选择 backend 和 device，然后检查 sample rate、bit depth、sample format、input/output channel count、samples per frame、test amplitude 等常用参数。
- 使用 `Refresh Devices` 重新扫描设备选项。
- 使用 `Probe` 检查所选设备或 backend 能力。
- 使用 `Connect` 和 `Disconnect` 只改变 ASIO/IIS 模块自身状态。
- 使用 `Tests` 打开 loopback 和 non-loopback 测试弹窗。测试弹窗可生成 sine、square 或 white-noise 信号，并显示 input、output 或两者叠加的曲线。

## 命令行诊断
打包版本中请使用 `CoreFlowStudioConsole.exe`。

打印构建信息：

```powershell
.\CoreFlowStudioConsole.exe --build-info
```

运行无界面的模拟器验证：

```powershell
.\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\smoke-data
```

写出占位 Modbus 寄存器表模板：

```powershell
.\CoreFlowStudioConsole.exe --write-register-map-template .\placeholder_modbus.json
```

写出确定性的 replay CSV 模板：

```powershell
.\CoreFlowStudioConsole.exe --write-replay-template .\replay_template.csv
```

运行 replay 驱动的模拟器 smoke：

```powershell
.\CoreFlowStudioConsole.exe --replay-smoke .\replay_template.csv --data-root .\replay-smoke-data
```

Replay CSV 必须包含 `mass_flow` 列。可选列包括 `captured_at`、`volume_flow`、`density`、`temperature`、`status_flags` 和 `source_channel`。Replay 设备属于只读模拟器设备。

## 安全说明
- 模拟器流程不需要硬件，属于安全路径。
- Calibration Preview 不会写入设备参数。
- Modbus Module 会在操作员于连接弹窗点击 `Connect` 时尝试打开所选 COM 口。
- 具备写入能力的 Modbus 操作必须经过明确的 write-guard 和审计流程。
- 使用源码提取的 `krohne-prj-main` 前仍须核对固件提交、设备字节序和联机只读结果；校准公式、夹具行为和验收阈值仍须另行确认。
- 不要使用占位寄存器表执行生产发射机写入。

## 故障排查
如果 UI 无法打开，请先运行控制台 smoke：

```powershell
.\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\smoke-data
```

如果 smoke 通过但 UI 不显示，请检查是否有系统安全策略阻止 GUI 程序执行。

如果窗口版 UI 在显示前退出，打包启动异常会追加写入：

```text
%LOCALAPPDATA%\CoreFlow Studio\logs\startup.log
```

如果设置了 `COREFLOW_DATA_ROOT`，日志会写入：

```text
<COREFLOW_DATA_ROOT>\logs\startup.log
```

需要查看可见控制台诊断时，请在 PowerShell 中运行 `.\CoreFlowStudioConsole.exe --ui`。

如果 Modbus Module 提示 `Unable to open Modbus RTU transport`，先确认连接弹窗里选中的是 USB 转串口适配器，而不是蓝牙或虚拟 COM 口；再检查该电脑是否安装了适配器驱动、该 COM 口是否已经被串口助手或另一个程序占用，以及波特率、校验位、停止位、Unit ID、超时和字/字节顺序是否与从机设置一致。连接失败信息会包含当前选择的 COM 口和串口参数，方便定位。

如果 `%LOCALAPPDATA%` 无法写入，CoreFlow Studio 会自动尝试其他可写位置。也可以强制指定数据目录：

```powershell
$env:COREFLOW_DATA_ROOT = "D:\CoreFlowStudioData"
.\CoreFlowStudio.exe
```

## 当前限制
- 没有签名安装包或 MSI。
- 没有生产校准公式。
- 已有从 DSP 源码提取的完整主寄存器表，但尚未经过真实设备联机验收，不能据此宣称生产写入已批准。
- 没有已武装的生产校准参数写入流程。
- 没有客户定制报告模板。
- 没有真实 ML 模型执行。
- Replay 文件 UI 当前支持手动输入 CSV 路径，尚未提供文件浏览器。
- 灌装模块 v1 仅支持手工输入：没有脉冲采集或脉冲总数、阀门控制、控制器/发射机写入，也没有生产合格判定阈值。
