from jaguar.app import main
from multiprocessing import freeze_support

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
