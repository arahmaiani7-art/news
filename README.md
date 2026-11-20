# 每日新闻海报自动生成

每天自动拉取「60 秒读懂世界」当日 15 条新闻，按固定模板渲染成 375px 宽竖版 PNG，
通过 GitHub Pages 提供**固定 URL 的图片直链**。

产出直链：

```
https://arahmaiani7-art.github.io/news/latest.png
```

`latest.png` 文件名恒定，每天内容更新，可直接喂给插件。

## 结构

```
src/
  lunar.py     农历与星期（香港天文台年历，不做推算）
  news.py      新闻抓取（60 秒读懂世界 API + 两个镜像）
  content.py   渲染前校验（15 条、单条 ≤100 字、无重复无换行）
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
- **新闻**：`https://60s-api.viki.moe/v2/60s`（开源项目 vikiboss/60s），
  失败依次换镜像 `60s.viki.moe`、`60s.coolsong.com/v2/60s`。接口直接给出 15 条成品文案，
  按原顺序上图。接口为 `news.fetch_daily()`。
- **文案**：**原样使用接口返回的 15 条**，不截断、不改写标点、不按题材重排。
  旧版用百度热榜标题 + 40 字截断做"归纳"，会把完整句子砍成半句，读起来呆板——
  这正是本次换源的原因。单条上限放宽到 100 字；模板条目无固定高度、
  海报按内容自动增高，实测最长 55 字约 3 行，不会被裁切。
- **微语**：优先用接口自带的 `tip`，缺失时才回退到页头文案。

接口数据不等于已核实事实。若要把海报对外正式发布，重要政策类信息需回查官方通稿。

> `--date` 只影响日期版式与卡片的农历/星期。该接口只提供**最新一期**，
> 不支持回放历史：补跑旧日期会明确报错并保留原图，不会把今天的新闻标成旧日期。

## 失败策略

任一步失败都以非 0 退出，且**不覆盖已有的 `latest.png`**（`tests/` 里有对应测试）。
CI 中 `Generate poster` 失败即中断，上一次部署的 `latest.png` 继续在线服务。
外部插件因此不会拿到 404 或半张图。

## 部署

`.github/workflows/daily-news.yml`：

- 每天 UTC 00:15（北京时间 08:15）自动跑，也支持手动 `workflow_dispatch`。
  接口通常 07:48 前后更新，08:15 能取到当天内容，且早于插件设定的发送时间。
- `concurrency` 保证同时只有一个实例，避免两次部署互相覆盖
- 上传 `output/` 为 Pages 产物，`deploy-pages` 发布

首次需在仓库 **Settings → Pages → Source** 选择 **GitHub Actions**。

## 已知限制

- **Pages 有缓存**。`latest.png` 是固定文件名，CDN 可能返回旧图（通常几分钟到十几分钟）。
  插件若要求强实时，需要加 cache-busting（如 `latest.png?d=YYYYMMDD`）
  或前面套一层 Cloudflare Worker。
- **只有一个数据源（三个入口）**。主站与两个镜像内容同源，同时挂掉当天会失败并保留旧图，
  不会生成残缺海报。
- **新闻由上游编辑**。文案质量取决于 60 秒接口本身；本仓库不再做归纳或润色。
