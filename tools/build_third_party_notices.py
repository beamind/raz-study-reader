"""Collect installed production dependency licenses for offline distribution."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    lock = json.loads((ROOT / 'package-lock.json').read_text(encoding='utf-8'))
    sections = ['RAZ Study Reader\n\n' + (ROOT / 'LICENSE').read_text(encoding='utf-8'),
                (ROOT / 'NOTICE.md').read_text(encoding='utf-8'),
                (ROOT / 'docs/native-components.md').read_text(encoding='utf-8'),
                (ROOT / 'licenses/Apache-2.0.txt').read_text(encoding='utf-8')]
    count = 0
    for relative, package in sorted(lock['packages'].items()):
        if not relative or package.get('dev'):
            continue
        folder = ROOT / relative
        if not folder.is_dir():
            raise ValueError(f'Missing production package; run npm ci: {relative}')
        files = sorted(path for path in folder.iterdir() if path.is_file() and
                       path.name.lower().startswith(('license', 'licence', 'copying', 'notice')))
        if not files:
            raise ValueError(f'Production package has no license text: {relative}')
        sections.append(f"{relative} @ {package['version']}\nLicense: {package.get('license', 'see below')}\n\n" +
                        '\n\n'.join(path.read_text(encoding='utf-8-sig') for path in files))
        count += 1
    output = ROOT / 'public/legal'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'THIRD_PARTY_NOTICES.txt').write_text('\n\n' + ('\n\n' + '=' * 72 + '\n\n').join(sections) + '\n', encoding='utf-8')
    (output / 'APP-LICENSE.txt').write_text((ROOT / 'LICENSE').read_text(encoding='utf-8'), encoding='utf-8')
    # Remove only this generator's former app-license filename. Dependency MIT
    # notices remain intact in THIRD_PARTY_NOTICES.txt.
    (output / 'MIT-LICENSE.txt').unlink(missing_ok=True)
    print(f'Collected license notices for {count} installed production packages and native components.')


if __name__ == '__main__':
    main()
