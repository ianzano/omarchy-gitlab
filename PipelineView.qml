import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

Rectangle {
    id: view
    color: "transparent"
    radius: Style.space(6)
    implicitWidth: Style.space(1040)
    implicitHeight: Style.space(760)
    property var pipelines: []
    property bool loading: false
    property bool refreshAllowed: true
    property int refreshWait: 0
    property bool canLoadMore: false
    property bool loadMoreAllowed: true
    property bool operationBusy: false
    property string operationNotice: ""
    property var jobViews: ({})
    property string selectedPipeline: ""
    property string selectedStage: ""
    readonly property var stagePopover: stagePopup
    property bool connected: false
    property string errorMessage: ""
    property string errorCode: ""
    property int projectCount: 0
    property int failedProjects: 0
    property int failedStages: 0
    property double fetchedAt: 0
    property double now: Date.now()
    property int tab: 0
    property string search: ""
    onSearchChanged: list.resetPosition()
    onTabChanged: list.resetPosition()
    readonly property color foreground: Commons.Color.foreground
    readonly property color muted: Commons.Color.muted
    readonly property color success: "#86c995"
    readonly property color progress: "#e4b76c"
    readonly property int runningCount: pipelines.filter(p => p.status === "running").length
    readonly property var visibleRows: pipelines.filter(function(p) {
        var active = ["running", "pending", "created", "preparing", "waiting_for_resource", "waiting_for_callback", "canceling"].indexOf(p.status) >= 0
        if (tab === 1 && !active) return false
        if (tab === 2 && active) return false
        if (tab === 3 && p.status !== "failed") return false
        if (tab === 4 && p.isTag) return false
        if (tab === 5 && !p.isTag) return false
        var needle = search.trim().toLowerCase()
        return !needle || [p.project, p.title, p.ref, p.sha, p.id, p.author, p.username, p.status].join(" ").toLowerCase().indexOf(needle) >= 0
    })
    signal refreshRequested()
    signal loadMoreRequested()
    signal loginRequested()
    signal pipelineRequested(string project, string pipelineId)
    signal jobsRequested(string pipelineId)
    signal actionRequested(string operation, string pipelineId, string jobId)
    signal dismissRequested()
    function showJobs(ident, stage, anchor) {
        if (stagePopup.opened && selectedPipeline === ident && selectedStage === stage) { stagePopup.close(); return }
        selectedPipeline = ident
        selectedStage = stage
        stagePopup.anchorItem = anchor || view
        stagePopup.open()
        jobsRequested(ident)
    }
    Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }
    LayoutMirroring.enabled: Commons.I18n.rightToLeft
    LayoutMirroring.childrenInherit: true
    focus: true
    Keys.onEscapePressed: dismissRequested()
    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Slash) { filter.forceActiveFocus(); event.accepted = true }
        else if (event.key === Qt.Key_R) { refreshRequested(); event.accepted = true }
        else if (event.key === Qt.Key_J || event.key === Qt.Key_Down) { list.incrementCurrentIndex(); event.accepted = true }
        else if (event.key === Qt.Key_K || event.key === Qt.Key_Up) { list.decrementCurrentIndex(); event.accepted = true }
        else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (list.currentIndex >= 0 && visibleRows[list.currentIndex]) {
                var row = visibleRows[list.currentIndex]
                pipelineRequested(row.project, row.id)
            }
            event.accepted = true
        }
    }
    Timer { interval: 15000; repeat: true; running: view.visible; onTriggered: view.now = Date.now() }
    function statusColor(status) {
        if (status === "success") return success
        if (status === "failed") return Commons.Color.urgent
        if (status === "running") return Commons.Color.accent
        if (["manual", "scheduled", "pending", "waiting_for_resource"].indexOf(status) >= 0) return progress
        return muted
    }
    function statusBackground(status) { return Util.alpha(statusColor(status), 0.14) }
    function statusSymbol(status) {
        if (status === "success") return "✓"
        if (status === "failed") return "×"
        if (status === "running") return "◔"
        if (status === "manual") return "Ⅱ"
        if (status === "skipped") return "»"
        if (status === "canceled") return "⊘"
        return "•"
    }
    function latestForRef(row) {
        return !pipelines.some(p => p.project === row.project && p.ref === row.ref && !!p.isTag === !!row.isTag &&
            (p.createdAt > row.createdAt || (p.createdAt === row.createdAt && Number(p.id) > Number(row.id))))
    }
    function statusText(status) { return strings.t("status." + status, status) }
    function duration(row) {
        var seconds = row.duration || 0
        if (row.status === "running" && row.startedAt) seconds = Math.max(seconds, (now - row.startedAt) / 1000)
        seconds = Math.max(0, Math.floor(seconds))
        return Math.floor(seconds / 3600).toString().padStart(2, "0") + ":" + Math.floor(seconds / 60 % 60).toString().padStart(2, "0") + ":" + (seconds % 60).toString().padStart(2, "0")
    }
    function age(value) {
        var minutes = Math.max(0, Math.floor((now - value) / 60000))
        if (minutes < 1) return strings.t("justNow")
        if (minutes < 60) return strings.t("minutesAgo").arg(Commons.I18n.formatNumber(minutes))
        if (minutes < 1440) return strings.t("hoursAgo").arg(Commons.I18n.formatNumber(Math.floor(minutes / 60)))
        return new Date(value).toLocaleString(Qt.locale(Commons.I18n.formatLocale), "dd MMM, HH:mm")
    }
    component Label: Text {
        textFormat: Text.PlainText
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        color: view.foreground
        elide: Text.ElideRight
    }
    component Small: Label { font.pixelSize: Style.font.caption; color: view.muted }
    component Line: Rectangle { height: 1; color: Util.alpha(view.foreground, 0.12) }
    component Action: Ui.Button {
        focusable: true
        bordered: true
        fontSize: Style.font.caption
        implicitHeight: Style.space(32)
        verticalPadding: 0
        iconSize: Style.space(14)
        opacity: enabled ? 1 : 0.4
        Layout.alignment: Qt.AlignVCenter
    }
    component IconAction: Action {
        id: iconAction
        property string glyph: ""
        implicitWidth: Style.space(32)
        horizontalPadding: 0
        iconSize: Style.space(14)
        Accessible.name: tooltipText
        Ui.OpticalGlyph {
            anchors.centerIn: parent
            width: iconAction.width - Style.space(10)
            height: iconAction.height - Style.space(10)
            text: iconAction.glyph
            paintedSize: iconAction.iconSize
            fontFamily: Style.font.family
            fontSize: Style.space(20)
            color: iconAction.foreground
        }
    }
    component StatusMark: Rectangle {
        id: mark
        property string status: ""
        implicitWidth: Style.space(24)
        implicitHeight: implicitWidth
        radius: width / 2
        color: view.statusBackground(status)
        Rectangle {
            anchors.centerIn: parent
            width: Math.round(parent.width * 2 / 3)
            height: width
            radius: width / 2
            color: view.statusColor(mark.status)
            Ui.OpticalGlyph {
                anchors.fill: parent
                text: view.statusSymbol(mark.status)
                paintedSize: parent.width * 0.63
                fontFamily: Style.font.family
                fontSize: Style.space(18)
                color: Commons.Color.background
            }
        }
    }
    component Metadata: Row {
        property string glyph: ""
        property string text: ""
        spacing: Style.space(6)
        height: Style.space(16)
        Ui.OpticalGlyph { width: Style.space(12); height: parent.height; text: parent.glyph; paintedSize: Style.space(10); color: view.muted }
        Small { height: parent.height; text: parent.text; verticalAlignment: Text.AlignVCenter }
    }
    component FilterTab: Action {
        id: control
        foreground: selected ? Commons.Color.accent : view.foreground
        color: selected ? Util.alpha(Commons.Color.accent, 0.24) : (hot || activeFocus ? Util.alpha(view.foreground, 0.08) : "transparent")
        borderSpec: Border.flat(selected || activeFocus ? Commons.Color.accent : Util.alpha(view.foreground, 0.2), 1)
        Rectangle {
            visible: control.selected
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: Style.space(7); rightMargin: Style.space(7); bottomMargin: Style.space(2) }
            height: Style.space(3)
            radius: height / 2
            color: Commons.Color.accent
        }
    }
    component Badge: Rectangle {
        property alias text: badgeLabel.text
        property color foreground: view.muted
        color: Util.alpha(view.foreground, 0.07)
        radius: Style.space(4)
        implicitWidth: badgeLabel.implicitWidth + Style.space(10)
        implicitHeight: Style.space(19)
        Label { id: badgeLabel; anchors { fill: parent; leftMargin: Style.space(5); rightMargin: Style.space(5) } verticalAlignment: Text.AlignVCenter; font.pixelSize: Style.font.caption; color: parent.foreground }
    }
    ColumnLayout {
        anchors { fill: parent; margins: Style.space(16) }
        spacing: Style.space(10)
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: Style.space(44)
            spacing: Style.space(12)
            Rectangle {
                implicitWidth: Style.space(44); implicitHeight: implicitWidth
                radius: Style.space(10)
                color: Util.alpha(Commons.Color.accent, 0.12)
                Ui.OpticalGlyph { anchors.fill: parent; text: "󰮠"; paintedSize: Style.space(25); fontSize: Style.space(30); color: Commons.Color.accent }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: Style.space(3)
                Label { Layout.fillWidth: true; text: "GitLab " + strings.t("pipelines"); font.bold: true; font.pixelSize: Style.font.title }
                Small { Layout.fillWidth: true; text: strings.t("summary").arg(Commons.I18n.formatNumber(view.runningCount)).arg(Commons.I18n.formatNumber(view.projectCount)) }
            }
            Label {
                text: "󰑐"; visible: view.loading || view.operationBusy; color: Commons.Color.accent; font.pixelSize: Style.space(18)
                RotationAnimation on rotation {
                    from: 0; to: 360; loops: Animation.Infinite
                    duration: Style.duration(900)
                    running: (view.loading || view.operationBusy) && !Style.reduceMotion
                }
            }
            Action { visible: ["auth", "permission"].indexOf(view.errorCode) >= 0 && view.pipelines.length > 0; text: strings.t("connect"); onClicked: view.loginRequested() }
            IconAction { objectName: "refresh"; glyph: "󰑐"; tooltipText: view.refreshAllowed ? strings.t(view.loading ? "refreshing" : "refresh") : strings.t("refreshIn").arg(Commons.I18n.formatNumber(view.refreshWait)); enabled: !view.loading && view.refreshAllowed; onClicked: view.refreshRequested() }
            IconAction { glyph: "󰅖"; tooltipText: strings.t("close"); onClicked: view.dismissRequested() }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(6)
            Repeater {
                model: [strings.t("all") + " (" + Commons.I18n.formatNumber(view.pipelines.length) + ")", strings.t("active"), strings.t("finished"), strings.t("failed"), strings.t("branch"), strings.t("tags")]
                delegate: FilterTab {
                    required property int index
                    required property string modelData
                    objectName: "tab-" + index
                    text: modelData
                    selected: view.tab === index
                    onClicked: { view.tab = index; list.currentIndex = 0 }
                }
            }
            Item { Layout.fillWidth: true }
        }
        Ui.TextField {
            id: filter
            objectName: "filter"
            Layout.fillWidth: true
            placeholderText: strings.t("filter")
            selectByMouse: true
            onTextChanged: { view.search = text; list.currentIndex = 0 }
            Keys.onEscapePressed: { if (text) text = ""; else view.forceActiveFocus() }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: notice.implicitHeight + Style.space(20)
            visible: !!view.errorMessage || view.failedProjects > 0 || view.failedStages > 0
            color: Util.alpha(view.progress, 0.08)
            radius: Style.space(6)
            border.width: 1
            border.color: Util.alpha(view.progress, 0.25)
            Label {
                id: notice
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Style.space(10) }
                wrapMode: Text.Wrap
                text: view.errorMessage || strings.t("partial").arg(Commons.I18n.formatNumber(view.failedProjects)).arg(Commons.I18n.formatNumber(view.failedStages))
                color: view.progress
                font.pixelSize: Style.font.caption
            }
        }
        Small {
            Layout.fillWidth: true
            visible: !!view.operationNotice
            color: view.success
            text: view.operationNotice
        }
        // Shared grid proportions keep headers and rows aligned at every panel width.
        RowLayout {
            id: tableHeader
            Layout.leftMargin: Style.space(12)
            Layout.rightMargin: Style.space(12) + pipelineScroll.rightPadding
            Layout.fillWidth: true
            Layout.preferredHeight: Style.space(24)
            spacing: Style.space(14)
            Label { objectName: "header-status"; text: strings.t("status"); font.pixelSize: Style.font.caption; color: view.muted; Layout.preferredWidth: Style.space(150); Layout.minimumWidth: Style.space(150); Layout.maximumWidth: Style.space(150) }
            Label { objectName: "header-pipeline"; text: strings.t("pipeline"); font.pixelSize: Style.font.caption; color: view.muted; Layout.fillWidth: true; Layout.minimumWidth: 0 }
            Label { objectName: "header-author"; text: strings.t("createdBy"); font.pixelSize: Style.font.caption; color: view.muted; Layout.preferredWidth: Style.space(126); Layout.minimumWidth: Style.space(126); Layout.maximumWidth: Style.space(126) }
            Label { objectName: "header-stages"; text: strings.t("stages"); font.pixelSize: Style.font.caption; color: view.muted; Layout.preferredWidth: Style.space(164); Layout.minimumWidth: Style.space(164); Layout.maximumWidth: Style.space(164) }
            Label { objectName: "header-actions"; text: strings.t("actions"); font.pixelSize: Style.font.caption; color: view.muted; Layout.preferredWidth: Style.space(104); Layout.minimumWidth: Style.space(104); Layout.maximumWidth: Style.space(104); horizontalAlignment: Text.AlignRight }
        }
        Line { Layout.fillWidth: true }
        Controls.ScrollView {
            id: pipelineScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            padding: 0
            rightPadding: verticalBar.visible ? verticalBar.width + Style.space(6) : 0
            contentWidth: availableWidth
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
            Controls.ScrollBar.vertical: Controls.ScrollBar { id: verticalBar; width: Style.space(8); interactive: true }
            contentItem: MomentumList {
                id: list
                objectName: "pipelines"
                clip: true
                model: view.visibleRows
                boundsBehavior: Flickable.StopAtBounds
                currentIndex: -1
                keyNavigationEnabled: false
                spacing: Style.space(10)
                footer: Item {
                    width: list.width
                    height: view.canLoadMore ? Style.space(52) : 0
                    visible: view.canLoadMore
                    Action {
                        objectName: "loadMore"
                        anchors.centerIn: parent
                        text: strings.t(view.loading ? "loading" : "loadMore")
                        iconText: "󰅀"
                        enabled: !view.loading && view.loadMoreAllowed
                        onClicked: view.loadMoreRequested()
                    }
                }
                delegate: Rectangle {
                    id: pipelineRow
                    required property var modelData
                    required property int index
                    objectName: "pipeline-" + modelData.id
                    width: list.width
                    height: Style.space(120)
                    readonly property color tint: view.statusColor(modelData.status)
                    radius: Style.space(10)
                    color: Util.alpha(tint, list.currentIndex === index ? 0.08 : hover.hovered ? 0.06 : 0.035)
                    border.width: 1
                    border.color: list.currentIndex === index ? Util.alpha(Commons.Color.accent, 0.65) : Util.alpha(tint, 0.18)
                    HoverHandler { id: hover }
                    RowLayout {
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: Style.space(12) }
                        height: Style.space(96)
                        spacing: Style.space(14)
                        Column {
                            objectName: "statusColumn-" + pipelineRow.modelData.id
                            Layout.preferredWidth: Style.space(150); Layout.minimumWidth: Style.space(150); Layout.maximumWidth: Style.space(150)
                            Layout.topMargin: Style.space(4)
                            Layout.alignment: Qt.AlignTop
                            spacing: Style.space(5)
                            Rectangle {
                                width: Math.min(Style.space(150), stateLabel.implicitWidth + Style.space(33))
                                height: Style.space(24)
                                radius: Style.space(14)
                                color: view.statusBackground(pipelineRow.modelData.status)
                                Rectangle {
                                    x: Style.space(5); anchors.verticalCenter: parent.verticalCenter
                                    width: Style.space(16); height: Style.space(16); radius: Style.space(8)
                                    color: view.statusColor(pipelineRow.modelData.status)
                                    Ui.OpticalGlyph { anchors.fill: parent; text: view.statusSymbol(pipelineRow.modelData.status); paintedSize: Style.space(10); color: Commons.Color.background }
                                }
                                Label { id: stateLabel; x: Style.space(27); width: parent.width - Style.space(32); anchors.verticalCenter: parent.verticalCenter; text: view.statusText(pipelineRow.modelData.status); font.pixelSize: Style.font.caption; color: view.statusColor(pipelineRow.modelData.status) }
                            }
                            Metadata { glyph: "󰥔"; text: view.duration(pipelineRow.modelData); visible: pipelineRow.modelData.duration > 0 || pipelineRow.modelData.status === "running" }
                            Metadata { glyph: "󰃭"; text: view.age(pipelineRow.modelData.createdAt) }
                        }
                        Column {
                            objectName: "pipelineColumn-" + pipelineRow.modelData.id
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            Layout.topMargin: Style.space(4)
                            Layout.alignment: Qt.AlignTop
                            spacing: Style.space(5)
                            Controls.AbstractButton {
                                width: parent.width
                                height: Style.space(24)
                                activeFocusOnTab: true
                                contentItem: Label { text: "#" + pipelineRow.modelData.id + "  ·  " + pipelineRow.modelData.project; font.pixelSize: Style.font.body; font.bold: true; color: view.foreground; verticalAlignment: Text.AlignVCenter; font.underline: parent.hovered || parent.activeFocus }
                                onClicked: view.pipelineRequested(pipelineRow.modelData.project, pipelineRow.modelData.id)
                                Accessible.name: pipelineRow.modelData.project + " #" + pipelineRow.modelData.id
                            }
                            Label { objectName: "title-" + pipelineRow.modelData.id; width: parent.width; text: pipelineRow.modelData.title || strings.t("noTitle"); color: Commons.Color.accent }
                            Row {
                                width: parent.width
                                spacing: Style.space(5)
                                Badge { objectName: "branchBadge-" + pipelineRow.modelData.id; width: Math.min(implicitWidth, Math.max(Style.space(35), parent.width - Style.space(85))); text: "⑂ " + pipelineRow.modelData.ref }
                                Badge { text: pipelineRow.modelData.sha.slice(0, 8) }
                            }
                            Row {
                                spacing: Style.space(5)
                                Badge { visible: view.latestForRef(pipelineRow.modelData); text: strings.t("latest"); color: Util.alpha(view.success, 0.14); foreground: view.success; radius: Style.space(10) }
                                Badge { text: strings.t(pipelineRow.modelData.isTag ? "tag" : "branch"); color: Util.alpha(Commons.Color.accent, 0.14); foreground: Commons.Color.accent; radius: Style.space(10) }
                            }
                        }
                        Column {
                            objectName: "authorColumn-" + pipelineRow.modelData.id
                            Layout.preferredWidth: Style.space(126); Layout.minimumWidth: Style.space(126); Layout.maximumWidth: Style.space(126)
                            Layout.alignment: Qt.AlignTop
                            spacing: Style.space(7)
                            Avatar {
                                objectName: "avatar-" + pipelineRow.modelData.id
                                name: pipelineRow.modelData.author || pipelineRow.modelData.username || ""
                                imagePath: pipelineRow.modelData.avatar || ""
                            }
                            Small { width: Style.space(126); text: pipelineRow.modelData.author || pipelineRow.modelData.username || "—" }
                        }
                        Item {
                            objectName: "stagesColumn-" + pipelineRow.modelData.id
                            Layout.preferredWidth: Style.space(164); Layout.minimumWidth: Style.space(164); Layout.maximumWidth: Style.space(164)
                            Layout.fillHeight: true
                            Row {
                                spacing: Style.space(4)
                                Repeater {
                                    model: pipelineRow.modelData.stages.slice(0, 5)
                                    delegate: Controls.AbstractButton {
                                        id: stageCircle
                                        required property var modelData
                                        objectName: "stage-" + pipelineRow.modelData.id + "-" + modelData.name
                                        onClicked: view.showJobs(pipelineRow.modelData.id, modelData.name, stageCircle)
                                        width: Style.space(24); height: Style.space(32)
                                        background: Item {
                                            StatusMark { anchors.centerIn: parent; status: stageCircle.modelData.status; border.width: stageCircle.activeFocus ? 1 : 0; border.color: Commons.Color.accent }
                                        }
                                        Accessible.role: Accessible.Button
                                        Accessible.name: modelData.name
                                        activeFocusOnTab: true
                                        HoverHandler { id: stageHover }
                                        Controls.ToolTip {
                                            visible: stageHover.hovered
                                            delay: 250
                                            contentItem: Label { text: stageCircle.modelData.name + " · " + view.statusText(stageCircle.modelData.status); color: Commons.Color.tooltip.text }
                                            background: Rectangle { color: Commons.Color.tooltip.background; radius: Style.space(5) }
                                        }
                                    }
                                }
                                Controls.AbstractButton {
                                    id: overflowStages
                                    objectName: "allJobs-" + pipelineRow.modelData.id
                                    visible: pipelineRow.modelData.stages.length > 5 || !!pipelineRow.modelData.stagesMore || !pipelineRow.modelData.stages.length
                                    width: Style.space(24); height: Style.space(32)
                                    activeFocusOnTab: true
                                    Accessible.name: strings.t("jobs")
                                    background: Item { Rectangle { anchors.centerIn: parent; width: Style.space(24); height: width; radius: width / 2; color: Util.alpha(view.muted, 0.14); border.width: overflowStages.activeFocus ? 1 : 0; border.color: Commons.Color.accent } }
                                    contentItem: Small { text: pipelineRow.modelData.stages.length ? "+" : "…"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                                    onClicked: view.showJobs(pipelineRow.modelData.id, "", overflowStages)
                                }
                            }
                        }
                        Item {
                            objectName: "actionsColumn-" + pipelineRow.modelData.id
                            Layout.preferredWidth: Style.space(104); Layout.minimumWidth: Style.space(104); Layout.maximumWidth: Style.space(104)
                            Layout.fillHeight: true
                            Row {
                                anchors.right: parent.right
                                spacing: Style.space(4)
                                IconAction {
                                    objectName: "retryPipeline-" + pipelineRow.modelData.id
                                    glyph: "󰑐"
                                    tooltipText: strings.t("retryPipeline")
                                    visible: !!pipelineRow.modelData.canUpdate && !!pipelineRow.modelData.retryable
                                    enabled: !view.operationBusy && view.loadMoreAllowed
                                    onClicked: view.actionRequested("retry-pipeline", pipelineRow.modelData.id, "")
                                }
                                IconAction {
                                    objectName: "newPipeline-" + pipelineRow.modelData.id
                                    glyph: ""
                                    tooltipText: strings.t("newPipeline").arg(pipelineRow.modelData.ref)
                                    visible: !!pipelineRow.modelData.canUpdate
                                    enabled: !view.operationBusy && view.loadMoreAllowed
                                    onClicked: view.actionRequested("new-pipeline", pipelineRow.modelData.id, "")
                                }
                                IconAction { glyph: "󰏌"; tooltipText: strings.t("openPipeline"); onClicked: view.pipelineRequested(pipelineRow.modelData.project, pipelineRow.modelData.id) }
                            }
                        }
                    }
                }
            }
            Column {
                anchors.centerIn: parent
                width: Math.min(parent.width - 40, 550)
                spacing: Style.space(14)
                visible: !view.visibleRows.length
                Label { width: parent.width; text: view.loading ? strings.t("loading") : !view.connected ? strings.t("connectTitle") : strings.t("empty"); font.pixelSize: Style.font.title; font.bold: true; horizontalAlignment: Text.AlignHCenter }
                Label { width: parent.width; text: view.loading ? strings.t("loadingHint") : !view.connected ? strings.t("connectHint") : strings.t("emptyHint"); color: view.muted; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter }
                Action { anchors.horizontalCenter: parent.horizontalCenter; visible: !view.loading && (!view.connected || view.errorCode === "auth"); text: strings.t("connect"); iconText: "󰮠"; onClicked: view.loginRequested() }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: Style.space(36)
            spacing: Style.space(12)
            Small { Layout.fillWidth: true; text: strings.t("footer").arg(Commons.I18n.formatNumber(view.visibleRows.length)).arg(Commons.I18n.formatNumber(view.projectCount)) }
            Small { visible: !view.refreshAllowed; text: strings.t("refreshIn").arg(Commons.I18n.formatNumber(view.refreshWait)) }
            Small { visible: view.fetchedAt > 0; text: strings.t("updated").arg(view.age(view.fetchedAt)) }
        }
    }
    Connections {
        target: list
        function onContentYChanged() { if (stagePopup.opened) stagePopup.close() }
        function onModelChanged() { if (stagePopup.opened) stagePopup.close() }
    }
    Controls.Popup {
        id: stagePopup
        objectName: "stagePopover"
        property Item anchorItem: null
        readonly property var jobView: view.jobViews[view.selectedPipeline] || {loading: false, jobs: []}
        readonly property var filteredJobs: (jobView.jobs || []).filter(job => !view.selectedStage || job.stage === view.selectedStage)
        parent: view
        width: Math.min(Style.space(280), view.width - Style.space(32))
        padding: Style.space(12)
        implicitHeight: popoverContent.implicitHeight + topPadding + bottomPadding
        x: {
            var point = anchorItem ? anchorItem.mapToItem(view, 0, 0) : Qt.point(0, 0)
            return Math.max(Style.space(8), Math.min(view.width - width - Style.space(8), point.x))
        }
        y: {
            var point = anchorItem ? anchorItem.mapToItem(view, 0, anchorItem.height) : Qt.point(0, 0)
            var below = point.y + Style.space(6)
            return below + height < view.height - Style.space(8) ? below : Math.max(Style.space(8), point.y - (anchorItem ? anchorItem.height : 0) - height - Style.space(6))
        }
        modal: false
        focus: true
        closePolicy: Controls.Popup.CloseOnEscape | Controls.Popup.CloseOnPressOutside
        onClosed: {
            view.selectedPipeline = ""
            view.selectedStage = ""
            anchorItem = null
        }
        background: Rectangle {
            color: Commons.Color.popups.background
            radius: Style.cornerRadius
            border.width: 1
            border.color: Commons.Color.popups.border
        }
        contentItem: Column {
            id: popoverContent
            spacing: Style.space(8)
            Small {
                width: parent.width
                text: view.selectedStage ? strings.t("stageHeading").arg(view.selectedStage) : strings.t("jobs")
                color: view.foreground
                font.bold: true
            }
            Line { width: parent.width }
            Small { width: parent.width; visible: !!stagePopup.jobView.message; wrapMode: Text.Wrap; text: stagePopup.jobView.message || ""; color: view.progress }
            Small { width: parent.width; visible: !!stagePopup.jobView.loading && !stagePopup.filteredJobs.length; text: strings.t("loadingJobs") }
            Small { width: parent.width; visible: !stagePopup.jobView.loading && !stagePopup.filteredJobs.length && !stagePopup.jobView.message; text: strings.t("noStageJobs") }
            MomentumList {
                id: popupJobs
                objectName: "stageJobs"
                width: parent.width
                height: Math.min(Style.space(280), contentHeight)
                clip: true
                model: stagePopup.filteredJobs
                spacing: Style.space(2)
                Controls.ScrollBar.vertical: Controls.ScrollBar {}
                delegate: RowLayout {
                    id: jobRow
                    required property var modelData
                    width: popupJobs.width
                    height: Style.space(36)
                    spacing: Style.space(8)
                    StatusMark { implicitWidth: Style.space(20); implicitHeight: implicitWidth; status: jobRow.modelData.status }
                    Label { objectName: "jobName-" + jobRow.modelData.id; Layout.fillWidth: true; text: jobRow.modelData.name; verticalAlignment: Text.AlignVCenter; Layout.alignment: Qt.AlignVCenter }
                    IconAction {
                        objectName: "playJob-" + jobRow.modelData.id
                        glyph: ""; tooltipText: strings.t("runJob")
                        implicitWidth: Style.space(24); implicitHeight: Style.space(24); iconSize: Style.space(12); radius: width / 2
                        visible: !!jobRow.modelData.playable
                        enabled: !view.operationBusy && view.loadMoreAllowed
                        onClicked: view.actionRequested("play-job", view.selectedPipeline, jobRow.modelData.id)
                    }
                    IconAction {
                        objectName: "retryJob-" + jobRow.modelData.id
                        glyph: "󰑐"; tooltipText: strings.t("retryJob")
                        implicitWidth: Style.space(24); implicitHeight: Style.space(24); iconSize: Style.space(12); radius: width / 2
                        visible: !!jobRow.modelData.retryable
                        enabled: !view.operationBusy && view.loadMoreAllowed
                        onClicked: view.actionRequested("retry-job", view.selectedPipeline, jobRow.modelData.id)
                    }
                }
            }
            Small { width: parent.width; visible: !!stagePopup.jobView.more; text: strings.t("moreJobs"); wrapMode: Text.Wrap }
        }
    }

}
