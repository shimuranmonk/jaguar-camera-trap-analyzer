"""Small PyInstaller launcher for the bundled interpreter and isolated workers."""
import os
import subprocess
import sys
from pathlib import Path


def main():
    root=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    runtime=root/'runtime/pythonw.exe'
    environment=dict(os.environ,JAGUAR_RESOURCE_ROOT=str(root/'resources'))
    command=[str(runtime),'-m','jaguar',*sys.argv[1:]]
    if '--smoke-test' in sys.argv:
        return subprocess.run(command,cwd=root,env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
    subprocess.Popen(command,cwd=root,env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
