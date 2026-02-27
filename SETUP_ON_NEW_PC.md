# 在另一台电脑上打开并运行本项目（Windows）

本项目是一个本地全栈 Demo：
- **后端**：FastAPI + `python-pptx` + LibreOffice（`soffice`）把 PPTX 渲染为 PNG，并解析文本框对象、生成/应用编辑建议
- **前端**：React + Vite，负责上传、预览 PNG、展示建议、勾选应用、下载结果

默认端口：
- 后端 `http://localhost:8000`
- 前端 `http://localhost:5173`

---

## 0. 你需要准备什么（必装）

- **Windows 10/11**
- **Python 3.10+**
  - 安装后建议确认：`python --version`
- **Node.js 18+（建议 LTS）**
  - 安装后建议确认：`node -v`、`npm -v`
- **LibreOffice（必须）**
  - 用于把 PPTX 转成每页 PNG
  - 安装后建议确认：`soffice --version`

> 如果公司电脑限制 PowerShell 脚本执行，前端命令建议用 **cmd（命令提示符）** 跑。

---

## 1. 把项目拷到新电脑（以及建议不要拷的东西）

把整个项目目录复制到新电脑任意路径即可（例如 `D:\Projects\HKtech300 Demo\`）。

建议 **不要携带** 下面这些目录/文件（换电脑通常不可用、也会增大体积）：
- `backend/.venv/`（Python 虚拟环境）
- `frontend/node_modules/`（前端依赖）
- `backend/storage/`（上传文件、渲染图片、缓存 objects/outputs；可带可不带）
- `backend/.env`（含 API Key 等敏感信息：建议在新电脑**重新创建**）

如果你拿到的是一个完整打包目录，也可以在新电脑上手动删除它们后再按下面步骤重新安装依赖。

---

## 2. 配置 LibreOffice（渲染 PPTX → PNG）

后端渲染依赖 `soffice.exe`。有两种配置方式（二选一）：

### 方式 A：把 LibreOffice 加入系统 PATH（推荐）

1. 安装 LibreOffice（Windows 版）
2. 找到 `soffice.exe` 路径，通常是：
   - `C:\Program Files\LibreOffice\program\soffice.exe`
3. 把其所在目录（`...\LibreOffice\program`）加入系统环境变量 `Path`
4. 重新打开终端，验证：

```powershell
soffice --version
```

### 方式 B：只给本项目指定 `SOFFICE_PATH`

如果你不想改系统 PATH，可设置环境变量 `SOFFICE_PATH` 指向 `soffice.exe` 完整路径。

PowerShell（当前窗口生效）：

```powershell
$env:SOFFICE_PATH="C:\Program Files\LibreOffice\program\soffice.exe"
```

> 后端代码会优先使用 `SOFFICE_PATH`，否则回退到默认路径 `C:\Program Files\LibreOffice\program\soffice.exe`。

---

## 3. 后端环境配置与启动（FastAPI）

在终端进入后端目录：

```powershell
cd "<你的项目路径>\backend"
```

创建并激活虚拟环境、安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

启动后端：

```powershell
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

验证后端是否正常：
- 打开 `http://localhost:8000/health` 应返回 `{"status":"ok"}`
- 也可以打开 `http://localhost:8000/docs` 查看 Swagger 文档

---

## 4.（可选）配置 DeepSeek（不用也能跑：会自动使用 Mock）

后端在 `backend/services/suggest.py` 里支持两种建议生成方式：
- **MockSuggestionProvider**：无需任何 Key，适合演示
- **DeepSeekSuggestionProvider**：需要 API Key

如要启用 DeepSeek：

1. 在 `backend/` 目录新建一个 `\.env` 文件（不要提交到仓库），内容示例：

```ini
USE_DEEPSEEK=true
DEEPSEEK_API_KEY=你的key

# 可选
DEEPSEEK_API_BASE=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

2. 重启后端进程

如不启用 DeepSeek（默认）：

```ini
USE_DEEPSEEK=false
```

或者不创建 `.env` 也可以，后端会默认走 Mock。

---

## 5. 前端环境配置与启动（React + Vite）

建议用 **cmd**（命令提示符）执行（避免公司策略拦截 `npm.ps1`）。

```bat
cd "<你的项目路径>\frontend"
npm install
npm run dev
```

启动后打开：
- `http://localhost:5173`

---

## 6. 跑一遍 Demo 流程（快速自检）

1. 确保后端在跑：`http://localhost:8000/health` OK
2. 打开前端页面：`http://localhost:5173`
3. 上传一个 `.pptx`
   - 后端会把 PPTX 渲染为每页 PNG
   - 前端会显示 PPT 页图，并在文本框位置叠加高亮框与文字
4. 输入指令生成建议（右侧 Chat）
5. 勾选建议并 Apply
   - 后端生成新的 `output_file_id`，并重新渲染 PNG
6. 点击“下载当前 PPTX”

---

## 7. 常见问题排查

### 7.1 `LibreOffice 'soffice' command not found` / 渲染失败

- 确认 LibreOffice 已安装
- 确认 `soffice --version` 能运行
- 如果不能运行：
  - 用「第 2 节」方式 A 加入 PATH，或方式 B 设置 `SOFFICE_PATH`

### 7.2 `Failed to render PPTX to images`

可能原因：PPT 文件损坏 / LibreOffice 对某些内容转换失败。

建议在命令行单独验证该文件：

```powershell
soffice --headless --convert-to png "<你的ppt路径>" --outdir "<输出目录>"
```

### 7.3 前端能打开但请求后端报跨域（CORS）

后端目前允许的前端来源是 `http://localhost:5173` / `http://127.0.0.1:5173`。

如果你改了前端端口或要用其它域名访问，需要在 `backend/main.py` 的 CORS 配置里把对应 origin 加进去，然后重启后端。

### 7.4 端口冲突

- 后端默认 `8000`，前端默认 `5173`
- 若被占用：
  - 后端可改 `--port`，前端可改 Vite 配置或 `npm run dev -- --port xxxx`

---

## 8.（建议）把大文件/缓存排除在版本控制之外

如果你用 Git 管理代码，建议确保这些不提交：
- `backend/.venv/`
- `backend/.env`
- `backend/storage/`
- `frontend/node_modules/`

