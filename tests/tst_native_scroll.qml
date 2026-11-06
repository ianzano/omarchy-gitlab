import QtQuick
import QtQuick.Controls
import QtTest
import ".."

TestCase {
    id: test
    name: "NativeScrolling"
    when: windowShown
    visible: true
    width: 700
    height: 480
    Component { id: row; Rectangle { required property int index; width: ListView.view.width; height: 60; color: index % 2 ? "#273142" : "#192332" } }
    ScrollView {
        width: 320; height: 440
        contentItem: MomentumList { id: subject; model: 100; delegate: row }
    }
    ScrollView {
        x: 350; width: 320; height: 440
        contentItem: ListView { id: reference; model: 100; delegate: row; boundsBehavior: Flickable.StopAtBounds; flickableDirection: Flickable.VerticalFlick }
    }
    function settle(view) {
        wait(60)
        tryVerify(function() { return !view.moving }, 2000)
        wait(30)
    }
    function init() {
        subject.resetPosition()
        reference.cancelFlick()
        reference.contentY = reference.originY
        wait(30)
    }
    function test_fullWheelNotches() {
        for (var i = 0; i < 5; i++) mouseWheel(subject, 100, 100, 0, -120, Qt.NoButton, Qt.NoModifier, 12)
        settle(subject)
        for (var j = 0; j < 5; j++) mouseWheel(reference, 100, 100, 0, -120, Qt.NoButton, Qt.NoModifier, 12)
        settle(reference)
        verify(subject.contentY > 0)
        fuzzyCompare(subject.contentY, reference.contentY, Math.max(5, reference.contentY * 0.12))
        console.log("FULL_NOTCH_DISTANCE", subject.contentY)
    }
    function test_highResolutionWheelPackets() {
        for (var i = 0; i < 16; i++) mouseWheel(subject, 100, 100, 0, -15, Qt.NoButton, Qt.NoModifier, 12)
        settle(subject)
        for (var j = 0; j < 16; j++) mouseWheel(reference, 100, 100, 0, -15, Qt.NoButton, Qt.NoModifier, 12)
        settle(reference)
        verify(subject.contentY > 0)
        fuzzyCompare(subject.contentY, reference.contentY, Math.max(5, reference.contentY * 0.12))
        console.log("HIGH_RES_DISTANCE", subject.contentY)
    }
    function test_trackpadFlickPhysics() {
        subject.flick(0, -1800)
        reference.flick(0, -1800)
        wait(75)
        verify(subject.moving)
        fuzzyCompare(subject.contentY, reference.contentY, 6)
        settle(subject)
        settle(reference)
        fuzzyCompare(subject.contentY, reference.contentY, 6)
    }
}
