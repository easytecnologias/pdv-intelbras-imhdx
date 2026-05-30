#!/usr/bin/env python3
import argparse
import json
import os
import unicodedata
from pathlib import Path


MAP = {
    "refrigerante": "bebida",
    "guarana": "bebida",
    "coca": "bebida",
    "bebida": "bebida",
    "biscoito": "biscoito",
    "wafer": "biscoito",
    "carne": "carne",
    "frango": "carne",
    "bisteca": "carne",
    "limao": "hortifruti",
    "manga": "hortifruti",
    "hortifruti": "hortifruti",
    "sabonete": "higiene",
    "colgate": "higiene",
    "barb": "higiene",
    "limpeza": "limpeza",
    "colorifico": "mercearia",
    "requeijao": "mercearia",
    "arroz": "mercearia",
    "feijao": "mercearia",
    "macarrao": "mercearia",
}


def normalize(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def category_for(product):
    text = normalize(product.get("descricao", ""))
    for label in product.get("labels_confirmados", []):
        text += " " + normalize(label)
    for word, category in MAP.items():
        if word in text:
            return category
    return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=os.environ.get("PRODUCT_LEARNING_DIR", "/var/log/pdv-product-learning"))
    args = parser.parse_args()
    path = Path(args.dir) / "products.json"
    if not path.exists():
        print(json.dumps({"updated": 0, "missing": True}))
        return
    products = json.loads(path.read_text(encoding="utf-8"))
    updated = 0
    for product in products.values():
        if product.get("categoria"):
            continue
        category = category_for(product)
        if category:
            product["categoria"] = category
            updated += 1
    path.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"updated": updated, "products": len(products)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
