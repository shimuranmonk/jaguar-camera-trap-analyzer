"""Private line protocol for the isolated, offline SpeciesNet interpreter."""
import contextlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import tempfile


def emit(value):
    # Embedded Windows Python ignores encoding environment variables. ASCII
    # escaping preserves every Unicode label regardless of its console locale.
    print('JAGUAR_RESULT:' + json.dumps(value, ensure_ascii=True), flush=True)


def main():
    cache = Path(tempfile.gettempdir()) / 'jaguar-species-cache'
    cache.mkdir(exist_ok=True)
    for name in ['YOLOV5_CONFIG_DIR', 'YOLO_CONFIG_DIR', 'MPLCONFIGDIR']:
        os.environ[name] = str(cache)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    def offline(*args, **kwargs):
        raise OSError('Species identification is offline; network access is disabled.')
    socket.socket.connect = offline
    socket.socket.connect_ex = offline
    socket.create_connection = offline
    socket.getaddrinfo = offline
    try:
        config = json.loads(sys.stdin.readline())
        root = Path(config['model_root']).resolve()
        info = json.loads((root / 'info.json').read_text(encoding='utf-8'))
        if info['type'] != 'always_crop':
            raise ValueError('The current integration requires an always-crop SpeciesNet model.')
        for key in ['classifier', 'classifier_labels', 'detector', 'taxonomy', 'geofence']:
            if str(info[key]).startswith(('http:', 'https:', 'hf:', 'kaggle:')) or not (root / info[key]).is_file():
                raise ValueError('All SpeciesNet manifest assets must be existing local files: ' + key)
        with contextlib.redirect_stdout(sys.stderr):
            from speciesnet.classifier import SpeciesNetClassifier
            from speciesnet.ensemble import SpeciesNetEnsemble
            from speciesnet.utils import BBox
            from speciesnet.constants import FORMAT_VERSION
            from PIL import Image
            classifier = SpeciesNetClassifier(str(root), device='cpu')
            ensemble = SpeciesNetEnsemble(str(root))
        emit({'ready': True, 'package_version': importlib.metadata.version('speciesnet'), 'model_version': info['version'], 'format_version':FORMAT_VERSION,'transport':'ascii-json-v2'})
    except Exception as error:
        emit({'error': str(error)})
        return
    for line in sys.stdin:
        results=[]
        result=None
        detections=[]
        try:
            request = json.loads(line)
            path, targets = request['path'], request['targets']
            detections = [{'category': {'animal': '1', 'person': '2', 'vehicle': '3'}[t['class']],
                'label': 'human' if t['class'] == 'person' else t['class'], 'conf': t['confidence'], 'bbox': t['box']} for t in targets]
            with contextlib.redirect_stdout(sys.stderr):
                with Image.open(path) as original:
                    image = original.convert('RGB')
                # One crop per inference bounds memory even in crowded scenes.
                for target in targets:
                    crops = classifier.preprocess(image, [BBox(*target['box'])])
                    result = classifier.predict(path, crops, num_classes=None)
                    if result.get('failures'):
                        raise ValueError(str(result['failures']))
                    results.extend(result['classifications_list'])
                classified = [dict(d, raw_classifications=c) for d, c in zip(detections, results)]
                native = ensemble.combine([path], {path: {'detections': classified}},
                    {path: {'detections': detections}}, {path: request.get('geolocation', {})},
                    max_classifications_per_detection=None)[0]
            emit({'native': native, 'classifier_native': {'filepath': path, 'classifications_list': results}})
        except Exception as error:
            emit({'error': str(error), 'classifier_native_partial':results,
                  'classifier_failed_result':result,'detector_inputs':detections})


if __name__ == '__main__':
    main()
