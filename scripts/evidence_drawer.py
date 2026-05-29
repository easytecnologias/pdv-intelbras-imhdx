from PIL import ImageDraw


COLORS = {
    "product": (255, 210, 0),
    "hand": (255, 120, 70),
    "person": (80, 170, 255),
    "basket": (180, 80, 220),
    "scanner": (0, 230, 90),
    "other": (220, 220, 220),
}


def draw_ai_overlay(image, lines=None, roi=None, tracks=None, ai_summary=None):
    draw = ImageDraw.Draw(image)
    if roi:
        x1, y1, x2, y2 = roi
        for offset in range(2):
            draw.rectangle((x1 - offset, y1 - offset, x2 + offset, y2 + offset), outline=(255, 255, 0))
    for line in lines or []:
        color = line_color(line.get("name"))
        x1, y1, x2, y2 = line["coords"]
        for offset in range(-2, 3):
            draw.line((x1, y1 + offset, x2, y2 + offset), fill=color)
        draw.text((x1, max(0, y1 - 18)), line.get("name", "").upper(), fill=color)
    for track in tracks or []:
        color = COLORS.get(track.get("kind"), COLORS["other"])
        x1, y1, x2, y2 = [int(value) for value in track.get("bbox", (0, 0, 0, 0))]
        label = "#%s %s %.2f" % (
            track.get("track_id", ""),
            track.get("kind") or track.get("label", ""),
            float(track.get("confidence", 0.0)),
        )
        for offset in range(2):
            draw.rectangle((x1 - offset, y1 - offset, x2 + offset, y2 + offset), outline=color)
        draw.rectangle((x1, max(0, y1 - 14), min(image.width, x1 + len(label) * 7), y1), fill=(0, 0, 0))
        draw.text((x1 + 2, max(0, y1 - 13)), label, fill=color)
    if ai_summary and ai_summary.get("ai_enabled"):
        text = "AI: %s" % ai_summary.get("ai_reason", "")
        draw.rectangle((6, 6, min(image.width, 12 + len(text) * 7), 24), fill=(0, 0, 0))
        draw.text((10, 10), text, fill=(255, 255, 255))
    return image


def save_ai_evidence(image, lines, roi, tracks, ai_summary, path):
    draw_ai_overlay(image, lines=lines, roi=roi, tracks=tracks, ai_summary=ai_summary)
    image.save(str(path), "JPEG", quality=88)


def line_color(name):
    colors = {
        "entrada": (0, 190, 255),
        "scanner": (0, 220, 80),
        "saida": (180, 80, 190),
    }
    return colors.get(name, (255, 255, 255))
