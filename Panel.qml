import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

Ui.Panel {
    id: root
    moduleName: "ianzano.pipelines"
    ipcTarget: moduleName
    manageIpc: false
    IpcHandler {
        target: root.moduleName
        function open(): void { root.open() }
        function close(): void { root.close() }
        function toggle(): void { root.toggle() }
        function refresh(): void { root.refresh() }
        function status(): string {
            var rows = root.snapshot.pipelines
            var visibleRows = panel.loadedContent ? panel.loadedContent.pipelines : []
            return JSON.stringify({opened: root.opened, busy: fetcher.running, rows: rows.length,
                photos: rows.filter(row => !!row.avatar).length, actionsAllowed: rows.filter(row => !!row.canUpdate).length,
                viewPhotos: visibleRows.filter(row => !!row.avatar).length, viewActionsAllowed: visibleRows.filter(row => !!row.canUpdate).length,
                errorCode: root.errorCode})
        }
    }
    readonly property string backend: decodeURIComponent(Qt.resolvedUrl("backend.py").toString().replace(/^file:\/\//, ""))
    readonly property string hostname: String(setting("hostname", "gitlab.com")).trim()
    property var snapshot: ({pipelines: [], projects: 0, failedProjects: 0, failedStages: 0, fetchedAt: 0})
    property string errorMessage: ""
    property string errorCode: ""
    property bool connected: false
    property double nextRefreshAt: 0
    property double retryAt: 0
    property int requestedCount: 20
    property double clockNow: Date.now()
    readonly property bool refreshAllowed: clockNow >= nextRefreshAt
    property var jobViews: ({})
    property string queuedJobs: ""
    property string lastJobsPipeline: ""
    property string operationNotice: ""
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }
    function refresh(more) {
        if (fetcher.running || actionProcess.running || Date.now() < retryAt || (!more && Date.now() < nextRefreshAt)) return
        fetcher.requestHost = hostname
        fetcher.requestCount = requestedCount
        fetcher.received = false
        fetcher.running = true
    }
    function pipelineUrl(project, ident) {
        if (!/^[a-zA-Z0-9][a-zA-Z0-9.:-]*$/.test(hostname) || !/^\d+$/.test(ident)) return ""
        return "https://" + hostname + "/" + project.split("/").map(encodeURIComponent).join("/") + "/-/pipelines/" + ident
    }
    function setJobs(ident, data) {
        var next = Object.assign({}, jobViews)
        next[ident] = data
        jobViews = next
    }
    function loadJobs(ident) {
        if (jobsProcess.running) { queuedJobs = ident; return }
        lastJobsPipeline = ident
        setJobs(ident, {loading: true, jobs: (jobViews[ident] || {}).jobs || [], message: ""})
        jobsProcess.ident = ident
        jobsProcess.requestHost = hostname
        jobsProcess.running = true
    }
    function execute(operation, ident, job) {
        if (actionProcess.running || Date.now() < retryAt) return
        operationNotice = ""
        actionProcess.operation = operation
        actionProcess.ident = ident
        actionProcess.jobId = job || ""
        actionProcess.requestHost = hostname
        actionProcess.succeeded = false
        actionProcess.received = false
        actionProcess.running = true
    }
    onOpenedChanged: if (opened) refresh()
    onHostnameChanged: {
        snapshot = {pipelines: [], projects: 0, failedProjects: 0, failedStages: 0, fetchedAt: 0}
        connected = false
        errorMessage = ""
        errorCode = ""
        nextRefreshAt = 0
        retryAt = 0
        requestedCount = 20
        jobViews = ({})
        operationNotice = ""
        if (opened) refresh()
    }
    Timer {
        interval: 1000
        running: root.opened
        repeat: true
        onTriggered: root.clockNow = Date.now()
    }
    Timer {
        interval: Math.max(60, Number(root.setting("refreshIntervalSec", 60)) || 60) * 1000
        running: root.opened && Number(root.setting("refreshIntervalSec", 60)) > 0 && root.errorCode !== "auth"
        repeat: true
        onTriggered: if (!fetcher.running && root.refreshAllowed) root.refresh()
    }
    Process {
        id: fetcher
        property string requestHost: "gitlab.com"
        property int requestCount: 20
        property bool received: false
        command: ["python3", root.backend, "list", "--host", requestHost, "--count", String(requestCount)]
        stdout: StdioCollector {
            onStreamFinished: {
                if (fetcher.requestHost !== root.hostname) return
                fetcher.received = true
                try {
                    var result = JSON.parse(text)
                    root.clockNow = Date.now()
                    root.nextRefreshAt = Number(result.nextRefreshAt || result.retryAt || 0)
                    root.retryAt = Number(result.retryAt || 0)
                    if (result.ok) {
                        root.snapshot = result
                        root.connected = true
                        root.errorMessage = result.message || ""
                        root.errorCode = result.code || ""
                    } else {
                        root.errorMessage = result.message || strings.t("fetchError")
                        root.errorCode = result.code || "response"
                    }
                } catch (error) { root.errorMessage = strings.t("fetchError"); root.errorCode = "response" }
            }
        }
        onExited: function(code, status) {
            if (!received && requestHost === root.hostname) {
                root.errorMessage = strings.t("fetchError")
                root.errorCode = "response"
            }
            if (requestHost !== root.hostname && root.opened) Qt.callLater(root.refresh)
        }
    }
    Process {
        id: jobsProcess
        property string ident: ""
        property string requestHost: "gitlab.com"
        command: ["python3", root.backend.replace(/backend\.py$/, "operations.py"), "jobs", "--host", requestHost, "--pipeline", ident]
        stdout: StdioCollector {
            onStreamFinished: {
                if (jobsProcess.requestHost !== root.hostname) return
                try {
                    var result = JSON.parse(text)
                    root.setJobs(jobsProcess.ident, {loading: false, jobs: result.jobs || [], message: result.message || "", more: !!result.more})
                    if (result.retryAt) {
                        root.retryAt = Math.max(root.retryAt, Number(result.retryAt))
                        root.nextRefreshAt = Math.max(root.nextRefreshAt, root.retryAt)
                    }
                } catch (error) { root.setJobs(jobsProcess.ident, {loading: false, jobs: [], message: strings.t("fetchError")}) }
            }
        }
        onExited: {
            if (requestHost === root.hostname && (root.jobViews[ident] || {}).loading)
                root.setJobs(ident, {loading: false, jobs: [], message: strings.t("fetchError")})
            if (root.queuedJobs) {
                var queued = root.queuedJobs
                root.queuedJobs = ""
                Qt.callLater(function() { root.loadJobs(queued) })
            }
        }
    }
    Process {
        id: actionProcess
        property string operation: ""
        property string ident: ""
        property string jobId: ""
        property string requestHost: "gitlab.com"
        property bool succeeded: false
        property bool received: false
        command: ["python3", root.backend.replace(/backend\.py$/, "operations.py"), operation, "--host", requestHost, "--pipeline", ident, "--job", jobId]
        stdout: StdioCollector {
            onStreamFinished: {
                if (actionProcess.requestHost !== root.hostname) return
                actionProcess.received = true
                try {
                    var result = JSON.parse(text)
                    actionProcess.succeeded = !!result.ok
                    root.operationNotice = result.ok ? strings.t("actionAccepted") : ""
                    root.errorMessage = result.ok ? "" : result.message || strings.t("actionError")
                    root.errorCode = result.ok ? "" : result.code || "action"
                    if (result.retryAt) {
                        root.retryAt = Math.max(root.retryAt, Number(result.retryAt))
                        root.nextRefreshAt = Math.max(root.nextRefreshAt, root.retryAt)
                    }
                } catch (error) { root.errorMessage = strings.t("actionError") }
            }
        }
        onExited: {
            if (requestHost !== root.hostname) return
            if (!received) root.errorMessage = strings.t("actionUncertain")
            if (succeeded) {
                root.nextRefreshAt = 0
                Qt.callLater(function() {
                    root.refresh(true)
                    if (root.lastJobsPipeline) root.loadJobs(root.lastJobsPipeline)
                })
            }
        }
    }
    Process {
        id: login
        command: ["omarchy", "launch", "terminal", "python3", root.backend, "login", "--host", root.hostname]
    }
    Ui.WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: "󰮠"
        tooltipText: strings.t("pipelines")
        active: root.snapshot.pipelines.some(function(p) { return p.status === "running" })
        activeColor: Commons.Color.accent
        onPressed: function(code) { if (code === Qt.MiddleButton) root.refresh(); else root.toggle() }
    }
    Ui.KeyboardPanel {
        id: panel
        anchorItem: button
        owner: root
        bar: root.bar
        open: root.opened
        padding: 0
        focusTarget: loadedContent
        contentWidth: fittedContentWidth(Style.space(1040))
        contentHeight: cappedContentHeight(Style.space(800))
        content: Component {
            PipelineView {
                anchors.fill: parent
                pipelines: root.snapshot.pipelines
                projectCount: root.snapshot.projects
                failedProjects: root.snapshot.failedProjects
                failedStages: root.snapshot.failedStages
                fetchedAt: root.snapshot.fetchedAt
                loading: fetcher.running
                refreshAllowed: root.refreshAllowed
                refreshWait: Math.max(0, Math.ceil((root.nextRefreshAt - root.clockNow) / 1000))
                loadMoreAllowed: root.clockNow >= root.retryAt
                canLoadMore: !!root.snapshot.canLoadMore
                connected: root.connected
                errorMessage: root.errorMessage
                errorCode: root.errorCode
                operationBusy: actionProcess.running
                operationNotice: root.operationNotice
                jobViews: root.jobViews
                onRefreshRequested: root.refresh()
                onJobsRequested: function(ident) { root.loadJobs(ident) }
                onActionRequested: function(operation, ident, job) { root.execute(operation, ident, job) }
                onLoadMoreRequested: {
                    if (!fetcher.running && Date.now() >= root.retryAt) {
                        root.requestedCount += 20
                        root.refresh(true)
                    }
                }
                onDismissRequested: root.close()
                onLoginRequested: if (!login.running) { login.running = true; root.close() }
                onPipelineRequested: function(project, ident) {
                    var url = root.pipelineUrl(project, ident)
                    if (url) Qt.openUrlExternally(url)
                }
            }
        }
    }
}
