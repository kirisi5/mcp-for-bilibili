"""
MCP Server 主入口 — 基于 FastMCP 框架
"""

import json
from typing import Annotated
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from src.config import settings
from src.crawler.http_client import get_http_client
from src.crawler.bili_client import get_bili_client
from src.services.bili_service import BiliVideoService, BiliSearchService, BiliUserService
from src.middleware.error_handler import with_error_handler

# ── 生命周期 ──

@asynccontextmanager
async def _lifespan(_server: FastMCP):
    """启动时初始化 HTTP 客户端，关闭时释放连接池"""
    _ = get_http_client()
    yield
    await get_http_client().close()

# ── FastMCP 实例 ──
mcp = FastMCP(
    name="B站综合认知 MCP Server",
    instructions="B站视频搜索、字幕/音频内容解析、评论、用户信息——一站式认知服务",
    lifespan=_lifespan,
)

# ── 服务实例 ──
_video_service: BiliVideoService | None = None
_search_service: BiliSearchService | None = None
_user_service: BiliUserService | None = None

def _vs() -> BiliVideoService:
    global _video_service
    if _video_service is None:
        _video_service = BiliVideoService()
    return _video_service

def _ss() -> BiliSearchService:
    global _search_service
    if _search_service is None:
        _search_service = BiliSearchService()
    return _search_service

def _us() -> BiliUserService:
    global _user_service
    if _user_service is None:
        _user_service = BiliUserService()
    return _user_service

# ═════════════════════════════════════════════
# Tool 1: 获取视频完整元数据
# ═════════════════════════════════════════════

@mcp.tool(name="get_bilibili_video_info",
          description="获取B站视频的完整元数据：标题、简介、UP主、播放量、点赞数、时长、分区等")
@with_error_handler
async def get_video_info(bvid: Annotated[str, "B站视频 BV 号，例如 BV1xx411c7mD"]) -> dict[str, object]:
    meta = await _vs().get_video_meta(bvid)
    return meta.model_dump()

# ═════════════════════════════════════════════
# Tool 2: 获取视频简介
# ═════════════════════════════════════════════

@mcp.tool(name="get_bilibili_video_description",
          description="获取B站视频的纯文本简介（description）")
@with_error_handler
async def get_video_description(bvid: Annotated[str, "B站视频 BV 号"]) -> dict[str, object]:
    meta = await _vs().get_video_meta(bvid)
    return {"bvid": meta.bvid, "title": meta.title, "description": meta.description,
            "owner_name": meta.owner_name}

# ═════════════════════════════════════════════
# Tool 3: 获取视频分P列表
# ═════════════════════════════════════════════

@mcp.tool(name="get_bilibili_video_pages",
          description="获取B站多P视频的所有分P列表，返回每个分P的 cid、标题和时长")
@with_error_handler
async def get_video_pages(bvid: Annotated[str, "B站视频 BV 号"]) -> list[dict[str, object]]:
    pages = await _vs().get_video_pages(bvid)
    return [p.model_dump() for p in pages]

# ═════════════════════════════════════════════
# Tool 4: 获取字幕（自动回退 ASR）★ 核心工具
# ═════════════════════════════════════════════

@mcp.tool(
    name="get_bilibili_video_subtitle",
    description=(
        "获取B站视频的字幕或转录文本。优先使用B站原生CC字幕，"
        "若无字幕则自动下载音频并通过语音识别（ASR）转为文字。"
        "适用于需要精确理解视频内容的场景。"
    ),
)
@with_error_handler
async def get_video_subtitle(
    bvid: Annotated[str, "B站视频 BV 号"],
    cid: Annotated[int | None, "分P的 cid，不提供则使用默认分P"] = None,
    force_asr: Annotated[bool, "强制使用 ASR 语音识别（忽略原生字幕）"] = False,
) -> dict[str, object]:
    """获取字幕/转录文本 — 智能回退逻辑"""
    if cid is None:
        meta = await _vs().get_video_meta(bvid)
        cid = meta.cid

    # 路径 1：原生字幕
    if not force_asr:
        subtitle = await _vs().get_video_subtitle(bvid, cid)
        if subtitle is not None:
            return {"bvid": bvid, "cid": cid, "source": "cc_subtitle",
                    "language": subtitle.language,
                    "subtitles": [s.model_dump() for s in subtitle.subtitles],
                    "full_text": subtitle.full_text}

    # 路径 2：ASR 语音识别
    from src.asr.transcriber import safe_asr_transcribe
    asr_result = await safe_asr_transcribe(bvid, cid)
    if asr_result is None:
        return {"bvid": bvid, "cid": cid, "source": "none",
                "error": "该视频既无原生字幕，ASR 转录也失败",
                "subtitles": [], "full_text": ""}
    return {"bvid": bvid, "cid": cid, "source": "asr_transcription",
            "language": asr_result.language,
            "subtitles": [s.model_dump() for s in asr_result.subtitles],
            "full_text": asr_result.full_text}

# ═════════════════════════════════════════════
# Tool 5: 获取 AI 章节
# ═════════════════════════════════════════════

@mcp.tool(name="get_bilibili_video_chapters",
          description="获取B站视频的AI自动章节（部分视频支持）")
@with_error_handler
async def get_video_chapters(
    bvid: Annotated[str, "B站视频 BV 号"],
    cid: Annotated[int | None, "分P的 cid"] = None,
) -> list[dict[str, object]]:
    if cid is None:
        meta = await _vs().get_video_meta(bvid)
        cid = meta.cid
    chapters = await _vs().get_video_chapters(bvid, cid)
    return [c.model_dump() for c in chapters]

# ═════════════════════════════════════════════
# Tool 6: 获取评论
# ═════════════════════════════════════════════

@mcp.tool(name="get_bilibili_video_comments",
          description="获取B站视频评论，支持热门/最新排序，支持分页")
@with_error_handler
async def get_video_comments(
    bvid: Annotated[str, "B站视频 BV 号"],
    page: Annotated[int, "页码，从 1 开始"] = 1,
    sort: Annotated[int, "排序: 2=热门(默认) 1=最新"] = 2,
) -> dict[str, object]:
    meta = await _vs().get_video_meta(bvid)
    comments, page_info = await _vs().get_comments(meta.aid, page=page, sort=sort)
    return {"bvid": bvid, "page": page, "total": page_info.total,
            "has_more": page_info.has_more,
            "sort": "热门" if sort == 2 else "最新",
            "comments": [c.model_dump() for c in comments]}

# ═════════════════════════════════════════════
# Tool 7: 获取弹幕
# ═════════════════════════════════════════════

@mcp.tool(name="get_bilibili_video_danmaku",
          description="获取B站视频弹幕（按6分钟分段）")
@with_error_handler
async def get_video_danmaku(
    bvid: Annotated[str, "B站视频 BV 号"],
    cid: Annotated[int | None, "分P cid"] = None,
    segment_index: Annotated[int, "弹幕分段索引，从 1 开始，每段 6 分钟"] = 1,
) -> dict[str, object]:
    if cid is None:
        meta = await _vs().get_video_meta(bvid)
        cid = meta.cid
    danmaku_list = await _vs().get_danmaku(cid, segment_index)
    return {"bvid": bvid, "cid": cid, "segment_index": segment_index,
            "count": len(danmaku_list),
            "danmaku": [d.model_dump() for d in danmaku_list]}

# ═════════════════════════════════════════════
# Tool 8: 搜索视频
# ═════════════════════════════════════════════

@mcp.tool(name="search_bilibili_videos",
          description="在B站搜索视频，返回标题、UP主、播放量、简介摘要")
@with_error_handler
async def search_videos(
    keyword: Annotated[str, "搜索关键词"],
    page: Annotated[int, "页码"] = 1,
    ps: Annotated[int, "每页条数，最大 50"] = 20,
) -> dict[str, object]:
    result = await _ss().search(keyword, page=page, ps=min(ps, 50))
    return result.model_dump()

# ═════════════════════════════════════════════
# Tool 9: 获取 UP主信息
# ═════════════════════════════════════════════

@mcp.tool(name="get_bilibili_user_info",
          description="获取B站UP主的公开信息：昵称、签名、等级、粉丝数等")
@with_error_handler
async def get_user_info(mid: Annotated[int, "B站用户 UID"]) -> dict[str, object]:
    info = await _us().get_user_info(mid)
    return info.model_dump()

# ═════════════════════════════════════════════
# Tool 10: 获取当前登录用户信息
# ═════════════════════════════════════════════

@mcp.tool(name="get_bilibili_my_info",
          description="获取当前登录用户的个人信息：UID、昵称、等级、B币余额、VIP状态")
@with_error_handler
async def get_my_info() -> dict[str, object]:
    info = await _us().get_my_info()
    return info.model_dump()

# ═════════════════════════════════════════════
# Tool 11: 获取互动+推荐
# ═════════════════════════════════════════════

@mcp.tool(name="get_bilibili_video_interaction",
          description="获取当前用户对视频的互动状态和相关推荐视频")
@with_error_handler
async def get_video_interaction(bvid: Annotated[str, "B站视频 BV 号"]) -> dict[str, object]:
    meta = await _vs().get_video_meta(bvid)
    client = get_bili_client()
    interaction = await client.get_interaction(bvid, meta.aid)
    related = interaction.related_videos
    return {"bvid": bvid, "interaction": interaction.model_dump(),
            "related_videos": [v.model_dump() for v in related]}

# ═════════════════════════════════════════════
# Tool 12: ★ 一键聚合（推荐 AI 使用）
# ═════════════════════════════════════════════

@mcp.tool(
    name="get_bilibili_video_full",
    description=(
        "【推荐】一次性获取B站视频的全貌信息：元数据、字幕/转录文本、"
        "AI章节、热门评论、互动状态、相关推荐。适合需要深入理解视频内容的场景。"
    ),
)
@with_error_handler
async def get_video_full(
    bvid: Annotated[str, "B站视频 BV 号"],
    include_asr: Annotated[bool, "无字幕时是否使用ASR语音识别转录？默认 True"] = True,
) -> dict[str, object]:
    """视频全貌查询 — 一次获取所有相关信息"""
    full = await _vs().get_video_full_view(bvid)
    if include_asr and not full.has_subtitles:
        from src.asr.transcriber import safe_asr_transcribe
        asr_result = await safe_asr_transcribe(bvid, full.meta.cid)
        full.subtitle = asr_result
    return full.model_dump()

# ═════════════════════════════════════════════
# Resources
# ═════════════════════════════════════════════

@mcp.resource(uri="bili://video/{bvid}", name="B站视频数据",
              description="获取指定 B站视频的结构化数据", mime_type="application/json")
@with_error_handler
async def video_resource(bvid: str) -> str:
    full = await _vs().get_video_full_view(bvid)
    return json.dumps(full.model_dump(), ensure_ascii=False, indent=2, default=str)

@mcp.resource(uri="bili://user/{mid}", name="B站用户数据",
              description="获取指定 B站用户的结构化数据", mime_type="application/json")
@with_error_handler
async def user_resource(mid: str) -> str:
    info = await _us().get_user_info(int(mid))
    return json.dumps(info.model_dump(), ensure_ascii=False, indent=2, default=str)

# ═════════════════════════════════════════════
# Prompts
# ═════════════════════════════════════════════

@mcp.prompt(name="analyze_video", description="视频内容分析提示模板")
def analyze_video_prompt(bvid: str) -> str:
    return f"""请分析 B站视频 {bvid} 的内容。

步骤：
1. 使用 get_bilibili_video_full 工具获取视频完整信息
2. 分析视频简介中的关键信息
3. 如果有字幕/转录文本，总结视频核心内容
4. 浏览热门评论，了解观众主要观点
5. 给出全面分析

请从第一步开始。"""

# ═════════════════════════════════════════════
# 启动入口
# ═════════════════════════════════════════════

def main():
    """MCP Server 启动入口 — 同时支持 STDIO（本地）和 SSE（远程）两种模式"""
    import sys
    if "--stdio" in sys.argv:
        # STDIO 模式：用于 Claude Desktop、CodeBuddy 等本地客户端
        mcp.run(transport="stdio")
    else:
        # SSE 模式：用于 Docker 部署、远程 HTTP 客户端
        mcp.run(transport="sse", host=settings.MCP_SERVER_HOST, port=settings.MCP_SERVER_PORT)

if __name__ == "__main__":
    main()