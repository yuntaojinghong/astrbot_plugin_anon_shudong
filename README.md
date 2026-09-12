<div align="center">

<img src="docs/logo.png" width="128" height="128" alt="匿名树洞 logo">

# 匿名树洞

**astrbot_plugin_anon_relay** · 给群聊一个可以放心说话的地方

白名单外的人私聊机器人即自动进入匿名模式，倾诉内容以随机昵称重新拼装成一条新消息转述到群聊。
**不是转发聊天记录**——群成员只看得到一个水果昵称。

[![Release](https://img.shields.io/github/v/release/yuntaojinghong/astrbot_plugin_anon_relay?color=4B7BE5&label=release)](https://github.com/yuntaojinghong/astrbot_plugin_anon_relay/releases)
[![License](https://img.shields.io/badge/license-MIT-5FE3C8)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16%20%3C5-6A4BE0)](https://github.com/AstrBotDevs/AstrBot)
[![Tests](https://img.shields.io/badge/tests-101%20passed-3fb950)](tests/test_logic.py)
[![Python](https://img.shields.io/badge/python-3.10%2B-3572A5)](https://www.python.org/)

[功能](#功能) · [安装](#安装) · [快速开始](#快速开始) · [配置](#配置面板) · [命令](#管理员命令) · [隐私](#数据与隐私) · [FAQ](#常见问题)

</div>

---

## 这是什么

很多群里有心理咨询、树洞、吐槽一类的匿名投稿需求，但直接用「转发聊天记录」会把发言者的
头像、昵称、QQ 号一并带过去，匿名形同虚设。

本插件的做法是：**丢弃原始消息，用机器人身份重新发一条新消息**，内容形如

```
【番茄】：最近工作压力好大，感觉快撑不住了……
```

「番茄」是插件为这位倾诉者随机抽取并长期绑定的匿名昵称。群里没有任何字段指向真实用户。

## 功能

### 匿名转述

| | 功能 | 说明 |
| --- | --- | --- |
| 🗣️ | **私聊自动匿名** | 白名单外的人私聊机器人**直接进入匿名模式**，第一句话就开始转述，无需记任何命令 |
| 🔒 | **白名单自由聊天** | 白名单内的人是普通用户，与机器人正常对话（LLM 正常回复）；想匿名时发送「开启匿名模式」即可 |
| 💬 | **群聊模式** | 群内直接发送「开启匿名模式」，之后该群消息会被转述（默认转述回本群，可映射到别处） |
| 🤐 | **沉默拦截** | 未开启匿名模式的私聊默认被静默拦截，不回复也不触发 LLM；群聊未开启时完全不干预 |
| 🎲 | **随机昵称池** | 从水果昵称池随机抽取并长期绑定；池为空时回退为「匿名者-001」编号 |
| 🧩 | **格式模板** | 默认 `【{name}】：{content}`，支持 `{name}` `{content}` `{time}` 占位符，留空则用旧版多行格式 |
| 🖼️ | **图文与分段** | 支持文字与图片；超长文本按 `max_msg_len` 自动分段，每段都套用格式模板 |

### 转述目标

| | 功能 | 说明 |
| --- | --- | --- |
| 🗺️ | **用户映射规则** | `user_target_rules=用户ID:群A,群B` 精确指定某人的倾诉去向 |
| 🔍 | **自动识别所在群** | 私聊时自动查出倾诉者在哪些候选群里，A 群的人转到 A 群；同时在 A、B 群则两处都转 |
| 🧭 | **群聊映射规则** | `group_target_rules=源群:目标群`，群内开启时决定转述到哪 |
| 📮 | **统一兜底** | 以上都没命中时，落到 `target_group_ids`（多群逗号分隔） |

解析顺序：**用户映射规则 → 自动识别所在群 → 统一目标**。

### 治理与安全

| | 功能 | 说明 |
| --- | --- | --- |
| 🛡️ | **内容审查** | 命中 `review_words` 直接拦截不转述，并告知倾诉者；**每条消息独立判断**，不沿用上一条的状态 |
| 🧼 | **脏话和谐** | 命中 `bad_words` 自动替换为 `censor_mask`（默认 `**`）后照常转述 |
| 🚫 | **身份管控** | 管理员可「禁言 昵称 分钟」「永久禁用 昵称」，按匿名昵称生效，重启不丢 |
| 📄 | **词库 txt 上传** | 三个词库都能在设置页上传，或管理员在聊天中直接发 txt 文件；支持一键重置 |
| 💾 | **状态持久化** | 会话与名单存在插件 KV 存储中，重启不丢 |

## 安装

**方式一：下载 zip 上传（推荐）**

1. 下载 [最新安装包](https://github.com/yuntaojinghong/astrbot_plugin_anon_relay/releases/latest/download/astrbot_plugin_anon_relay.zip)
   （zip 文件名需保持为 `astrbot_plugin_anon_relay.zip`，AstrBot 以 zip 文件名作为插件目录名）
2. WebUI → 插件管理 → 安装插件 → 上传该 zip
3. 在插件管理中**重载插件**
4. 进入插件**配置面板**填写「目标群号」，保存后再次重载插件生效

**方式二：从 GitHub 仓库安装**

WebUI → 插件管理 → 安装插件 → 从 GitHub 仓库安装，填入
`https://github.com/yuntaojinghong/astrbot_plugin_anon_relay`（要求服务器能访问 GitHub）。

**方式三：手动复制文件夹**

1. 将 `astrbot_plugin_anon_relay` 整个文件夹放入 AstrBot 的插件目录：
   - 桌面版：`C:\Users\<用户名>\.astrbot\data\plugins\`
   - 源码 / systemd 部署：`<AstrBot 根目录>/data/plugins/`
   - Docker：容器内 `/AstrBot/data/plugins/`（或挂载卷放入宿主机对应目录）
2. 在 WebUI「插件管理」中**重载插件**。

> 环境要求：AstrBot `>= 4.16, < 5`，无需安装任何第三方依赖。

## 快速开始

**私聊（默认自动匿名）**

```
白名单外用户 → 机器人

💬 最近工作压力好大，感觉快撑不住了……
🤖 🔇 匿名模式已开启，你的匿名身份是「番茄」  📮 本次将转述到：816111082
💬 感觉快撑不住了……
🤖 已为你转述 ✅
（群里显示）【番茄】：感觉快撑不住了……
💬 关闭匿名模式
🤖 匿名模式已关闭。感谢你的信任，随时可以再来倾诉 🌱
```

**群聊模式**

```
在群 A 里发送：开启匿名模式
（机器人私聊悄悄话）🔇 匿名模式已开启，你的匿名身份是「草莓」
在群 A 里发送：我失恋了……
（群 A 里显示）【草莓】：我失恋了……
（私聊悄悄话）已为你转述 ✅
```

**映射规则速查**

| 需求 | 配置 |
| --- | --- |
| A 群的人私聊 → A 群（自动） | `auto_detect_groups=true`，`detect_group_ids=群A号`（或留空=全部群） |
| A 群的人私聊 → B 群（手动） | `user_target_rules=成员A,成员B:群B号` |
| 某个用户同时转述到 A、B 群 | `user_target_rules=用户ID:群A号,群B号` |
| 所有人统一转述到 A、B 群 | 关闭自动识别（或让其落空），`target_group_ids=群A号,群B号` |
| 群内开启：A 群转述回 A 群 | `group_target_rules=群A号:` |

## 配置面板

WebUI → 插件管理 → 「匿名树洞」→ 配置。表单由 `_conf_schema.json` 自动生成。

### 基础

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关，关闭后插件完全不响应 |
| `start_keywords` | `开启匿名模式` | 开启会话的关键词（私聊/群聊均可），多个用英文逗号分隔 |
| `stop_keywords` | `关闭匿名模式,结束倾诉` | 关闭会话的关键词 |
| `session_timeout_min` | `0` | 会话空闲超时（分钟），`0` 表示不超时 |
| `max_msg_len` | `500` | 单条转述最大字数，超长自动分段（每段套用格式模板） |

### 转述格式

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `relay_format` | `【{name}】：{content}` | **转述格式模板**。占位符：`{name}` 匿名昵称、`{content}` 倾诉内容、`{time}` 时间（MM-DD HH:MM）；留空则用旧格式 |
| `nicknames` | 水果昵称池 | **随机昵称池**，新用户开启时随机抽取（同一用户固定不变）；留空则用编号兜底 |
| `nicknames_file` | （空） | 昵称池 txt 文件（设置页上传） |
| `anon_name_prefix` | `匿名者` | 编号兜底昵称前缀（昵称池为空时生效） |
| `relay_prefix` | `【匿名倾诉】` | 旧格式前缀（`relay_format` 留空时生效） |
| `relay_suffix` | （空） | 旧格式后缀（`relay_format` 留空时生效） |
| `show_time` | `true` | 提供 `{time}` 占位符 / 旧格式附带时间 |

### 转述目标

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `target_group_ids` | （空） | **必填（兜底）**。统一转述目标，多群逗号分隔 |
| `user_target_rules` | （空） | **私聊用户映射规则**（优先）：`用户ID:目标群1,目标群2;用户ID2:目标群3` |
| `auto_detect_groups` | `true` | **自动识别倾诉者所在群**（私聊）。仅 OneBot/QQ（aiocqhttp）支持，其他平台自动回退统一目标 |
| `detect_group_ids` | （空） | 自动识别的**候选群**（逗号分隔）；留空 = 机器人所在的全部群 |
| `group_target_rules` | （空） | **群聊映射规则**：`源群号:目标群1,目标群2;源群号2:`（分号或换行分隔）。目标留空 = 转述回来源群 |

### 会话与权限

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `private_whitelist` | （空） | **私聊白名单**：逗号分隔的用户 ID。白名单内可与机器人正常对话，同时仍可开启匿名模式；支持 `平台:ID` 精确匹配（如 `qq:2226175932`） |
| `auto_anon_private` | `true` | **私聊自动开启匿名模式**（白名单外），白名单内不受影响 |
| `silent_when_off` | `true` | 私聊未开启匿名模式时保持沉默；设为 `false` 则放行给其他处理器/LLM |
| `enable_group_mode` | `true` | 允许群聊内开启匿名模式；关闭后群聊消息完全不干预 |
| `notify_group_on_start` | `false` | 有人开启匿名模式时在目标群内播报一条提示 |
| `ack_on_relay` | `true` | 转述成功后回执。群聊会话优先发私聊悄悄话，私聊不可达时改在群内提示 |

### 内容治理

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `admin_commands_enabled` | `true` | 启用管理命令（禁言/解禁/永久禁用/解除禁用） |
| `mute_default_min` | `30` | 「禁言 昵称」不带时长时的默认分钟数 |
| `censor_enabled` | `true` | 脏话自动和谐开关 |
| `bad_words` | 内置脏话列表 | 和谐词库（逗号分隔），命中即替换 |
| `bad_words_file` | （空） | 和谐词库 txt 文件（设置页上传） |
| `censor_mask` | `**` | 和谐替换符号 |
| `review_enabled` | `true` | 内容审查开关：命中敏感词直接拦截不转述 |
| `review_words` | （空） | **审查词库**（逗号分隔）：需要拦截的敏感词 |
| `review_words_file` | （空） | 审查词库 txt 文件（设置页上传） |

**和谐 vs 审查**：和谐 = 替换为 `**` 后照常转述；审查 = 整条消息直接拦截、不发送。
两者**每条消息独立判断**，命中就提示，不沿用上一条消息的状态。

## 管理员命令

管理员（AstrBot 设置中的管理员 ID）在私聊或群聊中发送：

| 命令 | 效果 |
| --- | --- |
| `禁言 昵称 [分钟]` | 禁言该匿名身份，省略时长则用 `mute_default_min`。禁言期间其消息不会转述 |
| `解禁 昵称` | 解除禁言 |
| `永久禁用 昵称` | 永久禁用该匿名身份，其无法再开启匿名模式 |
| `解除禁用 昵称` | 恢复该匿名身份的匿名模式使用权限 |
| `上传和谐词库` / `上传审查词库` / `上传昵称池` | 附带 txt 文件批量导入词库 |
| `重置词库` | 清除聊天上传的词库，回退到设置页文件/面板配置 |
| `重置和谐词库` / `重置审查词库` / `重置昵称池` | 单独重置某一类词库 |

示例：`禁言 番茄 60`（禁言「番茄」1 小时）、`永久禁用 草莓`。
禁言/禁用按**匿名昵称**生效，管理名单永久保存。

## 词库 txt 批量导入

**方式一：设置页上传（推荐）** — 在「和谐词库文件 / 审查词库文件 / 昵称池文件」字段上传 txt 并**保存配置**。

**方式二：聊天中发文件** — 管理员直接发送 txt 文件即可。

| 词库 | 可识别的文件名 | 文本命令 |
| --- | --- | --- |
| 和谐词库 `bad_words` | `bad_words.txt` / `badwords.txt` / `和谐词库.txt` | 「上传和谐词库」+ 附件 |
| 审查词库 `review_words` | `review_words.txt` / `reviewwords.txt` / `审查词库.txt` / `敏感词.txt` | 「上传审查词库」+ 附件 |
| 昵称池 `nicknames` | `nicknames.txt` / `昵称池.txt` | 「上传昵称池」+ 附件 |

**通用规则**

- **格式**：txt 文本，每行一个词；`#` 开头是注释会被忽略；一行内也可用逗号、顿号、分号分隔多个词。
- **编码**：支持 UTF-8（含 BOM）与 GBK/GB18030（Windows 记事本「另存为 → ANSI」）。
- **优先级**：聊天上传 > 设置页上传文件 > 配置面板文本词库（缺失时自动回退到下一级）。
- **权限与限制**：聊天上传仅管理员可用；单文件不超过 2MB、单次最多导入 10000 个词（超出部分忽略）。

## 数据与隐私

- 插件只保存**会话状态**（用户平台标识 → 匿名编号、时间）与**管理名单**，**不保存聊天内容**。
- 数据存放在 AstrBot 的插件 KV 存储中（桌面版位于 `C:\Users\<用户名>\.astrbot\`）。
- 群内永远只出现匿名昵称，不会出现真实昵称、QQ 号等身份信息。
- 转发链路是**重新拼装的新消息**，不携带原作者信息，群成员无法通过消息元数据反查。

> 提醒：匿名昵称是「对群成员匿名」，不是「对管理员匿名」。管理员仍可通过 AstrBot 日志查看原始消息，
> 这是刻意设计——用于处置违规内容，请在群规中向成员说明。

## 常见问题

**私聊发消息没有反应？**
默认设计如此（`silent_when_off = true`）。若想放行给 LLM，把 `silent_when_off` 设为 `false`；
若是白名单用户却仍被拦截，检查 `private_whitelist` 是否填了正确的用户 ID（必要时用 `平台:ID` 格式）。

**群号填什么格式？**
QQ / OneBot 填群号数字；Telegram 填群聊 chat id（可能为负数）；多群用英文逗号分隔，如 `123456789,987654321`。

**语音、表情、视频能转述吗？**
不能，仅支持文字和图片。遇到这类消息机器人会提示一次，同一会话内不再重复提示。

**转述失败怎么排查？**
检查：目标群号是否正确、机器人是否在该群里、插件是否已重载、AstrBot 日志中是否有
`转述到群 ... 失败` 或 `未找到平台 ...` 报错。

**自动识别所在群不生效？**
该功能依赖 OneBot/QQ（aiocqhttp）适配器的群成员查询能力，其他平台会自动回退到统一目标 `target_group_ids`。

**匿名昵称会重复吗？**
昵称/编号全局持久化且互不重复。同一用户多次开启昵称保持不变；如需重置，删除插件存储数据即可
（WebUI 插件管理 → 数据管理）。

**能让两个人拿到同一个昵称吗？**
不能，昵称从池中不重复抽取。池子耗尽后会回退到编号昵称。

**旧版本 AstrBot 能用吗？**
本插件基于 AstrBot v4 Star API（`>= 4.16, < 5`），v3 及早期 v4 不支持。

## 兼容性

| 项目 | 要求 |
| --- | --- |
| AstrBot | `>= 4.16, < 5` |
| Python | 3.10+ |
| 第三方依赖 | 无（仅使用 AstrBot 内置能力） |
| 平台 | QQ / Telegram / 微信 / 飞书 / Discord 等，通过 AstrBot 统一消息通道发送；自动识别所在群仅 OneBot 支持 |

## 开发

```bash
# 运行离线回归测试（用桩模块模拟 AstrBot API，无需安装 AstrBot）
python -X utf8 tests/test_logic.py

# 打包发布 zip
python scripts/build_release.py
```

- 修改 `main.py` 中 `AnonRelayConfig` 的默认值时，请同步更新 `_conf_schema.json` 的对应 `default`，否则面板显示与实际行为会不一致。
- 测试用例集中在 `tests/test_logic.py`，新增行为请同步补用例。

## 文件结构

| 文件 | 用途 |
| --- | --- |
| `main.py` | 插件主体：`AnonRelay(Star)` 类，实现私聊拦截、会话控制、匿名转述、长文分段、词库导入、KV 持久化 |
| `metadata.yaml` | 插件元数据：名称、展示名、简介、版本、作者、仓库、兼容版本范围、支持平台 |
| `_conf_schema.json` | 设置面板 schema：WebUI「插件配置」的表单由此生成 |
| `logo.png` | 插件图标（256×256，AstrBot 插件列表/市场卡片使用） |
| `README.md` | 本文档 |
| `CHANGELOG.md` | 版本更新记录 |
| `LICENSE` | MIT 许可证 |
| `.gitignore` | Git 忽略规则 |
| `tests/test_logic.py` | 离线逻辑测试（101 项） |
| `scripts/build_release.py` | 打包脚本，产出可分发的 zip |
| `docs/` | 项目主页（GitHub Pages），不参与打包 |
| `.github/workflows/` | CI 与自动发版流程 |

AstrBot 运行时只需要三个文件：`main.py`、`metadata.yaml`、`_conf_schema.json`（外加可选的 `logo.png`）。

## 版本记录

见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

[MIT](LICENSE)

---

<div align="center">

如果这个插件帮到了你，欢迎点个 ⭐ Star 支持一下。

</div>
