"""PyInstaller entry: initialize streams before importing third-party libraries."""

from multiprocessing import freeze_support
import sys
import traceback


if __name__ == "__main__":
    freeze_support()
    from subtitle_app.runtime import configure_windowed_runtime
    log_path = configure_windowed_runtime()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
            from package_smoke import main
        else:
            from subtitle_app.main import main
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        if "--self-test" not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, f"程序未能启动。请保留完整解压目录，并查看日志：\n{log_path}",
                "巴别塔", 0x10)
        raise SystemExit(1)
