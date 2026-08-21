"""
匿名倾诉转述插件 (astrbot_plugin_anon_relay)
============================================

功能：
- 私聊开启匿名模式后倾诉，内容以匿名身份转述到指定群聊；也支持在群聊内直接开启匿名模式。
- 匿名昵称默认从昵称池随机抽取（如 【番茄】），转述格式模板可自由配置：
  默认 【{name}】：{content}，支持 {name}/{content}/{time} 三个占位符。
- 群聊目标映射：A 群的人倾诉只转述到 A 群；也可以统一转述到多个群。
- 未开启时私聊保持沉默（默认）；群聊未开启时完全不干预正常聊天。
- 会话状态通过插件 KV 存储持久化，重启后依然有效。

基于 AstrBot v4（Star API，>= 4.16）开发。
"""

import logging
import random
import re
import time
from dataclasses import MISSING, dataclass, fields

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register

logger = logging.getLogger("astrbot.plugin.anon_relay")

__version__ = "1.3.0"


@dataclass
class AnonRelayConfig:
    """代码级默认配置。WebUI 配置面板由 _conf_schema.json 生成，两者保持一致。"""

    enabled: bool = True                      # 总开关
    start_keywords: str = "开启匿名模式"       # 开启匿名模式的关键词（逗号分隔多个）
    stop_keywords: str = "关闭匿名模式,结束倾诉"  # 关闭会话的关键词（逗号分隔多个）
    target_group_ids: str = ""                # 私聊目标群 + 群聊未匹配规则时的统一兜底目标
    group_target_rules: str = ""              # 群聊映射规则，如 源群号:目标群1,目标群2;源群号2:（空目标=转述回本群）
    relay_format: str = "【{name}】：{content}"  # 转述格式模板，占位符 {name} {content} {time}
    nicknames: str = "番茄,苹果,橘子,草莓,葡萄,西瓜,芒果,菠萝,樱桃,柠檬,蓝莓,桃子,雪梨,石榴,柚子,椰子,荔枝,哈密瓜,火龙果,猕猴桃,香蕉"  # 随机昵称池
    anon_name_prefix: str = "匿名者"           # 昵称池为空时的兜底编号前缀（匿名者-001）
    relay_prefix: str = "【匿名倾诉】"          # 仅当 relay_format 为空（旧格式）时使用
    relay_suffix: str = ""                    # 仅当 relay_format 为空（旧格式）时使用
    show_time: bool = True                    # 在格式模板中提供 {time} 占位符（旧格式附带时间）
    ack_on_relay: bool = True                 # 转述成功后回执（群内会话优先私聊悄悄话）
    silent_when_off: bool = True              # 私聊未开启匿名模式时保持沉默（拦截私聊，不回复）
    private_whitelist: str = ""               # 私聊白名单（逗号分隔的用户ID），白名单内用户可与机器人正常私聊
    enable_group_mode: bool = True            # 允许在群聊内开启匿名模式
    notify_group_on_start: bool = False       # 开启匿名模式时在目标群内播报一条提示
    max_msg_len: int = 500                    # 单条转述最大字数，超长自动分段发送
    session_timeout_min: int = 0              # 会话空闲超时（分钟），0 为不超时


@register("astrbot_plugin_anon_relay", "guishe", "匿名倾诉转述：私聊/群聊开启匿名模式后，将内容以匿名身份转述到指定群聊", __version__)
class AnonRelay(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = self._merge_config(config)
        self.plugin_id = getattr(self, "plugin_id", None) or "anon_relay"
        self.logger = getattr(self, "logger", None) or logger
        self.sessions = {}
        self.user_nicknames = {}
        self.counter = 0
        self._kv_loaded = False

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #

    @staticmethod
    def _default_config() -> dict:
        return {f.name: f.default for f in fields(AnonRelayConfig) if f.default is not MISSING}

    @classmethod
    def _merge_config(cls, config):
        """兼容 dict（含 AstrBotConfig）与 dataclass 两种配置对象。"""
        defaults = cls._default_config()
        if isinstance(config, dict):
            return {**defaults, **config}
        d = getattr(config, "__dict__", None)
        if isinstance(d, dict):
            merged = dict(defaults)
            for k, v in d.items():
                if not k.startswith("_"):
                    merged[k] = v
            return merged
        return dict(defaults)

    def _cfg(self, key):
        v = self.config.get(key)
        if v is None:
            return self._default_config().get(key)
        return v

    def _cfg_bool(self, key):
        v = self._cfg(key)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on", "是", "开")
        return bool(v)

    def _cfg_int(self, key):
        try:
            return int(self._cfg(key))
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------ #
    # 会话持久化（插件 KV 存储）
    # ------------------------------------------------------------------ #

    async def _ensure_kv_loaded(self):
        if self._kv_loaded:
            return
        try:
            self.sessions = dict(await self.get_kv_data("sessions", {}) or {})
            self.user_nicknames = dict(await self.get_kv_data("user_nicknames", {}) or {})
            self.counter = int(await self.get_kv_data("counter", 0) or 0)
        except Exception as e:
            self.logger.warning("读取插件存储失败，本次运行会话数据仅保存在内存: %s", e)
        self._kv_loaded = True

    async def _save_sessions(self):
        try:
            await self.put_kv_data("sessions", self.sessions)
            await self.put_kv_data("user_nicknames", self.user_nicknames)
            await self.put_kv_data("counter", self.counter)
        except Exception as e:
            self.logger.warning("保存会话数据失败: %s", e)

    # ------------------------------------------------------------------ #
    # 消息入口
    # ------------------------------------------------------------------ #

    @filter.regex(r"[\s\S]*")
    async def on_message(self, event: AstrMessageEvent):
        if not self._cfg_bool("enabled"):
            return
        if self._is_private_chat(event):
            await self._ensure_kv_loaded()
            user_key = self._user_key(event)
            key = f"p:{user_key}"
            reply, consume = await self._handle_message(event, key, user_key, private=True)
            if consume:
                self._stop_event(event)
                self._block_default_llm(event)
            if reply:
                yield event.plain_result(reply)
        elif self._cfg_bool("enable_group_mode"):
            group_id = self._get_group_id(event)
            if not group_id:
                return
            await self._ensure_kv_loaded()
            user_key = self._user_key(event)
            key = f"g:{user_key}:{group_id}"
            reply, consume = await self._handle_message(event, key, user_key, private=False)
            if consume:
                self._stop_event(event)
                self._block_default_llm(event)
            if reply:
                # 群内会话的控制消息与回执优先私聊悄悄话，私聊不可达时改在群内提示
                if not await self._whisper(event, reply):
                    yield event.plain_result(reply)

    async def _handle_message(self, event, key, user_key, private):
        text = event.get_message_str().strip()
        if self._contains_keyword(text, self._cfg("start_keywords")):
            return await self._start_session(event, key, user_key, private)
        if self._contains_keyword(text, self._cfg("stop_keywords")):
            return await self._stop_session(key)
        if key in self.sessions:
            if self._session_expired(key):
                return await self._expire_session(key)
            return await self._relay(event, key, private)
        # 私聊未开启且配置为沉默：拦截（白名单用户放行，可与机器人正常聊天）；群聊未开启：完全放行
        if private and self._cfg_bool("silent_when_off") and not self._is_whitelisted(event):
            return None, True
        return None, False

    def _is_whitelisted(self, event):
        """私聊白名单：匹配用户 ID 或 平台:ID（如 qq:2226175932）。"""
        raw = str(self._cfg("private_whitelist") or "").strip()
        if not raw:
            return False
        try:
            sender_id = str(event.get_sender_id() or "").strip().lower()
        except Exception:
            sender_id = ""
        if not sender_id:
            return False
        try:
            platform = str(event.get_platform_name() or "").strip().lower()
        except Exception:
            platform = ""
        for entry in re.split(r"[,，、;；\s]+", raw):
            e = entry.strip().lower()
            if not e:
                continue
            if e == sender_id:
                return True
            if platform and e == f"{platform}:{sender_id}":
                return True
        return False

    # ------------------------------------------------------------------ #
    # 会话控制
    # ------------------------------------------------------------------ #

    async def _start_session(self, event, key, user_key, private):
        if key in self.sessions:
            return "你已处于匿名模式，直接发送内容即可。", True
        targets = self._target_groups() if private else self._targets_for_group(self._get_group_id(event))
        if not targets:
            return "⚠️ 管理员还未在插件设置中填写「目标群号」，暂时无法开启匿名模式。", True
        nickname = self._pick_nickname(user_key)
        self.sessions[key] = {
            "nickname": nickname,
            "started_at": time.time(),
            "last_active": time.time(),
        }
        await self._save_sessions()
        if self._cfg_bool("notify_group_on_start"):
            await self._send_to_groups(targets, event, [Plain(text=self._relay_header(nickname) + " 已开启匿名倾诉")])
        stop_hint = str(self._cfg("stop_keywords")).split(",")[0]
        return (
            f"🔇 匿名模式已开启，你的匿名身份是「{nickname}」\n"
            "现在可以开始倾诉了，我会把你的内容匿名转述到指定群聊。\n"
            f"发送「{stop_hint}」即可结束。"
        ), True

    async def _stop_session(self, key):
        if key not in self.sessions:
            return "你当前没有开启匿名模式。", True
        self.sessions.pop(key, None)
        await self._save_sessions()
        return "匿名模式已关闭。感谢你的信任，随时可以再来倾诉 🌱", True

    async def _expire_session(self, key):
        self.sessions.pop(key, None)
        await self._save_sessions()
        return "会话已超时自动结束，如需继续请重新发送开启关键词。", True

    def _session_expired(self, key):
        timeout = self._cfg_int("session_timeout_min")
        if timeout <= 0:
            return False
        s = self.sessions.get(key)
        if not s:
            return False
        return (time.time() - float(s.get("last_active", 0) or 0)) > timeout * 60

    # ------------------------------------------------------------------ #
    # 匿名昵称
    # ------------------------------------------------------------------ #

    def _pick_nickname(self, user_key):
        """同一用户昵称固定；新用户从昵称池随机抽取，池为空则回退编号。"""
        if user_key in self.user_nicknames:
            return self.user_nicknames[user_key]
        pool = self._nickname_pool()
        if pool:
            nickname = random.choice(pool)
        else:
            self.counter += 1
            nickname = f"{str(self._cfg('anon_name_prefix') or '匿名者')}-{self.counter:03d}"
        self.user_nicknames[user_key] = nickname
        return nickname

    def _nickname_pool(self):
        pool = []
        for part in re.split(r"[,，、;；\s]+", str(self._cfg("nicknames") or "")):
            p = part.strip()
            if p and p not in pool:
                pool.append(p)
        return pool

    # ------------------------------------------------------------------ #
    # 转述
    # ------------------------------------------------------------------ #

    async def _relay(self, event, key, private):
        session = self.sessions.get(key)
        if not session:
            return None, True
        chain = self._get_message_chain(event)
        parts = [c for c in chain if isinstance(c, (Plain, Image))]
        if not parts:
            # 语音/表情/视频等无法转述的消息：只提示一次，避免刷屏
            if session.get("warned_unsupported"):
                return None, True
            session["warned_unsupported"] = True
            session["last_active"] = time.time()
            await self._save_sessions()
            return "暂不支持转述这类消息（仅支持文字和图片），本会话内不再重复提示。", True
        if private:
            targets = self._target_groups()
        else:
            targets = self._targets_for_group(self._get_group_id(event))
        if not targets:
            return "⚠️ 目标群聊未配置，无法转述，请联系管理员。", True

        text = event.get_message_str().strip()
        images = [c for c in parts if isinstance(c, Image)]
        max_len = self._cfg_int("max_msg_len") or 500
        msgs = self._build_relay_messages(session["nickname"], text, images, max_len)

        ok = True
        for gid in targets:
            for m in msgs:
                if not await self._send_group(event, gid, m):
                    ok = False

        session["last_active"] = time.time()
        await self._save_sessions()

        if not ok:
            return "⚠️ 转述失败，请稍后重试。", True
        if self._cfg_bool("ack_on_relay"):
            return "已为你转述 ✅", True
        return None, True

    def _build_relay_messages(self, name, text, images, max_len):
        """按格式模板组装转述消息。格式为空时使用旧格式（前缀行 + 内容）。"""
        fmt = str(self._cfg("relay_format") or "").strip()
        time_str = time.strftime("%m-%d %H:%M") if self._cfg_bool("show_time") else ""

        def render(content):
            if fmt:
                return fmt.replace("{name}", name).replace("{content}", content).replace("{time}", time_str)
            head = " ".join(p for p in [
                str(self._cfg("relay_prefix") or ""),
                name,
                time_str,
                str(self._cfg("relay_suffix") or "").strip(),
            ] if p)
            return f"{head}\n{content}" if content else head

        chunks = self._split_text(text, max_len)
        msgs = []
        if not chunks:
            msgs.append([Plain(text=render("")), *images])
            return msgs
        for i, chunk in enumerate(chunks):
            comps = [Plain(text=render(chunk))]
            if i == len(chunks) - 1:
                comps.extend(images)
            msgs.append(comps)
        return msgs

    def _relay_header(self, name):
        parts = [str(self._cfg("relay_prefix") or ""), name]
        if self._cfg_bool("show_time"):
            parts.append(time.strftime("%m-%d %H:%M", time.localtime()))
        suffix = str(self._cfg("relay_suffix") or "").strip()
        if suffix:
            parts.append(suffix)
        return " ".join(p for p in parts if p)

    @staticmethod
    def _split_text(text, max_len):
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= max_len:
            return [text]
        return [text[i:i + max_len] for i in range(0, len(text), max_len)]

    # ------------------------------------------------------------------ #
    # 目标群解析
    # ------------------------------------------------------------------ #

    def _target_groups(self):
        raw = str(self._cfg("target_group_ids") or "")
        groups = []
        for part in re.split(r"[,，、;；\s]+", raw):
            p = part.strip()
            if p and p not in groups:
                groups.append(p)
        return groups

    def _targets_for_group(self, group_id):
        """群聊转述目标：优先匹配映射规则，未匹配时使用统一目标（target_group_ids）。"""
        rules = self._parse_group_rules()
        if group_id in rules:
            return rules[group_id]
        return self._target_groups()

    def _parse_group_rules(self):
        """解析群聊映射规则。格式：源群号:目标群1,目标群2;源群号2:（空目标=转述回本群）。"""
        raw = str(self._cfg("group_target_rules") or "").replace("：", ":")
        rules = {}
        for part in re.split(r"[;；\n]+", raw):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                src, _, tgt = part.partition(":")
                src = src.strip()
                tgt = tgt.strip()
                targets = [t.strip() for t in re.split(r"[,，、\s]+", tgt) if t.strip()] if tgt else [src]
            else:
                src, targets = part.strip(), [part.strip()]
            if src and src not in rules:
                rules[src] = targets
        return rules

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #

    async def _send_to_groups(self, groups, event, chain):
        ok = True
        for gid in groups:
            if not await self._send_group(event, gid, chain):
                ok = False
        return ok

    async def _send_group(self, event, group_id, chain):
        """通过 Context.send_message 主动发送到指定群聊（v4 官方通道）。"""
        if not isinstance(chain, MessageChain):
            chain = MessageChain(chain=chain)
        session_str = f"{event.get_platform_id()}:GroupMessage:{group_id}"
        try:
            ok = await self.context.send_message(session_str, chain)
            if not ok:
                self.logger.error("未找到平台 %s，无法转述到群 %s", event.get_platform_id(), group_id)
            return bool(ok)
        except Exception as e:
            self.logger.error("转述到群 %s 失败: %s", group_id, e)
            return False

    async def _whisper(self, event, text):
        """给用户私聊发悄悄话（群内会话的回执/控制消息优先走这里）。"""
        session_str = f"{event.get_platform_id()}:FriendMessage:{event.get_sender_id()}"
        try:
            return bool(await self.context.send_message(session_str, MessageChain(chain=[Plain(text=text)])))
        except Exception as e:
            self.logger.info("私聊悄悄话发送失败，改为群内提示: %s", e)
            return False

    # ------------------------------------------------------------------ #
    # 工具方法
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_message_chain(event):
        fn = getattr(event, "get_messages", None) or getattr(event, "get_message", None)
        return fn() if fn else []

    @staticmethod
    def _is_private_chat(event):
        try:
            if hasattr(event, "is_private_chat"):
                return bool(event.is_private_chat())
        except Exception:
            pass
        try:
            mo = getattr(event, "message_obj", None)
            if mo is not None:
                return getattr(mo, "group_id", None) is None
        except Exception:
            pass
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        return "friend" in origin.lower() or "private" in origin.lower()

    @staticmethod
    def _get_group_id(event):
        try:
            return str(event.get_group_id() or "")
        except Exception:
            return ""

    @staticmethod
    def _user_key(event):
        return f"{event.get_platform_name()}:{event.get_sender_id()}"

    @staticmethod
    def _stop_event(event):
        try:
            event.stop_event()
        except Exception:
            pass

    @staticmethod
    def _block_default_llm(event):
        try:
            event.should_call_llm(False)
        except Exception:
            pass

    @staticmethod
    def _contains_keyword(text, keywords):
        if not text or not keywords:
            return False
        for kw in re.split(r"[,，、;；]+", str(keywords)):
            if kw and kw in text:
                return True
        return False
