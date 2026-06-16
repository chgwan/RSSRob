# RSSRob

[English](README.md) | **中文**

**一个轻量、可配置的工具，可为任意网站生成 RSS 订阅源 —— 即使该网站本身并不提供 RSS。**

对于没有订阅源的网站，你只需把 RSSRob 指向某个页面，并（用 CSS 选择器或 XPath）告诉它条目及其字段所在的位置；对于已经发布 RSS/Atom 的网站，直接给出订阅源 URL 即可。无论哪种方式，RSSRob 都会按计划定时运行、构建符合规范的 RSS 订阅源，并通过 HTTP 提供访问。它会记住所有见过的条目，因此你的订阅源会不断累积历史记录，且永远不会重复展示同一条目。

---

## 功能特性

- **两种数据源类型** —— `html` 类型的站点用 **CSS 选择器或 XPath** 抓取；`rss` 类型的站点本身已发布订阅源，RSSRob 只需解析并重新提供（无需选择器）。
- **内置调度器** —— 单进程即可按各自的间隔抓取每个站点，无需外部 cron。
- **内置 HTTP 服务器** —— 在固定 URL 上提供每个订阅源，并附带一个简单的索引页。
- **去重 + 历史** —— 用 SQLite 记录所有出现过的条目。条目只入库一次（按 id 去重），订阅源保留最近 N 条的滚动窗口 —— 因此即使条目已从源页面滚走，历史依然保留。
- **符合规范的 RSS** —— 订阅源由 [`feedgen`](https://github.com/lkiesow/python-feedgen) 生成，而非手写 XML。
- **便于调试** —— `run-once` 抓取单个站点并打印提取结果，便于在正式提交前调好选择器。
- **轻量** —— 五个依赖，使用标准库的 HTTP 服务器与调度器，单进程运行。

---

## 工作原理

```
                         ┌──────────────────────────────────────┐
                         │             rssrob serve              │
                         │                                       │
   config.yaml ───────▶  │  ┌─────────────┐     ┌─────────────┐  │
                         │  │  scheduler  │     │ http.server │  │
                         │  │  (thread)   │     │ (main)      │  │
                         │  └──────┬──────┘     └──────┬──────┘  │
                         │         │ per-site          │ serves  │
                         │         ▼ interval          ▼         │
   site ────HTTP──▶ requests ─▶ extract (html, lxml) ─┐
                         │      parse   (rss,  feedparser) ─┴▶ store(SQLite) ─▶ feed.xml
                         │                                       (dedup)       (feedgen)
                         └──────────────────────────────────────┘
```

单个站点的一次抓取周期：

1. **抓取（Fetch）** —— `requests` 下载页面（或订阅源）。
2. **获取条目（Obtain items）** —— 按数据源 `type`：
   - `html`：`extract` 用 *item* 选择器取出各行，再用每行的 *field* 选择器取字段。
   - `rss`：`rss` 用 `feedparser` 解析已有的 RSS/Atom 订阅源。
   - 两者产出相同结构 → `[{id, title, link, summary, date}, …]`。
3. **入库 + 去重（Store + dedup）** —— `store` 插入 SQLite 中尚不存在的 `id` 条目并打上 `first_seen` 时间戳；已知条目跳过。
4. **生成（Generate）** —— `feed` 从 SQLite 读取该订阅源最近 N 条，写入 `feeds/<name>.xml`。

与此同时，HTTP 服务器独立于抓取周期，持续提供已有的 XML 文件。

---

## 环境要求

- Python 3.11+
- 依赖（5 个）：`requests`、`lxml`、`feedgen`、`pyyaml`、`feedparser`
  - `feedgen` 会间接引入 `python-dateutil` 和 `lxml`。

---

## 安装

```bash
git clone <your-repo-url> RSSRob
cd RSSRob
pip install -r requirements.txt
```

---

## 快速开始

1. 创建一个 `config.yaml`（见 [配置](#配置)）：

   ```yaml
   output_dir: ./feeds
   state_db: ./rssrob.db
   http:
     host: 127.0.0.1
     port: 8080
   defaults:
     interval: 1800     # 每次抓取的间隔秒数
     max_items: 50      # 每个订阅源保留的条目数

   sites:
     - name: example-blog
       url: https://example.com/blog
       title: "Example Blog"
       item: "css:div.post"
       fields:
         title: "css:h2 a"
         link: "css:h2 a@href"
         summary: "css:p.excerpt"
         date: "css:time@datetime"
   ```

2. 在**不写入任何订阅源**的前提下，针对实时页面测试你的选择器：

   ```bash
   rssrob run-once example-blog
   ```

   它会打印提取到的条目，便于你确认选择器是否正确。

3. 启动调度器 + 服务器：

   ```bash
   rssrob serve
   ```

4. 在你的 RSS 阅读器中订阅：

   ```
   http://127.0.0.1:8080/feeds/example-blog.xml
   ```

   或打开 `http://127.0.0.1:8080/` 查看所有订阅源的索引。

---

## 配置

RSSRob 默认从 **`configs/` 文件夹**加载配置 —— 其中每个 `*.yaml` 文件按文件名顺序读取并合并：全局设置（`output_dir`/`state_db`/`http`/`defaults`）按键合并，订阅源则从所有文件中汇总收集。把全局设置放在一个文件里（例如 `configs/00-settings.yaml`），并**每个订阅源一个文件**（顶层映射含 `name`，无需 `sites:` 包裹）：

```
configs/
├── 00-settings.yaml      # output_dir, state_db, http, defaults
├── ipp-notices.yaml      # 一个订阅源
├── python-insider.yaml
└── …
```

单个 `config.yaml` 文件同样可用。解析顺序：存在则用 `./configs/`，否则 `config.yaml`，再否则 `config.example.yaml`。可用 `--config <文件或目录>`（命令行）或 `RSSROB_CONFIG`（Web 应用）覆盖。

### 全局选项

| 键 | 默认值 | 说明 |
|-----|---------|-------------|
| `output_dir` | `./feeds` | 生成的 `<name>.xml` 文件写入并对外提供的目录。 |
| `state_db` | `./rssrob.db` | 存放条目历史 / 去重状态的 SQLite 文件。 |
| `http.host` | `127.0.0.1` | 服务器绑定的主机。用 `0.0.0.0` 可在局域网内暴露。 |
| `http.port` | `8080` | HTTP 服务器端口。 |
| `defaults.interval` | `3600` | 抓取间隔秒数（每个站点，可覆盖）。 |
| `defaults.max_items` | `50` | 每个订阅源保留的最大条目数（滚动窗口）。 |
| `defaults.timeout` | `20` | HTTP 抓取超时秒数。 |
| `defaults.user_agent` | `RSSRob/0.1` | 抓取页面时发送的 User-Agent。 |

### 单站点选项

| 键 | 是否必填 | 说明 |
|-----|----------|-------------|
| `name` | 是 | 唯一 id；用作订阅源文件名（`<name>.xml`）和命令行参数。 |
| `url` | 是 | 要抓取的页面（`html`）或要解析的订阅源 URL（`rss`）。 |
| `type` | 否 | `html`（默认）或 `rss`，决定数据源处理方式。 |
| `title` | html：是 / rss：否 | RSS 的 `<title>`。`rss` 时默认取源订阅源的标题。 |
| `description` | 否 | RSS 的 `<description>`。默认取标题（`rss` 时取源订阅源的描述）。 |
| `item` | 仅 html | 匹配页面上每个条目/行的选择器。`html` 必填，`rss` 忽略。 |
| `fields` | 仅 html | 字段名 → 选择器 的映射（见下）。`html` 必填，`rss` 忽略。 |
| `interval` | 否 | 覆盖本站点的 `defaults.interval`。 |
| `max_items` | 否 | 覆盖本站点的 `defaults.max_items`。 |
| `proxy` | 否 | 本订阅源专用代理；接受 `socks5://ip:port` 或 `http(s)://ip:port`。 |
| `article` | 否 | "深入"抓取的选择器（`title`/`content`）：跟进每个条目链接以取全标题 + 正文。 |

#### `rss` 数据源示例

对于已经发布订阅源的网站，无需任何选择器：

```yaml
sites:
  - name: python-insider
    type: rss
    url: https://blog.python.org/feeds/posts/default
    # title/description 可省略 —— 继承自源订阅源
    interval: 3600
    max_items: 30
```

RSSRob 会解析该订阅源，套用相同的去重/历史逻辑，并在 `/feeds/python-insider.xml` 重新提供。

### 字段（Fields）

`fields` 下的每一项都是一个**相对单个条目元素求值**的选择器：

- `title`（推荐）—— 条目标题。
- `link`（推荐）—— 条目 URL。会相对页面 URL 解析为绝对地址。
- `summary`（可选）—— 描述 / 摘要。
- `date`（可选）—— 发布日期。用 `dateutil` 尽力解析；若缺失或无法解析，则改用条目的 `first_seen` 时间。
- `id`（可选）—— 用于去重的稳定标识。**默认取 `link`。** 当链接含易变查询参数、或你更想用其他唯一字段时设置它。

### 选择器语法

选择器是一个字符串，可带可选的引擎前缀和可选的属性后缀：

```
[css:|xpath:] <selector> [@attribute]
```

- **引擎前缀** —— `css:` 表示 CSS 选择器，`xpath:` 表示 XPath。**无前缀即为 CSS。**
- **属性** —— **CSS** 用后缀 `@attr`（如 `css:h2 a@href`）；**XPath** 用原生属性轴（如 `xpath:.//a/@href`）—— XPath 不使用 `@attr` 后缀，因为它在谓词里已经用到了 `@`。不带属性时返回元素的文本内容。

示例：

| 选择器 | 含义 |
|----------|---------|
| `css:h2 a` | `<h2>` 内 `<a>` 的文本 |
| `css:h2 a@href` | 该链接的 `href` 属性 |
| `css:time@datetime` | `<time>` 标签的 `datetime` 属性 |
| `xpath:.//h2/a` | 第一个匹配链接的文本（XPath，相对条目） |
| `xpath:.//h2/a/@href` | 通过原生 XPath 属性轴取 `href` |

> CSS 与 XPath 可在不同站点之间、甚至同一站点的不同字段之间自由混用。哪种对某个页面更清晰就用哪种。

#### 通过标题文本定位某个区块

CSS 无法按文本匹配，因此当多个区块共用同一 class、只有标题不同时，用 XPath 锚定标题再走到列表。实例（某 IPP 首页的"通知公告"区块）：

```yaml
sites:
  - name: ipp-notices
    type: html
    url: http://www.ipp.cas.cn/
    title: "IPP 通知公告"
    item: "xpath://h2[normalize-space()='通知公告']/ancestor::div[contains(@class,'ipp2020-item')][1]//div[@class='bd']//ul/li"
    fields:
      title: "xpath:.//a"
      link:  "xpath:.//a/@href"
      date:  "xpath:.//span"
```

这样可精确选中那 6 条公告（共用同一 class 的同级区块被排除），并将每个相对链接解析为绝对 URL。

---

## 去重与历史模型

源页面通常只展示*当前*条目，但一个好的订阅源应当记住过去。RSSRob 的 SQLite 存储是订阅源的**后备存储**，而不仅是一个"已见集合"：

- 每次抓取提取页面上当前的条目。
- **新 `id`** 的条目带 `first_seen` 时间戳入库。
- 已在库中的条目被**跳过**（这就是去重）。
- 写出的 `.xml` 是该订阅源最近 `max_items` 条，按日期排序（无日期时按 `first_seen`）。

结果：即使条目已从源页面滚走，仍会持续出现在你的订阅源中，且永不重复。

---

## 命令行（CLI）

```bash
rssrob serve [--config config.yaml]
```
加载配置，启动后台调度器和 HTTP 服务器，并一直运行直到被中断（Ctrl-C）。这是常规模式。

```bash
rssrob run-once <site-name> [--config config.yaml]
```
**仅抓取一次**单个站点，把提取到的条目打印到终端后退出。用于在把站点加入常规计划前调试选择器。（默认不写入存储；加 `--write` 可同时持久化并重新生成该订阅源。）

---

## 项目结构

```
RSSRob/
├── README.md
├── requirements.txt          # 核心依赖
├── requirements-web.txt      # 预览 Web 应用的额外依赖（flask）
├── pyproject.toml            # pytest 配置（testpaths、import 路径）
├── config.example.yaml       # 单文件配置示例
├── configs/                  # 默认配置文件夹 —— 每个订阅源一个文件
│   ├── 00-settings.yaml      # 全局设置（output_dir, state_db, http, defaults）
│   └── <feed>.yaml           # 每个订阅源一个
├── rssrob/                   # 主包
│   ├── __init__.py
│   ├── __main__.py           # `python -m rssrob`
│   ├── cli.py                # argparse：serve / run-once
│   ├── config.py             # 加载 + 校验 YAML → dataclass（proxy、article 等）
│   ├── extract.py            # html：HTML + 选择器 → 条目（CSS/XPath、属性、绝对 URL）
│   ├── rss.py                # rss：解析已有 RSS/Atom 订阅源 → 条目（feedparser）
│   ├── article.py            # 跟进链接 → 全标题 + 正文（订阅源增强）
│   ├── store.py              # SQLite：插入/去重/取最近
│   ├── feed.py               # 条目 → 经 feedgen 生成 RSS XML
│   ├── scheduler.py          # 后台按站点间隔循环
│   └── server.py             # 标准库 http.server：/feeds/<name>.xml + 索引
├── web/                      # 预览 Web 应用
│   ├── webapp.py             # Flask：订阅源预览 + 选择器/过滤器试验台
│   └── templates/
├── tools/                    # 独立辅助脚本
│   ├── select_preview.py     # 一次性提取 → preview.html
│   └── request_url.py        # 把页面 HTML 下载到文件
├── samples/                  # 用于离线测试的已存页面（ipp_page.html 等）
├── tests/                    # pytest 测试套件 + 夹具 + conftest.py
└── docs/                     # 设计文档与计划
```

---

## 开发

```bash
pip install -r requirements.txt
pytest                 # 单元测试：extract、rss、article、store、feed、config 等
```

测试使用已保存的 HTML 夹具，因此提取在离线下即可验证（CSS + XPath + 属性 + 相对 URL 等情形），去重使用内存/临时 SQLite，并对生成的 RSS 做良构性检查。`pyproject.toml` 将仓库根目录加入 import 路径，并让 pytest 指向 `tests/`。

### 预览 Web 应用

一个浏览器工具，用于预览订阅源、并在写入配置前调好选择器/过滤器：

```bash
pip install -r requirements.txt -r requirements-web.txt
python web/webapp.py                       # 打开 http://127.0.0.1:5000/
python web/webapp.py --proxy-port 7890     # 为需要的订阅源设置默认代理
```

- `/` —— 订阅源预览（全标题 + 描述，会跟进文章链接）。
- `/playground` —— 实时的 **选择器与过滤器试验台**，支持 HTML *和* RSS 两种数据源；**保存（Save）** 会把测试好的站点（选择器、`filter`、`proxy`）以每订阅源一个文件写入 `configs/`（或在单文件模式下写入 `config.yaml`）。

---
