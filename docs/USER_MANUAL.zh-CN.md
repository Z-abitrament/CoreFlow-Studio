# CoreFlow Studio 用户手册

## 适用范围
本手册描述当前 M12 版本的 CoreFlow Studio。当前版本是一个 Windows 优先的桌面工具，主要用于基于模拟器的科里奥利流量计 PC 端自动化流程开发、验证和打包交付检查。

当前版本支持模拟设备、实时读数、校准预览、独立 Modbus Module 窗口、自动化工厂测试、基础灵活实验、运行记录检查以及报告/导出生成。独立 Modbus Module 可以在自己的窗口中尝试连接配置的串口 Modbus 设备，但生产真实发射机使用前仍需要验证后的寄存器表、确认后的校准公式和硬件验收结果。

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
UI 当前分为三个主要工作区。

- Connection：选择模拟器或串口模式，添加模拟通道，连接/断开设备，查看设备状态。
- Live Readings：显示质量流量、密度、温度、体积流量，以及质量流量实时曲线。
- Workflows And Results：启动流程，查看状态日志，浏览运行历史，检查结果，并生成导出。

## 模拟器流程
当前 UI 采用模拟器优先开发方式。

1. 保持 Mode 为 `Simulator`。
2. 点击 `Add Simulator`。
3. 选择新出现的设备行，例如 `SIM-UI-001`。
4. 点击 `Connect`。
5. 点击 `Read Live`。

实时读数区域和曲线会显示确定性的模拟器数据。

## Replay CSV 流程
Replay CSV 模式会把记录或生成的样本作为只读模拟器设备加载。

1. 准备或生成 replay CSV 文件。
2. 将 Mode 设置为 `Replay CSV`。
3. 在 Replay CSV 输入框中填写 CSV 路径。
4. 点击 `Add Replay`。
5. 选择 replay 通道。
6. 点击 `Connect`。
7. 点击 `Read Live`，或运行 `Run Experiment` 等已支持流程。

Replay CSV 必须包含 `mass_flow`。可选列包括 `captured_at`、`volume_flow`、`density`、`temperature`、`status_flags` 和 `source_channel`。

## Serial Modbus RTU 模式
主窗口连接面板中的 `Serial Modbus RTU` 目前作为未来真实硬件路径展示，但当前版本禁用了主窗口串口设备创建。

如果选择串口模式后点击 `Add Simulator`，状态日志会提示串口 Modbus 已配置但在硬件验收前禁用。这是有意设计：真实设备寄存器表、验收阈值、夹具规则和写入策略仍属于待确认项。

如需直接执行 Modbus 主站操作，请从工具栏或 `Modules` 菜单打开独立 Modbus Module。该模块独立于主窗口的模拟器/replay 设备列表。

如需生成占位寄存器表模板，仅用于工程评审，可运行：

```powershell
.\CoreFlowStudioConsole.exe --write-register-map-template .\placeholder_modbus.json
```

不要把该占位寄存器表当作生产发射机文档使用。

## 校准预览
Calibration Preview 会根据内置参考点采集模拟器样本并保存预览结果。它不会向设备写入参数。

1. 添加并连接一个模拟器。
2. 选择已连接的模拟器行。
3. 点击 `Calibration Preview`。
4. 等待流程完成。
5. 在 Run History 中选择新生成的运行记录。
6. 在 Result Details 中查看步骤、指标、判定和 artifacts。

当前计算模块仍是占位实现，生产校准公式提供后才能替换为真实算法。

## 独立 Modbus Module
可以从主工具栏或 `Modules` 菜单打开 Modbus Module。该模块拥有自己的连接状态、设备档案、连接弹窗、变量映射、`Operations` 菜单、通信数据码显示区和日志，不需要先在主窗口添加模拟器或 replay 通道。

- 连接前先创建或选择 `Device Profile`。点击 `New Profile` 新建设备档案，点击 `Edit Profile` 修改当前选中的设备档案。`Device ID` 是被测设备的稳定资产 ID，独立于 Modbus RTU 的 Unit ID。不要把 `01` 这类简单从站地址当作设备 ID。Modbus Module 打开时会自动选择最近使用过且仍然存在的设备档案。
- 设备档案会保存设备元数据、连接参数和寄存器映射。选择已有档案后，这些字段会自动加载到 Modbus 窗口。
- 连接前在设备档案弹窗里编辑完整寄存器映射，包括变量名、寄存器类型、地址、字数、数据类型、缩放、单位和是否可写。`Delete` 只删除当前选中的可复用设备档案配置，不会删除已经保存的设备记录和测试记录。
- 选择设备档案后，点击 `Connection...` 打开 Modbus 连接弹窗。端口列表会根据已接入的串口适配器自动发现。插入或拔出 USB 转串口适配器后，可点击 `Refresh Ports` 重新扫描。`Order` 用于选择 32 位数据的字节/字序，例如 `ABCD`、`BADC`、`CDAB` 或 `DCBA`。`Timeout` 和 `Retries` 可用于容忍从机响应较慢或偶发无响应的情况。
- 默认映射包含 `mass_rate`、`mass_acc`、`temperature`、`delta_t`、`zero_offset`、`k_factor`、`low_threshold` 和 `zero_calibration_start`。
- 主窗口显示精简的 `Live Variables` 表格，用于运行时读取、写入和轮询；寄存器类型、地址、字数、数据类型、缩放、单位和是否可写等配置列会隐藏，因为这些内容属于设备档案。
- 在设备档案弹窗里使用 `Add`、`Delete` 和 `Reset` 维护自定义变量行。保存档案后，地址、类型、缩放、单位、是否可写和行顺序会跟随该设备 ID 保存。
- 可编辑档案映射包含采样变量以及零点校准启动 coil。如需切换映射，请先断开连接再修改。
- `Connect` 只会在连接弹窗中打开所选的 Modbus RTU 串口。保存数据时使用当前设备档案的 `Device ID`，连接弹窗中的 Unit ID 只作为 Modbus 协议地址保存。连接完成后可以手动关闭弹窗，模块窗口会保持已连接状态。
- 连接后可使用每行的 `Read` 主动查询一个变量，并刷新 `Value` 显示列。可写变量可填写 `Write Value` 后点击 `Write`；不可写变量会禁用写入控件。写入仍会经过 write guard 和审计日志。
- 勾选变量行的 `Poll` 后点击 `Start Polling`，会每秒轮询一次选中的变量。每轮轮询会按变量逐个读；同一 Modbus 表内相邻地址会尽量合并成一次读请求。
- 使用 `Operations` 菜单执行 `Zero Cal`、`K Factor`、`Repeatability`、`Current Device Test Records` 和 `All Test Records`。当前版本隐藏旧的 `K Factor Inputs` 区域；`K Factor` 会打开独立弹窗。旧的 `Sample Variables` 菜单操作已移除；如需读取变量，请使用每行的 `Read` 或勾选变量后启动轮询。
- 通信数据码表会实时显示读写操作的 TX/RX Modbus 数据码。
- `Zero Cal` 会打开独立弹窗，点击 `Start` 后先读取 `zero_offset` 和 `delta_t`，再通过 write guard 将 `zero_calibration_start` 置 1，等待 3 秒后读取 coil 完成状态，并显示校准前后的 `zero_offset` 和 `delta_t`，供操作员自行判断结果。Live Variables 的 `Value` 列会刷新校准后的值，包括最终的 `zero_calibration_start` coil 状态。
- `K Factor` 会打开独立弹窗，当前启用 Simple 模式，Advanced 模式先保留选项。Simple 模式会像 Zero Cal 一样先采集用户选择的预快照变量，读取配置的流量累积量和当前 K factor，然后通过配置的瞬时流量变量检测一次从非零流量到回零的流量段；流量段结束后等待用户输入标准称重量，计算 `K1`，并可按用户选择写入从机，写入后会再次回读确认。点击 `Save Configuration` 可保存变量对应、轮询间隔和预快照勾选，下次打开 K Factor 时自动恢复；是否写入从机不会被保存。测试记录会保存捕获、计算、可选写入状态以及原始 Modbus 轮询曲线 artifact 引用。
- `Repeatability` 会打开独立弹窗，包含 Three Flow Ranges、Single Flow Range 和保留的 Advanced 模式。主操作弹窗只保留每个 trial 需要改的标准称重量输入；开始第一个 trial 前，点击 `Configuration...` 设置变量对应、轮询间隔、模式、目标流量范围、K Factor 变量、是否保存测试记录、操作备注和预快照勾选，并可点击 `Save Config` 按当前设备档案保存。不同 Device ID 的重复性配置互不共享，也没有全局重复性配置兜底。保存后的操作备注会显示在 Repeatability 操作弹窗中，该操作下每个完成计算的 trial 都会保存同一段备注。每个 trial 开始时会自动读取用户勾选的预快照变量和配置的 K Factor 变量，点击 `Capture Trial` 后会在 Repeatability 操作弹窗中弹出独立进度提示，显示正在获取数据；捕获完成后提示完成，2 秒后自动关闭，也可手动关闭。流量段从非零到回零完成后，输入 `Standard Mass` 并点击 `Calculate Trial Error`，程序才会保存该 trial、计算误差 `e = (delta_m - standard_mass) / standard_mass * 100%`，并记录自动读取的原始 K、`v1`、`v_mean`、流量开始/瞬时采样/结束时间戳，以及原始 Modbus 轮询 artifact。测试记录表中的 trial 时间就是本次 trial 误差计算并保存完成的时间；流量开始/瞬时采样/结束时间仍在详情指标中保留。未完成 9 次时关闭窗口，已经完成的 trial 仍保留在测试记录中；下次打开会进入新的操作，不恢复上次未完成的弹窗状态。`Calculate Repeatability` 用于选择一个流量点和该流量点下连续 3 次 trial 来计算重复性；该重复性记录在测试记录中的时间就是重复性结果计算并保存完成的时间。三个流量点都选好后，`Calculate Final K` 用选中的 9 次 trial、重复性结果和这些 trial 自动读取的原始 K 计算并保存最终 K 预览，重复点击只覆盖上一次最终 K 预览。基础 9 次完成后可用 `Add Trial` 继续追加 trial。
- `Current Device Test Records` 会打开锁定当前设备档案的测试记录窗口；`All Test Records` 会从 `Operations` 菜单打开所有使用本程序测试过的设备的全局记录窗口。两个窗口都可与校准弹窗同时存在，支持按操作类型筛选，表格包含具体时间，并在参数栏汇总 K factor 写入状态或重复性摘要等关键信息，同时允许在记录关联 run 时编辑备注。点击 `Export...` 后可先选择操作类型以及可选的开始/结束时间段，再导出方便其他电脑导入的 JSON 测试记录包；点击 `Import...` 可导入兼容包。重复运行记录会跳过；如果不同电脑产生了相同 run ID 但内容不同，导入时会自动使用新的 imported run ID 保留下来。Excel 导出入口先保留到后续版本。

当前模块在没有工程提供验证寄存器表时仍使用占位寄存器表模板。不要把占位寄存器表当作生产发射机文档使用。

## 工厂测试
Factory Test 会运行固定的模拟器出厂测试路径：

- 通过设备接口获取通信和设备上下文；
- 根据参考质量流量进行测量检查；
- 执行短时稳定性片段；
- 保存步骤级 pass/fail 结果；
- 保存原始 artifacts 和分析记录。

操作步骤：

1. 添加并连接一个模拟器。
2. 选择已连接的模拟器行。
3. 点击 `Factory Test`。
4. 在 Run History 中选择完成的运行记录。
5. 在 Result Details 中检查指标和 artifacts。

## 灵活实验
Run Experiment 会执行当前示例研发流程：

- 采集 6 个模拟器样本；
- 运行 `basic_signal_stats` 信号处理模块；
- 夹具控制保持为 no-op 占位；
- ML 推理保持为占位结果。

操作步骤：

1. 添加并连接一个模拟器。
2. 选择已连接的模拟器行。
3. 点击 `Run Experiment`。
4. 在 Run History 中选择完成的运行记录。
5. 查看处理指标和生成 artifacts。

## 报告与导出
Generate Export 会为选中的运行记录生成报告和导出 artifacts。

1. 在 Run History 中选择一个已完成的运行记录。
2. 点击 `Generate Export`。
3. 如有需要，再次选择该运行记录。
4. 在 Result Details 中查看生成的 artifacts。

导出包包括：

- `operator_report.txt`
- `metrics.csv`
- `measurements.csv`
- `export_manifest.json`

Result Details 中显示的 artifact 路径是相对于当前数据目录的相对路径。

## 状态日志与运行历史
状态日志显示连接动作、实时读数消息、流程开始/完成消息，以及用户请求取消的记录。

Run History 显示已保存运行：

- run ID；
- workflow name；
- device ID；
- status；
- start time。

选择某条运行记录后，Result Details 会显示运行元数据、流程步骤、分析结果、指标和 artifacts。

## 取消行为
`Cancel` 按钮会记录用户请求取消。当前流程是较短的模拟器任务，可能在真正停止前已经完成。如果请求取消后流程仍完成，该运行仍会保存，并可继续检查。

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
- 独立 Modbus Module 会在操作员于连接弹窗点击 `Connect` 时尝试打开所选 COM 口。
- 具备写入能力的 Modbus 操作必须经过明确的 write-guard 和审计流程。
- 真实发射机寄存器表、校准公式、夹具行为和验收阈值必须在硬件使用前提供。
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
- 没有经过生产批准的硬件寄存器表。
- 没有已武装的生产校准参数写入流程。
- 没有客户定制报告模板。
- 没有真实 ML 模型执行。
- Replay 文件 UI 当前支持手动输入 CSV 路径，尚未提供文件浏览器。
