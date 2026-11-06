#!/usr/bin/env python3
"""Exercise real QML scrolling, job controls and cached avatar rendering."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib

PROJECT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='pipelines-interactions-') as folder:
        root = Path(folder)
        for module in ('Ui', 'Commons'):
            (root / module).symlink_to(Path('/usr/share/omarchy/shell') / module, target_is_directory=True)
        for name in ('PipelineView.qml', 'MomentumList.qml', 'Avatar.qml'):
            shutil.copy(PROJECT / name, root / name)
        shutil.copytree(PROJECT / 'i18n', root / 'i18n')
        def chunk(name, data):
            return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
        (root / 'avatar.png').write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\x60\x90\xc0')) + chunk(b'IEND', b''))
        (root / 'runtime').mkdir(mode=0o700)
        (root / 'home').mkdir()
        (root / 'shell.qml').write_text('''
import QtQuick
import QtQuick.Window
import Quickshell
import qs.Commons
ShellRoot {
    id: test
    property int step: 0
    property double started: 0
    property string action: ""
    property string selectedJob: ""
    function find(name, node) {
        if (node.objectName === name) return node
        for (var i = 0; i < node.children.length; i++) {
            var result = find(name, node.children[i])
            if (result) return result
        }
        return null
    }
    Window {
        visible: true; width: 1040; height: 800
        PipelineView {
            id: view
            anchors.fill: parent
            connected: true
            onActionRequested: function(operation, ident, job) { test.action = operation; test.selectedJob = job }
            onJobsRequested: function(ident) {
                var data = {}
                data[ident] = {loading: false, jobs: [
                    {id: "gid://gitlab/Ci::Build/9", name: '<img src="https://example.invalid/tracker">', stage: "Deploy", status: "manual", playable: true, retryable: false},
                    {id: "gid://gitlab/Ci::Build/10", name: "Tests", stage: "Test", status: "failed", playable: false, retryable: true}
                ]}
                view.jobViews = data
            }
            Component.onCompleted: {
                var rows = []
                for (var i = 1; i <= 30; i++) rows.push({id: String(i), project: "group/project", ref: i === 1 ? "feature/" + "a".repeat(120) : "main", title: "Pipeline " + i,
                    sha: "abcd1234", status: "failed", author: "Example User", username: "example", duration: 30,
                    createdAt: Date.now(), canUpdate: true, retryable: true, avatar: Qt.resolvedUrl("avatar.png").toString(),
                    stagesKnown: true, stages: [{name: "Deploy", status: "manual"}, {name: "Test", status: "failed"}]})
                pipelines = rows
            }
        }
    }
    Timer {
        interval: 25; repeat: true; running: true
        onTriggered: {
            var list = test.find("pipelines", view)
            if (!list || list.contentHeight < 1000) return
            if (test.step === 0) {
                var photo = test.find("avatarImage", test.find("avatar-1", view))
                if (photo.status !== Image.Ready) return
                for (var name of ["status", "pipeline", "author", "stages", "actions"]) {
                    var header = test.find("header-" + name, view)
                    var cell = test.find(name + "Column-1", view)
                    if (Math.abs(header.mapToItem(view, 0, 0).x - cell.mapToItem(view, 0, 0).x) > 1) throw new Error("header/cell alignment: " + name)
                }
                var badge = test.find("branchBadge-1", view)
                if (badge.children[0].width > badge.width || badge.children[0].elide !== Text.ElideRight) throw new Error("branch badge overflow")
                var avatar = test.find("avatar-1", view)
                var circle = test.find("stage-1-Deploy", view)
                var action = test.find("newPipeline-1", view)
                var center = avatar.mapToItem(view, 0, avatar.height / 2).y
                if (Math.abs(center - circle.mapToItem(view, 0, circle.height / 2).y) > 1 || Math.abs(center - action.mapToItem(view, 0, action.height / 2).y) > 1) throw new Error("icon row centers")
                list.flick(0, -1800)
                if (!list.flicking) throw new Error("native flick did not start")
                test.started = Date.now()
                test.step = 1
            } else if (test.step === 1) {
                if (Date.now() - test.started < 70) return
                if (!(list.contentY > 0 && list.moving)) throw new Error("native momentum did not continue")
                test.step = 2
            } else if (test.step === 2) {
                if (list.moving) return
                list.resetPosition()
                test.find("retryPipeline-1", view).clicked()
                if (test.action !== "retry-pipeline") throw new Error("pipeline retry wiring")
                test.find("newPipeline-1", view).clicked()
                if (test.action !== "new-pipeline") throw new Error("new pipeline wiring")
                test.find("stage-1-Deploy", view).clicked()
                test.step = 3
            } else if (test.step === 3) {
                var play = test.find("playJob-gid://gitlab/Ci::Build/9", view.stagePopover.contentItem)
                if (!play) return
                if (!view.stagePopover.opened || view.stagePopover.anchorItem !== test.find("stage-1-Deploy", view)) throw new Error("stage popup anchoring")
                if (test.find("pipeline-1", view).height !== Style.space(120)) throw new Error("popup expanded the pipeline row")
                if (test.find("retryJob-gid://gitlab/Ci::Build/10", view.stagePopover.contentItem)) throw new Error("wrong stage jobs")
                var name = test.find("jobName-gid://gitlab/Ci::Build/9", view.stagePopover.contentItem)
                if (name.textFormat !== Text.PlainText || name.text.indexOf("<img") !== 0) throw new Error("unsafe job text")
                play.clicked()
                if (test.action !== "play-job" || test.selectedJob !== "gid://gitlab/Ci::Build/9") throw new Error("manual job wiring")
                view.loadMoreAllowed = false
                if (play.enabled) throw new Error("job control bypasses cooldown")
                view.loadMoreAllowed = true
                test.find("stage-1-Test", view).clicked()
                test.step = 4
            } else if (test.step === 4) {
                var retry = test.find("retryJob-gid://gitlab/Ci::Build/10", view.stagePopover.contentItem)
                if (!retry) return
                if (view.selectedStage !== "Test") throw new Error("stage switching")
                retry.clicked()
                if (test.action !== "retry-job" || test.selectedJob !== "gid://gitlab/Ci::Build/10") throw new Error("job retry wiring")
                test.find("stage-1-Test", view).clicked()
                if (view.stagePopover.visible) throw new Error("second stage click did not dismiss")
                list.positionViewAtEnd()
                test.step = 5
            } else if (test.step === 5) {
                var last = test.find("stage-30-Deploy", view)
                if (!last) return
                last.clicked()
                test.step = 6
            } else if (test.step === 6) {
                if (!view.stagePopover.opened) return
                if (view.stagePopover.y < 0 || view.stagePopover.y + view.stagePopover.height > view.height) throw new Error("popover overflows panel edge")
                list.flick(0, 1800)
                test.step = 7
            } else if (test.step === 7) {
                if (list.moving) return
                if (view.stagePopover.visible) throw new Error("scrolling left a detached popover")
                console.log("INTERACTIONS_PASSED")
                Qt.quit()
            }
        }
    }
    Timer { interval: 5000; running: true; onTriggered: { console.error("interaction timeout"); Qt.quit() } }
}
''')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QPA_PLATFORMTHEME='',
                   QT_QUICK_BACKEND='software', HOME=str(root / 'home'),
                   XDG_CONFIG_HOME=str(root / 'home/config'), XDG_RUNTIME_DIR=str(root / 'runtime'))
        result = subprocess.run(['quickshell', '-p', str(root / 'shell.qml')], env=env, capture_output=True, text=True, timeout=9)
        output = result.stdout + result.stderr
        if result.returncode or 'INTERACTIONS_PASSED' not in output or 'WARN scene:' in output:
            raise SystemExit(output)
        print('QML interactions passed: native flick momentum, aligned columns/icons, clipped long badges, cached avatar, anchored stage popover, fixed row height, stage filtering/switching/dismissal, job actions, plain text and cooldown controls.')


if __name__ == '__main__':
    main()
