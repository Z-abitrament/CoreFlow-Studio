# CoreFlow Studio 用户手册

## 适用范围
本手册描述当前 M12 版本的 CoreFlow Studio。当前版本是一个 Windows 优先的桌面工具，主要用于基于模拟器的科里奥利流量计 PC 端自动化流程开发、验证和打包交付检查。

当前版本支持模拟设备、实时读数、校准预览、自动化工厂测试、基础灵活实验、运行记录检查以及报告/导出生成。当前版本尚未在 UI 中启用真实硬件操作、生产校准公式、真实参数写入、签名安装包或客户定制报告模板。

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
```

如果从源码运行：

```powershell
.\.venv\Scripts\python -m coreflow --ui
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

## Serial Modbus RTU 模式
`Serial Modbus RTU` 目前作为未来真实硬件路径展示，但当前版本没有在 UI 中启用真实硬件操作。

如果选择串口模式后点击 `Add Simulator`，状态日志会提示串口 Modbus 已配置但在硬件验收前禁用。这是有意设计：真实设备寄存器表、验收阈值、夹具规则和写入策略仍属于待确认项。

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

## 安全说明
- 模拟器流程不需要硬件，属于安全路径。
- Calibration Preview 不会写入设备参数。
- 当前未启用硬件写入流程。
- 真实发射机寄存器表、校准公式、夹具行为和验收阈值必须在硬件使用前提供。
- 未来任何硬件写入都必须经过明确的 write-guard 和审计流程。

## 故障排查
如果 UI 无法打开，请先运行控制台 smoke：

```powershell
.\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\smoke-data
```

如果 smoke 通过但 UI 不显示，请检查是否有系统安全策略阻止 GUI 程序执行。

如果 `%LOCALAPPDATA%` 无法写入，CoreFlow Studio 会自动尝试其他可写位置。也可以强制指定数据目录：

```powershell
$env:COREFLOW_DATA_ROOT = "D:\CoreFlowStudioData"
.\CoreFlowStudio.exe
```

## 当前限制
- 没有签名安装包或 MSI。
- 没有生产校准公式。
- UI 尚未启用真实硬件。
- 没有真实校准参数写入流程。
- 没有客户定制报告模板。
- 没有真实 ML 模型执行。
- 没有 replay 文件 UI。
