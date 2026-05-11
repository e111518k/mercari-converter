"""
楽天商品ページ → メルカリShops 一括登録CSV 変換ツール

使い方:
    python rakuten_to_mercari.py <楽天商品ページURL> [オプション]

例:
    python rakuten_to_mercari.py https://item.rakuten.co.jp/hargio/ba-0002-0036/
    python rakuten_to_mercari.py https://item.rakuten.co.jp/hargio/ba-0002-0036/ --stock 10 --status on_sale
"""

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import requests
from bs4 import BeautifulSoup


@dataclass
class RakutenProduct:
    name: str = ""
    description: str = ""
    price: str = ""
    image_urls: list[str] = field(default_factory=list)
    item_code: str = ""
    skus: list[dict] = field(default_factory=list)  # [{"種類": "...", "在庫数": 1, "管理コード": "", "JAN": ""}]


_COLOR_WORDS = [
    "ブラック", "黒", "ホワイト", "白", "ブラウン", "茶", "ネイビー", "紺",
    "ベージュ", "グレー", "灰", "ピンク", "レッド", "赤", "カーキ",
    "イエロー", "黄", "グリーン", "緑", "ブルー", "青", "パープル", "紫",
    "オレンジ", "シルバー", "ゴールド", "ワイン", "ボルドー", "キャメル",
    "オフホワイト", "アイボリー", "ライトブルー", "ライトグレー",
]

# 英語カラー名 → 日本語
_EN_TO_JA_COLOR = {
    "black": "ブラック", "white": "ホワイト", "beige": "ベージュ",
    "brown": "ブラウン", "navy": "ネイビー", "gray": "グレー", "grey": "グレー",
    "pink": "ピンク", "red": "レッド", "khaki": "カーキ", "yellow": "イエロー",
    "green": "グリーン", "blue": "ブルー", "purple": "パープル",
    "orange": "オレンジ", "silver": "シルバー", "gold": "ゴールド",
    "wine": "ワイン", "bordeaux": "ボルドー", "camel": "キャメル",
    "ivory": "アイボリー", "offwhite": "オフホワイト", "cream": "クリーム",
    "lightblue": "ライトブルー", "lightgray": "ライトグレー", "lightgrey": "ライトグレー",
    "darkbrown": "ダークブラウン", "darkblue": "ダークブルー",
    "rose": "ローズ", "mint": "ミント", "lavender": "ラベンダー",
    "mustard": "マスタード", "terracotta": "テラコッタ", "sage": "セージ",
    "coral": "コーラル", "teal": "ティール", "olive": "オリーブ",
}

def _color_from_variant_id(variant_id: str) -> str:
    """variantId の末尾から色を取得: BA-0001-0103-black → ブラック"""
    suffix = variant_id.rsplit("-", 1)[-1].lower()
    return _EN_TO_JA_COLOR.get(suffix, suffix)

def _extract_color(text: str) -> str:
    for color in _COLOR_WORDS:
        if color in text:
            return color
    return ""

def _decode_selector_value(raw: str) -> str:
    """文字化けしたselectorValueを複数エンコーディングで再デコード試行"""
    if not raw or "\ufffd" not in raw:
        return raw
    raw_bytes = raw.encode("latin-1", errors="replace")
    for enc in ("utf-8", "shift_jis", "euc-jp", "cp932"):
        try:
            decoded = raw_bytes.decode(enc)
            if "\ufffd" not in decoded:
                return decoded
        except Exception:
            pass
    return raw


def _extract_item_json(soup) -> dict:
    import json as _json
    for script in soup.find_all("script"):
        text = script.string or ""
        if '"itemInfoSku"' not in text:
            continue
        start = text.find("{")
        if start == -1:
            continue
        depth, end = 0, start
        for i, c in enumerate(text[start:], start):
            if c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0: end = i + 1; break
        try:
            data = _json.loads(text[start:end])
            # api.data.itemInfoSku または newApi.itemInfoSku を返す
            return (data.get("api", {}).get("data", {}).get("itemInfoSku")
                    or data.get("newApi", {}).get("itemInfoSku")
                    or {})
        except Exception:
            pass
    return {}


def fetch_rakuten_product(url: str) -> RakutenProduct:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    # 楽天はEUC-JPページが多いため、raw bytesを複数エンコーディングで試みる
    html_text = None
    for enc in [resp.encoding, "euc-jp", "utf-8", "cp932", "shift_jis"]:
        if not enc:
            continue
        try:
            html_text = resp.content.decode(enc)
            break
        except Exception:
            pass
    if html_text is None:
        html_text = resp.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")

    product = RakutenProduct()

    # --- 商品名 ---
    item_json_pre = _extract_item_json(soup)
    raw_name = ""

    # 1) itemNameEnc（URLエンコードされた正確なタイトル）
    if item_json_pre:
        enc_name = item_json_pre.get("itemNameEnc", "")
        if enc_name:
            from urllib.parse import unquote
            for title_enc in ("euc-jp", "utf-8", "shift_jis"):
                try:
                    decoded = unquote(enc_name.replace("+", " "), encoding=title_enc)
                    if "\ufffd" not in decoded:
                        raw_name = decoded
                        break
                except Exception:
                    pass

    # 2) itemInfoSku.title（JSON直接）
    if not raw_name and item_json_pre:
        t = item_json_pre.get("title", "")
        if t and "\ufffd" not in t:
            raw_name = t

    # 3) og:title フォールバック
    if not raw_name:
        og_title = soup.find("meta", property="og:title")
        og_raw = og_title["content"].strip() if og_title and og_title.get("content") else ""
        if og_raw and "\ufffd" not in og_raw:
            raw_name = og_raw

    # 4) h1 フォールバック
    if not raw_name:
        h1 = soup.find("h1")
        raw_name = h1.get_text(strip=True) if h1 else ""

    raw_name = re.sub(r"^【[^】]*】", "", raw_name).strip()
    raw_name = re.sub(r"[：:][^：:]+$", "", raw_name).strip()
    product.name = raw_name[:130]

    # --- 価格 ---
    price_candidates = [
        soup.find("meta", property="product:price:amount"),
        soup.find("meta", attrs={"itemprop": "price"}),
    ]
    for meta in price_candidates:
        if meta and meta.get("content"):
            digits = re.sub(r"[^\d]", "", meta["content"])
            if digits:
                product.price = digits
                break
    if not product.price:
        for sel in ["span.price2", ".price2", "[class*='price--']",
                    "[itemprop='price']", ".item_price"]:
            el = soup.select_one(sel)
            if el:
                digits = re.sub(r"[^\d]", "", el.get_text())
                if digits:
                    product.price = digits
                    break
    if not product.price:
        # itemInfoSku.sku[0].taxIncludedPrice から取得
        skus_pre = item_json_pre.get("sku", []) if item_json_pre else []
        if skus_pre and skus_pre[0].get("taxIncludedPrice"):
            product.price = str(int(skus_pre[0]["taxIncludedPrice"]))
    if not product.price:
        # JSON-LD から価格を取得
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                import json
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    offers = item.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0]
                    p = str(offers.get("price", ""))
                    digits = re.sub(r"[^\d]", "", p)
                    if digits:
                        product.price = digits
                        break
            except Exception:
                pass
            if product.price:
                break

    # --- 画像URL ---
    seen_paths = set()

    def normalize_url(url: str) -> str:
        return re.sub(r"\?.*$", "", url.strip())

    def add_image(url: str):
        url = normalize_url(url)
        if not url or not url.startswith("http"):
            return
        # CDNドメインが異なっても同パスなら重複とみなす
        path = re.sub(r"^https?://[^/]+", "", url)
        if path not in seen_paths:
            seen_paths.add(path)
            product.image_urls.append(url)

    # 1) og:image でメイン画像を取得し、そのディレクトリを基準にする
    og_tag = soup.find("meta", property="og:image")
    og_url = normalize_url(og_tag.get("content", "")) if og_tag else ""
    add_image(og_url)

    # og:imageのパス（ドメイン除く）とタイムスタンプ接頭辞を基準にする
    # 例: https://shop.r10s.jp/hargio/cabinet/biiino/item/main-image/20260503144824_1.jpg
    #   → base_path = /hargio/cabinet/biiino/item/main-image/
    #   → ts_prefix  = 20260503144824_  （同一商品の複数画像を識別）
    if og_url:
        og_path = re.sub(r"^https?://[^/]+", "", og_url)
        base_path = og_path.rsplit("/", 1)[0] + "/"
        og_filename = og_url.rsplit("/", 1)[-1]
        # タイムスタンプ部分（_数字.拡張子 の前まで）を抽出
        ts_match = re.match(r"(\d+)_", og_filename)
        ts_prefix = ts_match.group(1) + "_" if ts_match else ""
    else:
        base_path = ""
        ts_prefix = ""

    RAKUTEN_IMG_DOMAINS = ("r10s.jp", "image.rakuten.co.jp", "shop.r10s.jp")

    def is_product_image(url: str) -> bool:
        if not any(d in url for d in RAKUTEN_IMG_DOMAINS):
            return False
        path = re.sub(r"^https?://[^/]+", "", url)
        if not path.startswith(base_path):
            return False
        # タイムスタンプが一致する画像のみ（同一商品の複数枚）
        filename = path.rsplit("/", 1)[-1]
        return not ts_prefix or filename.startswith(ts_prefix)

    # 2) <img> タグから収集
    for img in soup.find_all("img"):
        src = normalize_url(img.get("src", "") or img.get("data-src", ""))
        if is_product_image(src):
            add_image(src)

    # 3) <a href> に画像URLが直接含まれるケース
    for a in soup.find_all("a", href=True):
        href = normalize_url(a["href"])
        if re.search(r"\.(jpg|jpeg|png|webp)", href, re.I) and is_product_image(href):
            add_image(href)

    # --- 商品説明 ---
    # 楽天の商品説明は #item-description, .item_desc などにあることが多い
    desc_el = (
        soup.select_one("#item-description")
        or soup.select_one(".item_desc")
        or soup.select_one("[id*='desc']")
        or soup.select_one("[class*='description']")
    )
    if desc_el:
        product.description = desc_el.get_text(separator="\n", strip=True)
    else:
        # og:description フォールバック
        og_desc = soup.find("meta", property="og:description")
        if og_desc:
            product.description = og_desc.get("content", "")

    # --- 商品管理コード（URLから抽出）---
    m = re.search(r"/([^/]+)/?$", url.rstrip("/"))
    product.item_code = m.group(1) if m else ""

    # --- SKU/バリエーション ---
    # 優先度: 1) ページ内JSON(sku配列) > 2) identicalVariants > 3) selectタグ > 4) 商品名から色抽出

    # 1) ページ埋め込みJSONから itemInfoSku.sku を取得
    item_json = _extract_item_json(soup)
    sku_from_json = item_json.get("sku", []) if item_json else []
    identical_variants = item_json.get("identicalVariants", {}) if item_json else {}

    if sku_from_json:
        base_color = _extract_color(product.name + " " + product.description)
        for idx, sku_item in enumerate(sku_from_json[:10], start=1):
            variant_id = sku_item.get("variantId") or sku_item.get("merchantDefinedSkuId") or ""
            code = sku_item.get("skuId") or variant_id or product.item_code

            # 1) variantId末尾の英語カラーを日本語に変換（最も信頼性が高い）
            label = _color_from_variant_id(variant_id) if variant_id else ""

            # 2) attributes の "カラー" フィールド（文字化け対策デコード）
            if not label or label == variant_id.rsplit("-", 1)[-1]:
                for attr in sku_item.get("attributes", []):
                    title = _decode_selector_value(attr.get("title", ""))
                    if "カラー" in title or "色" in title or "color" in title.lower():
                        val = _decode_selector_value(attr.get("value", ""))
                        if val and "\ufffd" not in val:
                            label = val
                            break

            # 3) selectorValues（文字化けデコード試行）
            if not label:
                sel_values = sku_item.get("selectorValues") or []
                for v in sel_values:
                    decoded = _decode_selector_value(v)
                    if decoded and "\ufffd" not in decoded:
                        label = decoded
                        break

            # 4) フォールバック
            if not label:
                label = f"{base_color}{idx}" if base_color else f"種類{idx}"

            product.skus.append({"種類": label, "在庫数": 1, "管理コード": code, "JAN": ""})

    elif identical_variants:
        # identicalVariants: { "カラー名": "item-url" } の形式
        for color_name in list(identical_variants.keys())[:10]:
            product.skus.append({"種類": color_name, "在庫数": 1, "管理コード": "", "JAN": ""})

    else:
        # selectタグを確認
        sku_sets = []
        for sel in soup.select("select[name*='selectionName'], select[id*='select']"):
            label_el = sel.find_previous("th") or sel.find_previous("label") or sel.find_previous("dt")
            label = label_el.get_text(strip=True) if label_el else "種類"
            options = [
                opt.get_text(strip=True)
                for opt in sel.find_all("option")
                if opt.get("value") and opt.get("value") not in ("", "0")
            ]
            if options:
                sku_sets.append({"label": label, "values": options})

        if sku_sets:
            for val in sku_sets[0]["values"][:10]:
                product.skus.append({
                    "種類": f"{sku_sets[0]['label']}:{val}",
                    "在庫数": 1, "管理コード": "", "JAN": "",
                })
        else:
            # 商品名・説明から色を抽出
            color = _extract_color(product.name + " " + product.description)
            label = color if color else "標準"
            product.skus.append({"種類": label, "在庫数": 1, "管理コード": product.item_code, "JAN": ""})

    return product


# -----------------------------------------------------------------------
# メルカリShops CSV フォーマット定義
# -----------------------------------------------------------------------

MERCARI_HEADERS = [
    *[f"商品画像名_{i}" for i in range(1, 21)],
    "商品名", "商品説明",
    *[col for i in range(1, 11) for col in (
        f"SKU{i}_種類", f"SKU{i}_在庫数", f"SKU{i}_商品管理コード", f"SKU{i}_JANコード"
    )],
    "ブランドID", "販売価格", "カテゴリID", "商品の状態",
    "配送方法", "発送元の地域", "発送までの日数", "商品ステータス",
    "配送料の負担", "送料ID", "発売日", "予約受付開始日", "予約受付終了日",
    "キャンセル期限", "お届け予定", "メルカリBiz配送_クール区分",
]


# 都道府県名 → 都道府県ID
PREFECTURE_ID = {
    "北海道":"jp01","青森":"jp02","岩手":"jp03","宮城":"jp04","秋田":"jp05","山形":"jp06","福島":"jp07",
    "茨城":"jp08","栃木":"jp09","群馬":"jp10","埼玉":"jp11","千葉":"jp12","東京":"jp13","神奈川":"jp14",
    "新潟":"jp15","富山":"jp16","石川":"jp17","福井":"jp18","山梨":"jp19","長野":"jp20",
    "岐阜":"jp21","静岡":"jp22","愛知":"jp23","三重":"jp24",
    "滋賀":"jp25","京都":"jp26","大阪":"jp27","兵庫":"jp28","奈良":"jp29","和歌山":"jp30",
    "鳥取":"jp31","島根":"jp32","岡山":"jp33","広島":"jp34","山口":"jp35",
    "徳島":"jp36","香川":"jp37","愛媛":"jp38","高知":"jp39",
    "福岡":"jp40","佐賀":"jp41","長崎":"jp42","熊本":"jp43","大分":"jp44","宮崎":"jp45","鹿児島":"jp46","沖縄":"jp47",
}

def region_to_id(region: str) -> str:
    key = region.replace("都", "").replace("道", "").replace("府", "").replace("県", "")
    return PREFECTURE_ID.get(key, PREFECTURE_ID.get(region, "jp13"))


def product_to_mercari_row(
    product: RakutenProduct,
    default_stock: int = 1,
    status: int = 1,        # 1=公開, 2=下書き
    condition: int = 1,     # 1=新品・未使用
    shipping_method: int = 1,   # 1=らくらくメルカリ便
    shipping_payer: int = 1,    # 1=送料込み(出品者負担)
    shipping_region: str = "東京都",
    shipping_days: int = 1,     # 1=1〜2日, 2=2〜3日, 3=4〜7日
) -> dict:
    row = {h: "" for h in MERCARI_HEADERS}

    for i, url in enumerate(product.image_urls[:20], start=1):
        row[f"商品画像名_{i}"] = url

    row["商品名"] = product.name
    row["商品説明"] = product.description
    row["販売価格"] = product.price

    for i, sku in enumerate(product.skus[:10], start=1):
        row[f"SKU{i}_種類"] = sku["種類"]
        row[f"SKU{i}_在庫数"] = str(default_stock)
        row[f"SKU{i}_商品管理コード"] = sku.get("管理コード", "")
        row[f"SKU{i}_JANコード"] = sku.get("JAN", "")

    row["商品の状態"] = str(condition)
    row["配送方法"] = str(shipping_method)
    row["発送元の地域"] = region_to_id(shipping_region)
    row["発送までの日数"] = str(shipping_days)
    row["商品ステータス"] = str(status)
    row["配送料の負担"] = str(shipping_payer)

    return row


def save_csv(rows: list[dict], output_path: str):
    with open(output_path, "w", newline="", encoding="utf_8_sig") as f:
        writer = csv.DictWriter(f, fieldnames=MERCARI_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"保存しました: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="楽天商品ページ → メルカリShops CSV 変換")
    parser.add_argument("urls", nargs="+", help="楽天商品ページURL（複数指定可）")
    parser.add_argument("--output", "-o", default="mercari_import.csv", help="出力CSVファイル名")
    parser.add_argument("--stock", type=int, default=1, help="在庫数（デフォルト: 1）")
    parser.add_argument("--status", default="on_sale", choices=["on_sale", "draft"], help="商品ステータス")
    parser.add_argument("--condition", default="new", help="商品の状態")
    parser.add_argument("--region", default="東京都", help="発送元の地域")
    args = parser.parse_args()

    rows = []
    for url in args.urls:
        print(f"取得中: {url}")
        try:
            product = fetch_rakuten_product(url)
            row = product_to_mercari_row(
                product,
                default_stock=args.stock,
                status=args.status,
                condition=args.condition,
                shipping_region=args.region,
            )
            rows.append(row)
            print(f"  商品名: {product.name}")
            print(f"  価格: {product.price}")
            print(f"  画像数: {len(product.image_urls)}")
            print(f"  SKU数: {len(product.skus)}")
        except Exception as e:
            print(f"  エラー: {e}", file=sys.stderr)

    if rows:
        save_csv(rows, args.output)
    else:
        print("変換できた商品がありませんでした。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
