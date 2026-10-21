"""Build a reproducible Windows onedir release, optionally with a cached model."""

import argparse
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--model-source", type=Path)
    parser.add_argument("--without-model", action="store_true")
    parser.add_argument("--no-archive", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("请在 Windows 64 位 Python 环境中构建")
    output = args.output.resolve()
    release = output / "SubtitleApp"
    if release.exists():
        parser.error(f"输出目录已存在，请选择新的 --output 路径：{release}")
    model = args.model_source
    if not args.without_model:
        if model is None:
            from faster_whisper.utils import download_model
            cache = Path(os.environ["LOCALAPPDATA"]) / "SubtitleApp" / "models"
            model = Path(download_model("small", cache_dir=str(cache), local_files_only=True))
        if not all((model / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")):
            parser.error("small 模型目录不完整，需要 model.bin、config.json、tokenizer.json 和 vocabulary.txt")
    (ROOT / "build").mkdir(exist_ok=True)
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    QSvgRenderer(str(ROOT / "packaging/app-icon.svg")).render(painter, QRectF(0, 0, 256, 256))
    painter.end()
    if not image.save(str(ROOT / "build/app-icon.ico")):
        raise RuntimeError("无法生成 EXE 图标")
    # Developer tools can prepend unrelated DLLs (for example Poppler's ICU) to
    # PATH. Do not let the dependency collector package those instead of the
    # Windows/Qt dependencies used by this application.
    build_env = os.environ.copy()
    windows = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
    build_env["PATH"] = os.pathsep.join(map(str, (Path(sys.executable).parent, Path(sys.base_prefix), windows / "System32", windows)))
    for name in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        build_env.pop(name, None)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--distpath", str(output),
                    "--workpath", str(ROOT / "build/pyinstaller"),
                    str(ROOT / "packaging/SubtitleApp.spec")], cwd=ROOT, env=build_env, check=True)
    if not args.without_model:
        shutil.copytree(model, release / "models/small", symlinks=False)
    shutil.copyfile(ROOT / "packaging/便携版使用说明.md", release / "使用说明.md")
    shutil.copyfile(ROOT / "packaging/THIRD_PARTY.md", release / "THIRD_PARTY.md")
    shutil.copytree(ROOT / "packaging/licenses", release / "licenses")
    shutil.copytree(ROOT / "docs", release / "docs")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    metadata = {"version": version, "platform": "windows-x86_64", "bundled_model": "small" if not args.without_model else None,
                "python": sys.version, "source": "https://github.com/luoyeguishanhe/-"}
    (release / "release.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    dependencies = sorted(f"{dist.metadata['Name']}=={dist.version}" for dist in distributions())
    (release / "build-dependencies.txt").write_text("\n".join(dependencies) + "\n", encoding="utf-8")
    if not args.no_archive:
        archive = shutil.make_archive(str(output / f"SubtitleApp-{version}-win64"), "zip", output, "SubtitleApp")
        print(archive)
    print(f"构建完成：{release / 'SubtitleApp.exe'}")


if __name__ == "__main__":
    main()
