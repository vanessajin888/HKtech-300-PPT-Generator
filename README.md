## PPT Cursor-like 可视化编辑 Demo 系统

一个在本地 Windows 环境运行的全栈 Demo，用于对 PPTX 文档做「可视化 + 文本框级别编辑」演示。

当前阶段已实现：
- **后端**：FastAPI + python-pptx + LibreOffice 渲染
  - `POST /upload`：上传 PPTX，调用 LibreOffice 渲染为每页 PNG，并解析文本框对象（含 EMU 与像素坐标）
  - `GET /objects?file_id=...`：返回对应文件的 slides 元信息与 objects 列表
- **前端**：React + Vite 骨架（稍后补充交互）可上传并预览每页 PNG。

后续步骤会继续补齐：
- `/suggest`：Mock LLM 生成建议 + 严格 JSON 校验
- `/apply`：写回 PPTX、重新渲染 PNG
- `/download`：下载修改后的 PPTX。

### 目录结构

```text
backend/
  main.py
  requirements.txt
  services/
    render.py       # LibreOffice 渲染 PPTX → PNG
    parse.py        # python-pptx 解析文本框对象与 EMU 坐标
  storage/
    uploads/
    renders/{file_id}/
    outputs/
    objects/{file_id}.json

frontend/
  ... (React + Vite, 见 frontend/README 或 package.json)
```

---

### Windows 依赖安装

- **Python**：3.10+
- **Node.js**：建议 18+ LTS
- **LibreOffice**：
  1. 从官网下载安装 Windows 版 LibreOffice
  2. 安装完成后，找到 `soffice.exe` 所在目录，例如：
     - `C:\Program Files\LibreOffice\program\soffice.exe`
  3. 将该 `program` 目录加入系统 `PATH` 环境变量：
     - 控制面板 → 系统和安全 → 系统 → 高级系统设置 → 环境变量
     - 在「系统变量」中找到 `Path` → 编辑 → 新建
     - 粘贴 `C:\Program Files\LibreOffice\program`
     - 确认保存
  4. 新开一个 `PowerShell` 或 `cmd`，执行：
     ```powershell
     soffice --version
     ```
     能看到版本号说明配置成功。

---

### Backend 启动步骤

> 为了避免不同 `python` 解释器（如系统 Python、LibreOffice 自带 Python）之间的混淆，**统一使用项目自带的虚拟环境解释器 `.venv\Scripts\python.exe` 来启动后端**。

**第一次在这台机器上运行（需要安装依赖）**：

```powershell
cd "c:\Users\vanessajin\Desktop\HKtech300 Demo\backend"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**之后每次启动后端，直接用这一条命令即可**（不要求先 `activate`，也不依赖系统 `python` 指向谁）：

```powershell
cd "c:\Users\vanessajin\Desktop\HKtech300 Demo\backend"
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

启动成功后，可在浏览器访问：

- `http://localhost:8000/health`：健康检查

---

### Frontend 启动步骤

> 说明：公司电脑上 PowerShell 可能拦截 `npm.ps1`，推荐在 **命令提示符（cmd）** 中运行前端命令，或者在已放宽执行策略的 PowerShell 中运行。

#### 方式 A：在命令提示符（cmd）中启动（推荐）

```bat
cd "C:\Users\vanessajin\Desktop\HKtech300 Demo\frontend"
npm install
npm run dev
```

前端启动后，在浏览器访问：`http://localhost:5173`。

#### 方式 B：在 PowerShell 中启动（若已配置执行策略）

如果你已经执行过（且公司策略允许）：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

那么可以在 PowerShell 里直接运行：

```powershell
cd "C:\Users\vanessajin\Desktop\HKtech300 Demo\frontend"
npm install
npm run dev
```

访问地址同样是：`http://localhost:5173`。

---

### Demo 验收（当前阶段：上传 → 渲染 PNG → 解析对象）

1. 启动 backend。
2. 使用任意 HTTP 客户端（如 Postman / curl / 前端页面）调用：
   - `POST http://localhost:8000/upload`，`form-data` 中上传 `.pptx` 文件（字段名 `file`）。
3. 成功后会返回：
   - `file_id`
   - `file_name`
   - `page_count`
   - `image_urls[]`（每页 PNG 的访问 URL）
4. 调用：
   - `GET http://localhost:8000/objects?file_id=<上一步返回的 file_id>`
   - 可看到：
     - `slides`：每页的 `image_url`、`image_w`、`image_h`
     - `objects`：每个文本框的 `slide_index`、`shape_id`、`text`、`bbox_emu`、`slide_size_emu`、`bbox_px`

---

### 常见错误排查

- **错误：`LibreOffice 'soffice' command not found`**
  - 原因：`soffice` 没有加入 `PATH`
  - 排查：
    - 在终端执行 `soffice --version`
      - 如果提示不是内部或外部命令 → 说明 PATH 未配置好
    - 确认 LibreOffice 安装目录，重新按上文步骤加入 `PATH`
    - 关闭所有终端，重新打开后再试。

- **错误：`Failed to render PPTX to images` 或 returncode 非 0**
  - 可能原因：
    - PPTX 文件损坏或内容 LibreOffice 无法解析
    - LibreOffice 渲染过程中出现内部错误
  - 建议：
    - 在命令行用 `soffice --headless --convert-to png your.pptx` 单独测试该文件是否可以渲染成功。

---

### Assumptions（工程假设）

- **slide_index 统一从 0 开始**：
  - 第一页 slide 的 `slide_index = 0`。
- **文本框判断**：
  - 仅使用 `shape.has_text_frame` 作为“文本框类 shape”的判断依据；
  - 不对 run 级富文本做细粒度解析。
- **LibreOffice 输出文件命名**：
  - 假设 LibreOffice 会输出 `<文件名>.png`, `<文件名>_1.png`, `<文件名>_2.png` 等；
  - 代码通过按文件名排序建立 slide 与 png 的顺序映射。
- **前端访问静态资源路径**：
  - backend 将 `backend/storage` 挂载为 `/static`；
  - API 返回的 `image_url` 形如 `/static/renders/{file_id}/...png`。

后续在补齐 `/suggest`、`/apply`、`/download`、React UI 高亮交互时，会继续更新本 README 中的说明与完整运行步骤。 

