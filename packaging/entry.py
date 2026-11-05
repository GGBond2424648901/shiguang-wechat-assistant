"""Both the windowed app and console client use the regular package entry point."""
from multiprocessing import freeze_support

if __name__ == "__main__":
    freeze_support()
    from xiaomao.__main__ import main
    main()
