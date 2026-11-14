# 应用接口

服务绑定本地地址，输入输出为 UTF-8 JSON。错误输入为400，未找到对象为404，超过8MB请求为413。页面与服务端共用口径，前端不另算一份统计答案。

- `GET /health`：检查启动是否成功。
- `GET /api/context`：产品配置、默认数据和已保存材料目录。
- `GET /api/data?revision=版本`：查看完整原始表。
- `POST /api/datasets`：传入 `label` 与完整 `data`，校验后返回不可变 `revision`。
- `POST /api/explore`：传入可选的 `revision` 和 `selection`，返回实际选择、资料指纹与 `view`。现有 view 的 summary、rows、series、notes 只用于描述。
- `POST /api/threshold`：传入可选的 `revision` 和 `selection`（`year`、`window`、`donut`、`followup`、`order`、`kernel`），返回门槛分析。响应含 `analysis="threshold"`、`data_sha256` 与 `threshold`。`threshold` 内包含状态、估计、区间、有效样本、排除计数、识别检查、协变量、门槛附近分布、两侧描述与结论文字。识别条件不通过时估计为 `null`。
- `POST /api/compare`：同一选择下的若干合理规格对照，返回 `analysis="comparison"` 与 `comparison.scenarios`。
- `POST /api/notebooks`：传入选择与 `title`，以及可选 `analysis`（`threshold` 或 `comparison`；省略时保存原有描述），由服务端重新计算后保存，不接受客户端伪造结果。
- `GET /api/notebooks` 与 `GET /api/notebooks/编号`：目录以及冻结资料、选择和结果。

新增分析扩展了接口，但已有导入、描述和历史读取仍供业务使用，含义未改变。门槛分析的参数若无效（如排除范围不小于窗口、随访阈值低于政策值），返回400。
