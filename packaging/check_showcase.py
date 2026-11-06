"""Check public screenshot provenance and README media references offline."""
from pathlib import Path
import hashlib
import json
import re


def check(root: Path):
    folder=root/'docs/screenshots'
    record=json.loads((folder/'provenance.json').read_text('utf-8'))
    rows=record['screenshots']
    assert rows and len({row['file'] for row in rows})==len(rows)
    for row in rows:
        name=Path(row['file'])
        assert name.name==str(name) and name.suffix=='.png'
        assert row['origin']=='actual Windows application screenshot'
        assert row['unchanged_outside_redactions'] is True
        assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==row['output_sha256'], name
        width,height=row['source_size']
        for x0,y0,x1,y1 in row['redaction_rectangles']:
            assert 0<=x0<x1<=width and 0<=y0<y1<=height
        x0,y0,x1,y1=row['crop']
        assert 0<=x0<x1<=width and 0<=y0<y1<=height
    text=(root/'README.md').read_text('utf-8')
    screenshots=set()
    for target in re.findall(r'!\[[^\]]*\]\(([^)]+)\)',text):
        path=Path(target)
        assert not path.is_absolute() and '..' not in path.parts
        assert (root/path).is_file(), target
        if path.parent.as_posix()=='docs/screenshots':screenshots.add(path.name)
    assert screenshots.issubset({row['file'] for row in rows})
    assert '实机截图' in text and '截图介绍片' in text
    return dict(screenshots_verified=len(rows),readme_screenshots=len(screenshots),media_links_valid=True)


if __name__=='__main__':
    print(json.dumps(check(Path(__file__).resolve().parent.parent)))
