import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash

from gemini import (
    get_recipe_suggestions,
    get_recipe_detail,
    extract_ingredients_from_image,
    chat_with_character,
    CHARACTER,
    has_api_key,
    save_api_key,
)
from database import (
    create_tables,
    get_ingredients,
    add_ingredient,
    delete_ingredient,
    update_ingredient_quantity,
    consume_ingredient,
    use_ingredients,
    add_recipe,
    add_feedback,
    get_recipe_feedback,
    get_recipe_history,
    get_recipe_by_id,
    get_recipe_titles,
    delete_recipe,
    parse_recipe,
)

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

create_tables()

NOT_FOOD_MSG = "食品ではないようです。食材のラベルやレシートを撮影してください。"
UNREADABLE_MSG = "画像から食材情報を読み取れませんでした。明るい場所で、文字がはっきり写るように撮り直してください。"


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        name = request.form["name"]
        quantity = int(request.form["quantity"])
        unit = request.form["unit"]
        expiry_date = request.form["expiry_date"]

        add_ingredient(name, quantity, unit, expiry_date)

        return redirect(url_for("index"))

    ingredients = get_ingredients()

    return render_template(
        "index.html",
        ingredients=ingredients,
        has_api_key=has_api_key(),
        api_key_error=request.args.get("api_key_error")
    )


@app.route("/save_api_key", methods=["POST"])
def save_api_key_route():
    api_key = request.form.get("api_key", "").strip()

    if not api_key:
        return redirect(url_for("index", api_key_error=1))

    save_api_key(api_key)

    return redirect(url_for("index"))


_VULGAR = {"½": " 1/2", "⅓": " 1/3", "⅔": " 2/3", "¼": " 1/4", "¾": " 3/4"}


def parse_quantity(value):
    """
    "2", "0.5", "1/2", "1 1/2", "1と1/2", "１／２", "½", "1½" などを数値にする。
    読み取れない・0以下の場合は None を返す。
    """
    s = str(value or "")
    for k, v in _VULGAR.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKC", s).replace("⁄", "/").strip()

    if not s:
        return None

    try:
        num = float(s)
    except ValueError:
        m = re.fullmatch(
            r"(?:(\d+)\s*(?:と|\+|\s)\s*)?(\d+)\s*/\s*(\d+)", s
        )
        if not m or int(m.group(3)) == 0:
            return None
        num = int(m.group(1) or 0) + int(m.group(2)) / int(m.group(3))

    num = round(num, 2)
    return num if num > 0 else None


def normalize_expiry(value):
    value = (value or "").strip().replace("/", "-").replace(".", "-")
    if not value:
        return None
    today = date.today()

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        pass

    try:
        d = datetime.strptime(f"{today.year}-{value}", "%Y-%m-%d").date()
        if d < today - timedelta(days=180):
            d = d.replace(year=today.year + 1)
        return d
    except ValueError:
        return None


def parse_gemini_result(result_text):
    clean = re.sub(r"```json\s*", "", result_text, flags=re.IGNORECASE)
    clean = re.sub(r"```\s*", "", clean).strip()
    data = json.loads(clean)

    if isinstance(data, list):
        return "ok", "", data
    return data.get("status", "ok"), data.get("reason", ""), data.get("items", [])


@app.route("/upload", methods=["POST"])
def upload():
    if not has_api_key():
        return redirect(url_for("index"))

    if "image" not in request.files or request.files["image"].filename == "":
        flash("画像が選択されていません。ファイルを選んでください。", "error")
        return redirect(url_for("index"))

    file = request.files["image"]

    try:
        image_bytes = file.read()
        result_text = extract_ingredients_from_image(image_bytes, file.mimetype)
        status, reason, items = parse_gemini_result(result_text)
    except Exception as e:
        flash("読み取りに失敗しました。時間をおいてもう一度お試しください。", "error")
        return redirect(url_for("index"))

    if status == "not_food":
        flash(NOT_FOOD_MSG, "error")
        return redirect(url_for("index"))
    if status == "unreadable" or not items:
        flash(UNREADABLE_MSG, "error")
        return redirect(url_for("index"))

    today = date.today()
    registered = []

    for item in items:
        name = (item.get("name") or "").strip() or "名称不明"
        try:
            qty = int(item.get("quantity", 1))
        except (ValueError, TypeError):
            qty = 1

        expiry = normalize_expiry(item.get("expiry_date"))

        if expiry is not None and expiry < today:
            continue

        try:
            add_ingredient(
                name=name,
                quantity=qty,
                unit=item.get("unit") or "個",
                expiry_date=expiry.isoformat() if expiry else ""
            )
            registered.append(name)
        except Exception:
            pass

    if registered:
        flash(f"読み取り成功：{'、'.join(registered)} を登録しました。", "success")

    return redirect(url_for("index"))


@app.route("/delete/<int:ingredient_id>", methods=["POST"])
def delete(ingredient_id):
    quantity_to_delete = request.form.get("quantity")
    if quantity_to_delete:
        qty = parse_quantity(quantity_to_delete)
        if qty is not None:
            consume_ingredient(ingredient_id, qty)
    else:
        delete_ingredient(ingredient_id)
    return redirect(url_for("index"))


@app.route("/update/<int:ingredient_id>", methods=["POST"])
def update(ingredient_id):
    quantity = int(request.form["quantity"])
    update_ingredient_quantity(ingredient_id, quantity)
    return redirect(url_for("index"))


@app.route("/use", methods=["POST"])
def use():
    names = request.form.getlist("name")
    quantities = request.form.getlist("quantity")
    units = request.form.getlist("unit")

    items = []
    for name, q, unit in zip(names, quantities, units):
        qty = parse_quantity(q)
        if qty is None:
            continue
        items.append((name, int(qty) if float(qty).is_integer() else qty, unit))

    use_ingredients(items)

    if request.headers.get("X-Requested-With") == "fetch":
        return "", 204

    return redirect(url_for("index"))


# ---------------------------------------------------------------
# レシピ提案（ペルソナ対応）
# ---------------------------------------------------------------
@app.route("/recipe")
def recipe():
    if not has_api_key():
        return redirect(url_for("index"))

    # クエリパラメータからペルソナを取得（デフォルトは "default"）
    persona = request.args.get("persona", "default")

    ingredients = get_ingredients()

    if not ingredients:
        return render_template(
            "recipe.html",
            suggestions=[],
            no_ingredients=True,
            error=None,
            current_persona=persona
        )

    feedback = [
        (parse_recipe(text)[0], rating)
        for text, rating in get_recipe_feedback()
    ]

    past_titles = get_recipe_titles()

    suggestions = []
    error = None

    try:
        suggestions = get_recipe_suggestions(ingredients, feedback, past_titles, persona=persona)
    except Exception as e:
        print(f"\n[!!! エラー発生 !!!] 料理名の生成でエラー: {e}\n")
        error = "料理名の提案に失敗しました。もう一度お試しください。"

    return render_template(
        "recipe.html",
        suggestions=suggestions,
        no_ingredients=False,
        error=error,
        current_persona=persona
    )


@app.route("/recipe/detail", methods=["POST"])
def recipe_detail():
    if not has_api_key():
        return jsonify(error="APIキーが設定されていません。"), 400

    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    persona = str(data.get("persona", "default")).strip()

    if not name or len(name) > 100:
        return jsonify(error="料理名が正しくありません。"), 400

    try:
        detail = get_recipe_detail(name, get_ingredients(), persona=persona)
    except Exception as e:
        print(f"\n[!!! エラー発生 !!!] レシピ詳細の生成でエラー: {e}\n")
        return jsonify(error="レシピの取得に失敗しました。"), 502

    recipe_text = f"料理名：{name}\n{detail}"

    return jsonify(detail=detail, recipe_text=recipe_text)


@app.route("/save_recipe", methods=["POST"])
def save_recipe():
    recipe_text = request.form.get("recipe_text", "").strip()

    if not recipe_text:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="レシピが空です。"), 400
        return redirect(url_for("index"))

    recipe_id = add_recipe(recipe_text)
    history_url = url_for("history", _anchor=f"recipe-{recipe_id}")

    # チャット画面などから fetch で呼ばれたときは、画面遷移せず JSON を返す
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(id=recipe_id, url=history_url)

    return redirect(history_url)


@app.route("/feedback/<int:recipe_id>", methods=["POST"])
def feedback(recipe_id):
    rating = request.form.get("rating", type=int)
    is_fetch = request.headers.get("X-Requested-With") == "fetch"

    if rating not in (1, 2, 3, 4, 5) or get_recipe_by_id(recipe_id) is None:
        if is_fetch:
            return jsonify(error="評価を保存できませんでした。"), 400
        return redirect(url_for("history"))

    add_feedback(recipe_id, rating)

    # チャット画面から fetch で呼ばれたときは、画面遷移せず JSON を返す
    if is_fetch:
        return jsonify(ok=True, rating=rating)

    if request.form.get("next") == "detail":
        return redirect(url_for("history_detail", recipe_id=recipe_id))

    return redirect(url_for("history", _anchor=f"recipe-{recipe_id}"))


@app.route("/history")
def history():
    recipes = []

    for recipe_id, recipe_text, created_at, rating in get_recipe_history():
        title, body = parse_recipe(recipe_text)

        recipes.append({
            "id": recipe_id,
            "title": title,
            "body": body,
            "created_at": created_at,
            "rating": rating,
        })

    return render_template(
        "history.html",
        recipes=recipes
    )


@app.route("/delete_recipe/<int:recipe_id>", methods=["POST"])
def delete_recipe_route(recipe_id):
    delete_recipe(recipe_id)

    return redirect(url_for("history"))


@app.route("/history/<int:recipe_id>")
def history_detail(recipe_id):
    recipe = get_recipe_by_id(recipe_id)

    if recipe is None:
        return "レシピが見つかりません", 404

    return render_template(
        "recipe.html",
        recipe=recipe[1],
        recipe_id=recipe[0],
        rating=recipe[3],
        history_view=True
    )


# ---------------------------------------------------------------
# キャラクターとのチャット
# ---------------------------------------------------------------
MAX_CHAT_MESSAGE_LEN = 500
MAX_CHAT_HISTORY = 20


def sanitize_chat_history(raw):
    """ブラウザから届いた会話履歴を、安全な形（role と text だけ）に整える。"""
    cleaned = []

    if not isinstance(raw, list):
        return cleaned

    for turn in raw[-MAX_CHAT_HISTORY:]:
        if not isinstance(turn, dict):
            continue

        role = "user" if turn.get("role") == "user" else "model"
        text = str(turn.get("text", "")).strip()[:MAX_CHAT_MESSAGE_LEN * 2]

        if text:
            cleaned.append({"role": role, "text": text})

    return cleaned


CHARACTER_IMAGE_EXTS = ("png", "webp", "jpg", "jpeg", "gif", "svg")


def find_character_images():
    """
    static/character/ から表情ごとの画像を探す。
    拡張子（png/webp/jpg…）や大文字小文字が違っていても見つける。
    見つからない表情は .png のURLを返す（ブラウザ側で絵文字に切り替わる）。
    """
    folder = os.path.join(app.static_folder, "character")

    try:
        listing = sorted(os.listdir(folder))
    except OSError:
        listing = []

    files = {name.lower(): name for name in listing}

    images = {}

    for expression in CHARACTER["expressions"]:
        url = url_for("static", filename=f"character/{expression}.png")

        for ext in CHARACTER_IMAGE_EXTS:
            real_name = files.get(f"{expression}.{ext}")

            if real_name:
                mtime = int(os.path.getmtime(os.path.join(folder, real_name)))
                # v=更新時刻 を付けて、差し替えた画像がキャッシュで古いままになるのを防ぐ
                url = url_for("static", filename=f"character/{real_name}", v=mtime)
                break

        images[expression] = url

    return images, folder, listing


@app.route("/chat")
def chat_page():
    if not has_api_key():
        return redirect(url_for("index"))

    images, image_folder, found_files = find_character_images()

    # 画像が表示されないときの調査用に、サーバーが見ている場所をターミナルに出す
    print(f"[chat] 画像フォルダ: {image_folder}")
    print(f"[chat] フォルダ内のファイル: {found_files or '（フォルダが無い、または空です）'}")

    return render_template(
        "chat.html",
        character=CHARACTER,
        images=images,
        image_folder=image_folder,
        found_files=found_files
    )


@app.route("/chat/message", methods=["POST"])
def chat_message():
    if not has_api_key():
        return jsonify(error="APIキーが設定されていません。"), 400

    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if not message:
        return jsonify(error="メッセージが空です。"), 400

    if len(message) > MAX_CHAT_MESSAGE_LEN:
        return jsonify(error=f"メッセージは{MAX_CHAT_MESSAGE_LEN}文字以内にしてください。"), 400

    history = sanitize_chat_history(data.get("history"))

    feedback = [
        (parse_recipe(text)[0], rating)
        for text, rating in get_recipe_feedback()
    ]

    try:
        result = chat_with_character(
            message,
            history,
            get_ingredients(),
            feedback,
            get_recipe_titles()
        )
    except Exception as e:
        print(f"\n[!!! エラー発生 !!!] チャットの生成でエラー: {e}\n")
        return jsonify(error="うまく返事できませんでした。もう一度お試しください。"), 502

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)