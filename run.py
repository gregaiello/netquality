"""Launch the NetQuality GUI:  python run.py"""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()  # the test engine runs in a child process (needed for the frozen .exe)
    from netquality.app import main
    main()
