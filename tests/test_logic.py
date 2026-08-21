"""离线逻辑测试：在未安装 AstrBot 的环境下，用桩模块验证插件核心逻辑。

运行方式（在插件目录下）：
    python -X utf8 tests/test_logic.py
"""

import asyncio
import importlib.util
import logging
import os
import sys
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

    async def send_message(self, session_str, chain):
        if self.fail:
            return False
        self.sent.append((session_str, chain))
        return True


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
_module("astrbot.api.message_components", Plain=Plain, Image=Image)

# ---------------------------------------------------------------------- #
# 加载插件
# ---------------------------------------------------------------------- #

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(os.path.dirname(HERE), "main.py")
spec = importlib.util.spec_from_file_location("anon_relay_main", MAIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeEvent:
    def __init__(self, text, sender="u1", platform="qq", private=True, chain=None, group=""):
        self._text = text
        self._sender = sender
        self._platform = platform
        self._private = private
        self._chain = chain
        self._group = group
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

    def get_group_id(self):
        return self._group

    def plain_result(self, msg):
        return msg

    def stop_event(self):
        self.stopped = True

    def should_call_llm(self, value):
        self.llm_blocked = value is False


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
# 默认测试配置：昵称池留空，走编号兜底，保证断言确定
TARGET = {"target_group_ids": "123", "nicknames": ""}


async def main():
    # 1. 配置合并：默认 / dict / dataclass
    p0, _ = make_plugin(None)
    await check("默认配置合并", p0.config["target_group_ids"] == "" and p0._cfg("start_keywords") == "开启匿名模式"
                and p0._cfg("relay_format") == "【{name}】：{content}")

    p1, _ = make_plugin({"target_group_ids": "123", "max_msg_len": 10, "ack_on_relay": False})
    await check("dict 配置覆盖", p1._cfg("target_group_ids") == "123" and p1._cfg_int("max_msg_len") == 10 and p1._cfg_bool("ack_on_relay") is False)

    p2, _ = make_plugin(mod.AnonRelayConfig(target_group_ids="456"))
    await check("dataclass 配置", p2._cfg("target_group_ids") == "456" and p2._cfg("relay_prefix") == "【匿名倾诉】")

    # 2. 私聊未开启匿名模式：静默且拦截（stop_event + 阻止默认 LLM）
    p3, _ = make_plugin(None)
    ev = FakeEvent("随便聊聊")
    rs = await collect(p3.on_message(ev))
    await check("未开启时无回复", rs == [])
    await check("未开启时拦截事件", ev.stopped is True)
    await check("未开启时阻止默认LLM", ev.llm_blocked is True)

    # 3. silent_when_off=False 时不拦截
    p4, _ = make_plugin({"silent_when_off": False})
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
    await check("开启回复", len(rs) == 1 and "匿名身份" in rs[0])
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
    await check("群聊模式关闭", rs == [] and ev.stopped is False and not any(k.startswith("g:") for k in p24.sessions))

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    asyncio.run(main())
