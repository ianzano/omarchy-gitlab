#!/usr/bin/env python3
"""Render the real list with fictional demo fixtures; never load account data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='pipelines-preview-') as folder:
        root = Path(folder)
        for module in ('Ui', 'Commons'):
            (root / module).symlink_to(Path('/usr/share/omarchy/shell') / module, target_is_directory=True)
        (root / 'runtime').mkdir(mode=0o700)
        (root / 'home').mkdir()
        for component in ('PipelineView.qml', 'MomentumList.qml', 'Avatar.qml'):
            shutil.copy(PROJECT / component, root / component)
        shutil.copytree(PROJECT / 'i18n', root / 'i18n')
        (root / 'shell.qml').write_text('''
import QtQuick
import QtQuick.Window
import Quickshell
import qs.Commons
import qs.Commons as Commons
ShellRoot {
    id: test
    property bool loadMoreClicked: false
    property double clock: Date.now()
    Component.onCompleted: {
        Commons.Color.background = "#141b25"
        Commons.Color.foreground = "#e4eaf3"
        Commons.Color.accent = "#8ab8f4"
        Commons.Color.urgent = "#ed9292"
        Commons.Color.muted = "#8998ac"
        Style.fontFamily = "JetBrainsMono Nerd Font"
        Style.fontBaseSize = 14
        Style.cornerRadius = 8
    }
    function row(id, project, title, branch, status, minutes, duration, stages) {
        return {canUpdate: true, retryable: status === "failed", isTag: branch === "v2.0.0", id: String(id), project: project, title: title, ref: branch, sha: "0123456789ab",
            status: status, createdAt: clock - minutes * 60000, startedAt: clock - minutes * 60000,
            finishedAt: status === "running" ? 0 : clock - 60000, duration: duration,
            author: "Demo User", username: "demo", stagesKnown: true,
            stages: stages.map((s, i) => ({name: ["composer", "test", "deploy", "publish"][i], status: s}))}
    }
    function find(name, node) {
        if (node.objectName === name) return node
        for (var i = 0; i < node.children.length; i++) {
            var result = find(name, node.children[i])
            if (result) return result
        }
        return null
    }
    Window {
        id: window
        Rectangle { anchors.fill: parent; color: Commons.Color.background; radius: Style.cornerRadius; border.width: 1; border.color: "#30435c" }
        width: Style.space(1040); height: Style.space(800); visible: true; color: Commons.Color.background
        PipelineView {
            id: view
            anchors.fill: parent
            connected: true
            canLoadMore: true
            onLoadMoreRequested: test.loadMoreClicked = true
            projectCount: 3
            fetchedAt: test.clock
            pipelines: [
                test.row(1007, "demo / Example App", "Add example dashboard", "main", "running", 4, 193, ["success", "running", "created"]),
                test.row(1006, "demo / Example Website", "Update demo page title", "stage", "success", 9, 322, ["success", "success"]),
                test.row(1005, "demo / Example App", "Polish example navigation", "stage", "success", 37, 354, ["success", "success"]),
                test.row(1004, "demo / Example API", "Run example API tests", "main", "failed", 41, 178, ["success", "failed", "skipped"]),
                test.row(1003, "demo / Example Website", "Add demo search field", "stage", "success", 60, 382, ["success", "success"]),
                test.row(1002, "demo / Example API", "Publish example release", "v2.0.0", "manual", 120, 301, ["success", "success", "manual"]),
                test.row(1001, "demo / Example App", "Refresh demo documentation", "main", "success", 360, 1692, ["success", "success", "success"])
            ]
        }
    }
    Timer {
        interval: 600; running: true
        onTriggered: {
            if (view.visibleRows.length !== 7) throw new Error("initial model")
            view.tab = 3
            if (view.visibleRows.length !== 1 || view.visibleRows[0].status !== "failed") throw new Error("failed filter")
            view.tab = 1
            if (view.visibleRows.length !== 1 || view.visibleRows[0].status !== "running") throw new Error("active filter")
            view.tab = 4
            if (view.visibleRows.length !== 6) throw new Error("branch filter")
            view.tab = 5
            if (view.visibleRows.length !== 1 || !view.visibleRows[0].isTag) throw new Error("tag filter")
            view.tab = 0
            if (!view.latestForRef(view.pipelines[0]) || view.latestForRef(view.pipelines[6])) throw new Error("latest badges")
            var filter = test.find("filter", view)
            filter.text = "Example Website"
            if (view.visibleRows.length !== 2) throw new Error("project search")
            filter.text = ""
            test.find("loadMore", view).clicked()
            if (!test.loadMoreClicked) throw new Error("load more button signal")
            console.log("FILTERS_PASSED")
            test.find("pipelines", view).currentIndex = -1
            window.contentItem.grabToImage(function(result) {
                if (!result.saveToFile(Quickshell.env("PIPELINES_PREVIEW"))) throw new Error("save")
                console.log("PREVIEW_SAVED")
                // Network-provided markup must stay literal in the actual delegate.
                var rows = view.pipelines.slice()
                rows[0] = Object.assign({}, rows[0], {title: '<img src="https://example.invalid/tracker">'})
                view.pipelines = rows
                verify.restart()
            })
        }
    }
    Timer {
        id: verify; interval: 100
        onTriggered: {
            var label = test.find("title-1007", view)
            if (!label || label.textFormat !== Text.PlainText || label.text.indexOf("<img") !== 0) throw new Error("unsafe markup")
            console.log("PLAIN_TEXT_PASSED")
            Commons.Color.background = "#f4f6fa"
            Commons.Color.foreground = "#242a32"
            Commons.Color.accent = "#245992"
            Commons.Color.muted = "#637287"
            Commons.Color.urgent = "#b53843"
            themeVerify.restart()
        }
    }
    Timer {
        id: themeVerify; interval: 100
        onTriggered: {
            var input = test.find("filter", view)
            var label = test.find("title-1007", view)
            if (String(input.color) !== String(Commons.Color.foreground) || String(label.color) !== String(Commons.Color.accent)) throw new Error("theme bindings")
            console.log("THEME_PASSED")
            Commons.Color.background = "#141b25"
            Commons.Color.foreground = "#e4eaf3"
            Commons.Color.accent = "#8ab8f4"
            Commons.Color.muted = "#8998ac"
            Commons.Color.urgent = "#ed9292"
            var rows = view.pipelines.slice()
            rows[0] = Object.assign({}, rows[0], {title: "Add example dashboard"})
            view.pipelines = rows
            popupPreview.restart()
        }
    }
    Timer {
        id: popupPreview; interval: 100
        onTriggered: {
            view.jobViews = {"1007": {loading: false, jobs: [
                {id: "gid://gitlab/Ci::Build/9", name: "composer:prod", stage: "composer", status: "success", playable: false, retryable: true}
            ]}}
            view.showJobs("1007", "composer", test.find("stage-1007-composer", view))
            popupGrab.restart()
        }
    }
    Timer {
        id: popupGrab; interval: 150
        onTriggered: {
            window.contentItem.grabToImage(function(result) {
                if (!result.saveToFile(Quickshell.env("PIPELINES_PREVIEW").replace(".png", "-stage.png"))) throw new Error("stage preview save")
                console.log("STAGE_PREVIEW_SAVED")
                Qt.quit()
            })
        }
    }
    Timer { interval: 5000; running: true; onTriggered: { console.error("preview timeout"); Qt.quit() } }
}
''')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QPA_PLATFORMTHEME='',
                   QT_QUICK_BACKEND='software', HOME=str(root / 'home'),
                   XDG_CONFIG_HOME=str(root / 'home/config'), XDG_RUNTIME_DIR=str(root / 'runtime'),
                   PIPELINES_PREVIEW=str(PROJECT / 'preview.png'))
        result = subprocess.run(['quickshell', '-p', str(root / 'shell.qml')], env=env,
                                capture_output=True, text=True, timeout=10)
        output = result.stdout + result.stderr
        if result.returncode or 'THEME_PASSED' not in output or 'STAGE_PREVIEW_SAVED' not in output or 'WARN scene:' in output:
            raise SystemExit(output)
        print('Real QML passed: status/branch/tag filters, latest badges, search, literal markup, live dark-to-light theme change. Saved preview.png.')


if __name__ == '__main__':
    main()
