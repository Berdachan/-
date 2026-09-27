# 实验报告批阅智能体

> 自动识别学生实验报告，通过 OCR 提取文字，利用 LLM 进行智能评分与批阅建议生成。
> 支持批量处理、多用户管理、数据可视化与多格式导出。

---

## 📋 项目简介

**实验报告批阅智能体** 是一款面向高校计算机实验课程的智能辅助批阅系统。系统基于 **Tesseract OCR** 进行图像文字识别，结合 **LangChain** 构建评分链，调用 **Kimi LLM API** 实现实验报告的内容理解与智能评分，并生成结构化的评语与改进建议。

项目采用 **Python 3.12** 开发，提供 **GUI（tkinter）** 和 **CLI** 两种使用方式，支持 **MySQL 持久化存储**、**多用户管理**、**数据可视化仪表盘** 及 **Excel/PDF/PPT/Word 多格式导出**。

### 核心技术栈

| 技术 | 用途 |
|------|------|
| **Tesseract + Pillow** | 图像预处理与 OCR 文字识别 |
| **LangChain + Kimi API** | LLM 评分链构建与智能评分 |
| **tkinter** | 轻量级 GUI 界面 |
| **MySQL** | 多用户数据持久化 |
| **matplotlib** | 数据可视化仪表盘 |
| **openpyxl / reportlab / python-pptx / python-docx** | Excel / PDF / PPT / Word 导出 |

---

## 🚀 快速开始

### 1. 环境要求

- **Python**: 3.12+
- **Tesseract-OCR**: [下载安装](https://github.com/UB-Mannheim/tesseract/wiki)（Windows 默认路径 `D:\tesseract\tesseract.exe`）
- **MySQL**: 5.7+ 或 8.0+

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置数据库

#### 3.1 启动 MySQL 服务

**Windows（命令行）**：

```bash
# 以管理员身份打开 CMD，执行：
net start mysql（mysql不行的话用MySQL80）

# 验证服务是否启动
net start | findstr mysql
```

如果 MySQL 服务未安装，请先安装 MySQL 并记住 root 密码。

#### 3.2 编辑数据库配置

编辑 `config.py` 中的 `DB_CONFIG`，填入你的 MySQL 配置：

```python
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "你的密码",
    "database": "experiment_grader",
    "charset": "utf8mb4",
}
```

#### 3.3 初始化数据库

运行初始化脚本（自动创建数据库和数据表）：

```bash
python init_db.py
```

执行成功后会显示：
```
✅ 数据库 'experiment_grader' 已创建或已存在
✅ 数据表初始化完成
✅ 数据库准备就绪！
```

### 4. 配置 API 密钥（可选）

编辑 `config.py` 中的 `KIMI_API_KEY`：

```python
KIMI_API_KEY = "sk-你的Kimi API密钥"
```

> 获取 API Key: [Moonshot AI 开放平台](https://platform.moonshot.cn/)

### 5. 启动程序

**GUI 版本（推荐）**：

```bash
python main.py
```
注意：在登录界面注册账号的话，先输入要注册的账密再点注册就能注册成功了。

**CLI 版本（测试用，功能不完善）**：

```bash
python cli_app.py
```

---

## 📁 项目结构

```
experiment_report_grader/
├── main.py                 # GUI 入口
├── cli_app.py              # CLI 入口
├── config.py               # 全局配置（API密钥、数据库、主题等）
├── init_db.py              # 数据库初始化脚本
├── requirements.txt        # 依赖清单
├── README.md               # 项目说明（本文档）
├── .gitignore              # Git 忽略配置
├── data/
│   ├── input/              # 学生提交的实验报告
│   └── output/             # 批阅结果导出
├── modules/                # 核心模块
│   ├── ocr_processor.py    # OCR 图像预处理与识别
│   ├── text_structurer.py  # 文本清洗与结构化分段
│   ├── llm_grader.py       # LLM 评分链（含缓存、队列、退避）
│   ├── gui_app.py          # tkinter GUI 主界面
│   ├── exporter.py         # Excel/PDF/PPT/Word 导出
│   ├── database.py         # MySQL 数据库管理
│   ├── auth.py             # 用户认证（注册/登录/自动登录）
│   ├── history_manager.py  # 历史记录管理
│   ├── agents.py           # 智能摘要/邮件分发/行为推荐/助教/预警Agent
│   ├── dashboard.py        # 数据可视化仪表盘
│   ├── grading_style.py    # 批阅风格管理
│   ├── quality_checker.py  # 报告质量检测
│   ├── drag_drop.py        # 文件拖拽上传（Windows）
│   └── email_sender.py     # 邮件发送
└── assets/
    └── demo_reports/       # 示例报告
```

---

## ✨ 功能特性

### 🔍 OCR 与文本处理
- **图像预处理**：灰度化、自适应二值化、中值滤波去噪
- **多格式支持**：PNG、JPG、PDF、DOCX
- **结构化提取**：自动分段（实验目的 / 步骤 / 结果 / 结论）
- **质量检测**：OCR 置信度评估、文本质量分析、结构完整性检查

### 🤖 智能评分
- **5 种预设风格**：标准 / 严格 / 温和 / 科研 / 工程
- **自定义风格**：可调整严格度、维度权重、语气风格
- **动态模型选择**：根据文本长度自动切换 8k / 32k / 128k 模型
- **防过载机制**：串行队列 + 智能退避（指数退避 + 自适应恢复）
- **评分缓存**：相同文本避免重复调用 API

### 🧠 智能Agent增强
- **智能摘要Agent**：对历史批阅记录生成月度 / 学期 / 全部周期的实验能力雷达图与文字总结，不再只是罗列历史条目
- **邮件智能分发Agent**：根据批阅成绩、质量分和紧急程度自动生成邮件主题与正文；不及格时自动采用更温和、鼓励式措辞
- **行为感知Agent**：记录登录时段、操作频率和常用功能，自动推荐个性化快捷入口，并给出下一步资源预加载建议
- **智能助教Agent**：结合用户资料中的专业、年级、实验课程，自动补充评分侧重点和评语风格提示
- **进度预警Agent**：监测学生提交频率、成绩趋势和薄弱维度，主动提示教师关注可能存在困难的学生

### 👤 多用户系统
- 用户注册 / 登录 / 自动登录 / 退出
- 删除用户：登录界面支持删除用户账号，连带删除所有批阅历史记录、个人资料信息和自动登录凭证（操作前需二次确认）
- 个人资料管理（头像、昵称、邮箱、学工号、专业、年级、实验课程、手机号、简介）
- 用户数据隔离，历史记录独立存储
- **SMTP 配置按用户隔离**：每个用户的邮件配置独立存储，切换用户后需重新配置

### 📊 数据管理
- **历史记录**：查看、搜索、排序、删除、新增、改动
- **数据仪表盘**：统计概览、分数分布柱状图、维度雷达图、趋势分析、批量对比
- **重新批阅**：支持覆盖历史记录或生成新记录
- **历史记录导出**：支持导出选中记录为 Excel / PDF / PPT / Word，并支持直接发送邮件

### 📤 导出与分享
- **多格式导出**：Excel (.xlsx)、PDF (.pdf)、PPT (.pptx)、Word (.docx)
- **邮件发送**：支持 SMTP 配置（按用户隔离），可发送带附件的批阅结果
- **批量导出**：支持选中记录或全部记录导出

### 🎨 界面体验
- **主题切换**：浅色 / 深色模式
- **文件拖拽**：直接将文件拖入窗口上传（Windows）
- **快捷键支持**：Ctrl+O 导入、Ctrl+G 批阅、Ctrl+E 导出 Excel 等
- **最近文件**：自动记录最近导入的文件

---

## ⌨️ 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl + O` | 导入报告文件 |
| `Ctrl + F` | 导入文件夹 |
| `Ctrl + G` | 开始批阅 |
| `Ctrl + S` | 停止批阅 |
| `Ctrl + R` | 重新批阅选中报告 |
| `Ctrl + E` | 导出 Excel |
| `Ctrl + P` | 导出 PDF |
| `Ctrl + M` | 发送邮件 |
| `Ctrl + D` | 数据仪表盘 |
| `Ctrl + T` | 切换主题 |
| 菜单“分析 → 智能摘要Agent/行为推荐Agent/进度预警Agent” | 打开Agent增强功能 |
| `Delete` | 删除选中报告 |

---

## 🛠️ 开发环境

- **IDE**: Visual Studio Code
- **Python**: 3.12.10
- **代码格式化**: Black（行宽 100）
- **代码检查**: Ruff
- **版本控制**: Git

VS Code 配置参考 `.vscode/settings.json`（已包含 Black + Ruff 自动格式化配置）。

---

## ⚙️ 配置说明

### 评分标准

在 `config.py` 中可自定义评分维度与分值：

```python
GRADING_CRITERIA = {
    "实验目的理解": {"description": "是否准确理解实验目的", "max_score": 20},
    "实验步骤完整性": {"description": "步骤是否完整、逻辑清晰", "max_score": 30},
    "结果分析与数据处理": {"description": "数据记录是否准确，分析是否合理", "max_score": 30},
    "结论与总结": {"description": "结论是否明确，总结是否到位", "max_score": 20},
}
```

### 主题配置

`config.py` 中的 `THEMES` 字典定义了浅色 / 深色主题的全部颜色值，可按需调整。

### OCR 优化

```python
TESSERACT_CMD = r"D:\tesseract\tesseract.exe"  # Tesseract 安装路径
OCR_MAX_PAGES = 50        # PDF 最大处理页数
OCR_DEFAULT_DPI = 200     # 默认扫描 DPI
```

### 邮件配置（按用户隔离）

每个用户的 SMTP 配置独立存储在 `data/.cache/smtp_config_{user_id}.json`，切换用户后需重新配置。

**QQ 邮箱配置示例**：
- SMTP服务器：`smtp.qq.com`
- 端口：`465`
- 使用SSL：✅ 勾选
- 发件人邮箱：你的QQ邮箱（如 `123456@qq.com`）
- 授权码：QQ邮箱的**授权码**（不是登录密码）

> 获取授权码：登录 QQ邮箱 → 设置 → 账户 → 开启 SMTP 服务 → 获取授权码

---

## 📝 使用流程

```
1. 注册/登录账号
   ↓
2. 导入实验报告（拖拽或选择文件/文件夹）
   ↓
3. 选择批阅风格（标准/严格/温和/科研/工程/自定义）
   ↓
4. 点击"开始批阅"（系统自动 OCR → 结构化 → LLM 评分）
   ↓
5. 查看评分结果（总分、各维度评分、评语、建议）
   ↓
6. 导出报告（Excel/PDF/PPT/Word）或发送邮件
   ↓
7. 在历史记录中查看/管理所有批阅记录
   ↓
8. 历史记录支持：导出选中记录、发送邮件、新增/改动/删除记录
```

---

## 🐛 常见问题

### Q1: OCR 识别准确率不高怎么办？

- 确保上传的图片分辨率足够（建议 200 DPI 以上）
- 图片应清晰、无严重倾斜或模糊
- 系统会自动进行图像预处理（灰度化、二值化、去噪）

### Q2: API 调用频繁导致过载？

- 系统已内置 **串行队列 + 智能退避** 机制
- 遇到 429 错误会自动增加请求间隔并重试
- 建议批量批阅时控制同时处理的报告数量

### Q3: 数据库连接失败？

- **确认 MySQL 服务已启动**：在 CMD 中执行 `net start mysql`
- **检查配置**：确认 `config.py` 中的 `DB_CONFIG` 用户名、密码、端口正确
- **确认权限**：数据库用户需具有创建数据库、创建表和读写权限
- **手动测试连接**：
  ```bash
  mysql -u root -p
  # 输入密码后，执行：
  SHOW DATABASES;
  ```

### Q4: PDF 导出中文乱码？

- 系统会自动尝试注册 Windows 系统字体（宋体 / 微软雅黑）
- 确保系统安装了中文字体

### Q5: 如何删除用户账号？

在登录界面选择要删除的用户名，点击"删除用户"按钮。系统会弹出确认对话框，说明将删除的内容：
- 该用户的所有批阅历史记录
- 该用户的个人资料信息
- 该用户的自动登录凭证

**注意**：此操作不可恢复，请谨慎操作。

### Q6: 邮件发送失败（Connection unexpectedly closed）？

- **检查 SMTP 配置**：确认服务器、端口、邮箱、授权码正确
- **QQ 邮箱**：使用 `smtp.qq.com:465` + SSL + 授权码（不是登录密码）
- **163 邮箱**：使用 `smtp.163.com:465` + SSL + 授权码
- **Gmail**：使用 `smtp.gmail.com:587` + 不勾选SSL + 应用专用密码
- **切换用户后需重新配置**：SMTP 配置按用户隔离，切换用户后需重新保存配置

### Q7: 历史记录对话框中看不到记录？

- 检查 `__init__` 中是否已初始化历史记录变量（`history_search_var`、`history_sort_by`、`history_sort_order`、`history_select_all_var`、`history_tree`）
- 检查 `_show_history_dialog` 中 `tree` 创建顺序是否在按钮之前

---

## 📄 许可证

本项目为课程实训项目，仅供学习交流使用。

---

## 👥 贡献者

- 刘赛、陈奎、邱三元

---

> 💡 **提示**: 项目路径位于 `D:\jupyter\projects\experiment_report_grader`，使用 VS Code 打开即可开始开发。
