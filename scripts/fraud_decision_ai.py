def empty_ai_summary(enabled=False, reason="ai desativada"):
    return {
        "ai_enabled": bool(enabled),
        "objects_detected": 0,
        "product_detected": False,
        "hand_detected": False,
        "person_detected": False,
        "basket_detected": False,
        "scanner_interaction": False,
        "track_id": "",
        "ai_confidence": 0.0,
        "ai_reason": reason,
        "product_count": 0,
    }


def build_ai_context(tracks, virtual_lines=None, roi=None):
    summary = empty_ai_summary(True, "sem objetos detectados")
    summary["objects_detected"] = len(tracks)
    if not tracks:
        return summary

    product_tracks = [track for track in tracks if track.get("kind") == "product"]
    hand_tracks = [track for track in tracks if track.get("kind") == "hand"]
    person_tracks = [track for track in tracks if track.get("kind") == "person"]
    basket_tracks = [track for track in tracks if track.get("kind") == "basket"]
    scanner_tracks = [track for track in tracks if track.get("kind") == "scanner"]

    summary["product_detected"] = bool(product_tracks)
    summary["hand_detected"] = bool(hand_tracks)
    summary["person_detected"] = bool(person_tracks)
    summary["basket_detected"] = bool(basket_tracks)
    summary["product_count"] = len(product_tracks)

    scanner_line = find_line(virtual_lines or [], "scanner")
    scanner_interaction = False
    for track in product_tracks:
        if scanner_line and bbox_near_line(track["bbox"], scanner_line["coords"], 28):
            scanner_interaction = True
        if roi and bbox_overlaps(track["bbox"], roi):
            scanner_interaction = True
    if scanner_tracks and product_tracks:
        scanner_interaction = True
    summary["scanner_interaction"] = scanner_interaction

    best = best_track(product_tracks or hand_tracks or basket_tracks or person_tracks or tracks)
    summary["track_id"] = best.get("track_id", "")
    summary["ai_confidence"] = round(float(best.get("confidence", 0.0)), 3)

    reasons = []
    if product_tracks:
        reasons.append("produto=%d" % len(product_tracks))
    if hand_tracks:
        reasons.append("mao/braco=%d" % len(hand_tracks))
    if person_tracks:
        reasons.append("pessoa=%d" % len(person_tracks))
    if basket_tracks:
        reasons.append("cesta/sacola=%d" % len(basket_tracks))
    if scanner_interaction:
        reasons.append("interacao_scanner")
    summary["ai_reason"] = ", ".join(reasons) if reasons else "objeto sem classe antifraude"
    return summary


def add_ai_payload(payload, ai_summary):
    summary = ai_summary or empty_ai_summary(False)
    payload.update(
        {
            "ai_enabled": bool(summary.get("ai_enabled")),
            "objects_detected": int(summary.get("objects_detected", 0)),
            "product_detected": bool(summary.get("product_detected")),
            "hand_detected": bool(summary.get("hand_detected")),
            "person_detected": bool(summary.get("person_detected")),
            "basket_detected": bool(summary.get("basket_detected")),
            "scanner_interaction": bool(summary.get("scanner_interaction")),
            "track_id": summary.get("track_id", ""),
            "ai_confidence": float(summary.get("ai_confidence", 0.0)),
            "ai_reason": summary.get("ai_reason", ""),
        }
    )
    return payload


def should_ignore_without_product(subtype, ai_summary):
    if not ai_summary or not ai_summary.get("ai_enabled"):
        return False
    if ai_summary.get("product_detected"):
        return False
    return subtype in {"linhas_virtuais_sem_item", "movimento_sem_item", "consulta_sem_venda"}


def best_track(tracks):
    return sorted(tracks, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)[0]


def find_line(lines, name):
    for line in lines:
        if line.get("name") == name:
            return line
    return None


def bbox_near_line(bbox, coords, band):
    x1, y1, x2, y2 = bbox
    lx1, ly1, lx2, ly2 = coords
    left = min(lx1, lx2) - band
    right = max(lx1, lx2) + band
    top = min(ly1, ly2) - band
    bottom = max(ly1, ly2) + band
    return not (x2 < left or x1 > right or y2 < top or y1 > bottom)


def bbox_overlaps(bbox, roi):
    x1, y1, x2, y2 = bbox
    rx1, ry1, rx2, ry2 = roi
    return not (x2 < rx1 or x1 > rx2 or y2 < ry1 or y1 > ry2)
