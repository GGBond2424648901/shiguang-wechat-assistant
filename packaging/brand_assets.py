"""Preserve the owner's explicitly approved anime branding in public builds."""
from pathlib import Path
import shutil

NAMES=('app.png','app.ico','persona-avatar.png')


def create_assets(destination: Path):
    source=Path(__file__).resolve().parent.parent/'xiaomao/ui_assets'
    destination.mkdir(parents=True,exist_ok=True)
    for name in NAMES:
        original=source/name
        if not original.is_file():
            raise FileNotFoundError(f'Missing approved brand asset: {name}')
        target=destination/name
        if original.resolve()!=target.resolve():
            shutil.copyfile(original,target)
