"""Run the existing experimental pipeline on captured JPEGs on the GPU computer."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import uuid

from backend.app.config import ROOT, load_settings
from backend.app.reconstruction import reconstruct
from backend.app.images import decode_jpeg, MAX_UPLOAD_BYTES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', type=Path, help='Folder of JPEG keyframes copied from a capture scan.')
    args = parser.parse_args()
    settings = load_settings()
    if settings.mode != 'reconstruct':
        parser.error('Set mode = "reconstruct" in config.local.toml on the GPU machine first.')
    if not args.images.is_dir():
        parser.error('Image folder does not exist.')
    files = sorted(path for path in args.images.iterdir() if path.is_file() and path.suffix.lower() in {'.jpg', '.jpeg'})
    if not 12 <= len(files) <= settings.max_photos:
        parser.error(f'Expected between 12 and {settings.max_photos} JPEG images.')
    # A fresh immutable copy isolates the input from subsequent capture changes.
    output = ROOT / 'scans' / (datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8])
    images = output / 'images'
    images.mkdir(parents=True)
    shape = None
    try:
        for index, source in enumerate(files):
            if source.stat().st_size > MAX_UPLOAD_BYTES:
                raise ValueError(f'{source.name} exceeds the 25 MB image limit')
            data = source.read_bytes()
            _, shape = decode_jpeg(data, shape, MAX_UPLOAD_BYTES)
            (images / f'kf_{index:04d}.jpg').write_bytes(data)
        started = time.monotonic()
        result = reconstruct(images, output, settings)
        result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    except Exception as error:
        result = {'done': False, 'msg': str(error)}
    result['output_directory'] = str(output)
    (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result.get('model_ready') else 1


if __name__ == '__main__':
    sys.exit(main())
