"""离线逻辑测试：在未安装 AstrBot 的环境下，用桩模块验证插件核心逻辑。

运行方式（在插件目录下）：
    python -X utf8 tests/test_logic.py
"""

import asyncio
import importlib.util
import logging
import os
import sys
import tempfile
import types

# ---------------------------------------------------------------------- #
# AstrBot 桩模块（模拟 v4 Star API 行为）
# ---------------------------------------------------------------------- #

KV_STORE: dict[str, dict] = {}


def _module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class Plain:
    type = "plain"

    def __init__(self, text=""):
        self.text = text


class Image:
    type = "image"

    def __init__(self, file=""):
        self.file = file


class File:
    type = "file"

    def __init__(self, name="", file="", url=""):
        self.name = name
        self.file_ = file
        self.url = url

    async def get_file(self, allow_return_url=False):
        if allow_return_url and self.url:
            return self.url
        return self.file_ or self.url


class _Filter:
    @staticmethod
    def regex(pattern):
        return lambda f: f


class MessageChain:
    def __init__(self, chain=None):
        self.chain = chain or []

    def __iter__(self):
        return iter(self.chain)

    def __getitem__(self, i):
        return self.chain[i]


class AstrMessageEvent:
    pass


class Context:
    def __init__(self):
        self.sent = []
        self.fail = False
        self.platform_clients = {}

    async def send_message(self, session_str, chain):
        if self.fail:
            return False
        self.sent.append((session_str, chain))
        return True

    def get_platform_inst(self, platform_id):
        client = self.platform_clients.get(platform_id)
        if client is None:
            return None
        return types.SimpleNamespace(get_client=lambda: client)


class Star:
    plugin_id = "anon_relay"

    def __init__(self, context):
        self.context = context
        self.logger = logging.getLogger("anon_relay_test")

    async def get_kv_data(self, key, default=None):
        return KV_STORE.get(self.plugin_id, {}).get(key, default)

    async def put_kv_data(self, key, value):
        KV_STORE.setdefault(self.plugin_id, {})[key] = value

    async def delete_kv_data(self, key):
        KV_STORE.get(self.plugin_id, {}).pop(key, None)


def register(name, author, desc, version):
    return lambda cls: cls


_module("astrbot")
_module("astrbot.api")
_module("astrbot.api.event", filter=_Filter(), AstrMessageEvent=AstrMessageEvent, MessageChain=MessageChain)
_module("astrbot.api.star", Context=Context, Star=Star, register=register)
_module("astrbot.api.message_components", Plain=Plain, Image=Image, File=File)
# 设置页上传的词库文件存放在插件数据目录（stub 用临时目录）
TEST_DATA_ROOT = tempfile.mkdtemp(prefix="astrbot_plugin_data_")
_module("astrbot.core")
_module("astrbot.core.utils")
_module("astrbot.core.utils.astrbot_path", get_astrbot_plugin_data_path=lambda: TEST_DATA_ROOT)

# ---------------------------------------------------------------------- #
# 加载插件
# ---------------------------------------------------------------------- #

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(os.path.dirname(HERE), "main.py")
spec = importlib.util.spec_from_file_location("anon_relay_main", MAIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeEvent:
    def __init__(self, text, sender="u1", platform="qq", private=True, chain=None, group="", role="member"):
        self._text = text
        self._sender = sender
        self._platform = platform
        self._private = private
        self._chain = chain
        self._group = group
        self._role = role
        self.stopped = False
        self.llm_blocked = False

    def get_message_str(self):
        return self._text

    def get_sender_id(self):
        return self._sender

    def get_platform_name(self):
        return self._platform

    def get_platform_id(self):
        return self._platform

    def get_messages(self):
        if self._chain is not None:
            return self._chain
        return [Plain(self._text)]

    def is_private_chat(self):
        return self._private

    def is_admin(self):
        return self._role == "admin"

    def get_group_id(self):
        return self._group

    def plain_result(self, msg):
        return msg

    def stop_event(self):
        self.stopped = True

    def should_call_llm(self, value):
        self.llm_blocked = value is False

    def track_temporary_local_file(self, path):
        pass


async def collect(gen):
    return [r async for r in gen]


def make_plugin(config=None):
    ctx = Context()
    p = mod.AnonRelay(ctx, config)
    return p, ctx


async def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED += 1
        print(f"FAIL  {name}  {extra}")


PASSED = 0
FAILED = 0
# 默认测试配置：昵称池留空（编号兜底），关闭自动匿名（保持原有静默/拦截语义）
TARGET = {"target_group_ids": "123", "nicknames": "", "auto_anon_private": False}


async def main():
    # 1. 配置合并：默认 / dict / dataclass
    p0, _ = make_plugin(None)
    await check("默认配置合并", p0.config["target_group_ids"] == "" and p0._cfg("start_keywords") == "开启匿名模式"
                and p0._cfg("relay_format") == "【{name}】：{content}")

    p1, _ = make_plugin({"target_group_ids": "123", "max_msg_len": 10, "ack_on_relay": False})
    await check("dict 配置覆盖", p1._cfg("target_group_ids") == "123" and p1._cfg_int("max_msg_len") == 10 and p1._cfg_bool("ack_on_relay") is False)

    p2, _ = make_plugin(mod.AnonRelayConfig(target_group_ids="456"))
    await check("dataclass 配置", p2._cfg("target_group_ids") == "456" and p2._cfg("relay_prefix") == "【匿名倾诉】")

    # 2. 私聊未开启匿名模式（关闭自动匿名）：静默且拦截（stop_event + 阻止默认 LLM）
    p3, _ = make_plugin({"auto_anon_private": False})
    ev = FakeEvent("随便聊聊")
    rs = await collect(p3.on_message(ev))
    await check("未开启时无回复", rs == [])
    await check("未开启时拦截事件", ev.stopped is True)
    await check("未开启时阻止默认LLM", ev.llm_blocked is True)

    # 3. silent_when_off=False 时不拦截
    p4, _ = make_plugin({"silent_when_off": False, "auto_anon_private": False})
    ev = FakeEvent("你好呀")
    rs = await collect(p4.on_message(ev))
    await check("silent=False 不拦截", rs == [] and ev.stopped is False and ev.llm_blocked is False)

    # 4. 群消息（未提供群号）不处理
    p5, _ = make_plugin(TARGET)
    ev = FakeEvent("开启匿名模式", private=False)
    rs = await collect(p5.on_message(ev))
    await check("无效群消息不处理", rs == [] and ev.stopped is False and "p:qq:u1" not in p5.sessions)

    # 5. 未配置目标群时无法开启
    p6, _ = make_plugin(None)
    ev = FakeEvent("开启匿名模式")
    rs = await collect(p6.on_message(ev))
    await check("无目标群提示", len(rs) == 1 and "目标群号" in rs[0] and "p:qq:u1" not in p6.sessions)

    # 6. 私聊正常开启会话（编号兜底昵称）
    p7, ctx7 = make_plugin(TARGET)
    ev = FakeEvent("我想开启匿名模式倾诉")
    rs = await collect(p7.on_message(ev))
    await check("开启回复", len(rs) == 1 and "匿名身份" in rs[0] and "将转述到" in rs[0] and "123" in rs[0])
    await check("会话建立", "p:qq:u1" in p7.sessions and p7.sessions["p:qq:u1"]["nickname"] == "匿名者-001")
    await check("重复开启提示", len(await collect(p7.on_message(FakeEvent("开启匿名模式")))) == 1)

    # 7. 转述：格式模板 + 回执 + 发送目标
    ev = FakeEvent("最近好累啊……")
    rs = await collect(p7.on_message(ev))
    await check("转述回执", len(rs) == 1 and "已为你转述" in rs[0])
    await check("转述目标", len(ctx7.sent) == 1 and ctx7.sent[0][0] == "qq:GroupMessage:123")
    chain = ctx7.sent[0][1]
    await check("转述链类型", isinstance(chain, MessageChain))
    await check("格式模板", getattr(chain[0], "text", "") == "【匿名者-001】：最近好累啊……")

    # 8. 图片转述
    p8, ctx8 = make_plugin(TARGET)
    await collect(p8.on_message(FakeEvent("开启匿名模式", sender="uImg")))
    await collect(p8.on_message(FakeEvent("看图", sender="uImg", chain=[Plain("看图"), Image("http://x/a.png")])))
    await check("图片转述", len(ctx8.sent) == 1 and any(isinstance(c, Image) for c in ctx8.sent[0][1]))

    # 9. 长文分段（max_msg_len=10，20 字 → 2 条，每段套用格式模板）
    p9, ctx9 = make_plugin({**TARGET, "max_msg_len": 10})
    await collect(p9.on_message(FakeEvent("开启匿名模式", sender="uLong")))
    long_text = "一二三四五六七八九十一二三四五六七八九十"
    await collect(p9.on_message(FakeEvent(long_text, sender="uLong")))
    await check("长文分段", len(ctx9.sent) == 2)
    await check("分段套用模板", "【匿名者-" in getattr(ctx9.sent[0][1][0], "text", "")
                and long_text[10:] in getattr(ctx9.sent[1][1][0], "text", ""))

    # 10. 关闭会话
    ev = FakeEvent("结束倾诉")
    rs = await collect(p7.on_message(ev))
    await check("关闭回复", len(rs) == 1 and "已关闭" in rs[0])
    await check("会话清除", "p:qq:u1" not in p7.sessions)
    await check("关闭后静默", len(await collect(p7.on_message(FakeEvent("再聊两句")))) == 0)

    # 11. 持久化：重启后会话恢复（KV 存储共享）
    p10, _ = make_plugin(TARGET)
    await collect(p10.on_message(FakeEvent("开启匿名模式", sender="uPersist")))
    p11, _ = make_plugin(TARGET)
    await p11._ensure_kv_loaded()
    await check("持久化恢复", "p:qq:uPersist" in p11.sessions and p11.sessions["p:qq:uPersist"]["nickname"].startswith("匿名者-"))

    # 12. 会话超时
    p12, _ = make_plugin({**TARGET, "session_timeout_min": 1})
    await collect(p12.on_message(FakeEvent("开启匿名模式", sender="uT")))
    p12.sessions["p:qq:uT"]["last_active"] = 0
    rs = await collect(p12.on_message(FakeEvent("还在吗", sender="uT")))
    await check("超时结束", len(rs) == 1 and "超时" in rs[0] and "p:qq:uT" not in p12.sessions)

    # 13. 多群转述
    p13, ctx13 = make_plugin({"target_group_ids": "111,222", "nicknames": ""})
    await collect(p13.on_message(FakeEvent("开启匿名模式", sender="uMulti")))
    await collect(p13.on_message(FakeEvent("hello", sender="uMulti")))
    await check("多群转述", len(ctx13.sent) == 2
                and {s[0] for s in ctx13.sent} == {"qq:GroupMessage:111", "qq:GroupMessage:222"})

    # 14. ack 关闭
    p14, ctx14 = make_plugin({**TARGET, "ack_on_relay": False})
    await collect(p14.on_message(FakeEvent("开启匿名模式", sender="uAck")))
    rs = await collect(p14.on_message(FakeEvent("内容", sender="uAck")))
    await check("关闭回执", rs == [] and len(ctx14.sent) == 1)

    # 15. notify_group_on_start
    p15, ctx15 = make_plugin({**TARGET, "notify_group_on_start": True})
    await collect(p15.on_message(FakeEvent("开启匿名模式", sender="uNotify")))
    await check("开启播报", len(ctx15.sent) == 1 and "已开启" in getattr(ctx15.sent[0][1][0], "text", ""))

    # 16. 发送失败提示
    p16, ctx16 = make_plugin(TARGET)
    ctx16.fail = True
    await collect(p16.on_message(FakeEvent("开启匿名模式", sender="uFail")))
    rs = await collect(p16.on_message(FakeEvent("内容", sender="uFail")))
    await check("发送失败提示", len(rs) == 1 and "转述失败" in rs[0])

    # 17. 不支持的消息类型只提示一次
    p17, _ = make_plugin(TARGET)
    await collect(p17.on_message(FakeEvent("开启匿名模式", sender="uVoice")))
    voice1 = FakeEvent("", sender="uVoice", chain=[types.SimpleNamespace(type="record")])
    voice2 = FakeEvent("", sender="uVoice", chain=[types.SimpleNamespace(type="record")])
    rs1 = await collect(p17.on_message(voice1))
    rs2 = await collect(p17.on_message(voice2))
    await check("不支持消息只提示一次", len(rs1) == 1 and "暂不支持" in rs1[0] and rs2 == [])

    # 18. 群聊模式：映射规则转述 + 悄悄话回执
    p18, ctx18 = make_plugin({"group_target_rules": "111:456,457", "nicknames": ""})
    ev = FakeEvent("开启匿名模式", sender="uG", private=False, group="111")
    rs = await collect(p18.on_message(ev))
    await check("群聊开启悄悄话", rs == [] and any(s[0] == "qq:FriendMessage:uG" for s in ctx18.sent))
    await check("群会话建立", "g:qq:uG:111" in p18.sessions)
    rs = await collect(p18.on_message(FakeEvent("你好呀", sender="uG", private=False, group="111")))
    await check("群聊回执悄悄话", rs == [] and any(s[0] == "qq:FriendMessage:uG" for s in ctx18.sent))
    targets = {s[0] for s in ctx18.sent if s[0].startswith("qq:GroupMessage:")}
    await check("规则映射转述", targets == {"qq:GroupMessage:456", "qq:GroupMessage:457"})

    # 19. 规则目标留空 = 转述回本群
    p19, ctx19 = make_plugin({"group_target_rules": "111:", "nicknames": ""})
    await collect(p19.on_message(FakeEvent("开启匿名模式", sender="uS", private=False, group="111")))
    await collect(p19.on_message(FakeEvent("内容", sender="uS", private=False, group="111")))
    await check("空目标转述回本群", any(s[0] == "qq:GroupMessage:111" for s in ctx19.sent))

    # 20. 群聊无匹配规则 → 统一目标
    p20, ctx20 = make_plugin(TARGET)
    await collect(p20.on_message(FakeEvent("开启匿名模式", sender="uU", private=False, group="999")))
    await collect(p20.on_message(FakeEvent("内容", sender="uU", private=False, group="999")))
    await check("群聊统一兜底", any(s[0] == "qq:GroupMessage:123" for s in ctx20.sent))

    # 21. 群聊未开启会话：完全放行，不影响正常聊天
    p21, _ = make_plugin(TARGET)
    ev = FakeEvent("随便聊聊", private=False, group="111")
    rs = await collect(p21.on_message(ev))
    await check("群聊未开启放行", rs == [] and ev.stopped is False and ev.llm_blocked is False)

    # 22. 随机昵称池 + 昵称稳定性
    p22, ctx22 = make_plugin({"nicknames": "番茄", "target_group_ids": "123"})
    await collect(p22.on_message(FakeEvent("开启匿名模式", sender="uNick")))
    await collect(p22.on_message(FakeEvent("你好", sender="uNick")))
    await check("昵称池生效", getattr(ctx22.sent[0][1][0], "text", "") == "【番茄】：你好")
    await collect(p22.on_message(FakeEvent("关闭匿名模式", sender="uNick")))
    await collect(p22.on_message(FakeEvent("开启匿名模式", sender="uNick")))
    await check("昵称跨会话稳定", p22.sessions["p:qq:uNick"]["nickname"] == "番茄")

    # 23. 旧格式：relay_format 留空时使用前缀行 + 内容
    p23, ctx23 = make_plugin({**TARGET, "relay_format": ""})
    await collect(p23.on_message(FakeEvent("开启匿名模式", sender="uOld")))
    await collect(p23.on_message(FakeEvent("你好", sender="uOld")))
    text23 = getattr(ctx23.sent[0][1][0], "text", "")
    await check("旧格式", text23.startswith("【匿名倾诉】") and "匿名者-" in text23 and "\n你好" in text23)

    # 24. 关闭群聊模式后群消息完全不处理
    p24, _ = make_plugin({"enable_group_mode": False})
    ev = FakeEvent("开启匿名模式", private=False, group="111")
    rs = await collect(p24.on_message(ev))
    await check("群聊模式关闭", rs == [] and ev.stopped is False and "g:qq:u1:111" not in p24.sessions)

    # 25. 私聊白名单：白名单用户可正常聊天，非白名单仍拦截
    p25, _ = make_plugin({**TARGET, "private_whitelist": "uWl"})
    ev = FakeEvent("在吗？", sender="uWl")
    rs = await collect(p25.on_message(ev))
    await check("白名单放行", rs == [] and ev.stopped is False and ev.llm_blocked is False)
    ev2 = FakeEvent("在吗？", sender="uOther")
    rs2 = await collect(p25.on_message(ev2))
    await check("非白名单仍拦截", rs2 == [] and ev2.stopped is True and ev2.llm_blocked is True)
    await collect(p25.on_message(FakeEvent("开启匿名模式", sender="uWl")))
    await check("白名单可开匿名", "p:qq:uWl" in p25.sessions)

    # 26. 白名单平台限定格式：qq:uP 只匹配 qq 平台的 uP
    p26, _ = make_plugin({**TARGET, "private_whitelist": "qq:uP"})
    ev3 = FakeEvent("hi", sender="uP")
    rs3 = await collect(p26.on_message(ev3))
    await check("平台限定白名单放行", rs3 == [] and ev3.stopped is False)
    ev4 = FakeEvent("hi", sender="uP", platform="tg")
    rs4 = await collect(p26.on_message(ev4))
    await check("其他平台不匹配", rs4 == [] and ev4.stopped is True)

    # 27. 私聊用户映射规则：uM1 私聊 → 456,457
    p27, ctx27 = make_plugin({"user_target_rules": "uM1:456,457", "nicknames": ""})
    await collect(p27.on_message(FakeEvent("开启匿名模式", sender="uM1")))
    await collect(p27.on_message(FakeEvent("内容", sender="uM1")))
    await check("用户映射规则", {s[0] for s in ctx27.sent if "GroupMessage" in s[0]}
                == {"qq:GroupMessage:456", "qq:GroupMessage:457"})

    # 28. 自动识别所在群：uG1 属于 111、222 → 同时转述；uNone 不在任何候选群 → 统一目标
    client28 = types.SimpleNamespace()
    members28 = {"111": ["uG1", "uX"], "222": ["uG1"], "333": ["uX"]}

    async def gml28(group_id, no_cache=None):
        return [{"user_id": u} for u in members28.get(str(group_id), [])]

    client28.get_group_member_list = gml28
    p28, ctx28 = make_plugin({**TARGET, "detect_group_ids": "111,222,333"})
    ctx28.platform_clients["aiocqhttp"] = client28
    await collect(p28.on_message(FakeEvent("开启匿名模式", sender="uG1", platform="aiocqhttp")))
    await collect(p28.on_message(FakeEvent("内容", sender="uG1", platform="aiocqhttp")))
    await check("自动识别所在群(同时转述)", {s[0] for s in ctx28.sent if "GroupMessage" in s[0]}
                == {"aiocqhttp:GroupMessage:111", "aiocqhttp:GroupMessage:222"})
    await collect(p28.on_message(FakeEvent("开启匿名模式", sender="uNone", platform="aiocqhttp")))
    await collect(p28.on_message(FakeEvent("内容", sender="uNone", platform="aiocqhttp")))
    await check("不在候选群回退统一", any(s[0] == "aiocqhttp:GroupMessage:123" for s in ctx28.sent))

    # 29. 关闭自动识别 → 统一目标
    p29, ctx29 = make_plugin({**TARGET, "auto_detect_groups": False})
    ctx29.platform_clients["aiocqhttp"] = client28
    await collect(p29.on_message(FakeEvent("开启匿名模式", sender="uG1", platform="aiocqhttp")))
    await collect(p29.on_message(FakeEvent("内容", sender="uG1", platform="aiocqhttp")))
    await check("关闭自动识别用统一目标", {s[0] for s in ctx29.sent if "GroupMessage" in s[0]}
                == {"aiocqhttp:GroupMessage:123"})

    # 30. 非 OneBot 平台（qq）自动识别跳过 → 统一目标
    p30, ctx30 = make_plugin({**TARGET, "detect_group_ids": "111"})
    ctx30.platform_clients["qq"] = client28
    await collect(p30.on_message(FakeEvent("开启匿名模式", sender="uG1")))
    await collect(p30.on_message(FakeEvent("内容", sender="uG1")))
    await check("非OneBot平台回退统一", {s[0] for s in ctx30.sent if "GroupMessage" in s[0]}
                == {"qq:GroupMessage:123"})

    # 31. 管理员禁言：禁言后不转述，只提示一次
    p31, ctx31 = make_plugin({"nicknames": "番茄", "target_group_ids": "123"})
    await collect(p31.on_message(FakeEvent("开启匿名模式", sender="uMute")))
    rs = await collect(p31.on_message(FakeEvent("禁言 番茄 10", sender="admin1", role="admin", private=False, group="111")))
    await check("禁言命令回复", len(rs) == 1 and "已禁言" in rs[0] and "番茄" in rs[0])
    rs1 = await collect(p31.on_message(FakeEvent("第一条", sender="uMute")))
    await check("禁言期间提示一次", len(rs1) == 1 and "禁言" in rs1[0])
    await check("禁言不转述", not any(s[0].startswith("qq:GroupMessage:") for s in ctx31.sent))
    rs2 = await collect(p31.on_message(FakeEvent("第二条", sender="uMute")))
    await check("禁言后续静默", rs2 == [])

    # 32. 解禁后恢复转述
    await collect(p31.on_message(FakeEvent("解禁 番茄", sender="admin1", role="admin", private=False, group="111")))
    await collect(p31.on_message(FakeEvent("恢复后的第一条消息", sender="uMute")))
    await check("解禁后恢复转述", any(s[0] == "qq:GroupMessage:123" for s in ctx31.sent))

    # 33. 永久禁用：无法再开启匿名模式
    p33, _ = make_plugin({"nicknames": "草莓", "target_group_ids": "123"})
    await collect(p33.on_message(FakeEvent("永久禁用 草莓", sender="admin1", role="admin", private=False, group="111")))
    rs = await collect(p33.on_message(FakeEvent("开启匿名模式", sender="uBan")))
    await check("永久禁用拒绝开启", len(rs) == 1 and "永久禁用" in rs[0] and "p:qq:uBan" not in p33.sessions)
    await collect(p33.on_message(FakeEvent("解除禁用 草莓", sender="admin1", role="admin", private=False, group="111")))
    rs = await collect(p33.on_message(FakeEvent("开启匿名模式", sender="uBan")))
    await check("解除禁用后恢复", len(rs) == 1 and "已开启" in rs[0] and "p:qq:uBan" in p33.sessions)

    # 34. 非管理员无权执行
    p34, _ = make_plugin({"nicknames": "橘子", "target_group_ids": "123"})
    rs = await collect(p34.on_message(FakeEvent("禁言 橘子 10", sender="uAny")))
    await check("非管理员无权限", len(rs) == 1 and "没有管理员权限" in rs[0] and "橘子" not in p34.muted)

    # 35. 脏话自动和谐
    p35, ctx35 = make_plugin({**TARGET, "bad_words": "脏话,混蛋"})
    await collect(p35.on_message(FakeEvent("开启匿名模式", sender="uC")))
    await collect(p35.on_message(FakeEvent("你在说脏话，混蛋", sender="uC")))
    sent_text35 = getattr(ctx35.sent[0][1][0], "text", "")
    await check("脏话和谐", "**" in sent_text35 and "脏话" not in sent_text35 and "混蛋" not in sent_text35)
    await check("正常文字保留", "你在说" in sent_text35)

    # 36. 关闭和谐开关后原样转述
    p36, ctx36 = make_plugin({**TARGET, "bad_words": "脏话", "censor_enabled": False})
    await collect(p36.on_message(FakeEvent("开启匿名模式", sender="uC2")))
    await collect(p36.on_message(FakeEvent("说脏话测试", sender="uC2")))
    await check("关闭和谐原样转述", "脏话" in getattr(ctx36.sent[0][1][0], "text", ""))

    # 37. 私聊自动开启匿名模式（默认开启）：白名单外用户直接进入
    p37, ctx37 = make_plugin({"target_group_ids": "123", "nicknames": ""})
    rs = await collect(p37.on_message(FakeEvent("在吗", sender="uAuto")))
    await check("自动开启会话", len(rs) == 1 and "匿名身份" in rs[0] and "p:qq:uAuto" in p37.sessions)
    await check("自动开启首条不转述", not any(s[0].startswith("qq:GroupMessage:") for s in ctx37.sent))
    await collect(p37.on_message(FakeEvent("最近好累", sender="uAuto")))
    await check("自动开启后转述", any(s[0] == "qq:GroupMessage:123" for s in ctx37.sent))
    await collect(p37.on_message(FakeEvent("关闭匿名模式", sender="uAuto")))
    await check("自动开启可关闭", "p:qq:uAuto" not in p37.sessions)
    await collect(p37.on_message(FakeEvent("又来了", sender="uAuto")))
    await check("关闭后再次自动开启", "p:qq:uAuto" in p37.sessions)

    # 38. 白名单用户不受自动开启影响：自由聊天 + 关键词可开启
    p38, _ = make_plugin({"target_group_ids": "123", "private_whitelist": "uFree"})
    ev = FakeEvent("在吗", sender="uFree")
    rs = await collect(p38.on_message(ev))
    await check("白名单自由聊天", rs == [] and ev.stopped is False and "p:qq:uFree" not in p38.sessions)
    await collect(p38.on_message(FakeEvent("开启匿名模式", sender="uFree")))
    await check("白名单关键词开启", "p:qq:uFree" in p38.sessions)

    # 39. 关闭自动开启：白名单外回到静默拦截
    p39, _ = make_plugin({**TARGET, "auto_anon_private": False})
    ev = FakeEvent("在吗", sender="uSilent")
    rs = await collect(p39.on_message(ev))
    await check("关闭自动开启恢复静默", rs == [] and ev.stopped is True and "p:qq:uSilent" not in p39.sessions)

    # 40. 内容审查：命中敏感词拦截不转述；每条消息独立判断，每次命中都提示
    p40, ctx40 = make_plugin({**TARGET, "review_words": "反动,极端"})
    await collect(p40.on_message(FakeEvent("开启匿名模式", sender="uR")))
    rs = await collect(p40.on_message(FakeEvent("这是反动言论", sender="uR")))
    await check("审查拦截", len(rs) == 1 and "未通过审查" in rs[0])
    await check("审查不转述", not any(s[0].startswith("qq:GroupMessage:") for s in ctx40.sent))
    rs2 = await collect(p40.on_message(FakeEvent("极端言论测试", sender="uR")))
    await check("审查再次命中仍提示", len(rs2) == 1 and "未通过审查" in rs2[0])
    await collect(p40.on_message(FakeEvent("正常倾诉内容", sender="uR")))
    await check("审查后正常内容转述", any(s[0] == "qq:GroupMessage:123" for s in ctx40.sent))

    # 41. 关闭审查开关后照常转述
    p41, ctx41 = make_plugin({**TARGET, "review_words": "反动", "review_enabled": False})
    await collect(p41.on_message(FakeEvent("开启匿名模式", sender="uR2")))
    await collect(p41.on_message(FakeEvent("反动测试", sender="uR2")))
    await check("关闭审查照常转述", any(s[0] == "qq:GroupMessage:123" for s in ctx41.sent))

    # 42. 管理员上传和谐词库 txt（文件名识别）：替换面板词库并即时生效
    p42, ctx42 = make_plugin({**TARGET, "bad_words": "脏话"})
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "bad_words.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("# 注释行\n混蛋\n草泥马\n")
        ev = FakeEvent("", sender="admin42", role="admin", chain=[File("bad_words.txt", file=fp)])
        rs = await collect(p42.on_message(ev))
        await check("上传词库回复", len(rs) == 1 and "和谐词库" in rs[0] and "2 个词" in rs[0])
        await check("上传词库替换生效", p42._bad_words() == ["混蛋", "草泥马"])
        await collect(p42.on_message(FakeEvent("开启匿名模式", sender="u42")))
        await collect(p42.on_message(FakeEvent("说脏话，混蛋", sender="u42")))
        t42 = getattr(ctx42.sent[0][1][0], "text", "")
        await check("上传词库和谐生效", "混蛋" not in t42 and "说脏话" in t42)

    # 43. 文本命令「上传审查词库」+ 任意文件名 → 审查拦截生效
    p43, ctx43 = make_plugin({**TARGET, "review_words": "反动"})
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "任意名字.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("极端\n暴力\n")
        rs = await collect(p43.on_message(FakeEvent("上传审查词库", sender="admin43", role="admin",
                                                    chain=[File("任意名字.txt", file=fp)])))
        await check("命令上传审查词库", len(rs) == 1 and "审查词库" in rs[0] and "2 个词" in rs[0])
        await collect(p43.on_message(FakeEvent("开启匿名模式", sender="u43")))
        rs = await collect(p43.on_message(FakeEvent("这是极端言论", sender="u43")))
        await check("上传审查词库生效", len(rs) == 1 and "未通过审查" in rs[0])

    # 44. 非管理员上传被拒
    p44, _ = make_plugin(TARGET)
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "bad_words.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("混蛋\n")
        rs = await collect(p44.on_message(FakeEvent("", sender="u44", chain=[File("bad_words.txt", file=fp)])))
        await check("非管理员上传被拒", len(rs) == 1 and "没有管理员权限" in rs[0])

    # 45. 中文文件名「和谐词库.txt」
    p45, _ = make_plugin(TARGET)
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "和谐词库.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("煞笔\n")
        rs = await collect(p45.on_message(FakeEvent("", sender="admin45", role="admin",
                                                    chain=[File("和谐词库.txt", file=fp)])))
        await check("中文文件名识别", len(rs) == 1 and "1 个词" in rs[0] and p45._bad_words() == ["煞笔"])

    # 46. GBK 编码 + 注释行 + 逗号分隔 + 去重
    p46, _ = make_plugin(TARGET)
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "review_words.txt")
        with open(fp, "w", encoding="gbk") as f:
            f.write("# 敏感词\n反动,极端\n反动\n\n暴力\n")
        rs = await collect(p46.on_message(FakeEvent("", sender="admin46", role="admin",
                                                    chain=[File("review_words.txt", file=fp)])))
        await check("GBK编码解析", len(rs) == 1 and "3 个词" in rs[0] and p46._review_words() == ["反动", "极端", "暴力"])

    # 47. 上传昵称池
    p47, _ = make_plugin({"target_group_ids": "123", "nicknames": "番茄"})
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "nicknames.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("小夜\n小满\n")
        await collect(p47.on_message(FakeEvent("", sender="admin47", role="admin",
                                               chain=[File("nicknames.txt", file=fp)])))
        await collect(p47.on_message(FakeEvent("开启匿名模式", sender="u47")))
        await check("上传昵称池生效", p47.sessions["p:qq:u47"]["nickname"] in ("小夜", "小满"))

    # 48. 重置词库：单独重置 / 全部重置 / 回退面板配置；非管理员无权
    p48, _ = make_plugin({**TARGET, "bad_words": "脏话", "review_words": "反动"})
    rs = await collect(p48.on_message(FakeEvent("重置词库", sender="uAny")))
    await check("非管理员重置被拒", len(rs) == 1 and "没有管理员权限" in rs[0])
    await collect(p48.on_message(FakeEvent("重置和谐词库", sender="admin48", role="admin")))
    await check("单独重置和谐词库", "bad_words" not in p48.uploaded_words and "review_words" in p48.uploaded_words)
    await collect(p48.on_message(FakeEvent("重置词库", sender="admin48", role="admin")))
    await check("重置全部词库", not p48.uploaded_words)
    await check("重置后回退面板配置", p48._bad_words() == ["脏话"] and p48._review_words() == ["反动"])

    # 49. 空文件 / 无有效词 → 提示
    p49, _ = make_plugin(TARGET)
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "bad_words.txt")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("# 只有注释\n\n")
        rs = await collect(p49.on_message(FakeEvent("", sender="admin49", role="admin",
                                                    chain=[File("bad_words.txt", file=fp)])))
        await check("空词库提示", len(rs) == 1 and "未从文件中解析出任何词" in rs[0])

    # 50. 文件名不匹配且无命令的文件消息：不消费，按正常消息流程处理（群内放行）
    p50, _ = make_plugin(TARGET)
    ev = FakeEvent("", sender="u50", private=False, group="111", chain=[File("report.pdf", file="x")])
    rs = await collect(p50.on_message(ev))
    await check("无关文件放行", rs == [] and ev.stopped is False)

    # 51. 文件获取失败（仅 URL 且不可下载）→ 提示
    p51, _ = make_plugin(TARGET)
    rs = await collect(p51.on_message(FakeEvent("", sender="admin51", role="admin",
                                                chain=[File("bad_words.txt", url="http://x/bad_words.txt")])))
    await check("文件获取失败提示", len(rs) == 1 and "无法获取上传的文件" in rs[0])

    # 52. 设置页上传的词库文件（type=file 配置）：解析生效并用于和谐
    file_dir52 = os.path.join(TEST_DATA_ROOT, "astrbot_plugin_anon_relay", "files", "bad_words_file")
    os.makedirs(file_dir52, exist_ok=True)
    file52 = os.path.join(file_dir52, "词库.txt")
    with open(file52, "w", encoding="utf-8") as f:
        f.write("# 注释\n混蛋\n草泥马\n")
    p52, ctx52 = make_plugin({**TARGET, "bad_words": "脏话", "bad_words_file": ["files/bad_words_file/词库.txt"]})
    await check("设置页文件词库生效", p52._bad_words() == ["混蛋", "草泥马"])
    await collect(p52.on_message(FakeEvent("开启匿名模式", sender="u52")))
    await collect(p52.on_message(FakeEvent("说脏话，混蛋", sender="u52")))
    t52 = getattr(ctx52.sent[0][1][0], "text", "")
    await check("设置页文件词库和谐生效", "混蛋" not in t52 and "说脏话" in t52)

    # 53. 优先级：聊天上传 > 设置页文件 > 文本字段；重置后回退设置页文件
    p53, _ = make_plugin({**TARGET, "bad_words": "文本词", "bad_words_file": ["files/bad_words_file/词库.txt"]})
    await check("设置页文件优先于文本字段", p53._bad_words() == ["混蛋", "草泥马"])
    with tempfile.TemporaryDirectory() as td:
        fp53 = os.path.join(td, "bad_words.txt")
        with open(fp53, "w", encoding="utf-8") as f:
            f.write("聊天上传词\n")
        await collect(p53.on_message(FakeEvent("", sender="admin53", role="admin", chain=[File("bad_words.txt", file=fp53)])))
        await check("聊天上传优先于设置页文件", p53._bad_words() == ["聊天上传词"])
        await collect(p53.on_message(FakeEvent("重置词库", sender="admin53", role="admin")))
        await check("重置后回退设置页文件", p53._bad_words() == ["混蛋", "草泥马"])

    # 54. 设置页文件缺失 → 回退文本字段
    p54, _ = make_plugin({**TARGET, "bad_words": "兜底词", "bad_words_file": ["files/bad_words_file/不存在.txt"]})
    await check("设置页文件缺失回退", p54._bad_words() == ["兜底词"])

    # 55. file 配置项默认值为空列表
    p55, _ = make_plugin(None)
    await check("file 配置默认值", p55._cfg("bad_words_file") == []
                and p55._cfg("review_words_file") == [] and p55._cfg("nicknames_file") == [])

    # 56. 审查状态不跨会话/不跨消息：新一轮倾诉命中审查仍提示，正常内容可转述
    p56, ctx56 = make_plugin({**TARGET, "review_words": "极端"})
    await collect(p56.on_message(FakeEvent("开启匿名模式", sender="uN")))
    rs = await collect(p56.on_message(FakeEvent("极端内容", sender="uN")))
    await check("首轮命中审查提示", len(rs) == 1 and "未通过审查" in rs[0])
    await collect(p56.on_message(FakeEvent("关闭匿名模式", sender="uN")))
    await collect(p56.on_message(FakeEvent("开启匿名模式", sender="uN")))
    rs = await collect(p56.on_message(FakeEvent("还是极端内容", sender="uN")))
    await check("新一轮命中审查仍提示", len(rs) == 1 and "未通过审查" in rs[0])
    await collect(p56.on_message(FakeEvent("正常内容", sender="uN")))
    await check("新一轮正常内容可转述", any(s[0] == "qq:GroupMessage:123" for s in ctx56.sent))
    rs = await collect(p56.on_message(FakeEvent("又一条极端", sender="uN")))
    await check("同轮再次命中仍提示", len(rs) == 1 and "未通过审查" in rs[0])

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    asyncio.run(main())
