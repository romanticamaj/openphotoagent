"""fixtures/golden/tokenize.json — 100 reviewed sentences for the search tokenizer (SRCH-TOK-007).

Regenerate only when TOKENIZER_VERSION changes, and review the diff: the golden guards against silent changes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from opagent_search.tokenize import TOKENIZER_VERSION, Tokenizer

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "tokenize.json"
SENTENCES = [
    "海邊的貓",
    "夕陽下的海灘",
    "一隻柴犬在草地上奔跑",
    "花蓮縣新城鄉的海邊",
    "台北101夜景",
    "京都清水寺的楓葉",
    "生日蛋糕上的蠟燭",
    "婚禮上的新娘與新郎",
    "公園裡的小朋友",
    "陽明山的櫻花",
    "一碗熱騰騰的拉麵",
    "咖啡廳的拿鐵拉花",
    "雨天的街道",
    "夜市的小吃攤",
    "高鐵站的月台",
    "機場的登機門",
    "山上的雲海",
    "日月潭的湖景",
    "墾丁的沙灘",
    "阿里山的日出",
    "貓咪在沙發上睡覺",
    "狗狗在海邊玩水",
    "一群鴿子在廣場上",
    "動物園裡的長頸鹿",
    "水族館的水母",
    "家裡的盆栽",
    "收據與發票",
    "螢幕截圖",
    "白板上的會議筆記",
    "身分證影本",
    "畢業典禮合照",
    "全家福",
    "外婆的八十歲壽宴",
    "跨年煙火",
    "演唱會的舞台燈光",
    "籃球比賽",
    "腳踏車環島",
    "露營的帳篷",
    "溫泉旅館",
    "老街的紅燈籠",
    "廟口的媽祖遶境",
    "龍舟比賽",
    "猫在海边睡觉",
    "上海外滩的夜景",
    "北京故宫",
    "软件截图",
    "西湖的荷花",
    "广场上的鸽子",
    "a cat on the beach",
    "two dogs playing in the snow",
    "sunset over the ocean",
    "birthday party with balloons",
    "the Eiffel Tower at night",
    "a bowl of ramen",
    "receipts and invoices",
    "mountain lake reflection",
    "kids playing in the park",
    "wedding ceremony",
    "city skyline",
    "street food market",
    "snowy mountain cabin",
    "autumn leaves in Kyoto",
    "red lanterns in the old street",
    "a latte with heart art",
    "hiking trail",
    "cats",
    "Dogs",
    "running shoes",
    "group photo",
    "screenshot of settings",
    "IMG_2024_海邊.jpg",
    "2024年3月的照片",
    "去年夏天在沖繩",
    "富士山と桜",
    "ねこ",
    "いぬと散歩",
    "東京タワーの夜景",
    "貓 cat ねこ",
    "Kyoto 清水寺",
    "iPhone 拍的日落",
    "GoPro 潛水",
    "4K 空拍",
    "Taipei 101 煙火",
    "台南 beef soup",
    "ＣＡＴ　全形",
    "Ｃａｔ",
    "零​寬​字元",
    "柴犬",
    "新城鄉",
    "夕陽海灘",
    "海灘夕陽",
    "沙灘排球",
    "早餐的蛋餅與豆漿",
    "便當",
    "珍珠奶茶",
    "九份的山城",
    "鳳梨酥禮盒",
    "小籠包",
    "滷肉飯",
    "牛肉麵",
]


def main() -> int:
    assert len(SENTENCES) == 100, len(SENTENCES)
    tok = Tokenizer(custom_words=["柴犬", "新城鄉"])
    data = {"tokenizer": TOKENIZER_VERSION, "cases": [{"text": s, "words": tok.words(s)} for s in SENTENCES]}
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if "--check" in sys.argv:
        return 0 if OUT.read_text(encoding="utf-8") == text else 1
    OUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
