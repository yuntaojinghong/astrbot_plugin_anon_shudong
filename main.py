"""
匿名树洞 (astrbot_plugin_anon_shudong)
====================================

给机器人开一个「树洞」：把倾诉匿名转述到群聊，群成员看不到任何真实身份。

功能：
- 白名单外的用户私聊机器人自动进入匿名模式（也可用关键词主动开启）；群聊内也能直接开启。
- 匿名昵称默认从昵称池随机抽取（如 【番茄】），转述格式模板可自由配置：
  默认 【{name}】：{content}，支持 {name}/{content}/{time} 三个占位符。
- 转述目标是机器人重新拼装的一条新消息，不是转发聊天记录。
- 目标群映射：A 群的人倾诉只转述到 A 群；也可以统一转述到多个群。
- 词库 txt 文件上传：和谐词库 / 审查词库 / 昵称池 支持管理员直接上传 txt 文件批量导入，可一键重置。
- 未开启时私聊保持沉默（默认）；群聊未开启时完全不干预正常聊天。
- 会话状态通过插件 KV 存储持久化，重启后依然有效。

基于 AstrBot v4（Star API，>= 4.16）开发。
"""

import logging
import os
import random
import re
import time
from dataclasses import MISSING, dataclass, field, fields

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import File, Image, Plain
from astrbot.api.star import Context, Star, register

logger = logging.getLogger("astrbot.plugin.anon_relay")

__version__ = "2.1.0"


@dataclass
class AnonRelayConfig:
    """代码级默认配置。WebUI 配置面板由 _conf_schema.json 生成，两者保持一致。"""

    enabled: bool = True                      # 总开关
    start_keywords: str = "开启匿名模式"       # 开启匿名模式的关键词（逗号分隔多个）
    stop_keywords: str = "关闭匿名模式,结束倾诉"  # 关闭会话的关键词（逗号分隔多个）
    target_group_ids: str = ""                # 私聊目标群 + 群聊未匹配规则时的统一兜底目标
    group_target_rules: str = ""              # 群聊会话映射规则（群内开启时生效），如 源群号:目标群1,目标群2;源群号2:
    user_target_rules: str = ""               # 私聊用户映射规则（优先）：用户ID:目标群1,目标群2;用户ID2:
    auto_detect_groups: bool = True           # 私聊时自动识别倾诉者所在群并转述到这些群（支持 OneBot/QQ）
    detect_group_ids: str = ""                # 自动识别的候选群（逗号分隔；留空=机器人所在全部群）
    relay_format: str = "【{name}】：{content}"  # 转述格式模板，占位符 {name} {content} {time}
    nicknames: str = "番茄,苹果,橘子,草莓,葡萄,西瓜,芒果,菠萝,樱桃,柠檬,蓝莓,桃子,雪梨,石榴,柚子,椰子,荔枝,哈密瓜,火龙果,猕猴桃,香蕉"  # 随机昵称池
    nicknames_file: list = field(default_factory=list)  # 设置页上传的昵称池文件（files/ 相对路径列表）
    anon_name_prefix: str = "匿名者"           # 昵称池为空时的兜底编号前缀（匿名者-001）
    relay_prefix: str = "【匿名倾诉】"          # 仅当 relay_format 为空（旧格式）时使用
    relay_suffix: str = ""                    # 仅当 relay_format 为空（旧格式）时使用
    show_time: bool = True                    # 在格式模板中提供 {time} 占位符（旧格式附带时间）
    ack_on_relay: bool = True                 # 转述成功后回执（群内会话优先私聊悄悄话）
    silent_when_off: bool = True              # 私聊未开启匿名模式时保持沉默（拦截私聊，不回复）
    private_whitelist: str = ""               # 私聊白名单（逗号分隔的用户ID），白名单内用户可与机器人正常私聊
    auto_anon_private: bool = True            # 私聊自动开启匿名模式：白名单外用户私聊直接进入匿名模式，白名单用户可自由聊天（仍可用关键词开启）
    enable_group_mode: bool = True            # 允许在群聊内开启匿名模式
    notify_group_on_start: bool = False       # 开启匿名模式时在目标群内播报一条提示
    max_msg_len: int = 500                    # 单条转述最大字数，超长自动分段发送
    max_images: int = 4                       # 单条转述最多携带的图片数（超出忽略）
    allow_image_relay: bool = True            # 是否允许把图片转述到群（图片无法做内容审查）
    session_timeout_min: int = 0              # 会话空闲超时（分钟），0 为不超时
    admin_commands_enabled: bool = True       # 启用管理命令（禁言/解禁/永久禁用/解除禁用，仅管理员）
    mute_default_min: int = 30                # 禁言默认时长（分钟）
    censor_enabled: bool = True               # 脏话自动和谐
    bad_words: str = "傻逼,煞笔,傻B,草泥马,操你妈,去死,贱人,白痴,智障,废物,他妈的,妈的,混蛋,滚蛋,王八蛋,狗东西,杂种,婊子,你妈"  # 和谐词库（逗号分隔）
    bad_words_file: list = field(default_factory=list)  # 设置页上传的和谐词库文件（files/ 相对路径列表）
    censor_mask: str = "**"                   # 和谐替换符号
    review_enabled: bool = True               # 内容审查：命中敏感词直接拦截不转述
    review_words: str = ""                    # 审查词库（反动/极端言论等，逗号分隔；命中即拦截）
    review_words_file: list = field(default_factory=list)  # 设置页上传的审查词库文件（files/ 相对路径列表）


@register("astrbot_plugin_anon_shudong", "yuntaojinghong", "匿名树洞：私聊/群聊开启匿名模式后，把倾诉内容以匿名身份转述到指定群聊，群成员看不到真实身份", __version__)
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
        # 身份域 -> 已占用昵称集合的缓存（避免每次分配都全表扫描）
        self._name_index = {}
        self._member_cache = {}
        self._member_cache_ttl = 600
        self.muted = {}
        self.banned = []
        self.uploaded_words = {}  # 词库文件上传的词表：{bad_words: [...], review_words: [...], nicknames: [...]}
        self._file_words_cache = {}  # 设置页上传词库文件的解析缓存：{路径: (mtime_ns, size, [词])}
        self.plugin_name = self._resolve_plugin_name()

    @staticmethod
    def _resolve_plugin_name():
        """解析插件在 AstrBot 数据目录中的目录名（用于定位设置页上传的词库文件）。"""
        try:
            from astrbot.core.star.star import star_map
            meta = star_map.get(AnonRelay.__module__)
            if meta and getattr(meta, "name", None):
                return str(meta.name)
        except Exception:
            pass
        try:
            if getattr(AnonRelay, "name", None):
                return str(AnonRelay.name)
        except Exception:
            pass
        return "astrbot_plugin_anon_shudong"

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #

    @staticmethod
    def _default_config() -> dict:
        out = {}
        for f in fields(AnonRelayConfig):
            if f.default is not MISSING:
                out[f.name] = f.default
            elif f.default_factory is not MISSING:
                out[f.name] = f.default_factory()
        return out

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
            self.muted = dict(await self.get_kv_data("muted", {}) or {})
            self.banned = list(await self.get_kv_data("banned", []) or [])
            raw_uw = await self.get_kv_data("uploaded_words", {}) or {}
            self.uploaded_words = {
                k: list(v) for k, v in raw_uw.items()
                if k in ("bad_words", "review_words", "nicknames") and isinstance(v, (list, tuple))
            }
            self._rebuild_name_index()
        except Exception as e:
            self.logger.warning("读取插件存储失败，本次运行会话数据仅保存在内存: %s", e)
        self._kv_loaded = True

    def _rebuild_name_index(self):
        """从已加载的昵称映射重建「身份域 -> 已占用昵称」索引。"""
        index = {}
        for k, n in self.user_nicknames.items():
            if not n:
                continue
            scope = k.partition("|")[2]
            if not scope:
                continue
            index.setdefault(scope, set()).add(n)
        self._name_index = index

    async def _save_sessions(self):
        try:
            await self.put_kv_data("sessions", self.sessions)
            await self.put_kv_data("user_nicknames", self.user_nicknames)
            await self.put_kv_data("counter", self.counter)
            await self.put_kv_data("muted", self.muted)
            await self.put_kv_data("banned", self.banned)
            await self.put_kv_data("uploaded_words", self.uploaded_words)
        except Exception as e:
            self.logger.warning("保存会话数据失败: %s", e)

    # ------------------------------------------------------------------ #
    # 消息入口
    # ------------------------------------------------------------------ #

    @filter.regex(r"[\s\S]*")
    async def on_message(self, event: AstrMessageEvent):
        if not self._cfg_bool("enabled"):
            return
        await self._ensure_kv_loaded()
        # 管理命令：禁言/解禁/永久禁用/解除禁用（仅管理员，私聊与群聊均可）
        if self._cfg_bool("admin_commands_enabled"):
            cmd_reply, cmd_consumed = await self._try_admin_command(event)
            if cmd_consumed:
                self._stop_event(event)
                self._block_default_llm(event)
                if cmd_reply:
                    yield event.plain_result(cmd_reply)
                return
        # 词库 txt 文件上传 / 词库重置（仅管理员，私聊与群聊均可）
        wl_reply, wl_consumed = await self._try_wordlib_command(event)
        if wl_consumed:
            self._stop_event(event)
            self._block_default_llm(event)
            if wl_reply:
                yield event.plain_result(wl_reply)
            return
        if self._is_private_chat(event):
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

    # ------------------------------------------------------------------ #
    # 管理命令（禁言 / 解禁 / 永久禁用 / 解除禁用）
    # ------------------------------------------------------------------ #

    async def _try_admin_command(self, event):
        """识别管理命令。返回 (回复文本或 None, 是否已消费该消息)。"""
        text = event.get_message_str().strip()
        # 命令词后必须紧跟空格/冒号/结尾，避免把「解禁后内容」这类消息误判为命令
        m = re.match(r"^(禁言|解禁|永久禁用|解除禁用)(?=[\s:：]|$)\s*[:：]?\s*(\S+?)\s*(\d*)$", text)
        if not m:
            return None, False
        action, nickname, minutes = m.group(1), m.group(2), m.group(3)
        if not self._is_admin(event):
            return "⚠️ 你没有管理员权限，无法执行该操作。", True
        if action == "禁言":
            mins = int(minutes) if minutes else (self._cfg_int("mute_default_min") or 30)
            self.muted[nickname] = time.time() + mins * 60
            await self._save_sessions()
            return f"🔇 已禁言匿名身份「{nickname}」{mins} 分钟。", True
        if action == "解禁":
            if nickname in self.muted:
                del self.muted[nickname]
                await self._save_sessions()
                return f"✅ 已解除「{nickname}」的禁言。", True
            return f"「{nickname}」当前没有被禁言。", True
        if action == "永久禁用":
            if nickname not in self.banned:
                self.banned.append(nickname)
                await self._save_sessions()
            return f"🚫 已永久禁用匿名身份「{nickname}」，其无法再开启匿名模式。", True
        if action == "解除禁用":
            if nickname in self.banned:
                self.banned.remove(nickname)
                await self._save_sessions()
                return f"✅ 已解除「{nickname}」的永久禁用。", True
            return f"「{nickname}」未被永久禁用。", True
        return None, False

    @staticmethod
    def _is_admin(event):
        try:
            if hasattr(event, "is_admin"):
                return bool(event.is_admin())
        except Exception:
            pass
        try:
            return getattr(event, "role", "") == "admin"
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # 词库 txt 文件上传 / 重置（仅管理员）
    # ------------------------------------------------------------------ #

    WORDLIB_LABELS = {"bad_words": "和谐词库", "review_words": "审查词库", "nicknames": "昵称池"}
    WORDLIB_FILE_MAX = 2 * 1024 * 1024  # 词库文件大小上限 2MB
    WORDLIB_MAX_WORDS = 10000           # 单次导入词数上限

    async def _try_wordlib_command(self, event):
        """识别词库上传/重置命令。返回 (回复文本或 None, 是否已消费该消息)。"""
        text = event.get_message_str().strip()
        # 重置命令：重置词库 / 重置和谐词库 / 重置审查词库 / 重置昵称池
        m = re.match(r"^重置(全部词库|和谐词库|审查词库|昵称池|词库)?$", text)
        if m:
            if not self._is_admin(event):
                return "⚠️ 你没有管理员权限，无法执行该操作。", True
            target = {"和谐词库": "bad_words", "审查词库": "review_words", "昵称池": "nicknames"}.get(m.group(1))
            targets = [target] if target else list(self.WORDLIB_LABELS)
            labels = [self.WORDLIB_LABELS[t] for t in targets]
            for t in targets:
                self.uploaded_words.pop(t, None)
            await self._save_sessions()
            return f"✅ 已重置{'、'.join(labels)}，恢复为插件配置面板中的设置。", True

        # 上传：消息中带有文件（File 组件）才处理
        files = [c for c in self._get_message_chain(event) if isinstance(c, File)]
        if not files:
            return None, False
        target = self._wordlib_target_from_text(text) or self._wordlib_target_from_filename(files[0].name)
        if not target:
            # 带了文件但无法识别目标：仅当消息明确提到「上传词库」时提示用法，否则放行
            if "上传" in text and ("词库" in text or "词表" in text or "昵称池" in text):
                if not self._is_admin(event):
                    return "⚠️ 你没有管理员权限，无法上传词库。", True
                return ("⚠️ 无法识别要上传的词库。请将文件名改为 bad_words.txt（和谐词库）、"
                        "review_words.txt（审查词库）或 nicknames.txt（昵称池），"
                        "或在消息中说明，如「上传和谐词库」。"), True
            return None, False
        if not self._is_admin(event):
            return "⚠️ 你没有管理员权限，无法上传词库。", True
        path = await self._resolve_file(files[0], event)
        if not path:
            return "⚠️ 无法获取上传的文件（可能下载失败或超过 2MB 上限），请稍后重试。", True
        words = self._read_words_from_file(path)
        if not words:
            return ("⚠️ 未从文件中解析出任何词：请确保是 txt 文本（每行一个词，# 开头为注释行），"
                    "支持 UTF-8 / GBK 编码。"), True
        truncated = False
        if len(words) > self.WORDLIB_MAX_WORDS:
            words = words[:self.WORDLIB_MAX_WORDS]
            truncated = True
        self.uploaded_words[target] = words
        await self._save_sessions()
        label = self.WORDLIB_LABELS[target]
        tip = "（超过 10000 词的部分已忽略）" if truncated else ""
        return (f"✅ 已上传「{label}」：共加载 {len(words)} 个词（已去重）{tip}，即时生效。\n"
                "发送「重置词库」可恢复为插件配置面板中的设置。"), True

    async def _resolve_file(self, file_comp, event):
        """获取上传文件的本地路径；若为 URL 下载的临时文件，登记由框架在事件处理后清理。"""
        try:
            path = await file_comp.get_file()
        except Exception:
            return ""
        path = (path or "").strip()
        if not path or not os.path.exists(path):
            return ""
        if os.path.getsize(path) > self.WORDLIB_FILE_MAX:
            return ""
        try:
            if file_comp.url and not file_comp.file_:
                event.track_temporary_local_file(path)
        except Exception:
            pass
        return path

    @staticmethod
    def _wordlib_target_from_text(text):
        low = str(text or "").lower()
        if "和谐词库" in text or "bad_words" in low or "badwords" in low:
            return "bad_words"
        if "审查词库" in text or "review_words" in low or "reviewwords" in low:
            return "review_words"
        if "昵称池" in text or "nicknames" in low:
            return "nicknames"
        return None

    @staticmethod
    def _wordlib_target_from_filename(name):
        base = os.path.splitext(str(name or ""))[0].strip().lower()
        base = base.replace("词库", "").replace("池", "")
        if base in ("bad_words", "badwords", "bad", "和谐", "脏话"):
            return "bad_words"
        if base in ("review_words", "reviewwords", "review", "审查", "敏感词", "敏感"):
            return "review_words"
        if base in ("nicknames", "nickname", "昵称", "names"):
            return "nicknames"
        return None

    @staticmethod
    def _read_words_from_file(path):
        """读取词库 txt：支持 UTF-8（含 BOM）与 GBK/GB18030 编码；每行一个词，# 开头为注释行；
        一行内也可用逗号、顿号、分号或空白分隔多个词。"""
        raw = None
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                with open(path, "r", encoding=enc) as f:
                    raw = f.read()
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        if raw is None:
            return []
        words = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for part in re.split(r"[,，、;；\s]+", line):
                p = part.strip()
                if p and p not in words:
                    words.append(p)
        return words

    async def _handle_message(self, event, key, user_key, private):
        text = event.get_message_str().strip()
        # 永久禁用优先于一切：无论是否已在会话中、是否重发开启关键词，
        # 都必须先拦住。此前只在「开启会话」时判一次，被禁用的人只要不关会话
        # 就能继续发；重发开启关键词还会被当成「已在会话中」而放行。
        if self._is_user_banned(user_key):
            if key in self.sessions:
                self.sessions.pop(key, None)
                await self._save_sessions()
            return "🚫 该匿名身份已被管理员永久禁用，无法使用匿名模式。", True
        if self._contains_keyword(text, self._cfg("start_keywords")):
            return await self._start_session(event, key, user_key, private)
        if self._contains_keyword(text, self._cfg("stop_keywords")):
            return await self._stop_session(key)
        if key in self.sessions:
            if self._session_expired(key):
                return await self._expire_session(key)
            return await self._relay(event, key, private)
        if private:
            whitelisted = self._is_whitelisted(event)
            if not whitelisted:
                # 白名单外：开启自动匿名模式时，私聊直接进入匿名模式（无需关键词）
                if self._cfg_bool("auto_anon_private"):
                    return await self._start_session(event, key, user_key, private)
                # 否则按沉默策略处理
                if self._cfg_bool("silent_when_off"):
                    return None, True
        # 白名单内自由聊天；群聊未开启：完全放行
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
        if private:
            targets = await self._targets_for_private(event)
        else:
            targets = self._targets_for_group(self._get_group_id(event))
        targets = self._dedupe_targets(targets)
        if not targets:
            return "⚠️ 管理员还未在插件设置中填写「目标群号」，暂时无法开启匿名模式。", True
        # 永久禁用按「用户名下所有匿名身份」判定，换群拿新昵称也不能绕过
        if self._is_user_banned(user_key):
            return "🚫 该匿名身份已被管理员永久禁用，无法开启匿名模式。", True
        nickname = self._nickname_for(user_key, self._scope_of(targets[0], private))
        # 该昵称若已被管理员永久禁用，直接拒绝开启
        if nickname in self.banned:
            return "🚫 该匿名身份已被管理员永久禁用，无法开启匿名模式。", True
        self.sessions[key] = {
            "nickname": nickname,          # 主身份：单目标转述与回执展示用
            "nickname_scope": self._scope_of(targets[0], private),
            "started_at": time.time(),
            "last_active": time.time(),
        }
        await self._save_sessions()
        if self._cfg_bool("notify_group_on_start"):
            for i, gid in enumerate(targets):
                # 播报也要带上该群自己的匿名昵称，否则一播报就把人串起来了
                name = nickname if i == 0 else self._nickname_for(
                    user_key, self._scope_of(gid, False)
                )
                await self._send_group(
                    event, gid, MessageChain(chain=[Plain(text=self._relay_header(name) + " 已开启匿名倾诉")])
                )
        stop_hint = str(self._cfg("stop_keywords")).split(",")[0]
        lines = []
        if len(targets) > 1:
            extra = "、".join(
                f"{gid}→{self._nickname_for(user_key, self._scope_of(gid, False))}"
                for gid in targets[1:]
            )
            lines.append(f"🔇 匿名模式已开启，你在 {targets[0]} 的匿名身份是「{nickname}」")
            lines.append(f"其他群使用各自独立的昵称（{extra}），互相无法关联。")
            if self._cfg_bool("show_time"):
                # 同一秒发往多个群的消息时间戳相同，两个群的人可以把内容对上；
                # 昵称已经分开了，这里是仅存的关联线索，直接在会话里提醒一次。
                lines.append("💡 多群转述建议把「启用时间占位符 show_time」关掉：时间戳会削弱跨群匿名。")
        else:
            lines.append(f"🔇 匿名模式已开启，你的匿名身份是「{nickname}」")
        lines.append("现在可以开始倾诉了，我会把你的内容匿名转述到指定群聊。")
        lines.append(f"发送「{stop_hint}」即可结束。")
        lines.append(f"📮 本次将转述到：{'、'.join(targets)}")
        return "\n".join(lines), True

    @staticmethod
    def _scope_of(target_group, private):
        """把转述目标映射成「身份域」：每个群一套独立昵称。

        私聊会话也按**目标群**分域：同一个人在 A 群和 B 群拿到的昵称不同，
        两个群无法把内容关联到同一个人（跨群匿名是插件的核心承诺）。
        """
        if not target_group and private:
            return "p"
        return f"g:{target_group}"

    @staticmethod
    def _dedupe_targets(targets):
        """目标群去重并保序（映射规则里写重了不应导致重复转述）。"""
        out = []
        for t in targets or []:
            t = str(t).strip()
            if t and t not in out:
                out.append(t)
        return out

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

    def _nickname_for(self, user_key, scope):
        """取某用户在某「身份域」下的匿名昵称，没有就新建一个。

        身份域（scope）的含义：
        - ``g:<群号>``  —— 该用户在这个群里的匿名身份
        - ``p``         —— 私聊会话的主身份（用于单目标转述与回执展示）

        **每个群一套独立昵称**：同一个人在 A 群叫「番茄」、在 B 群叫「葡萄」，
        两个群的人都无法把两边的内容关联到同一个人身上。此前是「一个人一个
        昵称、全局复用」，只要有人同时在多个目标群，昵称一撞就能拼出完整轨迹，
        跨群匿名的承诺形同虚设。

        昵称在「同一身份域内」保证互不重复（README 承诺），池子用尽后追加
        编号而非静默重名——重名会让管理员「禁言 番茄」一次误伤多人。
        """
        scope_key = f"{user_key}|{scope}"
        cached = self.user_nicknames.get(scope_key)
        if cached:
            return cached

        pool = self._nickname_pool()
        # 两个约束同时满足：
        # 1) 同一身份域内不重名（别人不会拿到同一个昵称）；
        # 2) **同一个人在不同群不重名**——否则两个群一旦对上昵称就能确认是同一个人，
        #    跨群匿名的意义就没了（实测：仅约束 (1) 时同一人两个群仍会撞名）。
        taken = set(self._taken_nicknames(scope)) | self._nicknames_of_user(user_key)
        nickname = self._allocate_nickname(pool, taken)
        self.user_nicknames[scope_key] = nickname
        self._name_index.setdefault(scope, set()).add(nickname)
        return nickname

    def _taken_nicknames(self, scope):
        """该身份域内已被占用的昵称集合。

        索引按身份域（scope）本身索引，不做字符串截取——早先用「key 后缀匹配」
        实现，``g:22`` 与 ``gg:22`` 这类会被误判，导致不同群拿到同一个昵称。
        """
        taken = self._name_index.get(scope)
        if taken is None:
            taken = {
                n for k, n in self.user_nicknames.items()
                if n and k.partition("|")[2] == scope
            }
            self._name_index[scope] = taken
        return taken

    def _allocate_nickname(self, pool, taken):
        """从池中挑一个未被占用的昵称；池子用尽则追加编号。"""
        free = [n for n in pool if n not in taken]
        if free:
            return random.choice(free)
        if pool:
            base = random.choice(pool)
            for i in range(2, 10000):
                cand = f"{base}{i}"
                if cand not in taken:
                    return cand
            base = str(self._cfg("anon_name_prefix") or "匿名者")
        else:
            base = str(self._cfg("anon_name_prefix") or "匿名者")
        self.counter += 1
        return f"{base}-{self.counter:03d}"

    def _nicknames_of_user(self, user_key):
        """该用户在所有身份域下的昵称集合（供管理名单判定用）。"""
        prefix = f"{user_key}|"
        return {
            n for k, n in self.user_nicknames.items()
            if k.startswith(prefix) and n
        }

    def _is_user_banned(self, user_key):
        """用户是否被永久禁用。

        名单按昵称记录（管理员命令以昵称为目标），因此必须把该用户的**所有**
        昵称都查一遍：只查当前昵称的话，被禁用者换个群拿到新昵称就能绕过。
        """
        if not self.banned:
            return False
        names = self._nicknames_of_user(user_key)
        return any(n in self.banned for n in names) or bool(names & set(self.banned))

    def _mute_until(self, names):
        """返回这组昵称里最晚的禁言到期时间（未禁言返回 0）。"""
        if not self.muted:
            return 0.0
        return max((float(self.muted.get(n, 0) or 0) for n in names), default=0.0)

    def _nickname_pool(self):
        uploaded = self.uploaded_words.get("nicknames")
        if uploaded:
            return list(uploaded)
        file_words = self._words_from_config_files("nicknames_file")
        if file_words:
            return file_words
        pool = []
        for part in re.split(r"[,，、;；\s]+", str(self._cfg("nicknames") or "")):
            p = part.strip()
            if p and p not in pool:
                pool.append(p)
        return pool

    # ------------------------------------------------------------------ #
    # 设置页上传的词库文件（_conf_schema.json 中 type=file 的配置项）
    # ------------------------------------------------------------------ #

    def _config_file_paths(self, key):
        """把设置页上传的文件相对路径（files/...）解析为本地绝对路径列表。"""
        v = self._cfg(key)
        if not v:
            return []
        if isinstance(v, str):
            v = [v]
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
            root = os.path.join(get_astrbot_plugin_data_path(), self.plugin_name)
        except Exception:
            root = ""
        paths = []
        for item in v:
            rel = str(item or "").strip().replace("\\", "/")
            if not rel.startswith("files/"):
                continue
            p = os.path.join(root, rel) if root else rel
            if os.path.isfile(p):
                paths.append(p)
        return paths

    def _words_from_config_files(self, key):
        """从设置页上传的词库文件解析词表（带缓存，文件变化自动失效）。"""
        words = []
        for path in self._config_file_paths(key):
            try:
                st = os.stat(path)
                sig = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
            cached = self._file_words_cache.get(path)
            if cached and cached[0] == sig:
                words.extend(cached[1])
                continue
            parsed = self._read_words_from_file(path)
            self._file_words_cache[path] = (sig, parsed)
            words.extend(parsed)
        seen = set()
        out = []
        for w in words:
            if w not in seen:
                seen.add(w)
                out.append(w)
        return out

    # ------------------------------------------------------------------ #
    # 脏话和谐
    # ------------------------------------------------------------------ #

    def _censor(self, text):
        """将词库中的脏话替换为和谐符号。"""
        if not text or not self._cfg_bool("censor_enabled"):
            return text
        words = self._bad_words()
        if not words:
            return text
        mask = str(self._cfg("censor_mask") or "**")
        pattern = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
        return pattern.sub(mask, text)

    def _bad_words(self):
        uploaded = self.uploaded_words.get("bad_words")
        if uploaded:
            return list(uploaded)
        file_words = self._words_from_config_files("bad_words_file")
        if file_words:
            return file_words
        words = []
        for part in re.split(r"[,，、;；\s]+", str(self._cfg("bad_words") or "")):
            p = part.strip()
            if p and p not in words:
                words.append(p)
        return words

    def _review_hit(self, text):
        """内容审查：文本命中审查词库（反动/极端言论等）返回 True。"""
        words = self._review_words()
        if not words:
            return False
        return any(w in text for w in words)

    def _review_words(self):
        uploaded = self.uploaded_words.get("review_words")
        if uploaded:
            return list(uploaded)
        file_words = self._words_from_config_files("review_words_file")
        if file_words:
            return file_words
        words = []
        for part in re.split(r"[,，、;；\s]+", str(self._cfg("review_words") or "")):
            p = part.strip()
            if p and p not in words:
                words.append(p)
        return words

    # ------------------------------------------------------------------ #
    # 转述
    # ------------------------------------------------------------------ #

    async def _relay(self, event, key, private):
        session = self.sessions.get(key)
        if not session:
            return None, True
        user_key = self._user_key(event)
        # 永久禁用：按该用户名下所有匿名身份判定（换群拿新昵称也不放行）
        if self._is_user_banned(user_key):
            return None, True

        chain = self._get_message_chain(event)
        parts = [c for c in chain if isinstance(c, (Plain, Image))]
        text_raw = event.get_message_str().strip()

        # —— 内容审查：对「和谐之后」的文本再判一次 ——
        # 否则被和谐的脏话反而绕过了审查：原文命中就拦截、不和谐则照常转述，
        # 删除审查词会让用户看到 "*" 而管理员以为拦住了。
        if self._cfg_bool("review_enabled") and (
            self._review_hit(text_raw) or self._review_hit(self._censor(text_raw))
        ):
            return "⚠️ 该内容未通过审查（包含敏感词），未转述。", True

        images = [c for c in parts if isinstance(c, Image)]
        dropped_images = 0
        if images and not self._cfg_bool("allow_image_relay"):
            dropped_images = len(images)
            images = []
        elif images:
            cap = self._cfg_int("max_images") or 4
            if cap > 0 and len(images) > cap:
                dropped_images = len(images) - cap
                images = images[:cap]

        text = self._censor(text_raw)
        if not parts:
            # 语音/表情/视频等无法转述的消息：只提示一次，避免刷屏
            if session.get("warned_unsupported"):
                return None, True
            session["warned_unsupported"] = True
            session["last_active"] = time.time()
            await self._save_sessions()
            return "暂不支持转述这类消息（仅支持文字和图片），本会话内不再重复提示。", True
        if not text.strip() and not images:
            if dropped_images:
                return (
                    "⚠️ 图片转述已被管理员关闭，本条未转述。如已发送图片，请改用文字。", True
                )
            return "⚠️ 这条消息没有可转述的内容。", True

        if private:
            targets = await self._targets_for_private(event)
        else:
            targets = self._targets_for_group(self._get_group_id(event))
        targets = self._dedupe_targets(targets)
        if not targets:
            return "⚠️ 目标群聊未配置，无法转述，请联系管理员。", True

        # 禁言/永久禁用都按昵称记录，而昵称是分群的：
        # 逐个目标算出该群会用到的昵称，再按昵称判断能否转述。
        # 只要**任一**相关昵称被禁言就整体不转述（管理员禁言的是这个人）。
        #
        # 主身份（开启会话时告知用户的那个昵称）必须用在主目标上：
        # 否则「你的匿名身份是番茄」和群里实际显示的昵称会对不上，
        # 管理员按告知的昵称禁言也就管不到这个人。
        primary = str(session.get("nickname") or "")
        primary_scope = str(session.get("nickname_scope") or "")
        ready = []
        names = set()
        for gid in targets:
            scope = self._scope_of(gid, not private)
            # 主身份只在**它自己的身份域**里复用，保证「告知的昵称」与「群里显示的昵称」
            # 一致；其它群一律走各群自己的昵称，避免把主昵称带到已被别人占用的群里。
            if primary and scope == primary_scope:
                name = primary
            else:
                name = self._nickname_for(user_key, scope)
            names.add(name)
            ready.append((gid, name))
        names |= self._nicknames_of_user(user_key)

        mute_until = self._mute_until(names)
        if mute_until > time.time():
            if not session.get("muted_notified"):
                session["muted_notified"] = True
                await self._save_sessions()
                remain = int((mute_until - time.time()) / 60) + 1
                return f"🔇 你已被禁言，剩余约 {remain} 分钟。", True
            return None, True
        if session.get("muted_notified"):
            session["muted_notified"] = False
            await self._save_sessions()

        max_len = self._cfg_int("max_msg_len") or 500
        failed = 0
        for gid, name in ready:
            self.logger.info("匿名转述：%s → 目标群 %s", name, gid)
            for m in self._build_relay_messages(name, text, images, max_len):
                if not await self._send_group(event, gid, m):
                    failed += 1

        session["last_active"] = time.time()
        await self._save_sessions()

        if failed:
            return "⚠️ 转述失败，请稍后重试。", True
        if self._cfg_bool("ack_on_relay"):
            tip = ""
            if dropped_images:
                tip = f"\n（{dropped_images} 张图片未转述：管理员已关闭图片转述/超出数量上限）"
            return "已为你转述 ✅" + tip, True
        return None, True

    def _build_relay_messages(self, name, text, images, max_len):
        """按格式模板组装转述消息。格式为空时使用旧格式（前缀行 + 内容）。"""
        fmt = str(self._cfg("relay_format") or "").strip()
        time_str = time.strftime("%m-%d %H:%M") if self._cfg_bool("show_time") else ""

        def render(content):
            if fmt:
                # 模板里没有 {content} 就等于把内容整个吞掉——用户以为已经转述成功，
                # 群里却只看到一行【匿名】。此时自动把内容补在末尾。
                if "{content}" in fmt:
                    return (fmt.replace("{name}", name)
                               .replace("{content}", content)
                               .replace("{time}", time_str))
                head = (fmt.replace("{name}", name).replace("{time}", time_str)).strip()
                return f"{head} {content}".strip() if content else head
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
        """群聊会话转述目标：优先匹配映射规则，未匹配时使用统一目标（target_group_ids）。"""
        rules = self._parse_mapping_rules(str(self._cfg("group_target_rules") or ""))
        if group_id in rules:
            return rules[group_id]
        return self._target_groups()

    async def _targets_for_private(self, event):
        """私聊会话转述目标：① 用户映射规则 ② 自动识别用户所在群 ③ 统一目标（target_group_ids）。"""
        rules = self._parse_mapping_rules(str(self._cfg("user_target_rules") or ""))
        try:
            user_id = str(event.get_sender_id() or "")
        except Exception:
            user_id = ""
        if user_id and user_id in rules:
            return rules[user_id]
        if self._cfg_bool("auto_detect_groups") and user_id:
            try:
                detected = await self._detect_user_groups(event, user_id)
            except Exception:
                detected = []
            if detected:
                return detected
        return self._target_groups()

    @staticmethod
    def _parse_mapping_rules(raw):
        """解析映射规则。格式：源:目标1,目标2;源2:（目标留空=转述回源本身）。

        同时兼容 README/面板提示里写的「多个源共用一个目标」写法：
        ``成员A,成员B:群B号`` 会展开成两条规则。逗号既可分隔目标也可分隔源，
        仅在「冒号左边不是纯数字」时按多源处理——用户 ID 通常是数字，
        而群号/chat id 一定是数字，这样两种写法都不会被误判。
        """
        raw = str(raw or "").replace("：", ":")
        rules = {}
        for part in re.split(r"[;；\n]+", raw):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                src, _, tgt = part.partition(":")
                src = src.strip()
                tgt = tgt.strip()
                targets = [t.strip() for t in re.split(r"[,，、\s]+", tgt) if t.strip()] if tgt else None
                sources = AnonRelay._split_rule_sources(src)
                for s in sources:
                    body = targets if targets else [s]
                    if s and s not in rules:
                        rules[s] = body
            else:
                src = part.strip()
                if src and src not in rules:
                    rules[src] = [src]
        return rules

    @staticmethod
    def _split_rule_sources(src):
        """把规则左侧拆成多个源：``A,B:群`` 里 A、B 是两个源，``123:群`` 是一个。"""
        src = str(src or "").strip()
        if not src:
            return []
        if re.fullmatch(r"[+-]?\d+", src):
            return [src]
        parts = [p.strip() for p in re.split(r"[,，、\s]+", src) if p.strip()]
        return parts or [src]

    # ------------------------------------------------------------------ #
    # 自动识别用户所在群（OneBot/QQ 成员查询，带缓存）
    # ------------------------------------------------------------------ #

    async def _detect_user_groups(self, event, user_id):
        """查询倾诉者属于哪些候选群，返回群号列表；失败或平台不支持时返回空列表。"""
        try:
            if event.get_platform_name() != "aiocqhttp":
                return []
        except Exception:
            return []
        cache_key = f"{event.get_platform_id()}:{user_id}"
        now = time.time()
        hit = self._member_cache.get(cache_key)
        if hit and now - hit[0] < self._member_cache_ttl:
            return hit[1]
        groups = []
        try:
            platform = self.context.get_platform_inst(event.get_platform_id())
            client = platform.get_client()
            candidates = await self._detect_candidates(client)
            for gid in candidates:
                if await self._user_in_group(client, gid, user_id):
                    groups.append(str(gid))
        except Exception as e:
            self.logger.info("自动识别用户所在群失败，回退统一目标: %s", e)
            return []
        self._member_cache[cache_key] = (now, groups)
        return groups

    async def _detect_candidates(self, client):
        """候选群：优先用 detect_group_ids，留空则取机器人所在全部群。"""
        raw = str(self._cfg("detect_group_ids") or "")
        if raw.strip():
            return [g for g in re.split(r"[,，、;；\s]+", raw) if g.strip()]
        try:
            group_list = await client.get_group_list()
        except Exception:
            try:
                group_list = await client.call_action(action="get_group_list")
            except Exception:
                return []
        return [str(g.get("group_id")) for g in (group_list or [])]

    async def _user_in_group(self, client, gid, user_id):
        try:
            members = await client.get_group_member_list(group_id=int(gid))
        except Exception:
            try:
                members = await client.call_action(
                    action="get_group_member_list", group_id=int(gid), no_cache=False
                )
            except Exception:
                return False
        return any(str(m.get("user_id")) == str(user_id) for m in (members or []))

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
