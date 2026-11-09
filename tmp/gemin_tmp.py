import json
import os
import re
from datetime import date

from google import genai
from google.genai import types

# ---------------------------------------------------------------
# APIキーの管理（api.txt）
# ---------------------------------------------------------------
# gemini.py と同じフォルダに api.txt を置く
API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api.txt")


def load_api_key():
    """api.txt からAPIキーを読み込む。ファイルがない・空の場合は空文字を返す。"""
    try:
        with open(API_KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def save_api_key(api_key):
    """APIキーを api.txt に保存する。"""
    api_key = (api_key or "").strip()

    with open(API_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(api_key)

    # 他のユーザーから読まれないよう、可能なら権限を所有者のみに制限
    try:
        os.chmod(API_KEY_FILE, 0o600)
    except OSError:
        pass


def has_api_key():
    """APIキーが設定されているか（api.txt が存在し、中身が空でないか）を返す。"""
    return bool(load_api_key())


# ---------------------------------------------------------------
# Geminiクライアント
# ---------------------------------------------------------------
# キーはモーダルから後で入力されるため、起動時ではなく使うときに作成する。
# キーが変更された場合は自動で作り直す（アプリの再起動は不要）。
_client = None
_client_key = None


def get_client():
    global _client, _client_key

    api_key = load_api_key()

    if not api_key:
        raise RuntimeError("Gemini APIキーが設定されていません。")

    if _client is None or api_key != _client_key:
        _client = genai.Client(api_key=api_key)
        _client_key = api_key

    return _client


# ---------------------------------------------------------------
# レシピ提案（2段階）
#   ステップ1: 料理名3つ + 短い概要だけを素早く生成
#   ステップ2: 選ばれた1つの料理の「材料と作り方」を生成
# ---------------------------------------------------------------
RECIPE_MODEL = "gemini-3.5-flash-lite"


def _ingredient_text(ingredients):
    """冷蔵庫の食材を箇条書きの文章にする。"""
    text = ""

    for ingredient in ingredients:
        name = ingredient[1]
        quantity = ingredient[2]
        unit = ingredient[3]
        expiry_date = ingredient[4] or "不明"

        text += f"- {name} {quantity}{unit}（賞味期限：{expiry_date}）\n"

    return text


def _normalize(text):
    """空白を除いて比較用の文字列にする。"""
    return re.sub(r"\s+", "", text or "")


def _parse_json_list(text):
    """Geminiの返答からJSONの配列を取り出す（```json で囲まれていても対応）。"""
    cleaned = re.sub(r"```(?:json)?", "", text or "", flags=re.IGNORECASE).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 前後に余計な文章が付いていた場合は、[ ... ] の部分だけを取り出す
        m = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)

        if not m:
            raise ValueError("料理名の取得結果を読み取れませんでした。")

        data = json.loads(m.group(0))

    # {"recipes": [...]} のような形で返ってきた場合
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])

    if not isinstance(data, list):
        raise ValueError("料理名の取得結果を読み取れませんでした。")

    return data


def get_recipe_suggestions(ingredients, feedback, past_titles):
    """
    ステップ1：料理名3つ（と短い概要）を返す。
      ingredients : 冷蔵庫の食材（DBの行）
      feedback    : [(料理名, 評価), ...]  過去の評価
      past_titles : 過去に保存した料理名のリスト（これらとは被らないようにする）
    戻り値: [{"name": 料理名, "summary": 概要}, ...]（最大3件）
    """
    ingredient_text = _ingredient_text(ingredients)

    feedback_text = ""
    for title, rating in feedback:
        feedback_text += f"- 評価{rating}：{title}\n"

    past_text = ""
    for title in past_titles:
        past_text += f"- {title}\n"

    prompt = f"""
あなたは料理アシスタントです。

以下は冷蔵庫に登録されている食材です。

{ingredient_text}

以下はユーザーが過去に評価した料理です（評価は1〜5、5が最高）。

{feedback_text or "（まだありません）"}

以下はユーザーが過去に保存した料理です。

{past_text or "（まだありません）"}

ユーザーが好みそうな料理を3つ提案してください。
作り方はまだ書かず、料理名と短い概要だけを答えてください。

条件：
・過去に保存した上の料理とは被らない、新しい料理名を3つ提案する
・3つは互いにジャンルや調理法が異なるようにする
・冷蔵庫にある食材をできるだけ活用する
・賞味期限が近い食材を優先する
・過去に高評価だった料理の傾向を参考にする
・過去に低評価だった料理と似た料理は避ける

以下のJSON配列だけを出力してください（説明文や ```json は付けない）。
summary は、使う主な食材や特徴を30文字以内で書いてください。

[
  {{"name": "料理名", "summary": "短い概要"}},
  {{"name": "料理名", "summary": "短い概要"}},
  {{"name": "料理名", "summary": "短い概要"}}
]
"""

    print("Geminiに料理名を問い合わせ中...")

    response = get_client().models.generate_content(
        model=RECIPE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )

    items = _parse_json_list(response.text)

    # 念のため、過去の料理名と同じものはここでも取り除く
    excluded = {_normalize(t) for t in past_titles}
    seen = set()
    suggestions = []

    for item in items:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).strip()
        summary = str(item.get("summary", "")).strip()
        key = _normalize(name)

        if not name or key in excluded or key in seen:
            continue

        seen.add(key)
        suggestions.append({"name": name, "summary": summary})

        if len(suggestions) == 3:
            break

    if not suggestions:
        raise ValueError("新しい料理名を取得できませんでした。")

    return suggestions


def get_recipe_detail(dish_name, ingredients):
    """
    ステップ2：指定された料理の「材料」と「作り方」を返す。
    戻り値は「材料：...\n作り方：...」の形式の文章（料理名の行は含まない）。
    """
    ingredient_text = _ingredient_text(ingredients)

    prompt = f"""
あなたは料理アシスタントです。

「{dish_name}」の材料と作り方を教えてください。

以下は冷蔵庫に登録されている食材です。できるだけ活用してください。

{ingredient_text}

条件：
・1〜2人分で、簡潔に書く
・冷蔵庫にない材料は、一般的な調味料などにとどめる
・マークダウンの装飾（** や # など）は使わない
・料理名は書かない

以下の形式で回答してください。

材料：
- 材料名 分量
作り方：
1. 手順
"""

    print(f"Geminiに「{dish_name}」の詳細を問い合わせ中...")

    response = get_client().models.generate_content(
        model=RECIPE_MODEL,
        contents=prompt
    )

    text = response.text or ""

    # 念のため「料理名：」の行が混ざっていたら取り除く
    lines = [
        line for line in text.strip().splitlines()
        if not re.match(r"^[\s#*>\-]*料理名", line)
    ]

    detail = "\n".join(lines).strip()

    if not detail:
        raise ValueError("レシピの詳細を取得できませんでした。")

    return detail


# ---------------------------------------------------------------
# 画像から食材を読み取る（レシート・ラベル対応）
# ---------------------------------------------------------------
def extract_ingredients_from_image(image_bytes, mime_type):
    """食品ラベル／レシートの画像から食材情報をJSON文字列で返す。"""
    today = date.today().isoformat()

    prompt = f"""
あなたは優秀な画像認識アシスタントです。
送信された画像は「スーパーの食材パック等に貼られている食品表示ラベル・シール」またはレシートです。
今日の日付は {today} です。

まず画像の内容を判定し、次のいずれかの status を決めてください。
- "ok"         : 食品（食材）のラベル・レシートで、情報が読み取れた
- "not_food"   : 画像に写っているものが食品ではない（人物、風景、日用品、文房具など）
- "unreadable" : 食品のようだが、ぼやけ・暗さ・見切れ等で文字が読み取れない

【items に入れる情報】
- name: 食材の名前。「名称」欄や大きく印字された商品名（例：国産豚バラ肉、じゃがいも）。
- quantity: 「内容量」などの数値部分のみ（例：「内容量 200g」なら 200）。
- unit: 内容量の単位（例：「g」「ml」「個」「パック」）。
- expiry_date: 「消費期限」または「賞味期限」の日付。YYYY-MM-DD 形式に変換する。
  年が記載されていない場合は MM-DD 形式。読み取れない場合は ""。

【出力形式】
マークダウンの ```json などの記号は含めず、純粋なJSONオブジェクトのみを出力してください。
status が "ok" 以外のときは items を空配列にし、reason に短い理由（日本語）を入れてください。

{{"status": "ok", "reason": "", "items": [
    {{"name": "国産豚バラ肉", "quantity": 200, "unit": "g", "expiry_date": "2026-10-10"}}
]}}

※複数のラベルが写っている場合は items に複数入れてください。
※読み取れない項目は "" (空文字) にしてください。
"""

    response = get_client().models.generate_content(
        model="gemini-3.5-flash",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )

    return response.text