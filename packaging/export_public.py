"""Create a fresh public tree from reviewed files; never copy a checkout or its history."""
import argparse
import shutil
from zipfile import ZIP_DEFLATED, ZipFile
from pathlib import Path
from brand_assets import create_assets, NAMES

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "packaging" / "public-files.txt"


def review_names(files):
    forbidden={'.git','.codex','runtime','config','build','dist','design','assets','node_modules','.venv'}
    for name in files:
        if (name.is_absolute() or '..' in name.parts or any(part.lower() in forbidden for part in name.parts)
                or name.suffix.lower() in ('.dpapi','.sqlite','.db','.log','.env')
                or name.name.lower().endswith(('.sqlite3','-wal','-shm'))):
            raise ValueError(f'Private or development-only path is not publishable: {name}')


def export(destination: Path, archive: Path | None = None):
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Use a new output directory for every export")
    names = MANIFEST.read_text(encoding="utf-8").splitlines()
    files = [Path(name.strip()) for name in names if name.strip() and not name.startswith("#")]
    review_names(files)
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
    # Preserve the anime icon/avatar explicitly approved for this product.
    create_assets(destination/'xiaomao/ui_assets')
    # One minimal factory persona serves both the editor and missing-profile fallback.
    config = destination / "config"
    (config / "presets").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(destination / "xiaomao/default_persona.md", config / "persona.md")
    (config / "presets/xiaoling.yml").write_text(
        'keywords: [拾光]\nprompts:\n  - role: system\n    content: "{persona}"\nformat_user_prompt: "{prompt}"\n',
        encoding="utf-8",
    )
    if archive:
        with ZipFile(archive, "w", ZIP_DEFLATED) as package:
            generated=[Path('xiaomao/ui_assets')/name for name in NAMES]
            for name in sorted(set([*files,*generated,Path(".gitignore"),Path("config/persona.md"),Path("config/presets/xiaoling.yml")])):
                package.write(destination / name, name)
    print(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    export(args.destination, args.archive)
