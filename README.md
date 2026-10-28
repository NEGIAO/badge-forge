# badge-forge

**SVG 渲染服务** —— 三列徽章（图标 / 文字 / 数据）与 GitHub 贡献活动图，全部通过 URL 查询参数配置，
无需数据库、无需构建步骤。

> **在线实例**：<https://vpn.negiao.cn/badge/>
> 打开即配置页，填完表单直接复制链接。

```html
<img src="https://vpn.negiao.cn/badge/svg?icon=https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/github.svg&text=Stars&data=https://api.github.com/repos/NEGIAO/WebGIS-Dev&query=$.stargazers_count&rbg=blue" />
```

实际渲染：

![Stars](https://vpn.negiao.cn/badge/svg?icon=https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/github.svg&text=Stars&data=https://api.github.com/repos/NEGIAO/WebGIS-Dev&query=$.stargazers_count&rbg=blue)

> 左列图标、中列文字、右列数值。数值由**服务端**从任意 JSON/文本接口取回，因此不受浏览器跨域限制；
> 也正因如此，本服务内置了 SSRF 防护（见[安全说明](#安全说明)）。

---

## 为什么又造一个徽章服务

现成的方案各有取舍：

| 方案 | 不足 |
|---|---|
| shields.io `dynamic/json` | 内层 URL 需二次编码，改一次很痛苦；上游限流/故障时 README 直接破图；无法控制图标列 |
| [visitor-badge](https://github.com/hehuapei/visitor-badge) | 是**计数器**（按 `page_id` 自增存储），不能渲染外部接口的数据 |
| [badgen](https://github.com/badgen/badgen.net) | 需自建 Node 服务；三列布局要自己拼 |

badge-forge 的定位：**一个文件就能部署的渲染器**。它不存任何业务数据，只做「取数 → 取值 → 拼 SVG」，
因此可以随便 fork、改配色、加字段。

---

## 快速开始

### 本地运行

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 9010 --reload
```

打开 `http://127.0.0.1:9010` 即是配置页：填表 → 实时预览 → 复制链接。

> 只依赖三个纯 Python 包（fastapi / uvicorn / httpx），**不需要 Docker、不需要构建步骤**。
> 常驻内存约 33MB —— 这是刻意为小内存机器（1GB 级别）准备的：本项目最初的部署环境只有 841MB。

---

## URL 参数

`GET /badge`，全部参数可选（`data` 为空时右列显示 `n/a`）。

| 参数 | 说明 | 默认 |
|---|---|---|
| `icon` | 左侧图标 URL（http/https）。服务端抓取后**内联为 data URI**，避免图床防盗链导致缺角 | — |
| `text` | 中间文字，最长 40 字符；留空则该列不渲染 | — |
| `data` | 右侧数值来源 URL，支持 JSON 与纯文本 | — |
| `query` | 简化 JSONPath，如 `$.metrics.todayUsers` | 自动取 `message` / `value` / `total` / `count` / `data` |
| `format` | `comma`（79,782）/ `plain`（79782）/ `compact`（7.98万）/ `time`（10-28 12:04） | `comma` |
| `ibg` | 图标列底色 | `#555555` |
| `lbg` | 文字列底色 | `#555555` |
| `rbg` | 数值列底色 | `#4caf50` |
| `h` | 高度 16~32 px，字号按比例缩放 | `20` |
| `r` | 圆角 0~h/2 | `3` |
| `gloss` | `0` 关闭顶部高光 | 开启 |

颜色接受**命名色**（`green` `blue` `grey` `red` `orange` `yellow` `purple` `pink` `teal` `lightgrey` `black` `white`）
或 `#rgb` / `#rrggbb`。非法值一律回落默认色 —— 徽章内容来自 URL，是典型注入面。

### 取值语法

只支持「对象键 + 数组下标」，不做表达式求值：

```text
$.metrics.todayUsers      嵌套对象
metrics.todayUsers        省略 $ 亦可
$.list[0].name            数组下标（支持负数：[-1] 取末项）
$                         取整个响应（仅纯文本源有意义）
```

取不到时右列显示 `n/a`，HTTP 仍返回 **200** —— README 里不会出现破图；该响应带 `no-store`，下次请求立即重试。

### 取值诊断 `GET /api/explain`

配置页靠它回答「到底哪一步错了」。参数与 `/badge` 一致（`data` / `query` / `format`）：

```json
{"ok": true,  "stage": "ok",      "raw": 16, "type": "int", "value": "16", "ms": 1165}
{"ok": false, "stage": "extract", "reason": "响应里没有字段「nope」"}
{"ok": false, "stage": "guard",   "reason": "目标地址指向内网或保留地址，已拒绝"}
{"ok": false, "stage": "fetch",   "reason": "上游返回 HTTP 404"}
```

`stage` 取值：`guard`（安全校验）/ `fetch`（网络与状态码）/ `parse`（JSON 解析）/ `extract`（路径取值）。
**返回的 `reason` 已脱敏** —— 内网 IP 只进服务端日志，不会经这个接口泄露。

---

## 配置页

`GET /` 提供可视化配置：

- 表单改任意一项，预览即时刷新（防抖 220ms），**过期响应自动丢弃**（快速输入不会出现旧结果盖新结果）；
- 自动生成 **图片 URL / Markdown / HTML** 三种片段，一键复制；
- 内置预设（51.la 今日访客、GitHub Stars、更新时间、纯图标）；首次打开自动套用第一个，不会给你一个空的 `n/a`；
- 只输出与默认值不同的参数，链接不会又臭又长；
- **表单状态存 `localStorage`**，刷新/关标签页都不丢；右上角「重置」一键回到默认预设；
- **1× / 2× 预览缩放** —— 徽章本体只有 20px 高，2× 才看得清圆角与高光；
- 取数失败时自动请求 `/api/explain`，把「缺字段 / 404 / 超时 / 内网拦截」直接写在预览下方；
- **顶部 tab 切换「徽章 / 活动图」**，两套表单各自持久化；`?tab=graph` 可直接深链到活动图；
- **浅色 / 深色双主题**，右上角一键切换，选择存 `localStorage`，首帧前应用（无闪白）；
- 无外部 CDN 依赖，断网也能用。

> 资源引用带 `?v=` 版本串：部署环境若在 CDN 后面（Cloudflare 的静态资源规则会按扩展名缓存 7 天），
> 改 `style.css` / `app.js` 后必须同步改 `index.html` 里的版本号，否则读者看不到新版。

---

## 活动图（GitHub 贡献日历）

移植自 `negiao-api` Worker 的同名端点，接口保持兼容，只换域名即可：

```
https://vpn.negiao.cn/badge/activity-graph?username=NEGIAO&days=31
（等价别名：/badge/api/activity-graph.svg）
```

| 参数 | 说明 | 默认 |
|---|---|---|
| `username` | GitHub 登录名（公开数据，无需对方授权） | 必填 |
| `days` | 7~90 | `31` |
| `custom_title` | 自定义标题，最长 60 字符 | `{name}'s Contribution Graph` |
| `hide_title` | `1` / `true` 隐藏标题 | 关闭 |
| `color` | 主色（标题 / 折线 / 轴 / 面积） | `70a5fd`（tokyo-night） |
| `bg` | 背景色 | `1a1b27` |
| `point` | 数据点颜色 | `a9b1d6` |
| `width` | 画布宽 800~1600，高固定 420 | `1200` |

### 必须配一个 GitHub Token

GitHub 的 **GraphQL 接口对未认证请求直接返回 403**（REST 那套「匿名 60 次/小时」不适用于 GraphQL），
所以活动图必须配一个 PAT。**徽章功能不受影响** —— 没配时活动图会渲染一张
「未配置 GITHUB_TOKEN」的说明图，而不是 500 或破图。

**申请步骤（权限最小化）**：

1. 打开 <https://github.com/settings/tokens> → **Generate new token (classic)**
2. Note 随意（如 `badge-forge`），Expiration 建议 90 天
3. **Scopes 一个都不要勾** —— 查询公开用户的贡献日历不需要任何权限
4. 生成后复制 `ghp_...` 开头的 token

> 也可用 fine-grained token，但只勾 **Public Repositories (read-only)**。

**放置位置（不要写进仓库）**：

```bash
sudo tee /etc/badge-forge.env >/dev/null <<'EOF'
GITHUB_TOKEN=ghp_你的token
EOF
sudo chmod 600 /etc/badge-forge.env
```

systemd 单元用 `EnvironmentFile=-/etc/badge-forge.env` 引入。路径前的 `-` 表示文件不存在也不报错，
这样没配 token 时服务照常启动，只是活动图不可用。

**配额**：5000 点/小时，一次查询约 1 点。服务端按「用户名 + 天数」缓存 6 小时，正常使用碰不到上限。

---

## 部署到 VPS

**不用 Docker。** 这是个单进程 Python 服务，容器化只会多一层常驻开销（dockerd + 镜像层），
在 1GB 内存的机器上是纯负担。推荐 **venv + systemd**：常驻约 33MB，与 nginx 和其他服务共存毫无压力。

### 1. 代码与虚拟环境

```bash
sudo mkdir -p /srv/badge-forge && sudo chown "$USER" /srv/badge-forge
git clone https://github.com/NEGIAO/badge-forge.git /srv/badge-forge
python3 -m venv "$HOME/venvs/badge-forge"
"$HOME/venvs/badge-forge/bin/pip" install --no-cache-dir -r /srv/badge-forge/requirements.txt
```

### 2. systemd 单元

写入 `/etc/systemd/system/badge-forge.service`：

```ini
[Unit]
Description=badge-forge (three-column SVG badge generator)
After=network.target

[Service]
Type=simple
User=nobody
WorkingDirectory=/srv/badge-forge
Environment=BADGE_PUBLIC_BASE_URL=https://example.com/badge
# 活动图用的只读 PAT 放这里（600），文件不存在也不影响启动
EnvironmentFile=-/etc/badge-forge.env
ExecStart=/home/YOUR_USER/venvs/badge-forge/bin/uvicorn app:app --host 127.0.0.1 --port 9010 --workers 1 --proxy-headers
Restart=on-failure
RestartSec=3
MemoryMax=200M
MemoryHigh=160M
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now badge-forge
curl -s http://127.0.0.1:9010/health
```

### 3. nginx 反代（可挂在任意子路径）

```nginx
location = /badge { return 301 /badge/; }
location /badge/ {
    proxy_pass http://127.0.0.1:9010/;   # 末尾斜杠是关键
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 20s;
}
```

`proxy_pass` 末尾的斜杠会剥掉 `/badge/` 前缀，应用因此看到的是 `/`、`/svg`、`/style.css` ——
所以同一份代码既能独立跑在根路径，也能挂到已有站点的子路径下（本项目的线上实例就是后者）。

> **子路径下 Swagger 会 404**：FastAPI 默认的 `/docs` 去取**根路径** `/openapi.json`，
> 挂在 `/badge/` 下时请求落到站点根，必然 404。本项目已把 schema 收进 `/api/openapi.json`、
> 文档页改在 `/api/docs` 并用**相对地址**引用 schema，两种部署都不用额外配置。

若要独立域名 + 缓存，参考 [`deploy/nginx.conf`](deploy/nginx.conf)（含 `proxy_cache`、HTTP→HTTPS 跳转）：

```bash
certbot --nginx -d badge.example.com
```

> **若服务器位于中国大陆**：未备案域名指向 443 会在 SNI 层被拦截，需改用非标端口（如 8443）。

---

## 安全说明

徽章的 `data` / `icon` 由**服务端**发起请求，因此本服务天然是一个「可控的取数代理」。默认防护：

1. **只允许 http/https**，其余协议（`file://`、`gopher://`…）直接拒绝；
2. **拒绝内网地址**：私有段、回环、链路本地（含 `169.254.169.254` 云元数据）、组播、保留段；
   解析出的**全部** A/AAAA 记录逐一校验 —— 只查第一条会被「一条公网 + 一条内网」绕过；
3. **逐跳复检重定向**：关闭 httpx 自动跳转，自己跟 3 跳并每跳重新校验，防止「首跳合法、次跳指向内网」；
4. **超时与体积上限**：默认 5s / 256KB（图标 48KB）；
5. **图标类型白名单**：只接受 SVG/PNG/JPEG/GIF/WEBP，按魔数嗅探，防止把 HTML 当图片内联；
6. **错误不外泄**：取数失败只返回 `n/a` + `X-Badge-Status: error`，具体原因仅进服务端日志
   （需要排查时设 `BADGE_DEBUG=true`，此时原因会写入 `X-Badge-Error`）。

**残余风险**：「解析 → 校验 → 连接」之间存在 DNS rebinding 时间窗。彻底关闭需在 TLS 层把 SNI 与连接目标解耦，
复杂度远超收益。对外开放的实例建议用 `BADGE_ALLOWED_HOSTS` 收敛到白名单：

```bash
# systemd 单元里加一行，或直接 export
export BADGE_ALLOWED_HOSTS="api.negiao.cn,api.github.com"
```

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `9010` | 监听端口 |
| `BADGE_PUBLIC_BASE_URL` | 空 | 对外基址，配置页据此生成绝对链接 |
| `BADGE_ALLOWED_HOSTS` | 空（不限） | 逗号分隔的主机白名单 |
| `BADGE_DATA_TIMEOUT` | `5` | 上游超时（秒） |
| `BADGE_DATA_MAX_BYTES` | `262144` | 上游响应上限 |
| `BADGE_MAX_REDIRECTS` | `3` | 最大重定向跳数 |
| `BADGE_ICON_MAX_BYTES` | `49152` | 图标体积上限 |
| `BADGE_DATA_CACHE_TTL` | `300` | 取数缓存（秒） |
| `BADGE_SVG_CACHE_TTL` | `300` | 响应缓存（秒） |
| `BADGE_CACHE_MAXSIZE` | `2048` | 进程内缓存条目上限 |
| `BADGE_DEBUG` | `false` | 是否把错误原因写进响应头 |
| `GITHUB_TOKEN` | 空 | 只读 PAT，供活动图取数；留空则活动图不可用（徽章不受影响） |
| `BADGE_GITHUB_TIMEOUT` | `8` | GitHub 请求超时（秒） |
| `BADGE_CHART_DATA_TTL` | `21600` | 贡献数据缓存（秒） |
| `BADGE_CHART_SVG_TTL` | `21600` | 活动图 SVG 缓存（秒） |

---

## 项目结构

```text
badge-forge/
├── app.py                # FastAPI：/badge、/svg、/api/config、/api/explain、/api/docs、/health、静态页
├── config.py             # 全部配置走环境变量，默认值即开箱可用
├── core/
│   ├── render.py         # 三列徽章渲染（零依赖，宽度估算 + 转义 + 配色白名单）
│   ├── chart.py          # 活动图渲染（移植自 Worker 的 Chartist 布局）
│   ├── github.py         # GitHub GraphQL 取贡献日历（只读 PAT）
│   ├── source.py         # SSRF 防护、逐跳重定向复检、JSON 取值、格式化、图片嗅探与白化
│   └── cache.py          # 进程内 TTL + LRU
├── web/                  # 配置页（原生 HTML/CSS/JS，无 CDN）
├── tests/                # unittest，61 个用例
└── deploy/nginx.conf     # 独立域名部署的反代 + 缓存示例
```

> **部署形态**：`app.py` + `config.py` + `core/` + `web/` + `requirements.txt` 就是全部运行必需文件，
> 其余（tests、README、deploy）都不需要上传到服务器。

---

## 开发与测试

```bash
python -m unittest discover -s tests -t .   # 61 passed
```

单测覆盖：宽度估算、XML 转义、配色白名单、高度缩放、图标白化、异常阶段与脱敏、JSON 路径（含负数下标）、
活动图几何与配色、
四种格式化、SSRF 拒绝路径（回环/私有段/元数据端点/协议/白名单）、图片魔数嗅探。

---

## License

[MIT](LICENSE) · 数据由上游接口提供，本服务不做任何存储。
