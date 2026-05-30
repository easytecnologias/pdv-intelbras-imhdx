#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path


NEGATIVE_TERMS = (
    "nao tem",
    "nao ta",
    "nao esta",
    "nao aparece",
    "nao vejo",
    "sem produto",
    "imagem ruim",
    "produto nao visivel",
)
MENU_TERMS = {
    "status",
    "data",
    "caixa",
    "cupom",
    "dinheiro",
    "menu",
    "ultimo cupom",
    "buscar produto",
    "foto produto",
    "ensinar produtos",
    "produto mais vendido",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=os.environ.get("PRODUCT_LEARNING_DIR", "/var/log/pdv-product-learning"))
    return parser.parse_args()


def normalize(value):
    return str(value or "").strip().lower()


def is_bad_label(row):
    raw = normalize(row.get("raw_answer"))
    label = normalize(row.get("item_label"))
    desc = normalize(row.get("descricao"))
    visibility = normalize(row.get("visibility"))
    if visibility == "produto_nao_visivel":
        return True
    if raw in MENU_TERMS or label in MENU_TERMS:
        return True
    if raw.startswith("nao ") or label.startswith("nao "):
        return True
    category = normalize(row.get("category"))
    if category and category not in ("sem_produto", "imagem_ruim"):
        return False
    if label and desc:
        words = [word for word in label.split() if len(word) >= 4]
        if words and not any(word in desc for word in words):
            return True
    return any(term in raw or term in label for term in NEGATIVE_TERMS)


def read_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def main():
    args = parse_args()
    root = Path(args.dir)
    products_path = root / "products.json"
    labels_path = root / "labels.jsonl"
    products = {}
    if products_path.exists():
        products = json.loads(products_path.read_text(encoding="utf-8"))

    bad_codes = {str(row.get("code") or "") for row in read_jsonl(labels_path) if is_bad_label(row)}
    bad_codes.discard("")
    removed = []
    for code in sorted(bad_codes):
        if code in products:
            removed.append({"code": code, "descricao": products[code].get("descricao", "")})
            products.pop(code, None)

    products_path.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"removed": removed, "products_left": len(products)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
