"""
全量数据模型 — 基于 Pydantic v2

模型分类：
- 枚举: VideoQuality, CommentSort, SearchType
- 通用: PageInfo, APIResponse
- 视频: VideoStat, VideoMeta, VideoPage
- 字幕: SubtitleItem, ChapterItem, SubtitleResult
- 评论/弹幕: MemberInfo, Comment, Danmaku
- 用户: UserStat, UserInfo, MyInfo
- 搜索: SearchVideoItem, SearchResult
- 互动/推荐: RelationVideo, InteractionInfo
- 聚合: VideoFullView
"""

from __future__ import annotations
from typing import ClassVar
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


# ═══ 枚举 ═══

class VideoQuality(int, Enum):
    P360 = 16; P480 = 32; P720 = 64; P1080 = 80; P1080_PLUS = 112; P4K = 120

class CommentSort(str, Enum):
    HOT = "2"; NEW = "1"


# ═══ 通用 ═══

class PageInfo(BaseModel):
    page: int = 1; page_size: int = 20; total: int = 0; has_more: bool = False


# ═══ 视频相关 ═══

class VideoStat(BaseModel):
    """视频统计数据"""
    view: int = 0; danmaku: int = 0; reply: int = 0
    favorite: int = 0; coin: int = 0; share: int = 0; like: int = 0

    @property
    def engagement_score(self) -> int:
        """互动总分 = 点赞 + 投币×2 + 收藏×3"""
        return self.like + self.coin * 2 + self.favorite * 3


class VideoMeta(BaseModel):
    """视频完整元数据"""
    bvid: str = ""; aid: int = 0
    title: str = ""; description: str = ""; pic: str = ""
    pubdate: datetime | None = None
    duration: int = 0; cid: int = 0
    owner_name: str = ""; owner_mid: int = 0; owner_face: str = ""
    stat: VideoStat = Field(default_factory=VideoStat)
    videos: int = 1; copyright: int = 1
    tid: int = 0; tname: str = ""; dynamic: str = ""
    tags: list[str] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    @property
    def owner_uid(self) -> int:
        """UP主 UID（与 owner_mid 同值，兼容调用方）"""
        return self.owner_mid

    @property
    def duration_str(self) -> str:
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @property
    def video_url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}/"


class VideoPage(BaseModel):
    """视频分P信息"""
    cid: int = 0; page: int = 0; part: str = ""; duration: int = 0

    @property
    def duration_str(self) -> str:
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


# ═══ 字幕与章节 ═══

class SubtitleItem(BaseModel):
    """单条字幕"""
    from_time: float = 0.0; to_time: float = 0.0; content: str = ""


class ChapterItem(BaseModel):
    """AI 视频章节"""
    cid: int = 0; chapter_name: str = ""; time_point: int = 0; part: str = ""
    start_time: float = 0.0; end_time: float = 0.0; summary: str = ""


class SubtitleResult(BaseModel):
    """字幕查询结果 — CC字幕 / AI章节 / ASR转录 的统一容器"""
    subtitles: list[SubtitleItem] = Field(default_factory=list)
    source: str = ""  # "cc_subtitle" | "ai_chapter" | "asr_transcription"
    language: str = ""

    @property
    def full_text(self) -> str:
        """带时间戳的完整文本"""
        return "\n".join(f"[{s.from_time:.1f}s] {s.content}" for s in self.subtitles)

    @property
    def plain_text(self) -> str:
        """纯文本（适合 LLM 上下文）"""
        return "\n".join(s.content for s in self.subtitles)


# ═══ 评论与弹幕 ═══

class MemberInfo(BaseModel):
    """评论者信息"""
    mid: int = 0; uname: str = ""; avatar: str = ""
    level: int = 0; sex: str = ""; sign: str = ""


class Comment(BaseModel):
    """单条评论"""
    rpid: int = 0; mid: int = 0; content: str = ""
    ctime: datetime | None = None; like: int = 0; replies_count: int = 0
    member: MemberInfo | None = None


class Danmaku(BaseModel):
    """单条弹幕"""
    content: str = ""; time_point: float = 0.0
    color: int = 0xFFFFFF; font_size: int = 18
    send_time: datetime | None = None

    @property
    def color_hex(self) -> str:
        return f"#{self.color:06X}"


# ═══ 用户信息 ═══

class UserStat(BaseModel):
    """用户统计"""
    following: int = 0; follower: int = 0  # B站 API 字段名是 follower
    video_count: int = 0


class UserInfo(BaseModel):
    """UP主公开信息"""
    mid: int = 0; name: str = ""; sex: str = ""; face: str = ""
    sign: str = ""; level: int = 0; birthday: str = ""; top_photo: str = ""
    live_room_status: int = 0  # 0=无 1=未开播 2=直播中
    is_vip: bool = False
    stat: UserStat = Field(default_factory=UserStat)

    @property
    def space_url(self) -> str:
        return f"https://space.bilibili.com/{self.mid}/"


class MyInfo(BaseModel):
    """当前登录用户信息"""
    mid: int = 0; uname: str = ""; name: str = ""; face: str = ""; sign: str = ""
    level: int = 0; money: float = 0.0; vip_type: int = 0; birthday: str = ""
    is_vip: bool = False; sex: str = ""; coins: float = 0.0
    following: int = 0; follower: int = 0; moral: int = 0
    unread_msg: int = 0; wallet_bcoin: float = 0.0


# ═══ 搜索结果 ═══

class SearchVideoItem(BaseModel):
    """搜索结果的单个视频"""
    bvid: str = ""; aid: int = 0; title: str = ""; author: str = ""; mid: int = 0
    description: str = ""; pic: str = ""; duration: int = 0
    play: int = 0; danmaku: int = 0; pubdate: datetime | None = None

    @property
    def duration_str(self) -> str:
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

    @property
    def video_url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}/"


class SearchResult(BaseModel):
    """搜索结果"""
    keyword: str = ""; page: int = 1; total: int = 0; has_more: bool = False
    items: list[SearchVideoItem] = Field(default_factory=list)
    page_info: PageInfo = Field(default_factory=PageInfo)
    seid: str = ""; cost_time: float = 0.0


# ═══ 互动与推荐 ═══

class RelationVideo(BaseModel):
    """相关推荐视频"""
    bvid: str = ""; aid: int = 0; title: str = ""; author: str = ""
    pic: str = ""; play: int = 0; duration: int = 0
    danmaku: int = 0; reason: str = ""


class InteractionInfo(BaseModel):
    """视频互动状态"""
    bvid: str = ""
    has_liked: bool = False; has_favorited: bool = False; has_coin: bool = False
    related_videos: list[RelationVideo] = Field(default_factory=list)


# ═══ 聚合视图 ═══

class VideoFullView(BaseModel):
    """
    视频全貌聚合视图 — 一键获取所有相关信息。

    这是 MCP 工具的推荐返回模型，一个请求获取：
    - 视频元数据 + 简介
    - 字幕 / ASR 转录文本
    - AI 章节
    - 热门评论
    - 互动状态 + 相关推荐
    """
    meta: VideoMeta = Field(default_factory=VideoMeta)
    subtitle: SubtitleResult | None = None
    chapters: list[ChapterItem] = Field(default_factory=list)
    hot_comments: list[Comment] = Field(default_factory=list)
    interaction: InteractionInfo | None = None
    related_videos: list[RelationVideo] = Field(default_factory=list)

    @property
    def has_subtitles(self) -> bool:
        return self.subtitle is not None and len(self.subtitle.subtitles) > 0

    @property
    def text_content(self) -> str:
        """拼接所有文本内容为纯文本（专门为 LLM 设计）"""
        parts = [
            f"标题: {self.meta.title}",
            f"UP主: {self.meta.owner_name}",
            f"简介: {self.meta.description}",
        ]
        subtitle = self.subtitle
        if subtitle is not None and subtitle.subtitles:
            parts.append(f"\n--- 字幕/转录文本 (来源: {subtitle.source}) ---")
            parts.append(subtitle.plain_text)
        if self.chapters:
            parts.append("\n--- 章节 ---")
            for ch in self.chapters:
                parts.append(f"  [{ch.time_point}s] {ch.chapter_name}")
        if self.hot_comments:
            parts.append(f"\n--- 热门评论 ({len(self.hot_comments)}条) ---")
            for c in self.hot_comments:
                name = c.member.uname if c.member else "匿名"
                parts.append(f"  {name}: {c.content[:200]}")
        return "\n".join(parts)