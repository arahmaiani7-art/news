"""把热榜候选压成 15 条海报文案。

约束来自 SKILL.md：每条正文最多 40 字（正文宽 317px、14px 字号、行高 20px，
一行约 22 字、最多两行，否则被条目高度裁切）。

刻意不做的事：
- 不编造热榜里没有的事实。文案只从 `word` / `desc` 里取信息。
- 不调用大模型。GitHub Actions 环境没有模型凭据，靠规则保证可重复与免费。
  需要更自然的措辞时，在此处接入自己的模型，输出仍要过 40 字与 15 条校验。
"""
from __future__ import annotations

import re
from typing import Sequence

NEWS_COUNT = 15
MAX_CHARS = 40

# 题材配比（SKILL.md）：避免被单一题材刷屏
TARGET_MIX = {
    "国际/时政": 5,
    "经济/消费": 4,
    "政策/公共事务": 3,
    "健康/民生": 2,
    "科技": 1,
}

# 关键词 → 题材。顺序有意义：先匹配到的胜出。
TOPIC_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("国际/时政", ("习近平", "普京", "特朗普", "外交", "外长", "总统", "APEC", "会议",
                   "峰会", "访问", "联合国", "美国", "俄罗斯", "以色列", "伊朗", "欧盟",
                   "日本", "韩国", "乌克兰", "领导人")),
    ("经济/消费", ("捐", "亿元", "股市", "股", "经济", "消费", "价格", "薪", "工资",
                   "楼市", "房价", "银行", "央行", "财政", "贸易", "关税", "投资",
                   "企业", "营收", "利润")),
    ("政策/公共事务", ("政策", "纪委", "监委", "双开", "通报", "监管", "规划", "立法",
                       "法院", "检察", "调查", "处罚", "部委", "工信部", "国务院")),
    ("健康/民生", ("健康", "医院", "医生", "病", "疫苗", "食品", "教育", "学生", "学校",
                   "养老", "就业", "辟谣", "安全", "天气", "台风", "寒潮")),
    ("科技", ("芯片", "AI", "人工智能", "量子", "机器人", "模型", "苹果", "华为",
              "小米", "特斯拉", "卫星", "火箭", "操作系统", "半导体")),
]

_SENT_SPLIT = re.compile(r"[。！？!?]")

# 截断后落在这些词尾上，说明话说了一半，读不通
_DANGLING = (
    "表示", "称", "说", "指出", "认为", "透露", "介绍", "显示", "宣布", "回应",
    "在", "对", "向", "将", "把", "被", "为", "以", "与", "和", "的", "是", "于",
    "因", "由", "从", "据", "及", "或", "并", "而", "但", "就", "都", "也", "还",
    "更", "很", "最", "不", "未", "已", "曾", "再", "又", "让", "使", "其", "此",
)

# 太短的文案没有信息量（如只剩"11月19日"），不参与生成
MIN_CHARS = 10


def classify(text: str) -> str:
    for topic, keywords in TOPIC_RULES:
        if any(k in text for k in keywords):
            return topic
    return "其他"


def _first_sentence(desc: str) -> str:
    desc = desc.strip()
    if not desc:
        return ""
    parts = _SENT_SPLIT.split(desc, maxsplit=1)
    return parts[0].strip() if parts else desc


def _is_dangling(text: str) -> bool:
    return any(text.endswith(d) for d in _DANGLING)


def compose_item(item: dict) -> str:
    """单条文案。

    只有两种来源，都保持原文，不改写标点、不拼接、不拼接后重排：

    1. 描述首句：含具体数字与主体，信息密度最高。整句能放进 40 字且不是半句话时采用。
    2. 标题：编辑写好的完整标签，天然短且通顺。首句放不下时用它。

    刻意不做"按子句裁剪"：把 `、` 换成 `，` 重排会改变原文语义，
    而只留前几个子句会得到"11月19日上午"这类没有信息量的碎片。
    """
    word = (item.get("word") or "").strip()
    sentence = _first_sentence(item.get("desc") or "")

    if sentence and len(sentence) <= MAX_CHARS and not _is_dangling(sentence):
        return sentence

    if word:
        return word if len(word) <= MAX_CHARS else word[:MAX_CHARS]

    return sentence[:MAX_CHARS] if sentence else ""




def select(candidates: Sequence[dict], count: int = NEWS_COUNT) -> list[dict]:
    """按题材配比挑选，返回带 topic 与 text 的条目。

    榜单顺序代表热度，同一题材内保持原顺序；先按配比填充，不足再按热度补，
    最后按原始热度回排。文案重复的候选在此处就被剔除并补位——
    留到 validate() 才报错会让整次生成失败。
    """
    if len(candidates) < count:
        raise ValueError(f"候选不足：需要 {count} 条，实际 {len(candidates)} 条")

    # 归一化：带 topic 与原始顺序，并按 word 去重
    pool: list[dict] = []
    seen_words: set[str] = set()
    for idx, raw in enumerate(candidates):
        word = (raw.get("word") or "").strip()
        if not word or word in seen_words:
            continue
        seen_words.add(word)
        topic = classify(f"{word} {raw.get('desc', '')}")
        pool.append({**raw, "topic": topic, "order": idx})

    buckets: dict[str, list[dict]] = {}
    for item in pool:
        buckets.setdefault(item["topic"], []).append(item)

    picked: list[dict] = []
    used: set[int] = set()

    def take(item: dict) -> bool:
        """尝试收下一条。文案过短、为空、或与已选重复则跳过。

        文案重复时退回标题再试一次：热榜常有同一事件的多条描述，
        标题至少是唯一且通顺的。
        """
        if item["order"] in used:
            return False

        chosen_texts = {p["text"] for p in picked}

        for text in (compose_item(item), (item.get("word") or "").strip()):
            if len(text) < MIN_CHARS or text in chosen_texts:
                continue
            used.add(item["order"])
            picked.append({**item, "text": text})
            return True

        return False

    # 第一轮：按题材配比取
    for topic, quota in TARGET_MIX.items():
        got = 0
        for item in buckets.get(topic, []):
            if got >= quota or len(picked) >= count:
                break
            if take(item):
                got += 1

    # 第二轮：不足时按原热度顺序补
    for item in pool:
        if len(picked) >= count:
            break
        take(item)

    if len(picked) != count:
        raise ValueError(f"选题后条数不足：{len(picked)}/{count}（候选池 {len(pool)} 条）")

    picked.sort(key=lambda x: x["order"])
    return picked



def validate(items: Sequence[dict], count: int = NEWS_COUNT) -> None:
    """渲染前的硬校验。任一不满足就直接失败，不生成残缺海报。"""
    if len(items) != count:
        raise ValueError(f"条数不对：{len(items)}/{count}")

    for i, item in enumerate(items, 1):
        text = item.get("text", "")
        if not text:
            raise ValueError(f"第 {i} 条文案为空")
        if len(text) > MAX_CHARS:
            raise ValueError(f"第 {i} 条 {len(text)} 字，超过 {MAX_CHARS} 字上限：{text}")

    seen: dict[str, int] = {}
    for i, item in enumerate(items, 1):
        t = item["text"]
        if t in seen:
            raise ValueError(f"第 {i} 条与第 {seen[t]} 条文案重复：{t}")
        seen[t] = i
