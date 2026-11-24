# 每日新闻海报自动生成

每天自动拉取「60 秒读懂世界」当日 15 条新闻，按固定模板渲染成 1125px 宽竖版 PNG，
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
  motto.py     页头格言池（120 条，按日期确定性轮换）
  render.py    模板填充 + Playwright 截图
  generate.py  主流程编排
template/poster.html   375px 海报模板（唯一视觉来源，出图时按 dsf 放大）
output/                产物目录，整个目录发布为 Pages 站点
tests/                 unittest
```

入库的产物只有 `output/latest.json`（`freshness` job 靠它判断当天是否已生成）。

`output/latest.png` **不入库**：Pages 用 `upload-pages-artifact` 上传整个 `output/` 部署，
仓库里的副本不参与发布。早期它入过库，但保活步骤只提交 json，结果仓库里的 png
停在 2026-11-20 而 json 的 `date` 已到 11-24——线上正常，看仓库却会得出错误结论。
`TestRepoHygiene` 守住这三条约束（png 不入库、json 入库、保活只提交 json）。

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
- **微语**：优先用接口自带的 `tip`，缺失时才回退到格言池里**与页头相邻的下一句**
  （`pick_motto(day, offset=1)`），避免同一张图上下出现同一句话。
- **页头格言**：`src/motto.py` 的 120 条池子，索引取 `date.toordinal() % len(MOTTOS)`。
  选 `toordinal()` 而不是 `random`，是为了「同一天恒定、跨天必换」：双 cron 补位重跑时
  得到同一句，PNG 与 `latest.json` 的 `header_motto` 永远一致；一天一跑则必然 +1，不重样
  周期 = 池子长度 ≈ 120 天。纯本地常量，无网络依赖，不会失败。

  改文案只动 `MOTTOS` 列表。两条硬约束由测试守着：**每条 ≤ 26 字**（页头是固定宽度
  红条，超长会换行挤压 FRIDAY 与农历）、**池内不得重复**（重复可能让相邻两天撞句，
  破坏「跨天必换」）。当前最长 19 字，实测 8/13/19 字均只占 1 行。

接口数据不等于已核实事实。若要把海报对外正式发布，重要政策类信息需回查官方通稿。

## 清晰度

模板是 375px CSS 宽，但出图按 `device_scale_factor=3` 渲染成 **1125px** 宽。
原因：主流手机物理宽 1080–1170px，dsf=2 只有 750px，微信铺满屏时会放大 1.4–1.6 倍，
再叠上 1:3.3 长图在会话内整体缩略，14px 小字就糊了。

dsf 只改像素密度，**不改版式**：CSS 宽、字号、换行都是原来的，不会把 15 条挤乱。
实测 1125×3666 仅 492KB（大面积白底，PNG 压缩效率高），远低于 2MB。

为什么停在 3：dsf=4 是 1500px 宽、体积破 2MB，微信发送时会压得更狠，反而更糊。

不要用 `render_png(width=...)` 变宽：模板里 `body` 与 `.poster` 都写死 `375px`，
调大视口只会多出白边再被 dsf 一起放大。要真正变宽必须改模板这两处及全部字号。

模板没用 `-webkit-font-smoothing: antialiased`：它是给深底浅字用的，
白底 `#333` 的 14px 小字开了反而让笔画变细。实测关掉后正文墨水总量 +18%、
实心笔画像素 +18.5%。`--font-render-hinting=none` 保留，避免缩放时字距被 hinting 挤歪。

`latest.json` 同时记录 CSS 尺寸与真实像素（`width`/`height` + `scale` +
`pixel_width`/`pixel_height`），避免再出现「json 写 375、图是 1125」的假账。

> `--date` 只影响日期版式与卡片的农历/星期。该接口只提供**最新一期**，
> 不支持回放历史：补跑旧日期会明确报错并保留原图，不会把今天的新闻标成旧日期。

## 失败策略

任一步失败都以非 0 退出，且**不覆盖已有的 `latest.png`**（`tests/` 里有对应测试）。
CI 中 `Generate poster` 失败即中断，上一次部署的 `latest.png` 继续在线服务。
外部插件因此不会拿到 404 或半张图。

## 部署

`.github/workflows/daily-news.yml`：

- **触发是「提前 + 等待」，不是「卡点 + 单次取数」**。GitHub Actions 的
  `schedule` 有实测的排队延迟：原定 08:15 的 cron，2026-11-21/22/23 三天
  分别到 09:16 / 09:12 / 09:19 才启动，晚于插件 09:00 的发送时间。
  把 cron 调晚或调早都不解决，因为延迟量本身在 57–64 分钟之间浮动。
- 因此 cron 设在 **UTC 23:00（北京 07:00）**，任务内部轮询等待接口发布当天数据
  （60 秒接口通常 07:48 前后更新），**最迟等到北京 08:45** 就放弃并失败。
  这样即使排队延迟一小时，07:00 + 60 分钟 = 08:00 启动，仍有 45 分钟取数渲染余量；
  若恰好准点启动，就等 48 分钟再出图。两种情况都在 09:00 之前。
- **两条 cron 冗余，防止整条被丢弃**。schedule 除了延迟，高负载时还会直接丢弃，
  只有一条时被丢就当天无任务可等。第二条 `0 0 * * *`（北京 08:00）补位；
  `concurrency` 的 `cancel-in-progress: false` 保证不会并发写同一份产物。
- **第二条 cron 会幂等跳过，不是白跑**。正常情况下 07:00 那条已出图，08:00 那条
  若照常执行就是多一次构建、多一次 Pages 部署、多一个 commit
  （`generated_at_utc` 每次都变，`git diff` 判断不出内容其实没变）。
  因此 `build` 前面有独立的 `freshness` job：`src/should_skip.py` 检查
  `latest.json` 的 `date` 是否已是今天、且 `generated_at_utc` 不早于 07:00（北京时间）。
  两个条件都满足才输出 `SKIP=1`，`build` 整个跳过。
  判断放在最前面而不是保活步骤前，否则只能省掉 commit，构建与部署照样往返一次。
  方针是**任何不确定都照常生成**（文件缺失、JSON 损坏、字段缺失、时间早于 cutoff），
  宁可多跑一次也不冒空发的风险。手动 dispatch 永远不跳过。
- **`timeout-minutes: 130` 必须大于等待窗口**。准点启动时等待窗口是
  07:00→08:45 共 105 分钟，加上装字体/依赖/Chromium 与渲染约 5 分钟。
  若上限小于 105，会出现「触发越准点越必然被杀」的反向结果
  （曾误设为 90，07:00 准点启动会在 08:30 被杀，而截止是 08:45）。
- 轮询间隔 180 秒，对公开接口保持低频访问。
- `concurrency` 保证同时只有一个实例，避免两次部署互相覆盖
- 上传 `output/` 为 Pages 产物，`deploy-pages` 发布
- 手动 `workflow_dispatch` 默认**不等待**（立即出图或立即失败），
  需要模拟定时行为时可填 `wait_until`（北京时间 HH:MM）。

### 60 天自动禁用与保活

GitHub 规定公开仓库**连续 60 天无活动**会禁用 scheduled workflow（邮件提醒，需手动恢复）。
产物走 Pages artifact 不产生 commit，长期不 push 会让 cron 两个月后静默停掉。
因此 build 末尾有一步把 `output/latest.json` 回写仓库：既保活，也顺带留下
每日新闻的版本历史。为此 `permissions` 需要 `contents: write`。

回写失败**不会**中断流程（只 `::warning::`）：保活是附带目的，发图才是主线，
不能因为 non-fast-forward 让 `deploy` 不执行、把当天海报弄丢。

首次需在仓库 **Settings → Pages → Source** 选择 **GitHub Actions**。

## 已知限制

- **Pages 有缓存**。`latest.png` 是固定文件名，CDN 可能返回旧图（通常几分钟到十几分钟）。
  插件若要求强实时，需要加 cache-busting（如 `latest.png?d=YYYYMMDD`）
  或前面套一层 Cloudflare Worker。
- **只有一个数据源（三个入口）**。主站与两个镜像内容同源，同时挂掉当天会失败并保留旧图，
  不会生成残缺海报。
- **新闻由上游编辑**。文案质量取决于 60 秒接口本身；本仓库不再做归纳或润色。
