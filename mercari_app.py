"""
楽天 → メルカリShops CSV変換 Web UI
起動: python mercari_app.py
ブラウザで http://localhost:5001 を開く
"""

import io
import csv
from flask import Flask, render_template, request, jsonify, Response
from rakuten_to_mercari import fetch_rakuten_product, product_to_mercari_row, MERCARI_HEADERS

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("mercari.html")


@app.route("/api/convert", methods=["POST"])
def convert():
    data = request.get_json()
    urls = [u.strip() for u in data.get("urls", []) if u.strip()]
    stock = int(data.get("stock", 1))

    results = []
    for url in urls:
        try:
            product = fetch_rakuten_product(url)
            row = product_to_mercari_row(
                product,
                default_stock=stock,
                status=2,           # 下書き
                condition=1,        # 新品・未使用
                shipping_region="千葉県",
            )
            results.append({"success": True, "url": url, "row": row,
                            "name": product.name, "price": product.price,
                            "image": product.image_urls[0] if product.image_urls else "",
                            "sku_count": len(product.skus)})
        except Exception as e:
            results.append({"success": False, "url": url, "error": str(e)})

    return jsonify(results)


@app.route("/api/debug")
def debug():
    import requests as _req
    url = request.args.get("url", "https://item.rakuten.co.jp/hargio/ib-0001-0061/")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36", "Accept-Language": "ja,en;q=0.9"}
    resp = _req.get(url, headers=headers, timeout=15)
    _PERMISSIVE = {"iso-8859-1", "latin-1", "latin1", "iso8859-1"}
    tried = {}
    html_text = None
    for enc in [resp.encoding, "euc-jp", "utf-8", "cp932", "shift_jis"]:
        if not enc:
            continue
        try:
            c = resp.content.decode(enc)
            has_fffd = "\ufffd" in c
            tried[enc] = f"ok, has_fffd={has_fffd}"
            if not has_fffd and html_text is None and enc.lower() not in _PERMISSIVE:
                html_text = c
        except Exception as e:
            tried[enc] = f"error: {e}"
    from bs4 import BeautifulSoup
    from rakuten_to_mercari import _extract_item_json
    soup = BeautifulSoup(html_text or "", "html.parser")
    item_json = _extract_item_json(soup)
    enc_name = item_json.get("itemNameEnc", "")[:80] if item_json else ""
    title = item_json.get("title", "")[:80] if item_json else ""
    return jsonify({"resp_encoding": resp.encoding, "status_code": resp.status_code, "tried": tried, "enc_name": enc_name, "title_field": title, "html_len": len(html_text or ""), "html_preview": (html_text or "")[:200], "headers": dict(resp.headers)})


@app.route("/api/download", methods=["POST"])
def download():
    data = request.get_json()
    rows = data.get("rows", [])

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MERCARI_HEADERS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

    csv_bytes = buf.getvalue().encode("utf_8_sig")
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=mercari_import.csv"}
    )


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)
