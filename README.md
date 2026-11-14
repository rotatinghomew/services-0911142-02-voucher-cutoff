# 托育补贴门槛研究台

这是供服务规划人员核对资料、查看业务范围和留存讨论依据的本地应用。页面提供门槛两侧申请与实际核销记录，并在同一资料版本上运行门槛分析：区分资格带来的变化、门槛处实际核销跃迁和核销的局部效应，同时给出区间、有效样本、门槛附近分布、协变量与随访检查。分析设置（年度、窗口、边界排除、观察期、阶数、核函数）可调并可保存对照。示例为独立生成的虚构数据，不含真实家庭、医疗资料或对外服务凭据。

## 使用环境

使用 Python 3.11，独立容器镜像锁定到 3.11.14。页面为原生浏览器界面，数据处理通过本地 HTTP 服务完成，持久化使用应用内置数据库。基础浏览不依赖数值库；门槛分析所需的数值计算和建模库（NumPy、SciPy、statsmodels）已按 `requirements.txt` 固定所有传递版本。依赖安装及镜像构建只在环境准备时做，启动和检查不访问网络。

```bash
python3 -m pip install -r requirements.txt -r requirements-build.txt -r requirements-test.txt
python3 -m playwright install --with-deps chromium
python3 -m voucher_cutoff --host 127.0.0.1 --port 8080
```

浏览器打开本地8080端口。容器方式先准备镜像，运行时不需要其他服务：

```bash
docker build -t voucher_cutoff:local .
docker run --rm -p 8080:8080 -v voucher_cutoff-state:/app/.state voucher_cutoff:local
```

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m build --wheel --no-isolation
python3 tests/browser_smoke.py
python3 tests/browser_threshold.py
```

检查包含真实领域字段、非法导入、边界数据保留、HTTP交互、版本指纹及重启后存档；门槛分析另覆盖资料截面优先于最新订正、未成熟结局不补零、边界等号规则、样本不足与弱工具时不输出效应、保存后重开冻结，以及真实页面上的运行、对照和重开。运行检查使用临时状态，不改动资料。

字段语义、接口与门槛口径分别见 `docs/data-contract.md`、`docs/http.md` 和 `docs/threshold-analysis.md`。短时操作见 `docs/walkthrough.md`，环境准备不包含在正式操作中。
