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
├─ scripts/
│  └─ import_traffic_excel.py
├─ .env.example
├─ requirements.txt
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
- `GET /api/prediction` 基于历史窗口做简易预测。
