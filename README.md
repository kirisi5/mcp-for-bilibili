# B站综合认知 MCP Server

一个基于 [FastMCP](https://github.com/jlowin/fastmcp) 的 B站 (bilibili) 一站式认知查询服务，面向 AI Agent / LLM 提供标准化的 **Model Context Protocol (MCP)** 接口：

- 视频元数据、简介、分P
- **字幕 / ASR 语音转文字**（优先原生 CC 字幕，缺失时自动下载音频本地转录）
- AI 章节、弹幕、评论、搜索
- UP主 信息、互动状态、相关推荐
- 一键聚合工具 `get_bilibili_video_full`：一次调用拿全貌

---

## ✨ 功能特性

| 分类          | 说明                                                                    |
| ------------- | ----------------------------------------------------------------------- |
| 🎬 视频查询   | 元数据 / 简介 / 分P / AI 章节 / 弹幕 / 评论                             |
| 🗣️ 字幕与转录 | 原生 CC 字幕 → 自动回退 faster-whisper 本地 ASR（无需字幕也能听懂视频） |
| 👤 用户查询   | UP主 公开信息、当前登录用户信息                                         |
| 🔍 搜索       | 按关键词搜索视频                                                        |
| 🧠 一键聚合   | `get_bilibili_video_full` 聚合元数据+字幕+章节+评论+互动+推荐           |
| ⚙️ 工程化     | Redis 多级缓存 / 内存缓存降级、wbi 签名、限流、重试、结构化日志         |

### 内置工具

| Tool                             | 说明                                                 |
| -------------------------------- | ---------------------------------------------------- |
| `get_bilibili_video_info`        | 视频完整元数据（标题/简介/UP主/播放/点赞/时长/分区） |
| `get_bilibili_video_description` | 纯文本简介                                           |
| `get_bilibili_video_pages`       | 多P视频分P列表                                       |
| `get_bilibili_video_subtitle`    | 字幕或转录文本（原生字幕 → ASR 自动回退）            |
| `get_bilibili_video_chapters`    | AI 自动章节                                          |
| `get_bilibili_video_comments`    | 视频评论（热门/最新，分页）                          |
| `get_bilibili_video_danmaku`     | 弹幕（6 分钟分段）                                   |
| `search_bilibili_videos`         | 搜索视频                                             |
| `get_bilibili_user_info`         | UP主 公开信息                                        |
| `get_bilibili_my_info`           | 当前登录用户信息                                     |
| `get_bilibili_video_interaction` | 互动状态 + 相关推荐                                  |
| `get_bilibili_video_full`        | **一键聚合**（推荐优先使用）                         |

另有 2 个 Resource（`bili://video/{bvid}`、`bili://user/{mid}`）和 1 个 Prompt（`analyze_video`）。

---

## 📦 安装

从 PyPI 安装，**默认即包含全部功能**（本地语音识别 ASR、Redis 缓存均开箱即用，无需额外选择 extras）：

```bash
pip install bilibili-cognitive-server
```

> - ASR 语音识别需要系统已安装 `ffmpeg`（音频转码依赖）。
> - 未配置 Redis 时自动降级为本地内存缓存，不影响使用。

---

## 🚀 快速开始（接入 MCP 客户端）

本服务通过标准 **MCP 协议** 接入，仅需在支持 MCP 的客户端中使用，如 **Claude Desktop / CodeBuddy / Cursor / Cherry Studio** 等。

### 1. 获取 B站 Cookie

访问 B站并登录后，从浏览器 DevTools → Network → 任意 API 请求头中复制：

| 变量            | Cookie 字段 | 必填 |
| --------------- | ----------- | ---- |
| `BILI_SESSDATA` | `SESSDATA`  | ✅   |
| `BILI_BILI_JCT` | `bili_jct`  | ✅   |
| `BILI_BUVID3`   | `buvid3`    | 建议 |

### 2. 在 MCP 客户端中注册

在客户端的 MCP 服务器配置中添加以下内容（各客户端配置入口不同，字段结构通用）：

```json
{
  "mcpServers": {
    "bilibili": {
      "command": "bili-mcp",
      "args": ["--stdio"],
      "env": {
        "BILI_SESSDATA": "你的SESSDATA",
        "BILI_BILI_JCT": "你的bili_jct",
        "BILI_BUVID3": "你的buvid3"
      }
    }
  }
}
```

> 尚未全局安装时，可将 `command` 替换为 `uvx`（或 `pipx`），由客户端按需拉起，无需预先 `pip install`：
>
> ```json
> {
>   "mcpServers": {
>     "bilibili": {
>       "command": "uvx",
>       "args": ["--from", "bilibili-cognitive-server", "bili-mcp", "--stdio"],
>       "env": {
>         "BILI_SESSDATA": "你的SESSDATA",
>         "BILI_BILI_JCT": "你的bili_jct",
>         "BILI_BUVID3": "你的buvid3"
>       }
>     }
>   }
> }
> ```

### 3. 使用

配置保存后重启 / 刷新客户端，即可在对话中让 AI 调用 B站查询工具。

---

## 🛠 本地开发

```bash
git clone https://github.com/kirisi5/mcp-for-bilibili.git
cd kirisi_mcp_demo

# uv
uv sync --extra dev
cp .env.example .env   # 填入你的 Cookie，再注入环境变量运行

# 或 pip
pip install -e '.[dev]'

# 运行 / 测试
python -m src.mcp.server --stdio
pytest
```

## 📄 License

[MIT](LICENSE)
