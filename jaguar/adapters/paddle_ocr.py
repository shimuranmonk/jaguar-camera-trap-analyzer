import importlib.metadata
import hashlib
import os
from pathlib import Path


class PaddleEngine:
    def __init__(self, model_root):
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        import cv2
        import numpy as np
        from paddleocr import PaddleOCR
        root = Path(model_root).resolve()
        if not (root / "det" / "inference.yml").is_file() or not (root / "rec" / "inference.yml").is_file():
            raise ValueError("Local PaddleOCR detection and recognition models are required; automatic runtime downloads are disabled.")
        self.cv2, self.np = cv2, np
        self.engine = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det", text_detection_model_dir=str(root / "det"),
            text_recognition_model_name="en_PP-OCRv5_mobile_rec", text_recognition_model_dir=str(root / "rec"),
            use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
            device="cpu", enable_mkldnn=False)
        hashes = []
        for folder in ["det", "rec"]:
            with (root / folder / "inference.pdiparams").open("rb") as handle:
                hashes.append(hashlib.file_digest(handle, "sha256").hexdigest())
        self.version = "PaddleOCR " + importlib.metadata.version("paddleocr") + " | " + ":".join(hashes)

    def extract(self, path, profile):
        image = self.cv2.imdecode(self.np.fromfile(str(path), dtype=self.np.uint8), self.cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("The image could not be decoded for OCR.")
        height, width = image.shape[:2]
        tokens = []
        for name, x1, y1, x2, y2 in profile.regions:
            left, top = int(x1 * width), int(y1 * height)
            right, bottom = int(x2 * width), int(y2 * height)
            if profile.id == "bushnell-band" and name == "band":
                # The bright footer scales independently of image dimensions in
                # some Bushnell originals. Its height anchors the mode-symbol gap.
                white = self.np.min(image[:, int(.15*width):int(.98*width)], axis=2) > 235
                rows = self.np.mean(white, axis=1) > .60
                footer = 0
                for bright in rows[::-1]:
                    if not bright:
                        break
                    footer += 1
                if 20 <= footer <= min(300, .20 * height):
                    left, top = min(int(3*footer), int(.25*width)), height-footer
            crop = image[top:bottom, left:right]
            scale = min(3.0, max(1.0, 128 / crop.shape[0]))
            crop = self.cv2.resize(crop, None, fx=scale, fy=scale, interpolation=self.cv2.INTER_CUBIC)
            for prediction in self.engine.predict(crop):
                data = prediction.json
                if callable(data):
                    data = data()
                data = data.get("res", data)
                for text, confidence, polygon in zip(data["rec_texts"], data["rec_scores"], data["rec_polys"]):
                    tokens.append({"text": text, "confidence": float(confidence), "region": name,
                        "polygon": [[float(point[0]) / scale + left, float(point[1]) / scale + top] for point in polygon]})
        return tokens
