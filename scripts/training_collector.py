import json
from datetime import datetime


YOLO_CLASSES = {
    "product": 0,
    "hand": 1,
    "person": 2,
    "basket": 3,
    "scanner": 4,
}


class TrainingCollector:
    def __init__(self, root, station, min_interval=2.0, max_per_day=2000):
        self.root = root
        self.station = station
        self.min_interval = float(min_interval)
        self.max_per_day = int(max_per_day)
        self.last_saved = {}
        self.saved_today = 0
        self.day = ""

    def save(self, image, reason, ai_summary=None, ai_tracks=None, event_time=None):
        now = event_time or datetime.now()
        day = now.strftime("%Y%m%d")
        if day != self.day:
            self.day = day
            self.saved_today = 0
            self.last_saved = {}
        if self.saved_today >= self.max_per_day:
            return ""
        last = self.last_saved.get(reason)
        if last and (now - last).total_seconds() < self.min_interval:
            return ""

        day_dir = self.root / day
        image_dir = day_dir / "images"
        label_dir = day_dir / "labels"
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)

        stamp = now.strftime("%H%M%S_%f")[:-3]
        base = "pdv%s_%s_%s" % (self.station, reason, stamp)
        image_path = image_dir / ("%s.jpg" % base)
        label_path = label_dir / ("%s.txt" % base)
        meta_path = day_dir / "metadata.jsonl"

        image.save(str(image_path), "JPEG", quality=90)
        self._write_yolo_labels(label_path, image.size, ai_tracks or [])
        self._write_meta(meta_path, image_path, reason, ai_summary or {}, ai_tracks or [], now)

        self.last_saved[reason] = now
        self.saved_today += 1
        return str(image_path)

    def _write_yolo_labels(self, label_path, image_size, tracks):
        width, height = image_size
        lines = []
        for track in tracks:
            kind = track.get("kind")
            if kind not in YOLO_CLASSES:
                continue
            bbox = track.get("bbox")
            if not bbox:
                continue
            x1, y1, x2, y2 = bbox
            x1 = max(0.0, min(float(width), float(x1)))
            x2 = max(0.0, min(float(width), float(x2)))
            y1 = max(0.0, min(float(height), float(y1)))
            y2 = max(0.0, min(float(height), float(y2)))
            if x2 <= x1 or y2 <= y1:
                continue
            cx = ((x1 + x2) / 2.0) / float(width)
            cy = ((y1 + y2) / 2.0) / float(height)
            bw = (x2 - x1) / float(width)
            bh = (y2 - y1) / float(height)
            lines.append("%d %.6f %.6f %.6f %.6f" % (YOLO_CLASSES[kind], cx, cy, bw, bh))
        label_path.write_text("\n".join(lines), encoding="utf-8")

    def _write_meta(self, meta_path, image_path, reason, ai_summary, ai_tracks, now):
        payload = {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "image": str(image_path),
            "reason": reason,
            "ai_enabled": bool(ai_summary.get("ai_enabled")),
            "objects_detected": int(ai_summary.get("objects_detected", 0)),
            "product_detected": bool(ai_summary.get("product_detected")),
            "hand_detected": bool(ai_summary.get("hand_detected")),
            "person_detected": bool(ai_summary.get("person_detected")),
            "basket_detected": bool(ai_summary.get("basket_detected")),
            "scanner_interaction": bool(ai_summary.get("scanner_interaction")),
            "ai_reason": ai_summary.get("ai_reason", ""),
            "tracks": [
                {
                    "track_id": track.get("track_id", ""),
                    "kind": track.get("kind", ""),
                    "label": track.get("label", ""),
                    "confidence": round(float(track.get("confidence", 0.0)), 3),
                    "bbox": [round(float(value), 2) for value in track.get("bbox", ())],
                }
                for track in ai_tracks
            ],
        }
        with meta_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
