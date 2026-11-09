import sqlite3
from datetime import datetime, timedelta

# 追加したいサンプル食材データ（名前, 数量, 単位, 賞味期限のオフセット日数）
# 賞味期限はすべて 30日後（約1か月後）
EXPIRY_DAYS = 30

sample_ingredients = [
    # 野菜
    ("たまねぎ", 3, "個", EXPIRY_DAYS),
    ("にんじん", 4, "本", EXPIRY_DAYS),
    ("じゃがいも", 5, "個", EXPIRY_DAYS),
    ("キャベツ", 1, "玉", EXPIRY_DAYS),
    ("トマト", 4, "個", EXPIRY_DAYS),
    ("きゅうり", 3, "本", EXPIRY_DAYS),
    ("ほうれん草", 2, "束", EXPIRY_DAYS),
    ("ブロッコリー", 1, "個", EXPIRY_DAYS),
    ("なす", 3, "本", EXPIRY_DAYS),
    ("ピーマン", 5, "個", EXPIRY_DAYS),
    ("大根", 1, "本", EXPIRY_DAYS),
    ("しいたけ", 1, "パック", EXPIRY_DAYS),
    # 肉・魚
    ("鶏もも肉", 300, "g", EXPIRY_DAYS),
    ("豚バラ肉", 250, "g", EXPIRY_DAYS),
    ("牛こま切れ肉", 200, "g", EXPIRY_DAYS),
    ("ひき肉", 300, "g", EXPIRY_DAYS),
    ("ベーコン", 1, "パック", EXPIRY_DAYS),
    ("鮭の切り身", 2, "切れ", EXPIRY_DAYS),
    # 卵・乳製品・大豆製品
    ("卵", 10, "個", EXPIRY_DAYS),
    ("牛乳", 1000, "ml", EXPIRY_DAYS),
    ("ヨーグルト", 1, "パック", EXPIRY_DAYS),
    ("チーズ", 1, "袋", EXPIRY_DAYS),
    ("バター", 1, "個", EXPIRY_DAYS),
    ("豆腐", 2, "丁", EXPIRY_DAYS),
    ("納豆", 3, "パック", EXPIRY_DAYS),
    # 主食・その他
    ("食パン", 6, "枚", EXPIRY_DAYS),
    ("ごはん", 400, "g", EXPIRY_DAYS),
    ("うどん", 2, "玉", EXPIRY_DAYS),
    ("りんご", 3, "個", EXPIRY_DAYS),
    ("ツナ缶", 2, "缶", EXPIRY_DAYS),
]

def insert_sample_data():
    conn = sqlite3.connect("PBL.db")
    cursor = conn.cursor()

    today = datetime.now()

    print("=== 食材データの追加を開始します ===")
    for name, quantity, unit, days_offset in sample_ingredients:
        # 賞味期限の日付を計算 (YYYY-MM-DD)
        expiry_date = (today + timedelta(days=days_offset)).strftime("%Y-%m-%d")

        cursor.execute("""
            INSERT INTO ingredients (name, quantity, unit, expiry_date)
            VALUES (?, ?, ?, ?)
        """, (name, quantity, unit, expiry_date))
        
        print(f"追加: {name} {quantity}{unit} (賞味期限: {expiry_date})")

    conn.commit()
    conn.close()
    print("=== 追加が完了しました！ ===")

if __name__ == "__main__":
    insert_sample_data()


import sqlite3
from datetime import datetime, timedelta

# サンプルレシピデータ（レシピ内容, 評価の星の数1~5, 何日前の作成か）
sample_recipes = [
    (
        """料理名：豚肉とトマトのさっぱり炒め

材料：
- 豚肉：150g
- トマト：1個
- 醤油：小さじ2
- 塩コショウ：少々

作り方：
1. 豚肉と一口大に切ったトマトをフライパンで炒める。
2. 醤油と塩コショウで味を調えて完成。""",
        5,  # ★5
        1   # 1日前
    ),
    (
        """料理名：鶏もも肉とキャベツの塩蒸し

材料：
- 鶏もも肉：150g
- キャベツ：2枚
- ごま油：小さじ1
- 塩：小さじ1/2

作り方：
1. 耐熱容器に切ったキャベツと鶏肉を並べる。
2. ごま油と塩を振ってラップをし、レンジで4分加熱する。""",
        4,  # ★4
        3   # 3日前
    ),
    (
        """料理名：たまねぎと卵のスープ

材料：
- たまねぎ：1/2個
- 卵：1個
- コンソメ：小さじ1
- 水：300ml

作り方：
1. 鍋に水とスライスしたたまねぎ、コンソメを入れて煮る。
2. 溶き卵を回し入れて火を止める。""",
        3,  # ★3
        5   # 5日前
    )
]

def insert_sample_recipes():
    conn = sqlite3.connect("PBL.db")
    cursor = conn.cursor()

    now = datetime.now()

    print("=== レシピサンプルデータの追加を開始します ===")
    for recipe_text, rating, days_ago in sample_recipes:
        # 日時をフォーマット (YYYY-MM-DD HH:MM:SS)
        created_at = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. recipes テーブルにレシピを挿入
        cursor.execute("""
            INSERT INTO recipes (recipe_text, created_at)
            VALUES (?, ?)
        """, (recipe_text, created_at))

        recipe_id = cursor.lastrowid  # 直前に作成された recipe の ID を取得

        # 2. recipe_feedback テーブルに評価を挿入
        cursor.execute("""
            INSERT INTO recipe_feedback (recipe_id, rating, created_at)
            VALUES (?, ?, ?)
        """, (recipe_id, rating, created_at))

        first_line = recipe_text.split('\n')[0]
        print(f"追加: {first_line} (評価: ★{rating}, 日時: {created_at})")

    conn.commit()
    conn.close()
    print("=== レシピデータの追加が完了しました！ ===")

if __name__ == "__main__":
    insert_sample_recipes()