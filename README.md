# SD Image Sorter (AI 图像筛选管理器)

[English](#english) | [简体中文](#简体中文)

---

<a name="english"></a>

# 🎨 SD Image Sorter 

A powerful image management tool for Stable Diffusion users. Automatically extract metadata, tag images with AI, filter, sort, and organize your AI-generated artwork with a premium glassmorphism UI.

![Version](https://img.shields.io/badge/version-1.1.0-purple)
![Python](https://img.shields.io/badge/python-3.9+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## ✨ Features

### 🖼️ Gallery Management
- **Multi-source support**: ComfyUI, NovelAI, WebUI/Forge, and unknown formats.
- **Metadata extraction**: Automatically reads prompts, settings, checkpoints, and LoRAs.
- **Advanced filtering**: Filter by generator, tags, ratings, checkpoints, or LoRAs.
- **Smart sorting**: Sort by date, name, prompt length, tag count, or rating.

### 🏷️ AI Tagging (WD14 Tagger)
- **High-accuracy models**: EVA02-Large, SwinV2, ConvNeXt, etc.
- **Dual thresholds**: Separate recognition sensitivity for general vs. character tags.
- **Rating classification**: Predicts General, Sensitive, Questionable, or Explicit.

### 📁 Image Organization & Sorting
- **Auto-Separate**: Bulk move images matching filters to specific destination folders.
- **Manual Sort**: Fast, "game-like" sorting using **WASD** keys.
- **Undo Support**: Instantly revert sorting actions.

### 🔳 Censor Edit (V2)
- **AI Detection**: YOLOv8-based detection of sensitive areas (requires model).
- **Multiple Styles**: Mosaic, blur, black bar, or white bar.
- **Precision Tools**: Manual brush, eraser, and clone stamp for detail work.
- **Batch Processing**: Queue-based workflow with batch save and rename.

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.9+**
- **Windows** (Recommended) or Linux/Mac.

### Installation & Run

1. **Clone/Download** the repository:
   ```bash
   git clone https://github.com/yourusername/sd-image-sorter.git
   cd sd-image-sorter
   ```

2. **Run the app**:
   - **Windows**: Double-click `run.bat`
   - **Linux/Mac**: Run `chmod +x run.sh && ./run.sh`

3. **Access UI**: Open `http://localhost:8000` in your browser.

*The first run will automatically set up a virtual environment and install dependencies.*

---

## 📖 Tutorial

### 1. Scanning Images
1. Click **📂 Scan Folder** in the top navigation.
2. Enter the absolute path to your image directory.
3. Click **Start Scan**. The app will index your images and extract metadata.

### 2. AI Tagging
1. Click **🏷️ Tag Images**.
2. Select a model (e.g., `wd-eva02-large-tagger-v3`).
3. Click **Start Tagging**. You can now filter images by specific tags in the sidebar.

### 3. Rapid Manual Sorting
1. Navigate to the **Manual Sort** tab.
2. Set destination folders for **W**, **A**, **S**, and **D** slots.
3. Click **🎮 Start Sorting**.
4. Use **W/A/S/D** to move images, and **Space** to skip.

### 4. Censor Editing
1. Select images in the Gallery and click **🔳 Censor Edit**.
2. In the right sidebar, set your YOLO model path and confidence.
3. Click **🎯 Detect Current** for AI detection.
4. Manually refine with tools if needed, then click **💾 Save All Processed**.

---

## ⌨️ Shortcuts

| Context | Keys | Action |
| :--- | :--- | :--- |
| **Manual Sort** | `W/A/S/D` | Move to slot |
| | `Space` | Skip image |
| | `Z` | Undo |
| **Censor Edit** | `A / D` | Prev / Next image |
| | `B / P` | Brush / Pen tool |
| | `E` | Eraser (Restore) |
| | `G` | Clone Stamp |
| | `[ / ]` | Adjust Brush Size |
| | `Ctrl+Z` | Undo |
| | `Ctrl+Scroll`| Zoom Canvas |

---

<br>

<a name="简体中文"></a>

# 🎨 SD Image Sorter (AI 图像筛选管理器)

专为 Stable Diffusion 用户设计的图像管理工具，具备极简玻璃拟态 UI。支持自动元数据提取、AI 打标、智能过滤和极速排序。

## ✨ 功能特性

### 🖼️ 画廊管理
- **全面兼容**: 支持 ComfyUI, NovelAI, WebUI/Forge 等多种生成工具。
- **深度解析**: 自动读取正反向提示词、采样参数、模型信息及 LoRA。
- **精准过滤**: 支持按生成器、标签、内容分级、模型或 LoRA 组合筛选。
- **智能排序**: 支持按时间、提示词长度、标签密度或分级排序。

### 🏷️ AI 自动打标 (WD14 Tagger)
- **多模型矩阵**: 集成 EVA02-Large, SwinV2 等高精度打标模型。
- **双重阈值**: 针对通用内容与角色特征分别定义识别灵敏度。
- **安全评级**: 自动识别并标注内容分级（General 到 Explicit）。

### 📁 自动化整理与排序
- **自动分类 (Auto-Separate)**: 将符合过滤条件的图片一键归集到指定文件夹。
- **快捷手动排序**: 独创“WASD”键位操作，像玩游戏一样快速分类图片。
- **撤销机制**: 实时撤销误操作，排序流程更安全。

### 🔳 隐私打码 (Censor Edit V2)
- **智能识别**: 依托 YOLOv8 自动锁定敏感区域（需自备模型）。
- **多样化处理**: 提供马赛克、模糊、纯色遮盖等多种打码方式。
- **精细修补**: 内置画笔、橡皮擦及仿制图章，满足手动精度需求。
- **批量导出**: 队列化工作流，支持批量重命名与保存。

---

## 🚀 快速开始

### 环境要求
- **Python 3.9+**
- **Windows** (推荐) 或 Linux/Mac。

### 安装与运行

1. **获取代码**:
   ```bash
   git clone https://github.com/yourusername/sd-image-sorter.git
   cd sd-image-sorter
   ```

2. **启动程序**:
   - **Windows**: 双击 `run.bat`
   - **Linux/Mac**: 运行 `chmod +x run.sh && ./run.sh`

3. **访问界面**: 使用浏览器打开 `http://localhost:8000`。

*首次启动将自动创建虚拟环境并补全依赖包。*

---

## 📖 使用教程

### 1. 扫描入库
1. 点击顶部导航栏的 **📂 Scan Folder**。
2. 输入图片所在文件夹的绝对路径（例如 `D:\AI_Images`）。
3. 点击 **Start Scan**，程序将扫描并建立本地索引数据库。

### 2. AI 自动打标
1. 点击 **🏷️ Tag Images**。
2. 选择推荐模型 `wd-eva02-large-tagger-v3`。
3. 点击 **Start Tagging**。完成后，你可以通过左侧边栏搜索任意标签。

### 3. 极速手动分类
1. 切换至 **Manual Sort** 标签页。
2. 为 **W/A/S/D** 四个槽位选择目标路径。
3. 点击 **🎮 Start Sorting** 开启排序。
4. 敲击 **W/A/S/D** 移动图片，**空格** 跳过，**Z** 撤销。

### 4. 隐私打码编辑
1. 在画廊中选中图片，点击浮动栏的 **🔳 Censor Edit**。
2. 在右侧侧边栏指定 YOLO 模型路径并调整置信度。
3. 点击 **🎯 Detect Current** 自动识别敏感点。
4. 使用顶部工具栏进行精修后，点击 **💾 Save All Processed** 批量保存。

---

## ⌨️ 快捷键指南

| 场景 | 按键 | 动作 |
| :--- | :--- | :--- |
| **手动排序** | `W / A / S / D` | 移动到指定槽位 |
| | `空格` | 跳过当前图片 |
| | `Z` | 撤销上一步操作 |
| **打码编辑** | `A / D` | 切换上/下一张 |
| | `B / P` | 画笔 / 铅笔工具 |
| | `E` | 橡皮擦 (恢复原图) |
| | `G` | 仿制图章 |
| | `[ / ]` | 调整笔触大小 |
| | `Ctrl+Z` | 撤销编辑 |
| | `Ctrl+滚轮` | 画布缩放 |
