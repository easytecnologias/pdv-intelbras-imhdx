def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    left = max(ax1, bx1)
    top = max(ay1, by1)
    right = min(ax2, bx2)
    bottom = min(ay2, by2)
    if right <= left or bottom <= top:
        return 0.0
    inter = (right - left) * (bottom - top)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


class SimpleObjectTracker:
    def __init__(self, min_iou=0.25, max_missed=8):
        self.min_iou = min_iou
        self.max_missed = max_missed
        self.next_id = 1
        self.tracks = []

    def update(self, detections):
        used = set()
        for track in self.tracks:
            best_index = None
            best_score = 0.0
            for index, detection in enumerate(detections):
                if index in used:
                    continue
                if detection.get("kind") != track.get("kind"):
                    continue
                score = iou(track["bbox"], detection["bbox"])
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index is not None and best_score >= self.min_iou:
                detection = detections[best_index]
                used.add(best_index)
                track.update(detection)
                track["track_id"] = track["track_id"]
                track["missed"] = 0
                track["hits"] += 1
            else:
                track["missed"] += 1

        for index, detection in enumerate(detections):
            if index in used:
                continue
            track = dict(detection)
            track["track_id"] = self.next_id
            track["missed"] = 0
            track["hits"] = 1
            self.next_id += 1
            self.tracks.append(track)

        self.tracks = [track for track in self.tracks if track.get("missed", 0) <= self.max_missed]
        return [dict(track) for track in self.tracks if track.get("missed", 0) == 0]
