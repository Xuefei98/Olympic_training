# 训练日志提取器（中文举重训练口令）

这个项目可以把教练训练视频转换为结构化训练笔记。

它主要包含两个功能：
- `extract_training_log.py`：转写视频并生成 transcript + summary + term review 文件。
- `training_log_web.py`：提供本地 Web UI，用于审核/编辑总结要点和动作标签。

---

## 环境要求

- Python 3.10+（推荐：3.11+）
- 已安装 `ffmpeg`，并可在 `PATH` 中访问
- Python 包：`faster-whisper`
- 总结/术语审核必需：
  - [Ollama](https://ollama.com/)
  - 模型（例如）`qwen2.5:7b-instruct`

### 安装依赖

```bash
pip install faster-whisper
```

安装 `ffmpeg`（macOS + Homebrew 示例）：

```bash
brew install ffmpeg
```

安装并拉取 Ollama 模型：

```bash
# 安装 Ollama（macOS 示例）
brew install --cask ollama
```

```bash
ollama pull qwen2.5:7b-instruct
```

---

## 快速开始

### 1) 从视频生成训练日志

在项目根目录执行：

```bash
python3 extract_training_log.py --auto-correct-transcript
```

默认会扫描 `./videos`，并将结果写入 `./training_log_output`。

### 2) 启动本地审核 Web UI

```bash
python3 training_log_web.py
```

浏览器打开：

```text
http://127.0.0.1:8000/webui/
```

---

## 项目产出内容

针对每个视频，提取器可生成：
- `*.transcript.txt`：纠正后的转写（词典修正 + 可选 AI 修正）。
- `*.transcript.raw.txt`：原始转写备份（当应用修正时写出）。
- `*.summary.md`：三段式训练总结：
  - `## 关键问题`
  - `## 改进建议`
  - `## 下次训练检查点`
- `*.term_review.md`：确定性词典修正 + AI 候选术语修正。

全局文件：
- `training_log_output/training_log.md`
- `training_log_output/training_log.json`

---

## 目录结构

默认目录结构如下：

```text
project-root/
  videos/                     # 将所有 MP4 文件放在这里
  training_log_output/        # 生成输出目录
  extract_training_log.py
  training_log_web.py
  webui/
```

> `extract_training_log.py` 现在默认使用 `--input-dir videos`。

---

## 提取器 CLI（`extract_training_log.py`）

常用参数：

- `--input-dir`（默认：`videos`）
- `--output-dir`（默认：`training_log_output`）
- `--glob` / `--glob2`（默认：`*.MP4`, `*.mp4`）
- `--model-size`（默认：`small`）
- `--device`（默认：`auto`）
- `--compute-type`（默认：`int8`）
- `--disable-summary`
- `--disable-term-review`
- `--auto-correct-transcript`
- `--min-correction-confidence`（默认：`0.75`）
- `--disable-dictionary-corrections`

查看完整帮助：

```bash
python3 extract_training_log.py --help
```

---

## Web UI 功能（`training_log_web.py`）

- 视频选择器
- 三栏总结面板：
  - `关键问题`
  - `改进建议`
  - `下次训练检查点`
- 每条要点支持：
  - 动作下拉选择（会回写到 `*.summary.md`）
  - 文本编辑 + **Update** 按钮
  - **Delete** 按钮（删除要点并更新 `*.summary.md`）
- 完整转写面板

服务参数：

- `--host`（默认：`127.0.0.1`）
- `--port`（默认：`8000`）
- `--root`（默认：当前目录）
- `--log-dir`（默认：`training_log_output`）
- `--auto-stop-seconds`（默认：`20`，设置为 `0` 可禁用自动停止）

查看完整帮助：

```bash
python3 training_log_web.py --help
```

---

## 典型工作流

1. 将视频放入 `videos/`。
2. 运行提取器：
   - `python3 extract_training_log.py --auto-correct-transcript`
3. 打开 Web UI：
   - `python3 training_log_web.py`
4. 审核总结要点：
   - 分配动作标签
   - 编辑要点文本
   - 删除错误要点
5. 按需对新增视频重复执行提取流程。

---

## 故障排查

### `ffmpeg is required but not found`
安装 `ffmpeg` 并验证：

```bash
ffmpeg -version
```

### `Missing dependency faster-whisper`

```bash
pip install faster-whisper
```

### 总结 / 术语审核生成失败

- 确认 Ollama 已安装并正在运行。
- 确认模型已存在：

```bash
ollama list
```

- 如需跳过 LLM 步骤，可执行：

```bash
python3 extract_training_log.py --disable-summary --disable-term-review
```

### 未找到视频文件

- 确认文件位于 `videos/`
- 检查扩展名大小写与 glob（`*.MP4`, `*.mp4`）
- 或传入自定义输入目录：

```bash
python3 extract_training_log.py --input-dir /path/to/videos
```

---

## 备注

- 项目针对中文举重训练口令进行了优化。
- 默认启用确定性词典修正。
- AI 候选术语修正会列出供人工审核；是否自动应用取决于置信度阈值。
