import json
import os
import re
from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash

from gemini import (
    get_recipe_suggestions,
    get_recipe_detail,
    extract_ingredients_from_image,
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

# flash用の秘密鍵（本番環境では環境変数 SECRET_KEY で上書きしてください）
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# 起動時にテーブルを用意（なければ作成）
create_tables()

# ---- エラーメッセージの文言 ----
NOT_FOOD_MSG = "食品ではないようです。食材のラベルやレシートを撮影してください。"
UNREADABLE_MSG = "画像から食材情報を読み取れませんでした。明るい場所で、文字がはっきり写るように撮り直してください。"


# ---------------------------------------------------------------
# 食材一覧・手動登録
# ---------------------------------------------------------------
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
        has_api_key=has_api_key(),                       # False のときモーダルを表示
        api_key_error=request.args.get("api_key_error")  # 空送信時のエラー表示用
    )


# APIキーを保存する
@app.route("/save_api_key", methods=["POST"])
def save_api_key_route():
    api_key = request.form.get("api_key", "").strip()

    if not api_key:
        return redirect(url_for("index", api_key_error=1))

    save_api_key(api_key)

    return redirect(url_for("index"))


# ---------------------------------------------------------------
# 画像アップロード（読み取り成功/失敗メッセージ・賞味期限チェック付き）
# ---------------------------------------------------------------
def normalize_expiry(value):
    """賞味期限を date に揃える。解釈できなければ None を返す。"""
    value = (value or "").strip().replace("/", "-").replace(".", "-")
    if not value:
        return None
    today = date.today()

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        pass

    # 年なし（MM-DD）は今年として扱い、半年以上過去なら来年とみなす
    try:
        d = datetime.strptime(f"{today.year}-{value}", "%Y-%m-%d").date()
        if d < today - timedelta(days=180):
            d = d.replace(year=today.year + 1)
        return d
    except ValueError:
        return None


def parse_gemini_result(result_text):
    """Geminiの出力を (status, reason, items) にする。"""
    clean = re.sub(r"```json\s*", "", result_text, flags=re.IGNORECASE)
    clean = re.sub(r"```\s*", "", clean).strip()
    data = json.loads(clean)

    # 旧形式（配列のみ）にも対応
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
        print("====== Geminiの生データ ======")
        print(result_text)
        print("==============================")

        status, reason, items = parse_gemini_result(result_text)
    except json.JSONDecodeError as e:
        print(f"\n[!!! エラー発生 !!!] Geminiの出力をJSONとして解釈できません: {e}\n")
        flash("読み取り結果を解釈できませんでした。もう一度お試しください。", "error")
        return redirect(url_for("index"))
    except Exception as e:
        print(f"\n[!!! エラー発生 !!!] 画像解析でエラー: {e}\n")
        flash("読み取りに失敗しました。時間をおいてもう一度お試しください。", "error")
        return redirect(url_for("index"))

    # エラーメッセージ：食品ではない / 読み取れない
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

        # 賞味期限切れは登録しない
        if expiry is not None and expiry < today:
            flash(f"「{name}」は賞味期限が切れています（{expiry.isoformat()}）。登録しませんでした。", "error")
            continue

        if expiry is None:
            flash(f"「{name}」の賞味期限を読み取れませんでした。登録後に確認してください。", "warning")

        try:
            add_ingredient(
                name=name,
                quantity=qty,
                unit=item.get("unit") or "個",
                expiry_date=expiry.isoformat() if expiry else ""
            )
            registered.append(name)
            print(f"登録成功: {name}")
        except Exception as e:
            print(f"\n[!!! エラー発生 !!!] DB登録でエラー: {e}\n")
            flash(f"「{name}」の登録に失敗しました。", "error")

    if registered:
        flash(f"読み取り成功：{'、'.join(registered)} を登録しました。", "success")

    return redirect(url_for("index"))


# ---------------------------------------------------------------
# 食材の削除・更新・消費
# ---------------------------------------------------------------
@app.route("/delete/<int:ingredient_id>", methods=["POST"])
def delete(ingredient_id):
    # フォームから削除数(quantity)を取得
    quantity_to_delete = request.form.get("quantity")

    if quantity_to_delete:
        # 個数が指定されている場合は指定数分減らす（0以下になれば削除される）
        consume_ingredient(ingredient_id, int(quantity_to_delete))
    else:
        # 個数指定がない場合はレコードを完全削除する
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
        try:
            qty = float(q)
        except ValueError:
            continue
        items.append((name, int(qty) if qty.is_integer() else qty, unit))

    use_ingredients(items)

    # fetch（画面遷移なし）から呼ばれた場合は、リダイレクトせず成功だけ返す
    if request.headers.get("X-Requested-With") == "fetch":
        return "", 204

    return redirect(url_for("index"))


# ---------------------------------------------------------------
# レシピ提案（2段階）
# ---------------------------------------------------------------
# ステップ1：料理名3つ（と短い概要）だけを生成して表示する
@app.route("/recipe")
def recipe():
    if not has_api_key():
        return redirect(url_for("index"))

    ingredients = get_ingredients()

    if not ingredients:
        return render_template(
            "recipe.html",
            suggestions=[],
            no_ingredients=True,
            error=None
        )

    feedback = [
        (parse_recipe(text)[0], rating)
        for text, rating in get_recipe_feedback()
    ]

    past_titles = get_recipe_titles()

    suggestions = []
    error = None

    try:
        suggestions = get_recipe_suggestions(ingredients, feedback, past_titles)
    except Exception as e:
        print(f"\n[!!! エラー発生 !!!] 料理名の生成でエラー: {e}\n")
        error = "料理名の提案に失敗しました。もう一度お試しください。"

    return render_template(
        "recipe.html",
        suggestions=suggestions,
        no_ingredients=False,
        error=error
    )


# ステップ2：選ばれた料理の「材料と作り方」を生成して返す（fetch用のJSON）
@app.route("/recipe/detail", methods=["POST"])
def recipe_detail():
    if not has_api_key():
        return jsonify(error="APIキーが設定されていません。"), 400

    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()

    if not name or len(name) > 100:
        return jsonify(error="料理名が正しくありません。"), 400

    try:
        detail = get_recipe_detail(name, get_ingredients())
    except Exception as e:
        print(f"\n[!!! エラー発生 !!!] レシピ詳細の生成でエラー: {e}\n")
        return jsonify(error="レシピの取得に失敗しました。"), 502

    recipe_text = f"料理名：{name}\n{detail}"

    return jsonify(detail=detail, recipe_text=recipe_text)


# 「履歴に保存する」を押したときだけレシピをDBに保存する
@app.route("/save_recipe", methods=["POST"])
def save_recipe():
    recipe_text = request.form.get("recipe_text", "").strip()

    if not recipe_text:
        return redirect(url_for("index"))

    recipe_id = add_recipe(recipe_text)

    return redirect(url_for("history", _anchor=f"recipe-{recipe_id}"))


# ---------------------------------------------------------------
# 履歴・評価
# ---------------------------------------------------------------
@app.route("/feedback/<int:recipe_id>", methods=["POST"])
def feedback(recipe_id):
    rating = request.form.get("rating", type=int)

    if rating not in (1, 2, 3, 4, 5) or get_recipe_by_id(recipe_id) is None:
        return redirect(url_for("history"))

    add_feedback(recipe_id, rating)

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


# 履歴からレシピ（と評価）を削除する
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)