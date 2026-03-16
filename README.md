# Traffic Monitoring System

## 1. 项目结构

```text
dachuang/
├─ app/
│  ├─ __init__.py
│  ├─ config.py
│  ├─ db.py
│  ├─ main.py
│  ├─ models.py
│  ├─ schemas.py
│  └─ service.py
├─ artifacts/
├─ ml/
│  ├─ __init__.py
│  ├─ baselines.py
│  ├─ dataset.py
│  ├─ features.py
│  ├─ inference.py
│  └─ lstm_model.py
├─ scripts/
│  ├─ train_baseline.py
│  ├─ train_lstm.py
│  └─ import_traffic_excel.py
├─ .env.example
├─ requirements.txt
├─ requirements-ml.txt
├─ traffic_turning_5min.xlsx
├─ UI设计.md
├─ 细节分析.md
└─ README.md
```

## 2. 环境配置

1. 创建虚拟环境并安装依赖

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. 复制环境变量模板

```powershell
Copy-Item .env.example .env
```

3. 导入 Excel 数据到数据库

```powershell
python scripts/import_traffic_excel.py --excel traffic_turning_5min.xlsx --truncate
```

4. 启动服务

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

5. 训练预测基线模型

```powershell
pip install -r requirements.txt
python scripts/train_baseline.py
```

6. 可选：训练 LSTM

```powershell
pip install -r requirements-ml.txt
python scripts/train_lstm.py
```

说明：
- 当前这台环境是 `Python 3.13`，TensorFlow LSTM 训练通常需要单独使用 `Python 3.11` 或 `Python 3.12` 虚拟环境。
- 因此当前在线预测默认先使用基线最佳模型，LSTM 作为对比模型单独训练。

推荐流程：

```powershell
.\scripts\setup_lstm_env.ps1 -PythonVersion 3.11
.\scripts\run_lstm_compare.ps1 -PythonVersion 3.11
```

如果本机还没有 Python 3.11/3.12，请先安装对应版本，再执行上述脚本。

## 2.1 一键启动（推荐）

在项目根目录运行：

```powershell
.\start_all.ps1
```

首次部署（自动安装依赖 + 导入 Excel）：

```powershell
.\start_all.ps1 -InstallDeps -ImportData
```

也可以直接双击：

```text
start_all.bat
```

关闭前后端：

```powershell
.\stop_all.ps1
```

## 3. 关键配置说明

配置在 `app/config.py` 中统一读取，支持 `.env` 覆盖。

- `AMAP_API_KEY`：高德地图前端调用 Key
- `DATABASE_URL`：数据库连接串
- `DATABASE_PASSWORD`：数据库密码
- `REDIS_URL`：任务队列或缓存地址
- `CORS_ALLOW_ORIGINS`：跨域来源，多个用逗号分隔

## 4. 已写入的关键路口坐标

- 星峰路左：`106.502509,29.639365`
- 星峰路北：`106.506458,29.640724`
- 星光大道：`106.506525,29.640686`
- 恒福路：`106.507088,29.637516`

## 5. API 示例

1. 健康检查

```http
GET /health
```

2. 获取交通数据

```http
GET /api/traffic?time_range=2026-03-01 08:05:00&road_name=恒福路&direction=东向西
```

3. 获取预测数据

```http
GET /api/prediction?time_range=2026-03-01 08:05:00&road_name=恒福路
```

4. 获取天气数据

```http
GET /api/weather?time_range=2026-03-01 08:05:00
```

## 6. 当前说明

- `traffic_turning_5min.xlsx` 会导入到 `traffic_data` 表。
- `GET /api/traffic` 直接查库返回真实数据。
- `GET /api/weather` 按条件聚合库中的天气数据。
- `GET /api/prediction` 现按 `road_name + direction` 返回未来 1 小时的 6 个 10 分钟预测点。
