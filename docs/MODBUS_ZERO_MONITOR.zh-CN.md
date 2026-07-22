# Modbus 实时零点监视设计

> 文档状态：Phase 1-3及Phase 4只读联机测试代码已实现；真实设备证据待执行。
>
> 目标里程碑：M16 Modbus Real-Time Zero Monitor。
>
> 固件依据：`Krohne_prj` 的实时零点监视快照与上位机处理说明。

## 1. 目标

在现有 Modbus Module 内增加一个独立的小型只读操作 `Zero Monitor`，用于：

- 每 `100 ms` 一次读取 DSP 发布的连续18寄存器零点快照。
- 显示实时零点、正式零点、短期离散度和长期漂移。
- 检查快照一致性、数据有效性、漏读、时间回绕和设备重启。
- 保存可复算的原始快照 CSV、操作记录、配置快照和分析结果。
- 复用现有变量采集、后台任务、实时曲线、历史曲线和 artifact 基础设施。
- 为后续确定稳定阈值和 DSP 自适应零点校准提供实验数据。

本阶段不改变现有 `Zero Cal` 工作流，不自动触发正式零点校准，也不自动写入 `ZeroOffset`。

## 2. 非目标和安全边界

M16 不实现：

- 根据流量测量自动断言当前已经处于零流量。
- 把 `ZeroLive600ms` 或任何上位机统计值直接写入正式零点寄存器。
- 在零点监视运行期间并发执行其他 Modbus 操作。
- 固件侧“等待稳定后再完成校准”的状态机。
- 未经实验确认的生产稳定阈值、允许漂移或合格判定。
- 使用本地假数据替代真实 DSP 快照并将其标记为硬件证据。

零点监视是只读诊断操作。现有正式校准仍由 `Operations > Zero Cal`、`WriteGuardService`、显式写入状态和审计日志控制。

## 3. 已知固件行为

当前固件提供以下时间尺度：

| 项目 | 固件行为 |
| :--- | :--- |
| `deltaT_raw` 输入 | 每 `10 ms`，`100 Hz` |
| 基础统计 | 每10个输入形成一个不重叠 `100 ms` 块 |
| 实时零点 | 最近60点，即 `600 ms` 滑动窗口 |
| 快照发布 | 每 `100 ms`，`10 Hz` |
| 正式零点算法 | 60点排序，去掉最低和最高各10点，平均中间40点 |

DSP 已计算：

- `ZeroBaseMean100ms` 和 `ZeroBaseStd100ms`。
- `ZeroLive600ms`。
- `ZeroTrimStd600ms`、`ZeroTrimRange600ms` 和 `ZeroRawP2P600ms`。
- 快照序号、设备毫秒时间、状态位和窗口有效点数。

DSP 不确认零流量，也不计算长期稳定状态。上位机必须把零流量条件作为独立的操作上下文保存。

## 4. 逻辑变量和参考映射

应用代码只依赖逻辑变量名。地址、寄存器类型、字节序、字序和单位必须来自当前 Device Profile 的寄存器映射。

当前 `Krohne_prj` 固件可使用以下参考配置：

本仓库提供从DSP源码提取并经过本地契约测试的完整ABCD默认主列表
`config/register_maps/krohne_prj_main.json`。该文件对应DSP提交
`f0a1b39ba1f4394253ee0adf7d0aee47c123ff9a`，使用零基PDU地址；启动前仍必须
读取设备 `modbus_byte_order` 核对，不能把ABCD默认值当作已连接设备的事实。

| 逻辑变量名 | 相对offset | 推荐类型 | 固件索引 | PDU地址 | 字数 | 数据类型 |
| :--- | ---: | :--- | ---: | ---: | ---: | :--- |
| `zero_snapshot_sequence_begin` | 0 | input | `96` | `95` / `0x005F` | 1 | `uint16` |
| `zero_monitor_status` | 1 | input | `97` | `96` / `0x0060` | 1 | `uint16` |
| `zero_monitor_tick_ms` | 2 | input | `98` | `97` / `0x0061` | 2 | `uint32` |
| `zero_base_mean_100ms` | 4 | input | `100` | `99` / `0x0063` | 2 | `float32` |
| `zero_base_std_100ms` | 6 | input | `102` | `101` / `0x0065` | 2 | `float32` |
| `zero_live_600ms` | 8 | input | `104` | `103` / `0x0067` | 2 | `float32` |
| `zero_trim_std_600ms` | 10 | input | `106` | `105` / `0x0069` | 2 | `float32` |
| `zero_trim_range_600ms` | 12 | input | `108` | `107` / `0x006B` | 2 | `float32` |
| `zero_raw_p2p_600ms` | 14 | input | `110` | `109` / `0x006D` | 2 | `float32` |
| `zero_window_valid_count` | 16 | input | `112` | `111` / `0x006F` | 1 | `uint16` |
| `zero_snapshot_sequence_end` | 17 | input | `113` | `112` / `0x0070` | 1 | `uint16` |

完整快照必须转换为一次 Modbus 请求：

```text
功能码：FC04 Read Input Registers
起始 PDU 地址：0x005F
寄存器数量：18
```

固件同时允许 FC03 时，可以在专用 Device Profile 中把整块配置为 holding registers，但同一快照内不得混用 register kind。

辅助变量：

| 逻辑变量名 | 用途 | 当前参考PDU地址 |
| :--- | :--- | ---: |
| `zero_offset` | 正式零点基准，只在启动、校准完成或低频校验时读取 | `20` |
| `zero_calibration_start` | 现有正式零点校准触发 coil，只能由受保护工作流写入 | `16` |
| `modbus_byte_order` | 可选的设备字节序校验值 | `52` |

这些地址是已知固件的配置依据，不得硬编码到协议、分析或 UI 类中。绝对块起始
地址可以随Device Profile变化，但上述相对offset、字数和数据类型是零点快照
协议契约，不能由UI自由重排。

## 5. 寄存器映射校验

启动监视前，服务必须校验：

1. 11个快照逻辑变量各存在且只存在一次。
2. 以 `zero_snapshot_sequence_begin` 的配置地址为 `block_start`，每个变量地址
   必须严格等于 `block_start +` 第4节规定的相对offset；尾序号必须在offset 17。
3. 11个变量的word interval互不重叠，其并集必须恰好为
   `[block_start, block_start + 18)`，无空洞、别名或多余映射。
4. 该18字区间不得再被其他非快照逻辑变量或别名覆盖。
5. 快照变量属于同一种 Modbus register kind，只允许全部input或全部holding；
   profile选定后运行中不得自动切换。
6. 所有快照变量均为只读。
7. `uint16`严格1字，`uint32`和`float32`严格2字，数据类型必须与第4节一致。
8. 多字变量使用当前 Device Profile 配置的 byte order 和 word order；16位字段
   不应用32位字节/字序变换。
9. `zero_offset` 在快照块外单独检查；缺少或不可读时仍允许只采集，但offset
   check显示为不可用。
10. 所有32位快照变量必须使用相同byte/word order；`zero_offset` 存在时也必须
    使用同一组合。
11. 所有快照字段的scale必须为 `1.0`；六个float32零点值的单位必须为 `us`，
    `zero_monitor_tick_ms` 的单位必须为 `ms`，不得在通用解码层静默换算。
12. `zero_offset` 存在时必须是2字 `float32`、scale `1.0`、单位 `us`，并与
    快照32位字段使用相同byte/word order。它在当前DSP中是holding/RW寄存器，
    但本监视操作只读取，不因Profile如实标记 `writable=true` 而拒绝。

快照映射校验失败时不得发出任何设备请求，不得尝试部分读取、逐变量读取或
FC03/FC04 fallback。界面显示全部校验错误，并按第12节只保存无Run ID的error
attempt。结构化错误码至少包括：

```text
MISSING_VARIABLE
DUPLICATE_VARIABLE
WRONG_RELATIVE_OFFSET
WORD_OVERLAP
BLOCK_GAP
UNEXPECTED_BLOCK_MAPPING
MIXED_REGISTER_KIND
WRITABLE_SNAPSHOT_VARIABLE
WRONG_DATA_TYPE
WRONG_WORD_COUNT
WRONG_SCALE
WRONG_UNIT
WRONG_REGISTER_KIND
```

### 5.1 ByteOrder启动前核对

结构映射校验通过后、创建Run之前，如果Profile存在可读uint16
`modbus_byte_order`，读取一次该寄存器。它是16位值，不受待核对的32位顺序
影响。该低频启动检查沿用连接配置的传输重试次数，不使用10 Hz监视的
`transport_retry_count=0`覆盖。

当前DSP把该值映射为FC03 holding/RW。这里的“可读”不要求寄存器本身为只读；
Zero Monitor只调用读取接口，绝不写该寄存器，修改通信配置仍属于独立受保护流程。

| 设备枚举 | 固件顺序 | Profile byte order | Profile word order |
| ---: | :--- | :--- | :--- |
| 0 | `ABCD` | `big` | `big` |
| 1 | `BADC` | `little` | `big` |
| 2 | `CDAB` | `big` | `little` |
| 3 | `DCBA` | `little` | `little` |

处理规则：

- 枚举有效且与所有32位快照字段及可用的 `zero_offset` 配置一致：
  `byte_order_verification=verified`，允许继续启动。
- 枚举与Profile不一致：以 `BYTE_ORDER_MISMATCH` 阻止启动。
- 枚举不在0...3：以 `BYTE_ORDER_VALUE_INVALID` 阻止启动。
- 已配置该寄存器但读取失败：以 `BYTE_ORDER_READ_FAILED` 阻止启动。
- Profile没有该寄存器：允许诊断Run，保存
  `byte_order_verification=unavailable` 并显示 `BYTE_ORDER_UNVERIFIED`；该Run
  不得进入 `STABLE` 或产生pass/fail。

失败路径不读取18字快照、不创建Run，只保存无Run ID的error attempt，包含设备
枚举、解析顺序、Profile顺序、错误和核对时间。服务不得自动修改设备或Profile；
操作者只能停止并编辑Profile后重试。

## 6. 应用模块边界

目标代码边界：

```text
coreflow.protocols.modbus.device
    实现 ModbusConfigurationBlockReader capability
    复用 read_configuration_parameters(..., merge_adjacent=True,
                                      transport_retry_count=0)
        |
        v
coreflow.app.modbus_zero_monitor
    快照读取、序号/时间展开、连续段、轮询和结果模型
        |
        +--> coreflow.analysis.zero_monitor
        |       纯函数：独立候选、长短期指标、状态和原因
        |
        +--> coreflow.storage / ArtifactStore
        |       原始CSV、operation attempt、analysis result
        |
        v
coreflow.ui.modbus_zero_monitor
    非模态界面、状态、指标和复用实时曲线
```

`ModbusModuleRuntime` 只增加薄入口，提供当前 device、register map、repository、artifact store、test session 和 operation metadata。专用服务不把协议细节写入 Qt 类。

`ModbusConfigurationBlockReader` 是零点监视服务使用的小型typing Protocol，
不加入通用 `FlowmeterDevice` ABC。它由 `ModbusRtuFlowmeterDevice` 和fake reader
实现，约定：

```python
def read_configuration_parameters(
    parameter_names: tuple[str, ...],
    *,
    merge_adjacent: bool = False,
    transport_retry_count: int | None = None,
) -> tuple[ConfigurationParameter, ...]: ...
```

`transport_retry_count=None` 保持其他Modbus操作使用连接配置中的重试次数；零点
监视固定传入0。该参数只控制超时、CRC或异常响应后的传输重试，不控制首尾序号
不一致时的快照重读。

### 6.1 建议数据模型

`ZeroMonitorSnapshot` 至少包含：

```text
host_receive_time
sequence
sequence_delta
status
device_tick_ms_raw
device_tick_ms_unwrapped
base_mean_100ms
base_std_100ms
live_zero_600ms
trim_std_600ms
trim_range_600ms
raw_p2p_600ms
window_valid_count
official_zero_offset
zero_drift_from_cal
snapshot_consistent
communication_gap
restart_segment
accept_for_statistics
```

`ZeroMonitorAnalysisConfig` 至少包含：

```text
long_window_s
minimum_stable_duration_s
stability_thresholds:
    short_std:   {enabled, limit, source}
    short_range: {enabled, limit, source}
    raw_p2p:     {enabled, limit, source}
    repeat_std:  {enabled, limit, source}
    long_range:  {enabled, limit, source}
    trend_span:  {enabled, limit, source}
    max_step:    {enabled, limit, source}
offset_limit
offset_limit_source
```

已确认的工程单位契约：固件把输入作为 `deltaT_us` 发布，零点快照幅值和
`ZeroOffset` 使用相同单位。因此 `short_std`、`short_range`、`raw_p2p`、
`repeat_std`、`long_range`、`trend_span`、`max_step` 和可选offset limit的
配置单位均为 `us`；`ZeroLongSlope` 为 `us/s`，窗口和持续时间为 `s`。第一版
不执行隐式单位换算。

七项稳定性判据默认 `enabled=true`，但生产 `limit` 和 `source` 默认为未配置。
每个启用判据必须有有限且非负的limit和非空source；需要停用时必须明确保存
`enabled=false`，不得把空limit解释为隐式停用。没有启用判据，或任一启用判据
配置不完整时，允许采集和计算，但不能输出 `STABLE`。

`offset_limit` 是独立的可选偏差告警阈值，不属于七项稳定性判据。未配置时偏差
告警显示为不可用，但不阻止稳定性结论。

`minimum_stable_duration_s` 也必须显式配置为有限非负值；未配置或非法时属于
阈值配置不完整。值为0表示长期窗口就绪且判据首次全部满足时即可进入
`STABLE`，不增加隐含等待时间。

M16生产基线明确把七项limit、可选offset limit和
`minimum_stable_duration_s` 留空，等待多台设备只读台架实验和审批。界面显示
`Not configured`，会话允许采集、绘图和导出，但总体状态保持 `EVALUATING`，
pass/fail为null。任何自动化测试阈值必须带 `test_only=true`，不得保存为生产
Device Profile或由软件作为默认值补入。

## 7. 轮询流程

第一版轮询目标是协议常量 `ZERO_MONITOR_POLL_INTERVAL_MS = 100`，不是
Device Profile或每设备工作流配置。界面只读显示目标周期和当前实际频率，不提供
编辑控件。改变该常量需要单独评审带宽、调度、候选选择和阈值语义。

### 7.1 启动

1. 确认 Modbus Module 已连接并选中了稳定 Device ID。
2. 校验快照逻辑变量和连续映射。
3. 执行第5.1节ByteOrder核对；失败时在创建Run和读取快照前停止。
4. 校验当前零流量确认仍绑定同一个Device ID、Device Profile和register-map；
   上下文不匹配时先自动清除确认。
5. 保存 Device Profile、连接设置、register-map、ByteOrder核对、分析配置和本次
   零流量确认快照。
6. 读取一次 `zero_offset`。
7. 暂停主窗口普通轮询，并通过现有 `_busy` 操作互斥阻止其他 Modbus 操作。
8. 创建后台采集任务、取消事件和实时回调。

### 7.2 每个100 ms周期

1. 调用一次：

   ```python
   device.read_configuration_parameters(
       ZERO_MONITOR_VARIABLE_NAMES,
       merge_adjacent=True,
       transport_retry_count=0,
   )
   ```

2. 正常响应、超时、CRC错误或Modbus异常响应均只发出这一次物理请求；传输
   错误不在同一逻辑周期自动重试，下一个调度周期是自然重试机会。
3. 解码后检查首尾序号、状态位、有效点数、有限浮点数和设备时间。只有首尾序号
   不一致时允许立即执行一次相同的完整块读取，因此该逻辑周期最多两次物理请求。
4. 更新序号和设备时间展开状态。
5. 保存本次通信记录；只有合格快照进入稳定性统计。
6. 通过线程安全进度回调更新实时曲线和指标。
7. 使用单调时钟维护 `start + n * 100 ms` 调度点。一次读取或撕裂重读越过后续
   调度点时记录overrun和跳过的schedule slot，然后等待第一个严格晚于当前时间的
   调度点；不得立即突发追赶，也不得存在两个并发请求。

分别累计 `logical_poll_count`、`physical_request_count`、
`torn_snapshot_reread_count`、`transport_failure_count`、
`poll_overrun_count` 和 `missed_schedule_slot_count`。一次撕裂重读仍只增加一个
logical poll，但增加两次physical request和一次torn reread。

### 7.3 停止

1. 操作者点击 `Stop`、设备断开或模块关闭时设置取消事件。
2. 完成当前安全的读取边界后停止，不强制中断串口调用。
3. 写入原始 CSV artifact、分析结果和 `modbus_zero_monitor` operation attempt。
4. 恢复主窗口普通轮询和其他操作控件。
5. 如果有逻辑轮询行但没有成功快照，保留失败行并以 `DATA_GAP`、null数值指标
   完成诊断记录；如果连一个逻辑轮询都未开始，则操作为error且不创建空曲线。
6. Run完成持久化后清除界面的零流量确认；下一次Start必须重新确认。

## 8. 快照一致性和连续性

### 8.1 状态位

| 位 | 名称 | 上位机处理 |
| ---: | :--- | :--- |
| 0 | `BASE_READY` | 0时为 `NOT_READY`；与LIVE_READY矛盾时为数据错误 |
| 1 | `LIVE_READY` | 0时为 `NOT_READY`，不进入统计 |
| 2 | `DATA_VALID` | 0时为 `DATA_GAP`，结束连续段 |
| 3 | `ZERO_CAL_RUNNING` | 继续保存但暂停统计，状态为 `EVALUATING` |
| 4 | `INTERNAL_ERROR` | `DATA_GAP`，结束连续段并保留通信证据 |
| 5...15 | reserved | 非零时暂停统计并报告不支持的状态位 |

### 8.2 接受规则

快照只有同时满足以下条件才进入统计：

```text
SequenceBegin == SequenceEnd
BASE_READY == 1
LIVE_READY == 1
DATA_VALID == 1
ZeroWindowValidCount == 60
所有浮点值为有限数
ZERO_CAL_RUNNING == 0
INTERNAL_ERROR == 0
reserved bits == 0
```

首尾序号不一致时，第一次响应不进入统计，最多立即完整重读一次。第二次响应
一致时使用第二次快照；第二次传输失败或仍不一致时记录数据缺口，并回到下一个
正常调度周期，不无限重试。该快照重读不启用额外传输重试。

### 8.3 序号

```text
sequence_delta = (current - previous) mod 65536
```

- `0`：只有设备tick和完整18字payload也与上一响应一致时才是重复快照，不追加
  统计样本；同序号但tick或payload变化为 `DUPLICATE_PAYLOAD_CHANGED`，按
  `DATA_GAP` 处理。
- `1`：正常下一帧。
- `2...32767`：向前漏读，本行显示 `DATA_GAP`，结束当前连续统计段。
- `32768...65535`：无法解释的序号回退，结合设备tick标记设备重启/协议异常。
- `65535 -> 0`：正常回绕，不视为重启。

### 8.4 设备时间

`zero_monitor_tick_ms` 是32位毫秒时间，约49.7天回绕一次。对同一连续段计算：

```text
tick_delta = (current_tick - previous_tick) mod 2^32
```

- `0` 只允许在完整重复快照中出现。
- `1...0x7FFFFFFF` 是可向前展开的时间，包括32位正常回绕。
- `0x80000000...0xFFFFFFFF` 是无法解释的回退，标记设备重启并结束连续段。
- 对向前sequence delta `d`，固件基线要求 `tick_delta == d * 100 ms`；不相等为
  `DEVICE_TIME_DISCONTINUITY`。sequence gap仍按gap处理，即使时间增量匹配。

上位机把设备时间展开为段内单调64位值。设备重启后新段重新建立展开基准，
不得伪造跨重启的连续设备时间。

统计和绘图以展开后的设备时间为主时间轴；`host_receive_time` 单独用于通信延迟和轮询 overrun 分析。

### 8.5 连续段中断和恢复

服务维护显式 `segment_break_pending`：

- `DATA_VALID=0`、valid count不等于60、NaN/Inf、`INTERNAL_ERROR=1`、首尾序号
  重读失败、序号跳变、通信失败或设备重启时，本行状态为 `DATA_GAP`，结束旧段、
  清空候选和稳定计时，并设置break pending。
- `LIVE_READY=1` 但 `BASE_READY=0` 是 `READY_BITS_INCONSISTENT` 数据错误，按
  `DATA_GAP` 处理。正常启动阶段BASE或LIVE未就绪只显示 `NOT_READY`；已经存在
  活跃连续段后，BASE或LIVE从1变0会结束旧段、清空计时并等待重新就绪。
- `ZERO_CAL_RUNNING=1` 时继续跟踪序号/设备时间并保存行，但不进入统计；首次
  看到该位时结束旧段并清空稳定计时，持续显示
  `EVALUATING + ZERO_CAL_ACTIVE`。
- reserved bits非零时同样结束旧段并清空稳定计时，持续显示
  `EVALUATING + UNSUPPORTED_STATUS_BITS`，但不把未知位解释为已知故障。
- ZERO_CAL或reserved条件清除后的第一条一致、唯一且有效快照建立新连续段和
  `sequence_anchor`，作为新段第一个合格统计样本，清除break pending，状态从
  `NOT_READY` 重新开始；校准前或未知状态位出现前的样本不得复用。
- DATA_GAP类事件后的第一条一致、唯一且有效快照同样作为新段第一个统计样本和
  候选锚点，但因窗口未填满显示 `NOT_READY`；导致gap的事件行不进入新段统计。
- 重复序号保存为原始行，增加 `DUPLICATE_SNAPSHOT` advisory，但不结束段、不
  推进候选或稳定计时，实时状态保持上一次非重复快照的状态。
- 单纯poll overrun且设备序号/时间连续时只增加 `POLL_OVERRUN` advisory，不
  结束段。累计错误、gap和advisory计数不会因实时状态恢复而清零。

## 9. 上位机分析

### 9.1 实时和短期参数

每个有效快照直接显示：

```text
RealTimeZero = zero_live_600ms
ZeroDriftFromCal = zero_live_600ms - official_zero_offset
ShortStd = zero_trim_std_600ms
ShortRange = zero_trim_range_600ms
RawP2P = zero_raw_p2p_600ms
```

短期判据框架：

```text
ShortStable =
    AND(所有enabled的短期判据均未超限)
```

默认启用的短期判据为 `short_std`、`short_range` 和 `raw_p2p`。明确
`enabled=false` 的判据不参与合取，也不产生超限原因码。

### 9.2 独立600 ms候选值

相邻 `zero_live_600ms` 窗口共享50/60个底层样本，不能把全部10 Hz值当作独立重复校准结果。

每个连续段从第一个有效快照建立 `sequence_anchor`，只选择：

```text
(sequence_unwrapped - sequence_anchor) mod 6 == 0
```

形成相隔600 ms的独立候选序列 `Z[k]`。目标序号漏读时跳过该候选，不使用相邻重叠窗口替代。

### 9.3 长期指标

第一版长期判定窗口预设为 `30 s`、`60 s`、`300 s` 和自定义。自定义
`long_window_s` 必须位于闭区间 `[12, 86400] s`，即最大24小时。`10 s` 只
作为曲线快速观察范围，不写入 `long_window_s`，也不参与长期判据。

24小时是滚动判定窗口上限，不是采集会话时长上限。会话可以持续运行到操作者
Stop；超过24小时后CSV继续追加，分析只保留和计算最近 `Tlong` 内的独立候选。

以最新候选设备时间为右边界，只使用当前连续段内满足
`latest_device_time - candidate_device_time <= Tlong` 的独立候选 `Z[k]`，
等于左边界的候选包含在窗口内。

计算约定：

- `ZeroRepeatStd` 使用样本标准差，等价于 `numpy.std(Z, ddof=1)`。
- P5/P95使用 `numpy.percentile(Z, [5, 95], method="linear")`。
- 趋势拟合时间单位为秒；先从每个设备时间减去窗口设备时间均值，再执行最小
  二乘拟合，避免展开后的大时间戳降低数值稳定性。
- `ZeroLongSlope` 单位为“零点值单位/秒”；`ZeroTrendSpan` 固定投影到配置的
  `Tlong`，即 `abs(ZeroLongSlope) * Tlong`，不是乘实际首尾候选间隔。
- 所有状态阈值使用 `metric <= limit` 作为通过，恰好等于limit时通过；状态判定
  不加入未配置的epsilon或容差。

随后计算：

| 指标 | 定义 |
| :--- | :--- |
| `ZeroLongMean` | `mean(Z)` |
| `ZeroRepeatStd` | 样本标准差，分母 `M-1` |
| `ZeroLongRange` | `max(Z) - min(Z)` |
| `ZeroLongP95P5` | `P95(Z) - P5(Z)`，`M >= 20` 后显示 |
| `ZeroLongSlope` | 设备时间与 `Z` 的最小二乘直线斜率 |
| `ZeroTrendSpan` | `abs(ZeroLongSlope) * Tlong` |
| `ZeroMaxStep` | `max(abs(Z[k] - Z[k-1]))` |
| `ZeroAdjacentDiffRms` | `sqrt(0.5 * mean((Z[k] - Z[k-1])^2))` |

只有当前连续段已经覆盖所选 `Tlong` 且窗口内独立候选数 `M >= 20` 时，
长期窗口才视为就绪并允许计算 `LongStable`。即使时间窗口已经填满，漏读导致
`M < 20` 时仍保持 `NOT_READY`；不得用重叠600 ms窗口补足候选数。

长期判据框架：

```text
LongStable =
    AND(所有enabled的长期判据均未超限)
```

默认启用的长期判据为 `repeat_std`、`long_range`、`trend_span` 和
`max_step`。

`OffsetExceeded` 单独计算，不参与 `ShortStable`、`LongStable` 或总体状态：

```text
OffsetCheckAvailable = official_zero_offset可用
                       AND offset_limit有限且非负
                       AND offset_limit_source非空
OffsetExceeded = abs(ZeroDriftFromCal) > offset_limit  # 仅在Available时
```

因此允许出现 `STABLE + OFFSET_EXCEEDED`，含义是当前零点重复性和趋势稳定，
但相对正式零点存在显著偏差，操作者可以据此决定是否进入受保护的正式校准。

### 9.4 状态和原因

状态优先级：

1. 通信/一致性/序号/时间硬故障、就绪位矛盾、无效数据或内部错误：
   `DATA_GAP`。
2. `ZERO_CAL_RUNNING=1` 或reserved bits非零：`EVALUATING`。
3. BASE/LIVE窗口未就绪、segment break后的新段刚建立、连续段未覆盖 `Tlong`
   或长期独立候选 `M < 20`：`NOT_READY`。
4. 零流量未由操作者确认、没有启用稳定性判据、任一启用判据缺少有效limit、
   source或匹配单位、`minimum_stable_duration_s` 未有效配置，或ByteOrder未核对：
   `EVALUATING`。
5. 上述前置条件满足后，任一启用稳定性判据超限：`UNSTABLE`。
6. 所有启用判据满足、但持续时间小于 `minimum_stable_duration_s`：
   `EVALUATING`。
7. 零流量已确认、至少一项稳定性判据启用、配置完整且所有启用判据持续满足
   指定时间：`STABLE`。

稳定持续时间使用展开后的设备时间：

```text
stable_since_device_time = 所有前置条件和enabled判据首次同时满足的设备时间
stable_duration_s = (latest_device_time - stable_since_device_time) / 1000
```

数据缺口、设备重启、无效数据、内部错误、ZERO_CAL、reserved状态或任一启用
稳定性判据超限时，
`stable_since_device_time` 清空。重新满足后从新的设备时间重新计时。重复序号不
推进也不清空计时；单纯poll overrun但快照和序号连续时不清空；offset advisory
无论是否超限都不清空。

影响总体状态的原因码至少包括：

```text
ZERO_FLOW_UNCONFIRMED
THRESHOLD_CONFIGURATION_INCOMPLETE
THRESHOLD_UNIT_MISMATCH
NO_STABILITY_CRITERIA_ENABLED
BYTE_ORDER_UNVERIFIED
STABLE_DURATION_INSUFFICIENT
BASE_NOT_READY
LIVE_NOT_READY
READY_BITS_INCONSISTENT
DUPLICATE_PAYLOAD_CHANGED
DEVICE_TIME_DISCONTINUITY
ZERO_CAL_ACTIVE
UNSUPPORTED_STATUS_BITS
SHORT_NOISE_HIGH
SHORT_RANGE_HIGH
RAW_SPIKE_HIGH
REPEATABILITY_HIGH
LONG_RANGE_HIGH
TREND_HIGH
STEP_HIGH
DATA_INVALID
COMMUNICATION_GAP
DEVICE_RESTART
INTERNAL_ERROR
```

独立advisory code至少包括：

```text
OFFSET_EXCEEDED
OFFSET_CHECK_UNAVAILABLE
POLL_OVERRUN
DUPLICATE_SNAPSHOT
```

advisory不改变 `NOT_READY / DATA_GAP / EVALUATING / STABLE / UNSTABLE`。

## 10. 界面方案

入口：

```text
Modbus Module > Operations > Zero Monitor
```

使用独立非模态窗口，与现有 `Variable Sampling`、`Zero Cal` 和 `Repeatability` 操作一致。窗口关闭后当前 Modbus 连接保持不变。

### 10.1 顶部操作栏

- 当前 Device ID，只读显示。
- `Zero-flow confirmed` 复选框及确认时间；只能在Start前由操作者主动设置，
  运行期间锁定。未勾选仍可启动诊断采集，但总体状态不能显示 `STABLE`。
- 长期判定窗口选择：`30 s / 60 s / 300 s / Custom`；Custom必须在
  `12...86400 s`，越界时禁止保存和启动，并显示字段校验错误。
- 阈值配置入口。
- `Start`、`Stop`、`Save Config`、`Zero Cal...` 和关闭按钮。

`Zero Cal...` 只打开现有正式校准界面，不在监视界面内发送写请求。监视运行时该按钮禁用；操作者必须先停止并保存监视记录。

确认状态只对下一次Start创建的单个Run有效。确认记录包含 `confirmed`、
`operator`、`confirmed_at`、`device_id`、`profile_id` 和register-map checksum。
Stop、取消、错误结束、断开/重连、更换Device ID、更换Device Profile、修改
register map或重新打开窗口时自动清除。运行中不得修改确认状态；从未确认诊断
切换到正式稳定性判定时，必须Stop、重新确认并启动新的Run，旧样本不得带入。

### 10.2 状态区

固定显示：

- 采集状态和连续段编号。
- 快照序号、设备时间、有效点数。
- `BASE_READY`、`LIVE_READY`、`DATA_VALID`、`ZERO_CAL_RUNNING`、`INTERNAL_ERROR`。
- 正常帧、重复帧、漏读、无效帧、重启和 overrun 计数。
- `NOT_READY / DATA_GAP / EVALUATING / STABLE / UNSTABLE`。
- 当前原因码列表。
- 独立的offset check状态：`UNAVAILABLE / WITHIN_LIMIT / EXCEEDED`；
  `EXCEEDED` 可以和 `STABLE` 同时显示。
- ByteOrder核对状态：`VERIFIED / UNAVAILABLE`；UNAVAILABLE时显示
  `BYTE_ORDER_UNVERIFIED` 并保持诊断状态。

### 10.3 曲线区

复用现有实时多变量曲线能力：

- 主曲线默认显示 `zero_live_600ms` 和 `official_zero_offset`。
- 可选显示 `zero_drift_from_cal`。
- 短期曲线可选 `zero_trim_std_600ms`、`zero_trim_range_600ms` 和 `zero_raw_p2p_600ms`。
- 支持现有 overlay 和 separate 布局。
- 点击数据点显示设备时间、主机接收时间、序号、变量、值和单位。
- 曲线显示范围可以选择 `10 s` 快速观察，但该显示设置不改变长期判定窗口、
  候选集合或状态。

实现时优先把现有 `RepeatabilityFlowPlotDialog` 的通用实时绘图部分提取为共享组件，并保持现有 Variable Sampling 和 Repeatability 行为不变。

### 10.4 指标表

指标表按实时、短期和长期分组，显示当前值、阈值、状态和单位。表内不允许直接编辑正式零点或发送写请求。

## 11. 复用决策

| 现有能力 | 决策 | 原因 |
| :--- | :--- | :--- |
| `read_configuration_parameters(..., merge_adjacent=True)` | 直接复用 | 能把连续映射合并为一次请求 |
| `run_variable_sampling()` 轮询循环 | 不直接复用 | 当前逐变量读取，不能保证快照原子性 |
| Modbus Module `_run_task` 和取消事件模式 | 复用 | 已保证后台运行和UI线程隔离 |
| `RepeatabilityFlowPlotDialog` 实时曲线能力 | 提取通用组件后复用 | 已支持多变量、布局和数据点查看 |
| flow-sample宽表CSV和历史曲线查看器 | 扩展元数据后复用 | 已能保存、打开和比较多变量曲线 |
| per-Device配置目录 | 复用 | 与 Variable Sampling、Zero Cal配置一致 |
| `ModbusOperationAttempt` 和 Test Records | 复用 | 保持设备中心的历史与导入导出 |
| `analyze_stability()` | 不直接套用 | 缺少序号、设备时间、独立窗口和原因码语义 |
| `WriteGuardService` 和 `ZeroCalibrationWorkflow` | 正式校准时继续复用 | 监视服务保持只读 |

## 12. 配置和持久化

### 12.1 每设备配置

建议路径：

```text
config/workflow_templates/devices/<device>/modbus_zero_monitor.json
```

保存：

- 长期观察窗口。
- 曲线变量和布局。
- 阈值及阈值配置来源。
- 最小持续稳定时间。

不保存：

- `Zero-flow confirmed` 当前勾选状态。
- poll interval；第一版固定为协议常量100 ms。
- 自动校准或自动写入意图。
- 连接密码或不存在的生产阈值。

当前生产配置文件可以保存空阈值结构和 `status=pending_bench_approval`，但不得
保存来自fixture的test-only数值。以后录入生产阈值时必须同时记录值、单位
`us`、来源、审批标识和生效时间。

### 12.2 操作记录

操作名：

```text
modbus_zero_monitor
```

一个至少产生一行逻辑轮询记录的监视会话保存：

- 一个 `RunSession`，`run_type=stability`、
  `workflow_name=modbus_zero_monitor`、`workflow_version=1`。
- 两个 `WorkflowStep`：`zero_monitor_capture`（`capture`）和
  `zero_monitor_analysis`（`analysis`）。
- 一个 `ModbusOperationAttempt`。
- 一个原始宽表 CSV artifact。
- 一个 `modbus_zero_monitor_stability` Analysis Result；全部轮询失败时数值指标为
  null，但状态、错误和计数仍保存。
- Device Profile、register map、阈值、零流量确认和软件版本快照。

Run配置快照固定保存 `target_poll_interval_ms=100`。operation summary和
Analysis Result保存基于单调时钟逻辑轮询开始时间计算的实际周期统计：

```text
observed_poll_period_mean_ms
observed_poll_period_p50_ms
observed_poll_period_p95_ms
observed_poll_period_p99_ms
observed_poll_period_max_ms
observed_poll_rate_hz
```

实际周期是相邻逻辑轮询开始时间之差；实际频率在至少两个轮询开始时间存在时为
`(logical_poll_count - 1) / (last_poll_start - first_poll_start)`，否则为null。
独立600 ms候选始终按设备sequence每6帧选择，不根据主机实际周期重新采样。
实际频率不足只记录时序质量，不自动改变目标周期、DSP时间语义或稳定阈值。

零流量确认虽然不写入每设备配置，但必须作为该Run的不可变溯源快照保存并随
Test Records JSON导出。未确认Run保存 `confirmed=false`；已确认Run同时保存
操作者、确认时间、Device ID、profile ID和register-map checksum。

10 Hz长时间数据不逐点写入 SQLite `variable_samples`。SQLite保存摘要和artifact引用，逐点数据保存在文件中。

#### 12.2.1 创建和结束顺序

1. 连接、Device ID和寄存器映射的启动前校验成功后，创建状态为
   `running` 的 `RunSession`、capture step和operation attempt；analysis step
   以 `pending` 状态创建。启动前校验失败只保存无 `run_id` 的error attempt，
   不创建空run或artifact。
2. 同时在该run的artifact目录创建 `*.csv.partial`，把相对路径写入Run配置快照。
   每个逻辑轮询立即追加一行，包括超时、CRC和异常响应；CSV包含失败和无效
   快照，但只有合格快照进入统计。
3. writer至少每1秒执行一次 `flush` 和 `fsync`，并在Stop、取消、断开或错误时
   立即执行。正常结束时关闭文件，在同一目录内原子重命名为正式 `.csv`，然后
   计算checksum并保存artifact记录。
4. 只要至少一个逻辑轮询行已经落盘，就保存artifact；如果Start后尚未开始任何
   逻辑轮询便结束，则不创建空曲线。
5. 有至少一个逻辑轮询行时保存Analysis Result，并在其
   `input_artifact_ids` 中引用CSV；即使全部为失败行，也保存计数、错误和
   `DATA_GAP`，数值指标允许为null。没有任何逻辑轮询行时analysis step为
   `skipped`，不创建伪造Analysis Result或空曲线。
6. 更新capture/analysis step和operation attempt后，最后更新RunSession终态。
   Test Records不得看到已经标记终态、但仍缺少预期artifact或analysis引用的
   正常完成run。

#### 12.2.2 状态映射

| 结束原因或最终结论 | RunSession | capture step | analysis step | operation attempt | pass/fail |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 操作者Stop，最终为 `STABLE` | `passed` | `completed` | `passed` | `passed` | `passed` |
| 操作者Stop，最终为 `UNSTABLE` | `failed` | `completed` | `failed` | `failed` | `failed` |
| 操作者Stop，最终为 `NOT_READY`、`EVALUATING` 或 `DATA_GAP` | `completed` | `completed` | `completed` | `completed` | null |
| 操作者关闭或取消，已有逻辑轮询行 | `canceled` | `canceled` | `completed`，保存诊断结果 | `canceled` | null |
| 操作者关闭或取消，无逻辑轮询行 | `canceled` | `canceled` | `skipped` | `canceled` | null |
| 设备断开、通信异常或程序错误，已有逻辑轮询行 | `error` | `error` | `completed`，保存部分诊断结果 | `error` | null |
| 设备断开、通信异常、程序错误或Stop时无逻辑轮询行 | `error` | `error` | `skipped` | `error` | null |

正常Stop不是取消。`failed` 只表示已经形成的稳定性判据不通过；无法形成判据
使用 `completed` 和null pass/fail，执行故障使用 `error`。取消或错误路径保留已有
CSV和诊断结果，并在attempt summary和step `error_message` 中记录结束原因。

#### 12.2.3 异常退出恢复

应用启动或初始化Modbus Module历史服务时，检查
`workflow_name=modbus_zero_monitor` 且仍为 `running` 的run：

1. 只接受artifact根目录内、由Run快照记录的 `.csv.partial` 路径。
2. 文件含至少一条数据行时，关闭/校验后原子完成为CSV，保存
   `complete=false`、`recovered=true`、`recovery_reason=unclean_shutdown`
   metadata，并生成pass/fail为null的诊断Analysis Result；全部为失败行时指标
   为null，但错误和计数仍保留。
3. 没有数据行时不创建artifact或Analysis Result。
4. capture step和operation attempt标记 `error`，analysis step按是否存在数据行
   标记 `completed` 或 `skipped`，Run标记 `error`；恢复后的Run不得继续采集。

每秒 `flush+fsync` 将进程崩溃或断电时的设计数据损失窗口限制在最后不足1秒。
恢复失败必须保留明确诊断，不得把partial文件静默当作正常完成记录。

### 12.3 CSV字段

为直接复用现有 Test Records 通用曲线和数据表，CSV前三列固定沿用现有
flow-sample格式。每个逻辑轮询只写一行：

- `captured_at` 是该逻辑轮询成功接收或失败结束时的UTC ISO主机时间，因此失败
  行也必须有值。
- `elapsed_s` 使用单调时钟从Run开始时刻起算。
- `sample_index` 与 `logical_poll_index` 相同，在会话内从1连续递增。
- `host_receive_time` 只在收到至少一个Modbus响应时填写；无响应超时保持空值。
- 撕裂重读仍写一行，通过初次/重读raw words和计数字段保留两次物理响应证据。

字段顺序至少包含：

```text
captured_at
elapsed_s
sample_index
logical_poll_index
scheduled_elapsed_s
schedule_lag_ms
request_started_at
request_duration_ms
physical_request_count
torn_snapshot_reread_count
response_status
error_code
error_message
initial_raw_words_hex
reread_raw_words_hex
host_receive_time
device_tick_ms_raw
device_tick_ms_unwrapped
continuous_segment
sequence
sequence_delta
status
reserved_status_bits
base_ready
live_ready
data_valid
zero_cal_running
internal_error
valid_count
base_mean_100ms
base_std_100ms
live_zero_600ms
trim_std_600ms
trim_range_600ms
raw_p2p_600ms
official_zero_offset
zero_drift_from_cal
snapshot_consistent
communication_gap
segment_break_reason
analysis_state
state_reason_codes
advisory_codes
poll_overrun
missed_schedule_slot_count
accept_for_statistics
```

`response_status` 至少支持 `ok`、`timeout`、`crc_error`、
`exception_response`、`torn_reread_failed` 和 `program_error`。raw words使用
固定宽度的大写4位十六进制、空格分隔；没有对应响应时字段留空。失败行的设备
时间、序号、状态和测量字段留空，不填充上一帧值。

artifact metadata 固定复用现有通用曲线字段：

```text
source=modbus_module
operation_type=modbus_zero_monitor
curve_type=zero_monitor_samples
flow_rate_parameter=live_zero_600ms
x_axis_variable=device_tick_ms_unwrapped
x_axis_unit=ms
x_axis_scope=continuous_segment
segment_variable=continuous_segment
unit=<live_zero_600ms的单位>
units=<可绘制变量到单位的映射>
variable_names=<可绘制数值变量列表>
sample_count=<CSV数据行数>
successful_response_count=<至少收到一个响应的行数>
failed_poll_count=<失败逻辑轮询行数>
complete=<是否正常完成文件>
recovered=<是否由partial恢复>
```

`variable_names` 只列出允许在通用曲线查看器中选择的数值序列，不把时间戳、
状态文本或原因码当作曲线变量。第一版加载器在现有
`flow_rate_samples` 和 `variable_samples` 之外接受
`zero_monitor_samples`，继续使用现有CSV解析、曲线、数据表和多变量布局；
不新增专用历史查看器，也不引入 `primary_variable_name` 或
`sample_variable_names` 两套同义元数据字段。

通用查看器增加可选x-axis/segment metadata支持：零点监视曲线使用展开设备时间
并按 `continuous_segment` 断线，失败行不绘制，不允许跨gap或重启连线。默认
显示最新连续段，操作者可以选择其他单段或全部段；全部段仍保持独立trace和段
标签。数据表保留所有逻辑轮询行及主机时间。旧artifact没有这些metadata时继续
使用现有 `captured_at` 时间轴和单段行为。

采集线程只维护当前曲线显示范围需要的原始点环形缓冲区；分析线程只维护
`Tlong` 内的独立600 ms候选deque。完整轮询行不在内存累积，始终以partial CSV
为源。`Tlong` 最大86400秒，因此候选deque也有确定上界；会话运行超过24小时
不会继续增加分析内存。历史全量查看在文件完成后通过现有Test Records数据表
读取。

## 13. Modbus带宽和操作互斥

在 `9600 8N1` 下，18寄存器请求和响应连同必要间隔预计占用约 `50-60 ms`。因此：

- 零点监视运行时暂停主窗口普通轮询。
- 不同时运行 Variable Sampling、Zero Cal、K Factor 或 Repeatability。
- 不在每个100 ms周期重复读取 `zero_offset` 或连接配置。
- 读取超过100 ms时记录 `POLL_OVERRUN`，不启动重叠请求。
- 超时、CRC或异常响应不使用连接级自动重试；首尾序号不一致最多完整重读一次。
- overrun后跳过已经错过的调度点，不进行补偿性突发读取。
- 如果实际设备长期无法维持10 Hz，记录真实频率并评估提高波特率；不得静默降低时间语义。
- 第一版不得从UI、Device Profile或每设备配置改变100 ms目标周期。

## 14. 实现顺序

### Phase 1: 纯模型和算法

- 定义快照、配置、状态、原因和结果模型。
- 实现序号/时间展开、连续段、独立候选和长期指标。
- 按第15节固件基准提取仓库内确定性JSON fixture。

### Phase 2: 只读运行时和存储

- 校验逻辑映射。
- 实现单次连续读取和100 ms后台轮询。
- 写入CSV、operation attempt和analysis result。
- 接入现有Test Records导入导出。

### Phase 3: Modbus界面

- 增加 `Operations > Zero Monitor`。
- 提取并复用实时曲线组件。
- 实现状态、指标、原因和每设备配置。
- `Zero Cal...` 仅跳转到现有受保护工作流。

### Phase 4: 真实设备只读验证

- 确认FC04连续18寄存器读取、四种字节序和10 Hz发布行为。
- 在明确零流量的台架条件下执行长时间采集。
- 根据实验数据评估窗口和阈值。

后续单独事件才考虑“稳定后自动触发或完成校准”。该事件必须重新评估写保护、超时、恢复、原零点保留和真实设备验证。

## 15. 测试设计

### 固件基准和fixture契约

M16固件基准为：

```text
source project: E:/CFM DSP - Digital Driving/visualdsppp_/Krohne_prj
observed source HEAD: f0a1b39ba1f4394253ee0adf7d0aee47c123ff9a
```

精确内容以文件hash为准，不假设外部worktree始终干净：

| 来源文件 | SHA-256 |
| :--- | :--- |
| `tests/zero_monitor/pc/test_zero_monitor.c` | `59e600c469f214fb53d6daaeacc87a2198ad542276af5cad5ab161df852b6fd5` |
| `tests/zero_monitor/pc/test_zero_monitor_modbus.c` | `0407bfa99f8b43c38ae49cf74cda0ebc8a819e762d95c7df65ce08734cbc3147` |
| `module/modbus_handler/mb_map_def.h` | `f93a4707f67d63f4cea240627bd384aaa19bbbb368475b0c3e312367011874e0` |
| `application/flow_interaction/flow_interaction.c` | `2aa077c2ca753ba05cde8fc47bab3407f719fd0fef7304523f48cc5a05f8d87b` |

实现时把派生值保存到当前仓库：

```text
tests/fixtures/modbus_zero_monitor/firmware_snapshot_vectors.json
tests/fixtures/modbus_zero_monitor/host_scenarios.json
```

测试和CI只读取仓库内fixture，不读取外部绝对路径。每个fixture文件包含
`schema_version`、上述provenance、vector ID、输入寄存器或逻辑样本、期望解码值、
期望状态/计数和数值容差。所有快照原始向量必须是字面量18元素uint16数组，
不得在测试中使用被测编码函数动态生成期望值。

固件派生fixture至少固定以下事实：

- map index从 `0x60` 到 `0x71`，对应PDU起始地址 `0x005F`，共18寄存器。
- PC基准同时允许input和holding读取该块，但禁止写入；M16按Device Profile选择
  register kind，默认Krohne profile使用input/FC04，运行中不得自动切换FC03。
- float32值 `12.5` 的四种原始字为：ABCD `[0x4148, 0x0000]`、
  BADC `[0x4841, 0x0000]`、CDAB `[0x0000, 0x4148]`、
  DCBA `[0x0000, 0x4841]`。
- sequence、status、valid count等16位字段不随32位byte/word order改变。
- 固件随机流参考使用固定seed `0xC0FFEE12`，用于交叉检查DSP发布值；上位机
  fixture只保存有限的代表性输入/期望输出，不在CI中重新编译或运行固件C测试。

`host_scenarios.json` 单独保存上位机合成场景：正常完整快照、四种字节序、重复/
漏读/回绕序号、设备时间回绕和异常回退、撕裂及第二次重读、NaN/Inf、状态位、
超时/CRC/异常响应、overrun、稳定/噪声/趋势/步进序列及稳定计时边界。合成阈值
必须标记 `test_only=true`，不得被解释为生产阈值。

固件来源文件、hash、映射或期望值任一变化时，fixture更新必须作为显式评审
变更提交，不允许测试运行时静默刷新golden values。

### 单元测试

- 必需逻辑变量、类型、字数、只读和连续区间校验。
- 一次读取18寄存器，不退化为11次请求。
- 四种32位字节/字序组合。
- 首尾序号一致、不一致、重复、漏读和65535回绕。
- 32位毫秒时间回绕和异常回退。
- `LIVE_READY`、`DATA_VALID`、有效点数、NaN/Inf和内部错误。
- 每6帧构造一个独立候选；漏读不使用重叠窗口补位。
- 长期指标和状态优先级。
- 未配置阈值或未确认零流量时不能输出 `STABLE`。

### Fake transport集成测试

- 10 Hz确定性快照流、取消、断开、超时、CRC/异常响应和慢响应。
- 运行期间普通轮询和其他操作被互斥。
- CSV、artifact、operation attempt、analysis result和Test Records一致。
- JSON导出/导入后曲线和指标仍可查看。

### UI测试

- 从 `Operations > Zero Monitor` 打开窗口。
- 连接前不能开始；运行时只能停止，其他操作禁用。
- 状态位、原因、实时值、曲线和点选信息可见。
- 配置按Device ID保存；零流量确认不跨会话恢复。
- Stop后保存历史并可重新打开曲线和数据表。
- `Zero Cal...` 不直接写设备，监视运行时不可用。

### 真实设备验证

- 只读验证优先，不执行校准写入。
- 记录固件版本、Device ID、串口参数、register-map快照和原始帧证据。
- 确认读取不改变 `ZeroOffset`、触发coil或其他设备参数。
- 将本地/fake、在线只读、台架零流量和正式实验结论分级报告。

## 16. M16完成条件

- Fake transport证明正常或传输失败周期只有一次连续18寄存器读取，只有撕裂
  快照周期允许第二次完整读取，且任何路径都没有重叠或补偿性突发请求。
- 快照一致性、序号、时间、无效数据和漏读规则有确定性测试。
- 上位机能实时显示并保存零点监视曲线和长期指标。
- Test Records可以查看、导出和导入监视记录及其CSV artifact。
- 未确认零流量或未配置阈值时不会显示 `STABLE`。
- 监视路径不会触发设备写入，正式校准仍只走现有写保护和审计路径。
- 真实设备只读验证结果单独记录；在完成前不得声称硬件验收通过。

## 17. 台架与后续事项（不阻塞诊断版实现）

- 当前PC固件基准同时支持FC04和FC03读取；仍需确认各生产固件版本是否保持该
  能力。上位机只使用profile配置的register kind，不自动fallback。
- ByteOrder核对策略已确认；仍需在生产设备上确认枚举寄存器可读、实际枚举值与
  各固件Profile一致。
- `9600 8N1` 下真实10 Hz轮询的长期丢帧率和可接受上限。
- M16已确定使用单Run人工零流量确认；生产台架仍需记录具体关阀/稳压/排气流程，
  并确认是否存在可选的只读阀门或工况信号用于交叉核对。
- 生产阈值和最小持续稳定时间当前按已确认决策留空，待多设备只读台架实验和
  独立审批；该事项不阻塞诊断版代码，但阻塞生产 `STABLE`/pass-fail验收。
- 是否需要固件FIFO由长时间通信实验决定；没有FIFO不阻塞当前只读诊断实现。
- 自适应校准由DSP还是受保护上位机状态机完成属于后续独立硬件事件，不属于
  M16实现或验收范围。
