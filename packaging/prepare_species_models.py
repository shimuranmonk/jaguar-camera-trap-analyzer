"""Prepare an official local archive for the external-detector integration."""
import argparse
import json
import os
from pathlib import Path
import tarfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',required=True)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--detector',required=True)
    args=parser.parse_args()
    detector=Path(args.detector).resolve(strict=True)
    destination=Path(args.destination).resolve()
    destination.mkdir(parents=True,exist_ok=False)
    with tarfile.open(args.archive) as archive:
        archive.extractall(destination,filter='data')
    manifest=destination/'info.json'
    original=manifest.read_text(encoding='utf-8')
    (destination/'info.upstream.json').write_text(original,encoding='utf-8')
    info=json.loads(original)
    info['detector']=os.path.relpath(detector,destination).replace('\\','/')
    manifest.write_text(json.dumps(info,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
