import QtQuick
import Quickshell.Widgets as Widgets
import qs.Commons
import qs.Commons as Commons

Item {
    id: avatar
    property string imagePath: ""
    property string name: ""
    implicitWidth: Style.space(32)
    implicitHeight: implicitWidth
    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: Util.alpha(Commons.Color.accent, 0.14)
    }
    Widgets.ClippingRectangle {
        anchors.fill: parent
        radius: width / 2
        color: "transparent"
        Image {
            id: photo
            objectName: "avatarImage"
            anchors.fill: parent
            source: avatar.imagePath.startsWith("file:///") ? avatar.imagePath : ""
            fillMode: Image.PreserveAspectCrop
            sourceSize: Qt.size(64, 64)
            asynchronous: true
            cache: true
        }
    }
    Text {
        anchors.centerIn: parent
        visible: photo.status !== Image.Ready
        textFormat: Text.PlainText
        text: {
            var parts = avatar.name.trim().split(/\s+/)
            return avatar.name ? (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : parts[0].slice(1, 2))).toUpperCase() : "?"
        }
        color: Commons.Color.accent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        font.bold: true
    }
}
