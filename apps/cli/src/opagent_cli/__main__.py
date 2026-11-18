from .main import main

if __name__ == "__main__":  # not under multiprocessing spawn, which imports this as __mp_main__
    main()
