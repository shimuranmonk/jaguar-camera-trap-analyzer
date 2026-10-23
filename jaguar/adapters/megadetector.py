import hashlib
import importlib.metadata
import os
import tempfile
from pathlib import Path


CLASSES = {0: "animal", 1: "person", 2: "vehicle"}


def validated_box(class_id, confidence, xyxy, width, height, threshold):
    import math
    if class_id not in CLASSES or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("The detector returned an invalid class or score.")
    if confidence < threshold:
        return None
    x1, y1, x2, y2 = map(float, xyxy)
    if not all(math.isfinite(x) for x in [x1, y1, x2, y2]) or width <= 0 or height <= 0:
        raise ValueError("The detector returned invalid coordinates.")
    x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
    y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("The detector returned an empty bounding box.")
    return {"class": CLASSES[class_id], "confidence": confidence,
        "box": [x1/width, y1/height, (x2-x1)/width, (y2-y1)/height]}


class Detector:
    def __init__(self, model_root, threshold=.2, device="auto"):
        if not 0 <= threshold <= 1:
            raise ValueError("Detection threshold must be between zero and one.")
        os.environ.setdefault("YOLO_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "jaguar-ultralytics"))
        os.environ.setdefault("YOLOV5_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "jaguar-yolov5"))
        import torch
        self.torch = torch
        self.root = Path(model_root).resolve()
        self.weights = self.root / "checkpoints/MDV6-mit-yolov9-c.ckpt"
        self.config = self.root / "checkpoints/config_v9s.yaml"
        if not self.weights.is_file() or not self.config.is_file():
            raise ValueError("Local MIT MegaDetector V6 weights and configuration are required.")
        self.threshold = threshold
        self.device = "cuda:0" if device == "auto" and torch.cuda.is_available() else ("cpu" if device == "auto" else device)
        self.fallback = None
        torch.hub.set_dir(str(self.root))
        try:
            self.load()
        except RuntimeError:
            if not self.device.startswith("cuda"):
                raise
            self.device, self.fallback = "cpu", "GPU initialization failed; CPU selected."
            self.load()
        with self.weights.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        self.version = "MDV6-mit-yolov9-c | PyTorchWildlife " + importlib.metadata.version("PytorchWildlife") + " | " + digest

    def load(self):
        from PytorchWildlife.models.detection import MegaDetectorV6MIT
        self.backend = MegaDetectorV6MIT(device=self.device, version="MDV6-mit-yolov9-c")
        self.backend.cfg.task.nms.min_confidence = self.threshold
        self.backend._load_model(weights=str(self.weights), device=self.device, url=self.backend.url)
        self.backend.model.eval()

    def infer(self, image):
        tensor, _, reverse = self.backend.transform(image)
        tensor, reverse = tensor.to(self.device)[None], reverse.to(self.device)[None]
        with self.torch.inference_mode():
            outputs = self.backend.post_proccess(self.backend.model(tensor), reverse)
        return self.backend.results_generation(outputs, "local-image")["detections"]

    def extract(self, path):
        from PIL import Image
        with Image.open(path) as original:
            image = original.convert("RGB")
        width, height = image.size
        try:
            output = self.infer(image)
        except RuntimeError as error:
            if not self.device.startswith("cuda"):
                raise
            self.device, self.fallback = "cpu", "GPU inference failed; image retried on CPU."
            self.torch.cuda.empty_cache()
            self.load()
            output = self.infer(image)
        boxes = []
        for class_id, confidence, xyxy in zip(output.class_id, output.confidence, output.xyxy):
            box = validated_box(int(class_id), float(confidence), xyxy, width, height, self.threshold)
            if box:
                boxes.append(box)
        return {"model_version": self.version, "device": self.device, "batch_size": 1, "fallback": self.fallback,
            "threshold": self.threshold, "coordinate_format": "normalized_xywh", "detections": boxes,
            "outcome": "accepted_detection" if boxes else "no_accepted_detection"}
