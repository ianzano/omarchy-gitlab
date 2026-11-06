#!/usr/bin/env python3
"""Copy this plugin to the user registry and enable it without moving other widgets."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

SOURCE = Path(__file__).resolve().parent
PLUGIN = 'ianzano.pipelines'


def main():
    home = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'omarchy'
    target = home / 'plugins' / PLUGIN
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    subprocess.run(['omarchy', 'plugin', 'validate', str(SOURCE)], check=True)
    if target.exists():
        backup = home / 'plugin-backups' / (PLUGIN + '-' + stamp)
        shutil.copytree(target, backup, symlinks=True)
        print('Plugin backup:', backup)
    for name in ('Panel.qml', 'PipelineView.qml', 'MomentumList.qml', 'Avatar.qml', 'backend.py', 'avatars.py', 'operations.py', 'manifest.json', 'LICENSE', 'i18n/en.json', 'i18n/de.json'):
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise RuntimeError('Refusing to replace symlink: ' + str(destination))
        fd, temporary = tempfile.mkstemp(prefix='.pipelines-', dir=destination.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write((SOURCE / name).read_bytes())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    config = home / 'shell.json'
    if config.exists():
        shutil.copy2(config, home / ('shell.json.bak.pipelines-' + stamp))
    subprocess.run(['omarchy-shell', 'shell', 'rescanPlugins'], check=True)
    for _ in range(40):
        result = subprocess.run(['omarchy', 'plugin', 'enable', PLUGIN], capture_output=True, text=True)
        if result.returncode == 0:
            print(result.stdout.strip())
            print('Installed:', target)
            return
        if 'not known' not in result.stderr and 'not ready' not in result.stderr:
            raise RuntimeError(result.stderr.strip())
        time.sleep(0.1)
    raise RuntimeError('Plugin installed; registry discovery did not finish. Enable with: omarchy plugin enable ' + PLUGIN)


if __name__ == '__main__':
    main()
