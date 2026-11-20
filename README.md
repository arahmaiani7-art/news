# 每日新闻海报自动生成

每天自动抓热榜、按固定模板渲染成 375px 宽竖版 PNG，通过 GitHub Pages 提供**固定 URL 的图片直链**。

产出直链形如：

```
https://arahmaiani7-art.github.io/daily-news-poster/latest.png
```

`latest.png` 文件名恒定，每天内容更新，可直接喂给插件。

## 结构

```
src/
  lunar.py     农历与星期（香港天文台年历，不做推算）
  news.py      热榜抓取（百度实时榜）
  content.py   选题 + 压成 15 条 40 字以内文案
  render.py    模板填充 + Playwright 截图
  generate.py  主流程编排
template/poster.html   375px 海报模板（唯一视觉来源）
output/                产物目录，整个目录发布为 Pages 站点
tests/                 unittest
```

## 本地运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

python src/generate.py                  # 生成今天（按北京时间）
python src/generate.py --date 2026-11-20
python src/generate.py --dry-run        # 只取数选题，不截图
python -m unittest discover -s tests -v
```

macOS 本地跑需要中文字体（`PingFang SC` 自带即可）；CI 里显式装 `fonts-noto-cjk`，
否则 Chromium 把中文渲染成方框。

## 数据来源与口径

- **农历/星期**：香港天文台年历 `T{YYYY}c.txt`。农历**不推算**——公历月初常对上农历月末
  （2026-11-01 是九月廿三），按公历月份反推必错。接口为 `lunar.lookup_lunar()`。
- **新闻**：百度热搜实时榜。数据内联在 `<!--s-data:{...}-->` 注释里，正则取出即可，
  不需要无头浏览器。接口为 `news.fetch_candidates()`。
- **文案**：只取热榜原文，**不调用大模型、不改写标点、不拼接重排**。优先用描述首句
  （含数字与主体，信息密度最高），整句放不进 40 字就用编辑写好的标题。
  刻意不做"按子句裁剪"：把 `、` 换成 `，` 重排会改变原文语义，只留前几个子句会得到
  「11月19日上午」这类没有信息量的碎片。

热榜摘要不等于已核实事实。政策类保留了榜单原有表述；若要把海报对外正式发布，
需回查官方通稿。

## 失败策略

任一步失败都以非 0 退出，且**不覆盖已有的 `latest.png`**（`tests/` 里有对应测试）。
CI 中 `Generate poster` 失败即中断，上一次部署的 `latest.png` 继续在线服务。
外部插件因此不会拿到 404 或半张图。

## 部署

`.github/workflows/daily-news.yml`：

- 每天 UTC 23:00（北京时间 07:00）自动跑，也支持手动 `workflow_dispatch`
- `concurrency` 保证同时只有一个实例，避免两次部署互相覆盖
- 上传 `output/` 为 Pages 产物，`deploy-pages` 发布

首次需在仓库 **Settings → Pages → Source** 选择 **GitHub Actions**。

## 已知限制

- **Pages 有缓存**。`latest.png` 是固定文件名，CDN 可能返回旧图（通常几分钟到十几分钟）。
  插件若要求强实时，需要加 cache-busting（如 `latest.png?d=YYYYMMDD`）
  或前面套一层 Cloudflare Worker。
- **只有一个数据源**。百度热榜被限流或改版时当天会失败并保留旧图，
  不会生成残缺海报。要更高可用性可在 `news.fetch_candidates()` 里加第二源。
- **文案质量取决于榜单描述**。没有大模型润色，长描述会退回标题，短标题会显得信息量少。
  要更自然措辞可在 `content.compose_item()` 接入自己的模型，输出仍需过
  `MAX_CHARS`（40 字）与 `validate()` 校验。
