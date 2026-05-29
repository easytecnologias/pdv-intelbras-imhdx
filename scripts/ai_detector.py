class YoloObjectDetector:
    def __init__(self, model_path, device="cpu", confidence=0.35, image_size=640):
        try:
            from ultralytics import YOLO
        except Exception as exc:
            raise RuntimeError("ultralytics indisponivel para AUDITOR_AI_ENABLED=1: %s" % exc)
        self.model = YOLO(model_path)
        self.device = device
        self.confidence = confidence
        self.image_size = image_size

    def detect(self, image):
        results = self.model.predict(
            source=image,
            conf=self.confidence,
            imgsz=self.image_size,
            device=self.device,
            verbose=False,
        )
        detections = []
        if not results:
            return detections
        result = results[0]
        names = getattr(result, "names", {}) or {}
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return detections
        for box in boxes:
            xyxy = box.xyxy[0].tolist()
            cls_id = int(box.cls[0].item())
            confidence = float(box.conf[0].item())
            label = str(names.get(cls_id, cls_id)).lower()
            x1, y1, x2, y2 = [float(value) for value in xyxy]
            detections.append(
                {
                    "bbox": (x1, y1, x2, y2),
                    "label": label,
                    "kind": classify_label(label),
                    "confidence": confidence,
                    "center": ((x1 + x2) / 2.0, (y1 + y2) / 2.0),
                }
            )
        return detections


def classify_label(label):
    label = label.lower().strip().replace("-", "_").replace(" ", "_")
    if label in {"person", "operador", "cliente"}:
        return "person"
    if label in {"hand", "mao", "arm", "braco"}:
        return "hand"
    if label in {"basket", "cesta", "bag", "sacola", "handbag", "backpack", "suitcase"}:
        return "basket"
    if label in {"scanner", "barcode_scanner", "balanca", "scale"}:
        return "scanner"
    product_like = {
        "bottle",
        "cup",
        "banana",
        "apple",
        "orange",
        "broccoli",
        "carrot",
        "sandwich",
        "hot_dog",
        "pizza",
        "donut",
        "cake",
        "book",
        "cell_phone",
        "remote",
        "toothbrush",
        "produto",
        "product",
        "item",
    }
    if label in product_like:
        return "product"
    ignored = {"chair", "tv", "keyboard", "mouse", "laptop", "sink", "refrigerator"}
    if label in ignored:
        return "other"
    return "product"
