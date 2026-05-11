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
