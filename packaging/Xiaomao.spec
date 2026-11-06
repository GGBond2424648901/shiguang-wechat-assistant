# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import tomllib
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH).parent
icon = root / "xiaomao/ui_assets/app.ico"
version = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]
version_tuple = (*map(int, version.split(".")), 0)
version_info = VSVersionInfo(FixedFileInfo(filevers=version_tuple, prodvers=version_tuple), [
    StringFileInfo([StringTable("080404B0", [
        StringStruct("ProductName", "拾光微信聊天助手"),
        StringStruct("FileDescription", "Shiguang WeChat Assistant"),
        StringStruct("FileVersion", version),
        StringStruct("ProductVersion", version),
    ])]),
    VarFileInfo([VarStruct("Translation", [0x0804, 1200])]),
])
data = collect_data_files("rapidocr", includes=["config.yaml", "default_models.yaml", "models/*.onnx"])
data += [(str(root / "xiaomao/ui_assets/表情包收集说明.txt"), "xiaomao/ui_assets")]
data += [(str(root / "xiaomao/_vendor/dafeiyu" / name), "xiaomao/_vendor/dafeiyu")
         for name in ("persona-wechat.md", "persona-v2.1.md", "LICENSE", "PROVENANCE.md")]
data += [(str(p), "xiaomao/ui_assets") for p in (root / "xiaomao/ui_assets").glob("*") if p.suffix in (".ico", ".png", ".jpg", ".webp")]
analysis = Analysis(
    [str(root / "packaging/entry.py")], pathex=[str(root)], binaries=collect_dynamic_libs("onnxruntime"),
    datas=data, hiddenimports=["imageio_ffmpeg.binaries", "onnxruntime", "win32timezone"],
    excludes=["pytest", "IPython", "matplotlib", "torch", "tensorflow", "paddle", "onnx", "scipy",
              "rapidocr.inference_engine.pytorch", "rapidocr.inference_engine.paddle",
              "rapidocr.inference_engine.openvino", "rapidocr.inference_engine.tensorflow"],
    noarchive=False,
)
# Skip unrelated executables supplied by dependency wheels. The app uses
# OpenCV for static images and never opens a video-capture device.
def distributed(entry):
    name = Path(entry[0]).name.lower()
    return not (name.startswith("ffmpeg") and name.endswith(".exe") or name.startswith("opencv_videoio_ffmpeg")
                or name in ("direct_url.json", "uv_cache.json"))

pyz = PYZ(analysis.pure)
common = dict(exclude_binaries=True, upx=False, icon=str(icon), version=version_info)
app = EXE(pyz, analysis.scripts, [], name="Xiaomao", console=False, **common)
cli = EXE(pyz, analysis.scripts, [], name="XiaomaoCLI", console=True, **common)
bundle = COLLECT(app, cli, [e for e in analysis.binaries if distributed(e)],
                 [e for e in analysis.datas if distributed(e)], name="Xiaomao", upx=False)
