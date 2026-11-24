# 带材双幅测厚交班站

带材试制车间的白班设备员登记批次、触发清册及测厚阈值，再用双页设备录制左右两幅。夜班采集员按原请求取件，值班质检员用正式签件核对班总量和超限幅。停源后，质检员仍可按原号查询。

## 运行

需要 Python 3.11，使用标准库和 SQLite。由项目根目录运行完整回归和演示。

```text
python3 -B check_project.py
python3 -B demo.py
```

核验入口随平台物化提供。演示从 `samples/shift.json` 读入测厚班样例，使用独立临时站库。F7 左幅读回后启动 F8 录制。实际发生同页复用时，首次取件保留待办，随后同号取得 F8 的 [13,19]、32 μm 和唯一回执。演示再冻结班账，停源重开并按原号核对。业务对账失败会返回非零退出码。

## 本地 Python API

- `work.database.open_database(path)` 打开连接。调用方负责关闭。`work.bootstrap.initialize_station(db, config=None)` 初始化各业务表。`StationConfig(shift_id, lot_id, triggers, threshold_um)` 可登记一个初始批次。以批次号区分初始配置，同批同参重跑保留既有和人工新增资料，同批异参引发 `ValueError`。
- `work.lots.open_lot(db, shift_id, lot_id, triggers, threshold_um)` 原子登记班次、批次、触发和规程，返回 `LotRegistration`。标识为非空字符串，清册为非空的不同触发号集合，顺序无业务含义。触发全站唯一归属。空参数、无效阈值或冲突引发 `ValueError`。
- `device.recorder.record_frame(recorder, trigger_id, exposure_id, left_um, right_um, on_step=None)` 录制并返回 `Exposure`。创建录制端使用 `Recorder(db, pages)`，其中 `pages` 为 `PageBank(DeviceStore(db))`。设备号可重用，真实曝光仍有独立的递增 `write_id`。回调接收事件名 `start`、`left`、`right`、`complete` 和曝光。如果回调中断未完成录制，页持续显示写入中，可凭原曝光继续写幅并写成，或调用 `PageBank.abandon_write` 明确丢弃。参数校验先于占页。两个页都不可写时引发 `PageBusy`，调用方可择时重试。
- `capture.requests.collect(acceptance, pages, request_id, lot_id, trigger_id, readout=None)` 通过 `Acceptance(ReceiptRepository(db))` 取件。`CollectResult.status` 为 `signed` 或 `pending`。签收结果含 `artifact.measurement` 及 `artifact.receipt`。待办结果给出可重试原因，仍用原号续办。请求同号异参或批次与触发不一致引发 `ValueError`。已签同参可传 `pages=None` 直接复领。`Readout` 的 `after_copy(reading)` 可在实际拷贝之间推进设备，`dispatch(reading, deliver)` 可延后送达已拷贝的分幅。回调最终以原消息调用 `deliver`。延迟送达若遇发布错误，回调调用方收到 `PublishError`，仍可按原请求续办。
- `handover.register.close_shift(db, shift_id, report_id)` 冻结当班签件和逐幅超限项。未知班次、跨班复用交班号或使用 `measurement:` 保留前缀引发 `ValueError`。同班同号返回原版。`handover.history.read_artifact(db, identifier)` 读取成果号或交班号，未知号及普通待办号返回 `None`。成果号带 `measurement:` 前缀，交班号由调用方提供。

同一站库的在线模拟设备共享一个 `PageBank`，在其所属线程内使用并通过回调交错调度。短借读属于这一设备会话。独立数据库连接可同时读取正式业务账，也可办理签收或交班。重开时写入事实和未完成页仍可观察，分幅暂存属于当时的签收会话。每次重新取件分配新的持久尝试号。

## 计算口径

阈值和原读为整数微米，零有效。空值、布尔及非整数拒绝，无取整或浮点换算。只有同一次真实写成的指定曝光及对应的原幅出处可以签收。成果固定左后右，合计为两幅之和。班账只统计已提交的正式签件，按设备实际写入顺序排列，同一曝光按签出先后排列。逐幅比较各份签件绑定的冻结阈值。阈值为 10 μm 时，[10,11] 的班总量为 21 μm，右幅超限，左幅等于阈值。空班总量为零。后续签件可用于新交班版，旧版仍可读。
