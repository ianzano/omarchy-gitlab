#!/usr/bin/env python3
"""Compare real Qt wheel input with an unmodified native ScrollView/ListView."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix='pipelines-native-scroll-') as folder:
    root = Path(folder)
    (root / 'tests').mkdir()
    (root / 'runtime').mkdir(mode=0o700)
    shutil.copy(PROJECT / 'MomentumList.qml', root / 'MomentumList.qml')
    shutil.copy(PROJECT / 'tests/tst_native_scroll.qml', root / 'tests/tst_native_scroll.qml')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QPA_PLATFORMTHEME='', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', XDG_RUNTIME_DIR=str(root / 'runtime'))
    result = subprocess.run(['/usr/lib/qt6/bin/qmltestrunner', '-input', str(root / 'tests'), '-o', '-,txt'],
                            env=env, capture_output=True, text=True, timeout=20)
    output = result.stdout + result.stderr
    print(output)
    if result.returncode or '0 failed' not in output:
        raise SystemExit(1)
