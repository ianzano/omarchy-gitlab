import QtQuick

// Let Qt handle every wheel/touchpad packet and its native momentum.
// This component only remembers the reading position across model refreshes.
ListView {
    id: list
    boundsBehavior: Flickable.StopAtBounds
    flickableDirection: Flickable.VerticalFlick
    property real lastUserY: 0
    property int scrollEpoch: 0

    function resetPosition() { cancelFlick(); scrollEpoch++; lastUserY = originY; contentY = originY }
    onContentYChanged: if (moving) lastUserY = contentY
    onMovementEnded: lastUserY = contentY
    onModelChanged: {
        cancelFlick()
        var saved = lastUserY
        var epoch = scrollEpoch
        Qt.callLater(function() {
            if (epoch === scrollEpoch) contentY = Math.max(originY, Math.min(saved, originY + Math.max(0, contentHeight - height)))
        })
    }
}
