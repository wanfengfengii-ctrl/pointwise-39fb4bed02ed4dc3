# 测厚原页试拍

夜班与设备员共用两个物理录制页。试拍在原帧所在的页上真正写入，哪怕先写了一幅，夜班仍须能新取上一张正式帧。两幅写成后由设备员决定是否采用。放弃后仍可采原帧，确认后本页开始供应新帧。跨页有多个正式候选时，沿用按实际 `write_id` 取最新的规则。

## 设备调用

- `PageBank.begin_trial(trial_id, original_write_id, exposure_id)` 在原正式曝光所在的页开始同批同触发试拍，返回本次试拍的 `Exposure`。`trial_id` 和 `exposure_id` 是非空字符串，`original_write_id` 是当前页正式曝光的正整数 `write_id`。试拍有自己的 `write_id`。同试拍号原样重投返回同一 `Exposure`，已确认或已放弃也可以复领。异参、原曝光不可用或该页已有未决试拍时引发 `ValueError`。
- 用现有 `write_half(exposure, side, value)` 和 `finish_write(exposure)` 写入及写成试拍。原页的物理读口 `read_half(page, side)` 在落幅后可看到实际写入的新值。写成待决期间该物理页仍由试拍占用，普通录制可用另一页。
- `PageBank.confirm_trial(trial_id)` 只在两幅写成后采纳。`PageBank.abandon_trial(trial_id)` 可放弃未决的试拍。两者成功均返回试拍的 `Exposure`。重复同一终局返回原结果，即使原页已被复用。未知试拍号、提前确认、申请相反终局均引发 `ValueError`，原决定和候选保持原样。试拍的放弃使用试拍号。

## 取件与班账

沿用 `collect` 和 `Readout`。分幅顺序可由现有 `order` 设置，`after_copy` 在相应幅实际取得后触发，`dispatch` 可延后交件。首幅实读固定本次取件的曝光归属。查询候选或登记请求时还未实际取得原幅。如果确认发生在首幅实读之前，本次使用实读时的正式候选。如果确认发生在旧帧首幅实读之后，无论试拍在该首幅之前还是之后开始，本次都可沿同一旧曝光读齐，即使确认后原页又被普通录制覆盖。普通覆页取件仍按 README 的原规则处理。晚送原幅时，按实际取读发生的时序认定出处。新尝试、提交失败重试、按成果号或原请求复领及班账仍使用原有规则。

## 录制示例与运行

`samples/shift.json` 中 F7 的左右幅是 7 和 11，另一页录制 N1。同页试拍 F8 写入 13 和 19。F8 未确认时，取件可签 F7。F7 的在途取件读到 7 后，让设备确认 F8，另起取件签 F8，再覆写原页，最后送达 F7 的右幅。两份签件分别为 18 μm 和 32 μm，均带实际曝光身份和首执。停源重开按号仍能领回这两份签件，班账合计 50 μm。

`python3 -B check_project.py` 和 `python3 -B demo.py` 可以核对已有流程。实现试拍后，`python3 -B trial_scenario.py verify` 运行原回归并核对试拍，`python3 -B trial_scenario.py demo` 展示停源领取。将两条入口接入 `.pointwise-runtime.json` 的 `verify` 和 `demo`。沿用清单中的 `version`、`kind` 和 `setup`，每个入口用参数数组表示。核验出错须返回非零退出码。
