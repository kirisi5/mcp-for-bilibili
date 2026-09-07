"""
B站 爬虫客户端 — Phase 1 核心入口

═══════════════════════════════════════════════════════════════
架构定位：
    这是爬虫层的总入口，整合了 HTTP 客户端、WBI 签名、Cookie 管理。
    上层 Service 只需调用这里的方法，不用关心签名细节。

调用链：
    MCP Tool → Service → BiliClient → WBISigner + HTTPClient → B站 API
═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx
from loguru import logger

from src.crawler.http_client import get_http_client
from src.crawler.wbi_signer import get_wbi_signer
from src.middleware.error_handler import MCPBusinessError
from src.models.schemas import (
    VideoMeta, VideoPage,
    SubtitleItem, ChapterItem,
    Comment, Danmaku, MemberInfo,
    SearchResult, SearchVideoItem, PageInfo,
    UserInfo, UserStat, MyInfo,
    InteractionInfo, RelationVideo,
)


def _ts_to_datetime(ts: object) -> datetime | None:
    """B站秒级时间戳 → datetime，无效值返回 None"""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(str(ts)))
    except (TypeError, ValueError, OSError):
        return None


def _build_danmaku_message_class() -> type[Any]:
    """构造 B站 DmSegMobileReply 消息类。"""
    from google.protobuf import descriptor_pb2, descriptor_pool
    from google.protobuf.message_factory import GetMessageClass

    file_descriptor = descriptor_pb2.FileDescriptorProto(
        name="bilibili_dm.proto", package="bilibili.dm", syntax="proto3"
    )
    elem = file_descriptor.message_type.add(name="DanmakuElem")
    for name, number, field_type in (
        ("progress", 2, 5), ("mode", 3, 5), ("fontsize", 4, 5),
        ("color", 5, 13), ("content", 7, 9), ("ctime", 8, 3),
    ):
        _ = elem.field.add(name=name, number=number, label=1, type=field_type)
    reply = file_descriptor.message_type.add(name="DmSegMobileReply")
    field = reply.field.add(name="elems", number=1, label=3, type=11)
    field.type_name = ".bilibili.dm.DanmakuElem"

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_descriptor)
    return GetMessageClass(pool.FindMessageTypeByName("bilibili.dm.DmSegMobileReply"))


def _duration_to_seconds(value: object) -> int:
    """B站搜索接口时长格式不定（'00:12:34' 或秒数），统一转秒"""
    if isinstance(value, (int, float)):
        return int(value)
    parts = str(value).split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    if not nums:
        return 0
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0]


class BiliClient:
    """
    B站 爬虫客户端

    职责：
    - 整合 HTTP + WBI 签名
    - 解析 B站 API 原始 JSON → Pydantic 模型
    - 提供语义化方法 (get_video_meta / search / get_comments ...)
    """

    async def _signed_get(self, url: str, params: dict[str, Any] | None = None, rate_limit: str = "default"):
        """内部方法：带 WBI 签名的 GET 请求"""
        signer = get_wbi_signer()
        signed_params = await signer.sign_params(params or {})
        client = get_http_client()
        return await client.get(url, params=signed_params, scene=rate_limit)

    def _parse_response(self, resp, model_class=None):
        """
        解析 B站 API 响应为 Pydantic 模型（同步方法）

        注意：httpx.Response.json() 是同步的（从内存中已下载的 bytes 解析），
        所以这个方法不需要 async。
        """
        data = resp.json()
        if data.get("code") != 0:
            msg = data.get("message") or data.get("msg") or "未知错误"
            logger.error(f"B站 API 错误: code={data.get('code')}, msg={msg}")
            raise MCPBusinessError(
                f"B站接口返回错误: {msg} (code={data.get('code')})",
                code="BILI_API_ERROR",
            )
        result = data.get("data")
        if result is None:
            raise MCPBusinessError(
                "B站接口未返回有效数据(可能用户/资源不存在、内容被删除或触发风控)",
                code="BILI_EMPTY_DATA",
            )
        if model_class:
            return model_class(**result)
        return result

    async def _get_user_stat(self, mid: int, scene: str = "default") -> tuple[int, int]:
        """
        获取用户的关注数/粉丝数（x/relation/stat）。
        acc/info 与 nav 接口均不包含这两个字段，需单独查询。
        失败时降级为 (0, 0)，不阻塞主流程。
        """
        try:
            resp = await self._signed_get(
                "https://api.bilibili.com/x/relation/stat",
                params={"vmid": mid},
                rate_limit=scene,
            )
            stat = self._parse_response(resp)
            return stat.get("following", 0), stat.get("follower", 0)
        except Exception:
            logger.warning("获取用户关注/粉丝数失败(mid=%s), 降级为 0", mid, exc_info=True)
            return 0, 0

    # ================================================================
    # 视频元数据
    # ================================================================

    async def get_video_meta(self, bvid: str) -> VideoMeta:
        """
        获取视频元数据

        API: x/web-interface/view
        返回: 标题、简介、UP主、统计、标签等完整信息
        """
        resp = await self._signed_get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid},
            rate_limit="video",
        )
        data = self._parse_response(resp)
        owner = data.get("owner", {})
        return VideoMeta(
            bvid=data.get("bvid", bvid),
            aid=data.get("aid", 0),
            title=data.get("title", ""),
            description=data.get("description", ""),
            pic=data.get("pic", ""),
            pubdate=_ts_to_datetime(data.get("pubdate")),
            duration=data.get("duration", 0),
            cid=data.get("cid", 0),
            owner_name=owner.get("name", ""),
            owner_mid=owner.get("mid", 0),
            owner_face=owner.get("face", ""),
            stat=data.get("stat", {}),
            videos=data.get("videos", 1),
            copyright=data.get("copyright", 1),
            tid=data.get("tid", 0),
            tname=data.get("tname", ""),
            dynamic=data.get("dynamic", ""),
            tags=[t.get("tag_name", "") for t in data.get("tag", [])]
            if isinstance(data.get("tag"), list) else [],
        )

    async def get_video_pages(self, bvid: str) -> list[VideoPage]:
        """获取多P视频的分P列表"""
        resp = await self._signed_get(
            "https://api.bilibili.com/x/player/pagelist",
            params={"bvid": bvid},
            rate_limit="video",
        )
        data = self._parse_response(resp)
        return [VideoPage(**p) for p in data]

    # ================================================================
    # 字幕 / AI 章节
    # ================================================================

    async def get_video_subtitle(self, bvid: str, cid: int) -> list[SubtitleItem]:
        """
        获取视频字幕（CC字幕）

        API: x/player/v2 → subtitle.subtitles[].subtitle_url
        返回的 JSON 格式: { body: [{from, to, content}, ...] }
        """
        resp = await self._signed_get(
            "https://api.bilibili.com/x/player/v2",
            params={"bvid": bvid, "cid": cid},
            rate_limit="video",
        )
        data = self._parse_response(resp)

        subtitles: list[SubtitleItem] = []
        subtitle_data = data.get("subtitle", {}) if isinstance(data, dict) else {}
        subtitle_list = subtitle_data.get("subtitles", []) if isinstance(subtitle_data, dict) else []
        for sub in subtitle_list:
            sub_url = sub.get("subtitle_url", "")
            if not sub_url:
                continue
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url

            client = get_http_client()
            sub_resp = await client.get(sub_url, scene="video")
            sub_data = sub_resp.json()
            body = sub_data.get("body", []) if isinstance(sub_data, dict) else []
            if not isinstance(body, list):
                continue
            for item in body:
                if not isinstance(item, dict):
                    continue
                try:
                    subtitles.append(SubtitleItem(
                        from_time=float(item.get("from", 0)),
                        to_time=float(item.get("to", 0)),
                        content=str(item.get("content", "")),
                    ))
                except (TypeError, ValueError):
                    continue
        return subtitles

    async def get_video_chapters(self, bvid: str, cid: int) -> list[ChapterItem]:
        """
        获取 AI 视频章节

        API: x/web-interface/view/conclusion

        注意：该接口仅在视频开通「AI 总结」时才有数据，
        未开通时 B站 直接返回 HTTP 404（{"code":-404,"message":"啥都木有"}），
        这里把 404 视为「无章节」返回空列表，不视为错误。
        """
        try:
            resp = await self._signed_get(
                "https://api.bilibili.com/x/web-interface/view/conclusion",
                params={"bvid": bvid, "cid": cid},
                rate_limit="video",
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info(f"视频 {bvid} 未开通 AI 总结（B站返回404），章节列表为空")
                return []
            raise

        data = self._parse_response(resp)
        chapters = []
        for ch in data.get("model_list", {}).get("chapters", []):
            chapters.append(ChapterItem(
                cid=ch.get("cid", cid),
                chapter_name=ch.get("chapter_name", ""),
                start_time=ch.get("start_time", 0.0),
                end_time=ch.get("end_time", 0.0),
                summary=ch.get("summary", ""),
            ))
        return chapters

    # ================================================================
    # 评论
    # ================================================================

    async def get_comments(
        self,
        oid: int,
        page: int = 1,
        sort: int = 1,  # 0=按时间, 1=按热度
        page_size: int = 20,
    ) -> tuple[list[Comment], PageInfo]:
        """
        获取视频评论

        API: x/v2/reply
        """
        resp = await self._signed_get(
            "https://api.bilibili.com/x/v2/reply",
            params={"oid": oid, "type": 1, "pn": page, "ps": page_size, "sort": sort},
            rate_limit="comment",
        )
        data = self._parse_response(resp)

        comments = []
        for reply in data.get("replies", []):
            member_data = reply.get("member", {})
            comments.append(Comment(
                rpid=reply["rpid"],
                member=MemberInfo(
                    mid=member_data.get("mid", 0),
                    uname=member_data.get("uname", ""),
                    avatar=member_data.get("avatar", ""),
                    level=member_data.get("level_info", {}).get("current_level", 0),
                ),
                content=reply["content"]["message"],
                ctime=_ts_to_datetime(reply.get("ctime")),
                like=reply.get("like", 0),
                replies_count=reply.get("rcount", 0),
            ))

        page_info = PageInfo(
            page=page, page_size=page_size,
            total=data.get("page", {}).get("count", 0),
            has_more=(page * page_size) < data.get("page", {}).get("count", 0),
        )
        return comments, page_info

    # ================================================================
    # 弹幕
    # ================================================================

    async def get_danmaku(self, cid: int, segment_index: int = 1) -> list[Danmaku]:
        """
        获取弹幕（protobuf 格式）

        API: x/v2/dm/web/seg.so (protobuf)
        注意：这里用的是 DmSegMobileReply protobuf 格式，
        而不是旧版 XML 格式。需要手动解析 protobuf。
        """
        resp = await self._signed_get(
            "https://api.bilibili.com/x/v2/dm/web/seg.so",
            params={"oid": cid, "type": 1, "segment_index": segment_index},
            rate_limit="danmaku",
        )

        # 弹幕返回的是 protobuf 二进制，解析为结构化弹幕
        return await self._parse_danmaku_protobuf(resp.content)

    async def _parse_danmaku_protobuf(self, raw: bytes) -> list[Danmaku]:
        """解析 B站 DmSegMobileReply protobuf 弹幕响应。"""
        proto_message = _build_danmaku_message_class()()
        proto_message.ParseFromString(raw)
        danmakus: list[Danmaku] = []
        for elem in proto_message.elems:
            content = str(elem.content)
            if not content:
                continue
            danmakus.append(
                Danmaku(
                    content=content,
                    time_point=float(elem.progress) / 1000.0,
                    color=int(elem.color),
                    font_size=int(elem.fontsize),
                    send_time=_ts_to_datetime(elem.ctime),
                )
            )
        return danmakus

    # ================================================================
    # 搜索
    # ================================================================

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        search_type: str = "video",
    ) -> SearchResult:
        """
        搜索视频

        API: x/web-interface/search/type
        """
        resp = await self._signed_get(
            "https://api.bilibili.com/x/web-interface/wbi/search/type",
            params={
                "keyword": keyword,
                "page": page,
                "page_size": page_size,
                "search_type": search_type,
            },
            rate_limit="search",
        )
        data = self._parse_response(resp)

        items = []
        for item in data.get("result", []):
            items.append(SearchVideoItem(
                bvid=item.get("bvid", ""),
                aid=item.get("aid", 0),
                title=item.get("title", "").replace('<em class="keyword">', "").replace("</em>", ""),
                author=item.get("author", ""),
                mid=item.get("mid", 0),
                play=item.get("play", 0),
                danmaku=item.get("video_review", 0),
                duration=_duration_to_seconds(item.get("duration")),
                pubdate=_ts_to_datetime(item.get("pubdate")),
                description=item.get("description", ""),
            ))

        return SearchResult(
            items=items,
            keyword=keyword,
            page_info=PageInfo(
                page=page, page_size=page_size,
                total=data.get("numResults", 0),
                has_more=data.get("numResults", 0) > page * page_size,
            ),
            seid=data.get("seid", ""),
            cost_time=data.get("cost_time", {}).get("total", 0.0),
        )

    # ================================================================
    # UP主信息
    # ================================================================

    async def get_user_info(self, mid: int) -> UserInfo:
        """获取 UP主/用户 信息"""
        resp = await self._signed_get(
            "https://api.bilibili.com/x/space/acc/info",
            params={"mid": mid},
            rate_limit="user",
        )
        data = self._parse_response(resp)

        # acc/info 不包含关注数/粉丝数，需另调 relation/stat 补充
        following, follower = await self._get_user_stat(mid, scene="user")

        return UserInfo(
            mid=data.get("mid", mid),
            name=data.get("name", ""),
            face=data.get("face", ""),
            sign=data.get("sign", ""),
            level=data.get("level", 0),
            birthday=data.get("birthday", ""),
            is_vip=data.get("vip", {}).get("status", 0) == 1,
            sex=data.get("sex", ""),
            stat=UserStat(
                follower=follower,
                following=following,
                video_count=data.get("video_count", 0),
            ),
        )

    # ================================================================
    # 互动信息
    # ================================================================

    async def get_interaction(self, bvid: str, aid: int) -> InteractionInfo:
        """
        获取视频互动状态 + 相关推荐

        API: x/web-interface/archive/stat (需要aid)
             x/web-interface/archive/related

        ⚠️ 修正：之前 await asyncio.gather 的结果直接传给了
        await _parse_response，但 _parse_response 是同步方法，
        不需要 await。现在直接调用 _parse_response(resp)。
        """
        # 两个接口相互独立，任一失败时保留另一部分结果。
        stat_result, related_result = await asyncio.gather(
            self._signed_get(
                "https://api.bilibili.com/x/web-interface/archive/stat",
                params={"bvid": bvid, "aid": aid},
                rate_limit="user",
            ),
            self._signed_get(
                "https://api.bilibili.com/x/web-interface/archive/related",
                params={"bvid": bvid},
                rate_limit="video",
            ),
            return_exceptions=True,
        )
        stat_data: dict[str, Any] = {}
        related_data: Any = []
        if not isinstance(stat_result, Exception):
            try:
                parsed = self._parse_response(stat_result)
                stat_data = parsed if isinstance(parsed, dict) else {}
            except Exception as exc:
                logger.warning("获取视频互动状态失败(bvid=%s): %s", bvid, exc)
        if not isinstance(related_result, Exception):
            try:
                related_data = self._parse_response(related_result)
            except Exception as exc:
                logger.warning("获取相关推荐失败(bvid=%s): %s", bvid, exc)

        related = []
        if isinstance(related_data, list):
            for item in related_data[:6]:
                related.append(RelationVideo(
                    bvid=item.get("bvid", ""), aid=item.get("aid", 0),
                    title=item.get("title", ""),
                    author=(item.get("owner") or {}).get("name", "") if isinstance(item.get("owner"), dict) else "",
                    pic=item.get("pic", ""),
                    play=(item.get("stat") or {}).get("view", 0) if isinstance(item.get("stat"), dict) else 0,
                    danmaku=(item.get("stat") or {}).get("danmaku", 0) if isinstance(item.get("stat"), dict) else 0,
                    reason=((item.get("rcmd_reason") or {}).get("content", "")
                            if isinstance(item.get("rcmd_reason"), dict)
                            else str(item.get("rcmd_reason") or "")),
                ))

        return InteractionInfo(
            bvid=bvid,
            has_liked=(stat_data.get("liked", 0) == 1),
            has_favorited=(stat_data.get("favorited", 0) == 1),
            has_coin=(stat_data.get("coin_number", 0) > 0),
            related_videos=related,
        )

    # ================================================================
    # 当前登录用户信息（自己）
    # ================================================================

    async def get_my_info(self) -> MyInfo:
        """
        获取当前登录用户（自己）的详细信息

        API: x/web-interface/nav（返回登录态信息）
        """
        resp = await self._signed_get(
            "https://api.bilibili.com/x/web-interface/nav",
            rate_limit="default",
        )
        data = self._parse_response(resp)
        if not data.get("isLogin"):
            raise MCPBusinessError(
                "当前未登录(B站返回 isLogin=false), 请检查 BILI_SESSDATA 是否有效",
                code="BILI_NOT_LOGIN",
            )

        wallet = data.get("wallet", {})
        # 新版 B站昵称在 name 字段，uname 可能为空，需回退
        uname = data.get("uname") or data.get("name") or ""
        name = data.get("name") or uname
        mid = data.get("mid", 0)
        # nav 接口不包含关注数/粉丝数，需另调 relation/stat 补充
        following, follower = await self._get_user_stat(mid, scene="default")
        return MyInfo(
            mid=mid,
            uname=uname,
            name=name,
            face=data.get("face", ""),
            sign=data.get("sign", ""),
            level=data.get("level_info", {}).get("current_level", 0),
            money=data.get("money", 0.0),
            vip_type=data.get("vipType", 0),
            birthday=data.get("birthday", ""),
            is_vip=data.get("vipStatus", 0) == 1,
            sex=data.get("sex", ""),
            coins=data.get("money", 0.0),
            following=following,
            follower=follower,
            moral=data.get("moral", 0),
            unread_msg=data.get("unread_msg", 0),
            wallet_bcoin=wallet.get("bcoin_balance", 0.0),
        )


# ================================================================
# 全局单例
# ================================================================

_bili_client: BiliClient | None = None


def get_bili_client() -> BiliClient:
    """获取 B站 爬虫客户端单例"""
    global _bili_client
    if _bili_client is None:
        _bili_client = BiliClient()
    return _bili_client
