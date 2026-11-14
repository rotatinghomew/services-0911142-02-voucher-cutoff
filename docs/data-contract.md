# 资料与统计口径

每条 `applications.application` 对应一个年度申请，重复申请跨年度不合并。`policies.cutoff_days` 是申请当日年龄天数的严格上界；等于边界不具资格。年份规则分别使用。`age_history` 以 `visible_at` 记录字段版本，`decision_vintage` 指本次政策资料截面，不能借用之后才知道的订正。`observed_days` 决定60天结局是否成熟；`visit_60d=null` 表示尚无结局，不是零。`voucher_used` 是实际核销而非被分配的资格。原始数据包括两侧不完全依从、字段订正、不同成熟度和离散年龄。现有页按年龄窗口列明细；门槛分析页另行估算，并明确使用哪些年度、截面、窗口和结局人群，口径见 `docs/threshold-analysis.md`。

## 原始表

- `applications`：480条示例，主键为 `application`。字段：`application`, `year`, `applied_day`, `age_days`, `voucher_used`, `visit_60d`, `observed_days`, `income_band`, `center`, `recorded_at`。
- `age_history`：524条示例，主键为 `change`。字段：`change`, `application`, `visible_at`, `age_days`。
- `policies`：2条示例，主键为 `year`。字段：`year`, `cutoff_days`, `eligibility`, `followup_days`, `data_cutoff`, `decision_vintage`。

## 版本与扩展

`fixtures/data.json` 是完整导入材料，`product.json` 是当前浏览器默认选择。全量导入校验后按规范化内容指纹保存，相同内容不重复建版本。原始事实不原地修改；资料修订形成新版本。`null` 表示明确未观测，不因筛选或合计自动变为0。

`voucher_cutoff/domain.py` 是领域入口，负责字段校验及资料描述；`threshold.py` 承担门槛估计、识别检查与规格对照；`service.py` 连接范围选择、数据版本与服务端计算后的存档；`storage.py` 管理不可变数据和保存材料；`web/app.js` 提供浏览、门槛分析、对照和导出交互。保存的材料同时包含原数据与选择，打开旧材料不重算为新数据。新增分析不限内部组织方式，但不能丢失现有原始记录核对与历史材料读取能力。统计方法、模型选择和业务结论没有预设答案。
