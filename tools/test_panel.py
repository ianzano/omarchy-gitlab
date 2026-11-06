#!/usr/bin/env python3
"""Load the real native host and test refresh recovery through real QML processes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='pipelines-host-') as folder:
        root = Path(folder)
        for module in ('Ui', 'Commons'):
            (root / module).symlink_to(Path('/usr/share/omarchy/shell') / module, target_is_directory=True)
        shutil.copytree(PROJECT / 'i18n', root / 'i18n')
        for component in ('PipelineView.qml', 'MomentumList.qml', 'Avatar.qml'):
            shutil.copy(PROJECT / component, root / component)
        source = (PROJECT / 'Panel.qml').read_text().replace('    id: root\n', '''    id: root
    readonly property var testView: panel.loadedContent
    readonly property bool testBusy: fetcher.running
    function ensureTestView() { panel.ensureContent() }
''', 1)
        (root / 'PipelinePanel.qml').write_text(source)
        (root / 'backend.py').write_text('''import json
from pathlib import Path
import time
counter = Path(__file__).with_name('counter')
count = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(count + 1))
time.sleep(0.1)
if count <= 1:
    print(json.dumps(dict(ok=True, pipelines=[dict(id='42', project='group/project', status='success', title='<b>Literal</b>',
        ref='main', sha='abcd1234', duration=42, createdAt=1, author='A', username='a', stages=[], stagesKnown=True)],
        projects=1, failedProjects=0, failedStages=0, canLoadMore=True, fetchedAt=123)))
else:
    print(json.dumps(dict(ok=False, code='rate', message='Synthetic rate limit', retryAt=(time.time() + 120) * 1000)))
''')
        (root / 'operations.py').write_text('''import json
import sys
if sys.argv[1] == 'jobs':
    print(json.dumps(dict(ok=True, jobs=[dict(id='gid://gitlab/Ci::Build/9', name='Deploy', status='manual', stage='Deploy', playable=True, retryable=False)])))
else:
    assert sys.argv[1] == 'play-job'
    assert sys.argv[sys.argv.index('--job') + 1] == 'gid://gitlab/Ci::Build/9'
    print(json.dumps(dict(ok=True, message='Action accepted.')))
''')
        (root / 'runtime').mkdir(mode=0o700)
        (root / 'home').mkdir()
        (root / 'shell.qml').write_text('''
import QtQuick
import Quickshell
ShellRoot {
    id: test
    property int stage: 0
    Loader { id: plugin; source: "PipelinePanel.qml" }
    Timer {
        interval: 40; repeat: true; running: true
        onTriggered: {
            var p = plugin.item
            if (!p) return
            if (test.stage === 0) {
                if (p.testView) throw new Error("view should load lazily")
                if (p.pipelineUrl("group/project name", "42") !== "https://gitlab.com/group/project%20name/-/pipelines/42") throw new Error("URL encoding")
                if (p.pipelineUrl("group/project", "javascript:evil")) throw new Error("unsafe ID")
                p.ensureTestView()
                p.refresh()
                test.stage = 1
            } else if (test.stage === 1 && !p.testBusy && p.snapshot.pipelines.length) {
                if (!p.testView || !p.connected || p.testView.pipelines.length !== 1) throw new Error("snapshot binding")
                p.testView.jobsRequested("42")
                test.stage = 5
            } else if (test.stage === 5 && p.jobViews["42"] && !p.jobViews["42"].loading) {
                if (p.jobViews["42"].jobs.length !== 1) throw new Error("job process binding")
                p.testView.actionRequested("play-job", "42", "gid://gitlab/Ci::Build/9")
                test.stage = 6
            } else if (test.stage === 6 && p.operationNotice && !p.testBusy && !p.testView.operationBusy) {
                if (p.errorMessage) throw new Error("action result binding")
                if (p.requestedCount !== 20 || !p.testView.canLoadMore) throw new Error("default page size")
                p.testView.loadMoreRequested()
                if (p.requestedCount !== 40) throw new Error("load more request")
                test.stage = 2
            } else if (test.stage === 2 && !p.testBusy && p.errorMessage) {
                if (p.snapshot.pipelines.length !== 1 || p.testView.pipelines.length !== 1) throw new Error("error cleared previous rows")
                if (p.errorCode !== "rate" || p.testView.loadMoreAllowed) throw new Error("cooldown binding")
                p.refresh()
                p.testView.loadMoreRequested()
                if (p.testBusy || p.requestedCount !== 40) throw new Error("refresh bypassed cooldown")
                p.settings = {hostname: "gitlab.example.org"}
                if (p.snapshot.pipelines.length !== 0 || p.connected) throw new Error("host change kept private snapshot")
                console.log("PANEL_PASSED")
                Qt.quit()
            }
        }
    }
    Timer { interval: 5000; running: true; onTriggered: { console.error("host test timeout"); Qt.quit() } }
}
''')
        display = os.environ.get('WAYLAND_DISPLAY', 'wayland-1')
        if not display.startswith('/'):
            display = str(Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid()))) / display)
        env = dict(os.environ, WAYLAND_DISPLAY=display, QT_QPA_PLATFORM='wayland', QT_QPA_PLATFORMTHEME='',
                   QT_QUICK_BACKEND='software', HOME=str(root / 'home'),
                   XDG_CONFIG_HOME=str(root / 'home/config'), XDG_RUNTIME_DIR=str(root / 'runtime'))
        result = subprocess.run(['quickshell', '-p', str(root / 'shell.qml')], env=env,
                                capture_output=True, text=True, timeout=9)
        output = result.stdout + result.stderr
        if result.returncode or 'PANEL_PASSED' not in output or 'WARN scene:' in output:
            raise SystemExit(output)
        print('Native host passed: lazy content, process fetch, snapshot binding, rows preserved after failure, host change clears data, safe links, 20-item pagination, load-more wiring, cooldown enforcement.')


if __name__ == '__main__':
    main()
