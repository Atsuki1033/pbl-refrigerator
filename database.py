import os
import re
import sqlite3

# データベースファイルは database.py と同じフォルダに作る
# （どのフォルダからアプリを起動しても、同じ PBL.db を使うため）
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PBL.db")

# アプリが使うテーブルの定義（PBL_scheme.sql と同じ内容）。
# CREATE TABLE IF NOT EXISTS なので、すでにあるテーブルやデータは変更されない。
SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit TEXT NOT NULL,
    expiry_date TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS recipe_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    created_at TEXT NOT NULL,

    FOREIGN KEY (recipe_id)
        REFERENCES recipes(id)
);

CREATE TABLE IF NOT EXISTS shopping_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    quantity TEXT DEFAULT '',
    is_checked INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

_initialized = False


def init_db():
    """PBL.db とテーブルが無ければ作成する（あれば何も変更しない）。"""
    global _initialized

    conn = sqlite3.connect(DB_PATH)

    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

    _initialized = True


def _connect():
    """
    DBに接続する。
    初回の接続時（またはPBL.dbが削除されたとき）に、自動でテーブルを作成する。
    """
    if not _initialized or not os.path.exists(DB_PATH):
        init_db()

    return sqlite3.connect(DB_PATH)


# ---------------------------------------------------------------
# 食材に関する操作
# ---------------------------------------------------------------
def get_ingredients():
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, quantity, unit, expiry_date
        FROM ingredients
    """)

    ingredients = cursor.fetchall()
    conn.close()

    return ingredients


def add_ingredient(name, quantity, unit, expiry_date):
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, quantity
        FROM ingredients
        WHERE name = ?
        AND expiry_date = ?
        AND unit = ?
    """, (name, expiry_date, unit))

    existing = cursor.fetchone()

    if existing:
        new_quantity = existing[1] + quantity

        cursor.execute("""
            UPDATE ingredients
            SET quantity = ?
            WHERE id = ?
        """, (new_quantity, existing[0]))

    else:
        cursor.execute("""
            INSERT INTO ingredients
            (name, quantity, unit, expiry_date)
            VALUES (?, ?, ?, ?)
        """, (name, quantity, unit, expiry_date))

    conn.commit()
    conn.close()


def delete_ingredient(ingredient_id):
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM ingredients
        WHERE id = ?
    """, (ingredient_id,))

    conn.commit()
    conn.close()


def update_ingredient_quantity(ingredient_id, new_quantity):
    """数量を直接書き換える。0以下なら削除する。"""
    conn = _connect()
    cursor = conn.cursor()

    if new_quantity <= 0:
        cursor.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))
    else:
        cursor.execute(
            "UPDATE ingredients SET quantity = ? WHERE id = ?",
            (new_quantity, ingredient_id)
        )

    conn.commit()
    conn.close()


def increase_ingredient(ingredient_id, amount):
    """既存の在庫の数量を amount だけ増やす。"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("SELECT quantity FROM ingredients WHERE id = ?", (ingredient_id,))
    result = cursor.fetchone()

    if result:
        cursor.execute(
            "UPDATE ingredients SET quantity = ? WHERE id = ?",
            (round(result[0] + amount, 2), ingredient_id)
        )

    conn.commit()
    conn.close()


def consume_ingredient(ingredient_id, amount=1):
    """指定した量だけ減らす。0以下になったら削除する。"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("SELECT quantity FROM ingredients WHERE id = ?", (ingredient_id,))
    result = cursor.fetchone()

    if result:
        new_quantity = round(result[0] - amount, 2)

        if new_quantity <= 0:
            cursor.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))
        else:
            cursor.execute(
                "UPDATE ingredients SET quantity = ? WHERE id = ?",
                (new_quantity, ingredient_id)
            )

    conn.commit()
    conn.close()


def use_ingredients(items):
    """レシピで使った食材を冷蔵庫から減らす。
    items: [(name, quantity, unit), ...]
    同じ名前・単位の食材が複数ある場合は、賞味期限が近いものから使う。
    （賞味期限が空欄のものは最後に使う）
    """
    conn = _connect()
    cursor = conn.cursor()

    for name, quantity, unit in items:
        remaining = quantity

        cursor.execute("""
            SELECT id, quantity
            FROM ingredients
            WHERE name = ? AND unit = ?
            ORDER BY (expiry_date = '') ASC, expiry_date ASC
        """, (name, unit))

        for ingredient_id, current in cursor.fetchall():
            if remaining <= 0:
                break

            if current <= remaining:
                cursor.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))
                remaining = round(remaining - current, 4)
            else:
                cursor.execute(
                    "UPDATE ingredients SET quantity = ? WHERE id = ?",
                    (round(current - remaining, 2), ingredient_id)
                )
                remaining = 0

    conn.commit()
    conn.close()


# ---------------------------------------------------------------
# レシピ・評価に関する操作
# ---------------------------------------------------------------
def add_recipe(recipe_text):
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO recipes (recipe_text, created_at)
        VALUES (?, datetime('now', 'localtime'))
    """, (recipe_text,))

    recipe_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return recipe_id


def add_feedback(recipe_id, rating):
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO recipe_feedback
        (recipe_id, rating, created_at)
        VALUES (?, ?, datetime('now', 'localtime'))
    """, (recipe_id, rating))

    conn.commit()
    conn.close()


def get_recipe_feedback():
    """
    Geminiに渡す過去の評価。
    履歴画面で評価をやり直せるようになったため、
    同じレシピに複数の評価がある場合は「最新の評価」だけを返す。
    """
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT recipes.recipe_text, recipe_feedback.rating
        FROM recipe_feedback
        JOIN recipes
        ON recipe_feedback.recipe_id = recipes.id
        WHERE recipe_feedback.id = (
            SELECT rf.id
            FROM recipe_feedback rf
            WHERE rf.recipe_id = recipes.id
            ORDER BY rf.created_at DESC, rf.id DESC
            LIMIT 1
        )
        ORDER BY recipe_feedback.created_at DESC
    """)

    feedback = cursor.fetchall()
    conn.close()

    return feedback


def get_recipe_history():
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            r.id,
            r.recipe_text,
            r.created_at,
            (
                SELECT rf.rating
                FROM recipe_feedback rf
                WHERE rf.recipe_id = r.id
                ORDER BY rf.created_at DESC, rf.id DESC
                LIMIT 1
            ) AS rating
        FROM recipes r
        ORDER BY r.id DESC
    """)

    history = cursor.fetchall()
    conn.close()

    return history


def get_recipe_by_id(recipe_id):
    """(id, recipe_text, created_at, 最新の評価 or None) を返す。"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            r.id,
            r.recipe_text,
            r.created_at,
            (
                SELECT rf.rating
                FROM recipe_feedback rf
                WHERE rf.recipe_id = r.id
                ORDER BY rf.created_at DESC, rf.id DESC
                LIMIT 1
            ) AS rating
        FROM recipes r
        WHERE r.id = ?
    """, (recipe_id,))

    recipe = cursor.fetchone()
    conn.close()

    return recipe


def delete_recipe(recipe_id):
    """レシピと、それに紐づく評価(recipe_feedback)をまとめて削除する。"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM recipe_feedback
        WHERE recipe_id = ?
    """, (recipe_id,))

    cursor.execute("""
        DELETE FROM recipes
        WHERE id = ?
    """, (recipe_id,))

    conn.commit()
    conn.close()


def parse_recipe(recipe_text):
    """
    レシピ本文から (料理名, 材料と作り方の本文) を取り出す。
    「料理名：」の行を探し、見つからなければ最初の行を料理名として扱う。
    """
    text = (recipe_text or "").strip()
    lines = text.splitlines()

    for i, line in enumerate(lines):
        m = re.match(r"^[\s#*>\-]*料理名[\s*]*[：:][\s*]*(.*)$", line)

        if m:
            title = m.group(1).strip(" *#_")

            if title:
                body = "\n".join(lines[:i] + lines[i + 1:]).strip()
                return title, body

    # 「料理名：」が見つからない場合
    first = next((l for l in lines if l.strip()), "")
    title = first.strip(" *#_") or "レシピ"

    if len(title) > 40:
        title = title[:40] + "…"

    return title, text


def get_recipe_titles(limit=100):
    """
    過去に保存されたレシピの料理名一覧（新しい順・重複なし）を返す。
    Geminiに「これらとは被らない料理を」と伝えるために使う。
    """
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT recipe_text
        FROM recipes
        ORDER BY id DESC
    """)

    rows = cursor.fetchall()
    conn.close()

    titles = []
    seen = set()

    for (recipe_text,) in rows:
        if not (recipe_text or "").strip():
            continue

        title, _ = parse_recipe(recipe_text)

        if title in seen:
            continue

        seen.add(title)
        titles.append(title)

        if len(titles) >= limit:
            break

    return titles

def get_shopping_list():
    """未チェック（未購入）を優先し、ID降順で取得"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, quantity, is_checked, created_at
        FROM shopping_list
        ORDER BY is_checked ASC, id DESC
    """)

    items = cursor.fetchall()
    conn.close()

    return items


def add_shopping_item(name, quantity=""):
    """買い物リストにアイテムを追加"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO shopping_list (name, quantity, created_at)
        VALUES (?, ?, datetime('now', 'localtime'))
    """, (name, quantity))

    conn.commit()
    conn.close()


def toggle_shopping_item(item_id):
    """購入チェック状態（0と1）を切り替え"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE shopping_list
        SET is_checked = CASE WHEN is_checked = 1 THEN 0 ELSE 1 END
        WHERE id = ?
    """, (item_id,))

    conn.commit()
    conn.close()


def delete_shopping_item(item_id):
    """指定したアイテムを削除"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM shopping_list
        WHERE id = ?
    """, (item_id,))

    conn.commit()
    conn.close()


def clear_completed_shopping_items():
    """チェック済み（購入済み）アイテムを一括削除"""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM shopping_list
        WHERE is_checked = 1
    """)

    conn.commit()
    conn.close()


def create_tables():
    """手動実行用エイリアス"""
    init_db()
    print("テーブルを作成しました")


if __name__ == "__main__":
    create_tables()