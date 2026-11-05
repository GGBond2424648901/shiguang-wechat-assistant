"""Collect upstream distribution notices from the locked, isolated build venv."""
import argparse
import hashlib
import importlib.metadata as metadata
import shutil
import sys
import tomllib
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
GEOS = "geos-3.14.1.tar.bz2"
GEOS_SHA256 = "3c20919cda9a505db07b5216baa980bacdaa0702da715b43f176fb07eff7e716"


def collect(payload, cache):
    notices = payload / "licenses"
    notices.mkdir(exist_ok=True)
    locked = {p["name"].lower(): p for p in tomllib.loads((ROOT/"uv.lock").read_text("utf-8"))["package"]}
    sources = []
    permitted = set(locked) | {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "setuptools", "pefile", "pywin32-ctypes"}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name", "").lower().replace("_", "-")
        if name not in permitted or name == "xiaomao-wechat-assistant":
            continue
        for rel in dist.files or ():
            if ".." in rel.parts or rel.is_absolute():
                continue
            file = dist.locate_file(rel)
            legal = any(word in rel.name.lower() for word in ("license", "licence", "notice", "copying", "copyright"))
            if file.is_file() and (legal or "/licenses/" in str(rel)) and file.suffix.lower() not in (".py", ".pyc", ".pyd", ".exe"):
                target = notices / name / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
        # The exact sources of MPL Python dependencies ship beside their notices.
        if name in ("tqdm", "certifi"):
            for rel in dist.files or ():
                if rel.suffix != ".py" or rel.parts[0] != name:
                    continue
                target = notices / name / "source" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dist.locate_file(rel), target)
        if name in locked:
            source = locked[name].get("sdist",{}).get("url") or locked[name]["wheels"][0]["url"]
            sources.append(f"{name} {dist.version} {source}")
    for file in Path(sys.base_prefix).glob("LICENSE*"):
        if file.is_file():
            shutil.copyfile(file, notices / ("Python-" + file.name))
    tcl = Path(sys.base_prefix) / "tcl"
    for file in tcl.rglob("license.terms"):
        target = notices / "Tcl-Tk" / file.relative_to(tcl)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
    (notices / "sources.txt").write_text("\n".join(sorted(sources)) + "\n", "utf-8")
    for file in (ROOT / "licenses").glob("*.txt"):
        shutil.copyfile(file, notices / file.name)
    for file in ("LICENSE", "NOTICE.md", "README.md"):
        shutil.copyfile(ROOT / file, payload / file)
    shutil.copyfile(ROOT / "docs/third-party/whale-persona-MIT.txt", notices / "whale-persona-MIT.txt")
    shutil.copyfile(ROOT / "xiaomao/_vendor/dafeiyu/LICENSE", notices / "dafeiyu-MIT.txt")
    shutil.copyfile(ROOT / "xiaomao/_vendor/dafeiyu/PROVENANCE.md", notices / "dafeiyu-PROVENANCE.md")
    # Ship GEOS as separate, replaceable upstream wheel DLLs and supply source.
    import shapely
    if shapely.geos_version_string != "3.14.1":
        raise ValueError("Review GEOS source and license before packaging this version")
    cache.mkdir(exist_ok=True, parents=True)
    archive = cache / GEOS
    if not archive.exists():
        with urlopen(f"https://download.osgeo.org/geos/{GEOS}", timeout=120) as response:
            archive.write_bytes(response.read())
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != GEOS_SHA256:
        raise ValueError("GEOS source checksum failed")
    shutil.copyfile(archive, notices / GEOS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("cache", type=Path)
    args = parser.parse_args()
    collect(args.payload, args.cache)
