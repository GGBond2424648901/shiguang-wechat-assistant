"""Create a fresh public tree from reviewed files; never copy a checkout or its history."""
import argparse
import shutil
from zipfile import ZIP_DEFLATED, ZipFile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "packaging" / "public-files.txt"


def export(destination: Path, archive: Path | None = None):
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Use a new output directory for every export")
    names = MANIFEST.read_text(encoding="utf-8").splitlines()
    files = [Path(name.strip()) for name in names if name.strip() and not name.startswith("#")]
    runtime_modules = {path.relative_to(ROOT) for path in (ROOT / "xiaomao").rglob("*.py")}
    missing_modules = runtime_modules.difference(files)
    if missing_modules:
        raise ValueError(f"Review new application modules before export: {sorted(missing_modules)}")
    for name in files:
        source = ROOT / name
        if name.is_absolute() or ".." in name.parts or not source.is_file() or source.resolve() != source:
            raise ValueError(f"Unreviewed, missing, or redirected source file: {name}")
    destination.mkdir(parents=True)
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    shutil.copyfile(destination / "packaging/public.gitignore", destination / ".gitignore")
    # Supply an upstream-derived factory persona, never a user's edited persona.
    config = destination / "config"
    (config / "presets").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(destination / "xiaomao/_vendor/dafeiyu/persona-wechat.md", config / "persona.md")
    (config / "presets/xiaoling.yml").write_text(
        'keywords: [小玲, 小猫, 大肥鱼]\nprompts:\n  - role: system\n    content: "{persona}"\nformat_user_prompt: "{prompt}"\n',
        encoding="utf-8",
    )
    if archive:
        with ZipFile(archive, "w", ZIP_DEFLATED) as package:
            for name in sorted([*files, Path(".gitignore"), Path("config/persona.md"), Path("config/presets/xiaoling.yml")]):
                package.write(destination / name, name)
    print(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    export(args.destination, args.archive)
