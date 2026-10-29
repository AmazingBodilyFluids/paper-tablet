// The companion UI. Panel is 1620 x 2160 portrait; `view` is rotated to how the tablet sits.
// Black and white only: greys ghost in the fast pen waveform.
extern const char *COMPANION_QML;
const char *COMPANION_QML = R"QML(
import QtQuick

Window {
    id: win
    // Sized from the screen as in reMarkable's epaper example: FullScreen alone left the window
    // 0x0 on the device, so nothing was ever drawn.
    width: Screen.width
    height: Screen.height
    visible: true
    color: "white"
    property int rot: 0
    property string mode: "blank"
    property int nextTarget: 0
    property var area: [0, 0, 1, 1]
    property string status: "Connected to PC"
    property var last: null
    property var stroke: null      // segments of the stroke being drawn
    property int fadeMs: 1500      // how long a finished stroke stays; 0 keeps it ("fade MS")
    property var lastStroke: null  // the stroke that just ended, in case the PC snaps it
    property var held: null        // snapped shape while the pen is still down
    property bool penDown: false

    // side panel
    property string panelSide: "left"   // left | right | off
    property real panelFrac: 0.14       // of the view width
    property bool panelOpen: true       // folded away with the tab in its top corner
    property bool gesturesOn: true
    property bool penOut: true          // states the PC reports ("state pen 1 snap 0")
    property bool snapOn: true
    property var latched: ({})          // hold buttons locked down, by index
    property int pressing: -1           // button under a finger or the pen right now
    property real holdStart: 0
    property bool keepLatch: false
    property string toast: ""
    property real penSeen: 0            // last time the pen was in range (palm rejection)
    property int penBtn: -1             // what the pen tip is on in the panel (-2 = tab, 99 = nothing)
    readonly property real tabS: 84
    readonly property bool panelOn: mode === "ink" && panelSide !== "off"
    readonly property real panelW: panelOn && panelOpen ? panelFrac * view.width : 0
    readonly property real panelX: panelSide === "right" ? view.width - panelW : 0
    readonly property real headH: 230
    readonly property real profY: tabS + 10   // the profile row: arrows either side of its name
    readonly property real profH: 72
    property string profileName: ""
    // paper under the ink: plain | lined | squared | dots | cross | blueprint | isometric
    property string paper: "plain"
    property real paperStep: 45          // px between lines (the PC converts from mm)
    // tracing image under the ink: file on the tablet, its scale and offset in view pixels
    property string traceSrc: ""
    property real traceScale: 1
    property real traceX: 0
    property real traceY: 0
    property real traceOpacity: 0.5
    property bool traceAdjust: false     // fingers move / zoom the image instead of gestures
    property bool lightOn: false
    property int profileCount: 1
    readonly property real footH: 190
    readonly property int rows: 8
    readonly property real cellW: panelW / 2
    readonly property real cellH: (view.height - headH - footH) / rows

    // The PC sends its own layout ("buttons [json]"); this is the same as its default.
    // kind: tap (keys), hold (keys held / locked), pen, snap, local (clear ink), none
    property var buttons: [
        {t: "Undo", s: "Ctrl Z", kind: "tap"},         {t: "Redo", s: "Ctrl Shift Z", kind: "tap"},
        {t: "Size -", s: "[", kind: "tap"},            {t: "Size +", s: "]", kind: "tap"},
        {t: "Brush", s: "B", kind: "tap"},             {t: "Eraser", s: "E", kind: "tap"},
        {t: "Hand", s: "holds Space", kind: "hold"},   {t: "Pick", s: "holds Alt", kind: "hold"},
        {t: "Ctrl", s: "holds Ctrl", kind: "hold"},    {t: "Shift", s: "holds Shift", kind: "hold"},
        {t: "Zoom -", s: "Ctrl -", kind: "tap"},       {t: "Zoom +", s: "Ctrl =", kind: "tap"},
        {t: "Fit", s: "Ctrl 0", kind: "tap"},          {t: "Shapes", s: "", kind: "snap"},
        {t: "Pen", s: "", kind: "pen"},                {t: "Save", s: "Ctrl S", kind: "tap"}
    ]

    // To the PC: one line on stderr, picked out by the C++ message handler.
    function act(s) { console.warn("ACT:" + s) }
    function say(s) { toast = s; toastTimer.restart() }

    function command(line) {
        if (line.indexOf("buttons ") === 0) {
            try { buttons = JSON.parse(line.substring(8)); latched = ({}) }
            catch (e) { console.warn("LOG:bad buttons line: " + e) }
            return
        }
        var a = line.split(" ")
        var c = a.shift()
        if (c === "rot") rot = parseInt(a[0]) % 4
        else if (c === "mode") { mode = a[0]; endStroke(); if (mode !== "ink") clearInk() }
        else if (c === "next") nextTarget = parseInt(a[0])
        else if (c === "status") status = a.join(" ")
        else if (c === "area") area = a.map(parseFloat)
        else if (c === "fade") fadeMs = Math.max(0, parseInt(a[0]))
        else if (c === "clear") clearInk()
        else if (c === "snap") snap(a)
        else if (c === "panel") { panelSide = a[0]; if (a.length > 1) panelFrac = parseFloat(a[1]) }
        else if (c === "panelopen") panelOpen = a[0] === "1"
        else if (c === "paper") { paper = a[0]; if (a.length > 1) paperStep = Math.max(12, parseFloat(a[1])) }
        else if (c === "trace") {  // trace <opacity> <scale> <x> <y> <file or empty>
            traceOpacity = parseFloat(a[0]); traceScale = parseFloat(a[1])
            traceX = parseFloat(a[2]); traceY = parseFloat(a[3])
            var src = a.slice(4).join(" ")
            traceSrc = src ? "file://" + src : ""
        }
        else if (c === "traceadjust") traceAdjust = a[0] === "1"
        else if (c === "profile") { profileCount = parseInt(a[1]); profileName = a.slice(2).join(" ") }
        else if (c === "gestures") {
            gesturesOn = a[0] === "1"
            if (a.length > 1) swipeStep = parseFloat(a[1])
            if (a.length > 2) palmSize = parseFloat(a[2])
        }
        else if (c === "toast") say(a.join(" "))
        else if (c === "state") {
            for (var i = 0; i + 1 < a.length; i += 2) {
                if (a[i] === "pen") penOut = a[i + 1] === "1"
                else if (a[i] === "snap") snapOn = a[i + 1] === "1"
                else if (a[i] === "light") lightOn = a[i + 1] === "1"
            }
        } else if (c === "unlatch") latched = ({})
    }

    function clearInk() {
        stroke = null; last = null; lastStroke = null; held = null
        // only strokes: the e-paper pen-mode item also lives in `ink` and must stay
        for (var i = ink.children.length - 1; i >= 0; i--)
            if (ink.children[i].trail) ink.children[i].destroy()
    }

    // "snap W x y x y ...": the PC recognised a shape. Replace the stroke with the clean outline
    // (points as fractions of the digitizer, like the pen) and ignore the pen until it lifts.
    function snap(a) {
        var victim = stroke || lastStroke
        if (victim) victim.destroy()
        stroke = null; last = null; lastStroke = null
        if (mode !== "ink") return
        var w = 2 + 5 * parseFloat(a[0])
        var st = fader.createObject(ink, {})
        var prev = null
        for (var i = 1; i + 1 < a.length; i += 2) {
            var p = view.mapFromItem(win.contentItem, parseFloat(a[i]) * win.width, parseFloat(a[i + 1]) * win.height)
            if (prev) {
                var dx = p.x - prev.x, dy = p.y - prev.y
                seg.createObject(st, {x: prev.x, y: prev.y, len: Math.sqrt(dx * dx + dy * dy), w: w,
                                      rotation: Math.atan2(dy, dx) * 180 / Math.PI})
            }
            prev = p
        }
        if (penDown) held = st
        else if (fadeMs > 0) st.fade(fadeMs)
    }

    // ---- panel: the fold tab and the buttons, by finger or pen
    function tabHit(p) {
        return panelOn && p.y < tabS && (panelSide === "right" ? p.x >= view.width - tabS : p.x < tabS)
    }
    function inPanel(p) {
        if (!panelOn) return false
        if (!panelOpen) return tabHit(p)
        return p.x >= panelX && p.x < panelX + panelW
    }
    // -1 / +1 on the profile arrows (only with more than one profile), 0 elsewhere
    function profileArrow(p) {
        if (!panelOpen || profileCount < 2 || !inPanel(p) || p.y < profY || p.y >= profY + profH) return 0
        var x = p.x - panelX
        return x < panelW * 0.3 ? -1 : x >= panelW * 0.7 ? 1 : 0
    }
    function buttonAt(p) {
        if (!panelOpen || !inPanel(p) || p.y < headH || p.y >= view.height - footH) return -1
        var i = Math.floor((p.y - headH) / cellH) * 2 + (p.x - panelX >= cellW ? 1 : 0)
        return i < buttons.length ? i : -1
    }
    function setLatch(i, on) {
        var l = {}
        for (var key in latched) if (latched[key]) l[key] = true
        if (on) l[i] = true; else delete l[i]
        latched = l
    }
    function fold() {
        panelOpen = !panelOpen
        act("panel " + (panelOpen ? 1 : 0))
    }
    // Returns what was pressed: -2 for the fold tab, a button index, or -1.
    function pressAt(p) {
        if (tabHit(p)) { fold(); return -2 }
        var arrow = profileArrow(p)
        if (arrow) { latched = ({}); act("profile " + (arrow > 0 ? "next" : "prev")); return -3 }
        var i = buttonAt(p)
        pressBtn(i)
        return i
    }
    function pressBtn(i) {
        if (i < 0) return
        var b = buttons[i]
        if (!b || b.kind === "none") return
        pressing = i
        if (b.kind === "tap") { act("press " + i); say(b.t) }
        else if (b.kind === "pen") { penOut = !penOut; act("pen " + (penOut ? 1 : 0)); say(penOut ? "Pen on" : "Pen off") }
        else if (b.kind === "snap") { snapOn = !snapOn; act("snap " + (snapOn ? 1 : 0)); say(snapOn ? "Shapes on" : "Shapes off") }
        else if (b.kind === "local") { clearInk() }
        else if (b.kind === "trace") { act("traceadjust") }
        else if (b.kind === "light") { act("light") }
        else if (b.kind === "hold") {
            if (latched[i]) { act("release " + i); setLatch(i, false); keepLatch = false; say(b.t + " released") }
            else { act("press " + i); setLatch(i, true); keepLatch = true; holdStart = Date.now() }
        }
    }
    // A quick tap locks a hold button down until it's tapped again; holding it with a finger
    // works like the key itself (down while touched). The pen always locks.
    function releaseBtn(i, byPen) {
        pressing = -1
        if (i < 0) return
        var b = buttons[i]
        if (!b || b.kind !== "hold" || !keepLatch) return
        keepLatch = false
        if (!byPen && Date.now() - holdStart > 400) { act("release " + i); setLatch(i, false) }
        else say(b.t + " locked: tap again to release")
    }

    // Each segment is its own small item rather than pixels in one big Canvas: a Canvas
    // re-uploads the whole 1620x2160 texture per change, so the e-paper backend refreshes a
    // large area every time. Small items keep each update to the few pixels the pen touched.
    function pen(px, py, pressure, touching, eraser, inRange) {
        if (inRange) {
            penSeen = Date.now()
            if (gMode !== "") endGesture()  // the pen came back: a hand on the screen is a palm
        }
        var p = view.mapFromItem(win.contentItem, px, py)
        if (penBtn !== -1 || (touching && inPanel(p))) {
            // the panel takes the pen; the PC ignores the pen over the panel too
            endStroke()
            if (touching && penBtn === -1) { penBtn = pressAt(p); if (penBtn === -1) penBtn = 99 }
            else if (!touching || !inPanel(p)) { if (penBtn >= 0 && penBtn !== 99) releaseBtn(penBtn, true); else pressing = -1; penBtn = -1 }
            return
        }
        penDown = touching
        if (held) {
            if (touching) return
            if (fadeMs > 0) held.fade(fadeMs)
            held = null
        }
        if (mode !== "ink" || !touching || eraser) { endStroke(); return }
        if (!stroke) { stroke = fader.createObject(ink, {}); last = p; return }
        var dx = p.x - last.x, dy = p.y - last.y
        var len = Math.sqrt(dx * dx + dy * dy)
        if (len < 2) return
        seg.createObject(stroke, {x: last.x, y: last.y, len: len, w: 2 + 5 * pressure,
                                  rotation: Math.atan2(dy, dx) * 180 / Math.PI})
        last = p
    }

    function endStroke() {
        if (stroke && fadeMs > 0) stroke.fade(fadeMs)
        if (stroke) lastStroke = stroke
        stroke = null; last = null
    }

    // ---- finger gestures on the drawing area
    //   two fingers: drag = pan (the PC holds Space and drags), pinch = zoom in / out
    //   three fingers: swipe right-to-left = undo, left-to-right = redo
    //   five fingers: twist = rotate the canvas (the PC holds the rotate key and drags in a circle)
    // Ignored while the pen is near the screen (that's a resting hand) and with 6+ fingers.
    property var gPts: ({})        // pointId -> {x, y}
    property string gMode: ""      // "" | pan | pinch | three | five | rotate | dead
    property var gAng0: ({})       // five fingers: pointId -> angle around the centroid at the start
    property real gRotSent: 0      // degrees last sent
    property int gMax: 0
    property real gD0: 0           // finger distance when the second finger landed
    property real gDLast: 0        // distance at the last zoom step
    property var gC0: null         // centroid when the current finger count began
    property var gSent: null       // last pan offset sent
    property var touchBtn: ({})    // pointId -> panel item, for fingers that landed on the panel
    property real palmSize: 0      // contact size (panel units) above which a touch is a palm; 0 = off
    property var palms: ({})       // pointId -> true: contacts judged to be a palm, ignored until lifted
    property real gLastEv: 0       // last touch event of any kind, for the stuck-contact sweep
    property real swipeStep: 100   // three-finger swipe: px of drag per undo / redo step
    property int gSteps: 0         // net steps sent this swipe: < 0 undos, > 0 redos
    // taps: a touch (all fingers down and up again) that is short and barely moves
    property var gStart: ({})      // pointId -> where it landed
    property real gMoved: 0        // furthest any finger strayed this touch
    property real gT0: 0           // when the first finger landed
    property bool gTapOk: true     // nothing (pan, zoom, swipe, pen) happened this touch
    property int tapN: 0           // fingers of the tap series in progress
    property int tapK: 0           // taps so far in it

    function gList() { var l = []; for (var k in gPts) l.push(gPts[k]); return l }
    function centroid(l) {
        var x = 0, y = 0
        for (var i = 0; i < l.length; i++) { x += l[i].x; y += l[i].y }
        return {x: x / l.length, y: y / l.length}
    }
    function spread(l) { return Math.sqrt(Math.pow(l[0].x - l[1].x, 2) + Math.pow(l[0].y - l[1].y, 2)) }
    function fingerAngles(l, ids) {
        var c = centroid(l), out = {}
        for (var i = 0; i < ids.length; i++) { var p = gPts[ids[i]]; out[ids[i]] = Math.atan2(p.y - c.y, p.x - c.x) }
        return out
    }
    function twist() {  // mean turn of the five fingers since the start, in degrees (clockwise +)
        var ids = Object.keys(gPts), now = fingerAngles(gList(), ids), sum = 0, n = 0
        for (var i = 0; i < ids.length; i++) {
            if (!(ids[i] in gAng0)) continue
            var d = now[ids[i]] - gAng0[ids[i]]
            while (d > Math.PI) d -= 2 * Math.PI
            while (d < -Math.PI) d += 2 * Math.PI
            sum += d; n++
        }
        return n ? sum / n * 180 / Math.PI : 0
    }
    function endGesture() {
        if (gMode === "pan") act("pan end")
        if (gMode === "rotate") act("rotate end")
        gMode = "dead"   // until every finger is up
        gTapOk = false
    }
    // A finished touch: count it towards a tap series and report the series once no further
    // tap follows (the PC decides what "tap 1 3" etc. do; unassigned ones do nothing).
    function touchEnded() {
        var tap = gTapOk && gMax <= 3 && gMoved < 30 && Date.now() - gT0 < 350
        if (!tap) { tapTimer.stop(); tapK = 0; return }
        if (tapTimer.running && tapN === gMax) tapK++
        else { tapN = gMax; tapK = 1 }
        tapTimer.restart()
    }
    // Contact size as Qt reports it (the panel's ABS_MT_TOUCH_MAJOR); 0 if the platform gives none.
    function touchSize(t) {
        var d = t.ellipseDiameters
        return d ? Math.max(d.width, d.height) : 0
    }
    // A palm: drop the contact and end whatever gesture it was part of. Sticky until it lifts,
    // because a palm often lands small at its edge and grows.
    function palmCheck(t) {
        if (palmSize <= 0 || touchSize(t) <= palmSize || palms[t.pointId]) return false
        var pl = palms; pl[t.pointId] = true; palms = pl
        console.warn("LOG:palm " + Math.round(touchSize(t)))
        if (t.pointId in gPts) { var g = gPts; delete g[t.pointId]; gPts = g }
        endGesture()
        return true
    }
    // A contact whose release got lost would block gestures for good: after 3 s with no touch
    // events at all, forget every contact.
    Timer {
        interval: 1000; repeat: true; running: Object.keys(win.gPts).length > 0
        onTriggered: if (Date.now() - win.gLastEv > 3000) {
            console.warn("LOG:touch sweep " + Object.keys(win.gPts).length)
            win.endGesture(); win.gPts = ({}); win.palms = ({}); win.fingersChanged()
        }
    }
    Timer { id: tapTimer; interval: 330; onTriggered: { if (win.tapK > 0) win.act("tap " + win.tapN + " " + win.tapK); win.tapK = 0 } }
    // Adjusting the tracing image: one finger drags it, two zoom it (about its centre).
    property var adj: null         // {c, d, x, y, s} where the current finger count began
    function adjustChanged() {
        var l = gList(), n = l.length
        if (n === 0) {
            if (adj) act("trace " + traceX.toFixed(1) + " " + traceY.toFixed(1) + " " + traceScale.toFixed(4))
            adj = null; gMode = ""; gMax = 0; return
        }
        adj = {c: centroid(l), d: n > 1 ? spread(l) : 0, x: traceX, y: traceY, s: traceScale}
    }
    function adjustMoved() {
        var l = gList()
        if (!adj || !l.length) return
        var c = centroid(l)
        traceX = adj.x + c.x - adj.c.x
        traceY = adj.y + c.y - adj.c.y
        if (l.length > 1 && adj.d > 20) traceScale = Math.max(0.05, Math.min(20, adj.s * spread(l) / adj.d))
    }
    function fingersChanged() {
        if (traceAdjust) { adjustChanged(); return }
        var l = gList(), n = l.length
        gMax = Math.max(gMax, n)
        if (n === 0) {
            if (gMode === "pan") act("pan end")
            if (gMode === "rotate") act("rotate end")
            if (gMax > 0) touchEnded()
            gMode = ""; gMax = 0; gC0 = null; gMoved = 0; gTapOk = true; gStart = ({}); gT0 = 0; return
        }
        if (gT0 === 0) gT0 = Date.now()
        if (gMode === "dead") return
        if (gMax > 5 || Date.now() - penSeen < 700 || !gesturesOn || mode !== "ink") { endGesture(); return }
        if (gMode === "rotate" && n !== 5) { endGesture(); return }
        if (n === 5) {
            if (gMode === "three" && gSteps !== 0) { endGesture(); return }
            if (gMode === "pan") act("pan end")
            gMode = "five"; gC0 = centroid(l); gAng0 = fingerAngles(l, Object.keys(gPts)); gRotSent = 0
        } else if (gMode === "five") {
            gMode = "dead"   // a finger lifted before any turn: just a five-finger touch
        } else if (n === 3) {
            if (gMode === "pan") act("pan end")
            gMode = "three"; gC0 = centroid(l); gSteps = 0
        } else if (n === 2 && gMode === "") {
            gC0 = centroid(l); gD0 = spread(l); gDLast = gD0
        } else if (n < 2 && (gMode === "pan" || gMode === "pinch")) {
            endGesture()
        }
    }
    function fingersMoved() {
        if (traceAdjust) { adjustMoved(); return }
        var l = gList()
        for (var id in gPts) {
            var a = gPts[id], b = gStart[id]
            if (b) gMoved = Math.max(gMoved, Math.abs(a.x - b.x) + Math.abs(a.y - b.y))
        }
        if (gMode === "dead" || !gC0) return
        if (l.length === 2 && (gMode === "" || gMode === "pan" || gMode === "pinch")) {
            var c = centroid(l), d = spread(l)
            if (gMode === "") {
                if (gD0 > 0 && Math.abs(d / gD0 - 1) > 0.18) { gMode = "pinch"; gDLast = gD0 }
                else if (Math.abs(c.x - gC0.x) + Math.abs(c.y - gC0.y) > 30) { gMode = "pan"; act("pan begin"); gSent = {x: 0, y: 0} }
            }
            if (gMode !== "") gTapOk = false
            if (gMode === "pinch") {
                while (d / gDLast > 1.25) { act("zoomin"); gDLast *= 1.25; say("Zoom in") }
                while (d / gDLast < 0.8) { act("zoomout"); gDLast *= 0.8; say("Zoom out") }
            } else if (gMode === "pan") {
                var dx = c.x - gC0.x, dy = c.y - gC0.y
                if (Math.abs(dx - gSent.x) + Math.abs(dy - gSent.y) >= 3) {
                    act("pan " + Math.round(dx) + " " + Math.round(dy)); gSent = {x: dx, y: dy}
                }
            }
        } else if ((gMode === "five" || gMode === "rotate") && l.length === 5) {
            var a = twist()
            if (gMode === "five") {
                if (Math.abs(a) < 8) return
                gMode = "rotate"; gTapOk = false; act("rotate begin")
            }
            if (Math.abs(a - gRotSent) >= 1) { act("rotate " + a.toFixed(1)); gRotSent = a }
        } else if (gMode === "three" && l.length === 3) {
            // one undo per step of drag to the left, one redo per step to the right; dragging
            // back takes steps back. Mostly sideways only, so a vertical slide does nothing.
            var c3 = centroid(l), sx = c3.x - gC0.x, sy = c3.y - gC0.y
            if (gSteps === 0 && Math.abs(sx) < 1.5 * Math.abs(sy)) return
            var k = Math.floor(Math.abs(sx) / swipeStep), target = sx < 0 ? -k : k
            if (target === gSteps) return
            gTapOk = false
            while (gSteps > target) { act("undo"); gSteps-- }
            while (gSteps < target) { act("redo"); gSteps++ }
            say(gSteps < 0 ? "Undo " + (-gSteps) : gSteps > 0 ? "Redo " + gSteps : "No change")
        }
    }
    function fingersUp() {
        if (gMode === "three") { gMode = "dead"; if (gSteps !== 0) gTapOk = false }
    }

    Timer { id: toastTimer; interval: 1600; onTriggered: win.toast = "" }

    Component {  // one stroke, live or finished
        id: fader
        Item {
            id: st
            property bool trail: true
            function fade(ms) { hold.interval = ms; hold.start() }
            // E-ink can't fade smoothly (grey steps ghost and flash in the fast pen waveform),
            // so a trail holds for fadeMs after the pen lifts and is then removed in one update.
            Timer { id: hold; onTriggered: st.destroy() }
        }
    }

    Component {
        id: seg
        Item {  // zero-size pivot at the segment start; rotation turns it toward the end point
            property real len
            property real w
            Rectangle { x: -w / 2; y: -w / 2; width: len + w; height: w; radius: w / 2; color: "black"; antialiasing: false }
        }
    }

    Item {
        id: view
        width: rot % 2 ? win.height : win.width
        height: rot % 2 ? win.width : win.height
        anchors.centerIn: parent
        rotation: rot * 90

        // ------------------------------------------------ paper and tracing image, under the ink
        // Drawn once as plain black items: crisp 1-3 px lines stay sharp on e-ink, where
        // grey or anti-aliased strokes dither. Only the drawing area, never under the panel.
        Item {
            id: paperArea
            visible: win.mode === "ink"
            x: win.panelSide === "left" ? win.panelW : 0
            width: view.width - win.panelW; height: view.height
            clip: true
            Image {
                visible: win.traceSrc !== ""
                source: win.traceSrc
                asynchronous: true; cache: false; smooth: true
                sourceSize.width: 2400; sourceSize.height: 2400   // keep memory sane for huge photos
                fillMode: Image.PreserveAspectFit
                width: Math.max(1, view.width * win.traceScale); height: Math.max(1, view.height * win.traceScale)
                x: (paperArea.width - width) / 2 + win.traceX; y: (paperArea.height - height) / 2 + win.traceY
                opacity: win.traceOpacity
            }
            Loader {
                anchors.fill: parent
                active: win.paper !== "plain"
                sourceComponent: win.paper === "dots" || win.paper === "cross" ? marks : lines
            }
            Rectangle {  // while adjusting: a frame says fingers move the image
                visible: win.traceAdjust; anchors.fill: parent; anchors.margins: 6
                color: "transparent"; border.width: 6; border.color: "black"
                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter; y: 24; width: hint.width + 40; height: 60
                    color: "black"; radius: 12
                    Text { id: hint; anchors.centerIn: parent; color: "white"; font.pixelSize: 26; font.bold: true
                           text: "Moving the image: 1 finger drags, 2 fingers zoom" }
                }
            }
        }
        Component {  // families of parallel lines, each rotated about the centre
            id: lines
            Item {
                id: fam
                readonly property real step: win.paperStep
                readonly property var sets: win.paper === "lined" ? [[0, step, 2]]
                    : win.paper === "squared" ? [[0, step, 1], [90, step, 1]]
                    : win.paper === "blueprint" ? [[0, step, 1], [90, step, 1], [0, step * 5, 3], [90, step * 5, 3]]
                    : win.paper === "isometric" ? [[0, step * 0.866, 1], [60, step * 0.866, 1], [120, step * 0.866, 1]] : []
                readonly property real diag: Math.ceil(Math.sqrt(width * width + height * height) / step + 2) * step
                Repeater {
                    model: fam.sets
                    Item {
                        property var set: modelData
                        width: fam.diag; height: fam.diag
                        x: (fam.width - width) / 2; y: (fam.height - height) / 2
                        rotation: set[0]
                        Repeater {
                            model: Math.floor(fam.diag / parent.set[1]) + 1
                            Rectangle {
                                y: index * parent.set[1]; width: parent.width; height: parent.set[2]
                                color: "black"; antialiasing: false
                            }
                        }
                    }
                }
            }
        }
        Component {  // a dot or a small cross at every grid point
            id: marks
            Item {
                id: mk
                readonly property real step: win.paperStep
                readonly property int cols: Math.floor(width / step) + 1
                readonly property bool cross: win.paper === "cross"
                Repeater {
                    model: mk.cols * (Math.floor(mk.height / mk.step) + 1)
                    Item {
                        x: (index % mk.cols) * mk.step; y: Math.floor(index / mk.cols) * mk.step
                        Rectangle { visible: !mk.cross; x: -2; y: -2; width: 4; height: 4; color: "black"; antialiasing: false }
                        Rectangle { visible: mk.cross; x: -7; y: -1; width: 14; height: 2; color: "black"; antialiasing: false }
                        Rectangle { visible: mk.cross; x: -1; y: -7; width: 2; height: 14; color: "black"; antialiasing: false }
                    }
                }
            }
        }

        Item { id: ink; objectName: "ink"; anchors.fill: parent }

        Rectangle {  // active area that maps to the monitor
            visible: win.mode === "ink"
            x: win.area[0] * view.width; y: win.area[1] * view.height
            width: (win.area[2] - win.area[0]) * view.width; height: (win.area[3] - win.area[1]) * view.height
            color: "transparent"; border.color: "black"; border.width: 2
        }

        // ---------------------------------------------------------------- side panel
        Item {
            id: panel
            visible: win.panelOn && win.panelOpen
            x: win.panelX; width: win.panelW; height: view.height
            Rectangle { anchors.fill: parent; color: "white" }
            Rectangle { x: win.panelSide === "right" ? 0 : parent.width - 3; width: 3; height: parent.height; color: "black" }
            Column {
                x: win.panelSide === "right" ? 22 : win.tabS + 14; y: 24
                width: parent.width - win.tabS - 36; spacing: 8
                Text { text: "PAPER\nTABLET"; font.pixelSize: 22; font.bold: true; font.letterSpacing: 4; color: "black"; lineHeight: 1.1 }
            }
            Item {  // profile row: < name >
                id: prof
                x: 16; y: win.profY; width: parent.width - 32; height: win.profH
                property bool many: win.profileCount > 1
                Rectangle { anchors.fill: parent; radius: 14; border.width: 3; border.color: "black"; color: "white" }
                Text {
                    x: prof.many ? 56 : 12; width: parent.width - 2 * x; anchors.verticalCenter: parent.verticalCenter
                    horizontalAlignment: Text.AlignHCenter; text: win.profileName; font.pixelSize: 26; font.bold: true
                    fontSizeMode: Text.HorizontalFit; minimumPixelSize: 14; elide: Text.ElideRight; color: "black"
                }
                Repeater {
                    model: prof.many ? 2 : 0
                    Item {  // solid triangles from 2 px rows, like the fold tab
                        x: index === 0 ? 16 : prof.width - 16 - 22; anchors.verticalCenter: parent.verticalCenter
                        width: 22; height: 30; rotation: index === 0 ? 180 : 0
                        Repeater {
                            model: 15
                            Rectangle {
                                property real w: 22 * (1 - Math.abs(index - 7) / 7.5)
                                y: index * 2; width: w; height: 2; color: "black"; antialiasing: false
                            }
                        }
                    }
                }
            }
            Text { x: 22; y: win.profY + win.profH + 8; width: parent.width - 44; text: win.toast || win.status
                   font.pixelSize: 21; font.bold: win.toast !== ""; color: "black"; elide: Text.ElideRight }
            Repeater {
                model: win.buttons.length
                Item {
                    id: cell
                    property var b: win.buttons[index]
                    property bool none: !b || b.kind === "none"
                    property bool on: win.pressing === index || (b.kind === "hold" && !!win.latched[index])
                                      || (b.kind === "pen" && win.penOut) || (b.kind === "snap" && win.snapOn)
                                      || (b.kind === "trace" && win.traceAdjust) || (b.kind === "light" && win.lightOn)
                    x: (index % 2) * win.cellW
                    y: win.headH + Math.floor(index / 2) * win.cellH
                    width: win.cellW; height: win.cellH
                    Rectangle {
                        visible: !cell.none
                        anchors.fill: parent; anchors.margins: 8
                        radius: 14; border.width: 3; border.color: "black"
                        color: cell.on ? "black" : "white"
                        Column {
                            anchors.centerIn: parent; spacing: 6; width: parent.width - 12
                            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: cell.b.t
                                   font.pixelSize: 28; font.bold: true; fontSizeMode: Text.HorizontalFit; minimumPixelSize: 16
                                   color: cell.on ? "white" : "black" }
                            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 18
                                   fontSizeMode: Text.HorizontalFit; minimumPixelSize: 12
                                   text: cell.b.kind === "pen" ? (win.penOut ? "on" : "off") : cell.b.kind === "snap" ? (win.snapOn ? "on" : "off")
                                       : cell.b.kind === "light" ? (win.lightOn ? "on" : "off") : cell.b.kind === "trace" ? (win.traceAdjust ? "moving" : "image") : cell.b.s
                                   color: cell.on ? "white" : "black" }
                        }
                    }
                }
            }
            Text {
                x: 22; y: view.height - win.footH + 18; width: parent.width - 44; wrapMode: Text.Wrap
                font.pixelSize: 19; color: "black"; lineHeight: 1.1
                text: win.gesturesOn ? "3 fingers swipe: left undo, right redo\n2 fingers: pan, pinch to zoom\n5 fingers: twist to rotate"
                                     : "Power button: back to notebooks"
            }
        }

        // The fold tab: top-left corner (top-right for a right-hand panel), open or folded.
        Rectangle {
            visible: win.panelOn
            x: win.panelSide === "right" ? view.width - win.tabS : 0; y: 0
            width: win.tabS; height: win.tabS
            color: win.penBtn === -2 ? "black" : "white"; border.width: 3; border.color: "black"
            // a solid triangle pointing the way the panel will move, built from 2 px rows so it
            // stays crisp on e-ink (no anti-aliased edges to drop out)
            Item {
                anchors.centerIn: parent; width: 26; height: 36
                rotation: ((win.panelSide === "right") === win.panelOpen) ? 180 : 0
                Repeater {
                    model: 18
                    Rectangle {
                        property real w: 26 * (1 - Math.abs(index - 8.5) / 9)
                        x: 26 - w; y: index * 2; width: w; height: 2
                        color: win.penBtn === -2 ? "white" : "black"; antialiasing: false
                    }
                }
            }
        }

        // Fingers: panel buttons, the fold tab and gestures. Mouse events are left alone, so the
        // pen never lands here.
        MultiPointTouchArea {
            anchors.fill: parent
            mouseEnabled: false
            maximumTouchPoints: 10
            onPressed: (pts) => {
                var changed = false
                win.gLastEv = Date.now()
                for (var i = 0; i < pts.length; i++) {
                    var t = pts[i], p = Qt.point(t.x, t.y)
                    console.warn("LOG:touch " + Math.round(t.x) + "," + Math.round(t.y) + " size " + Math.round(win.touchSize(t)))
                    if (win.inPanel(p)) {  // the panel answers even with the pen hovering: the other hand taps it
                        var tb = win.touchBtn; tb[t.pointId] = win.pressAt(p); win.touchBtn = tb
                        continue
                    }
                    if (win.palmCheck(t)) { changed = true; continue }
                    var g = win.gPts; g[t.pointId] = {x: t.x, y: t.y}; win.gPts = g
                    var st = win.gStart; st[t.pointId] = {x: t.x, y: t.y}; win.gStart = st
                    changed = true
                }
                if (changed) win.fingersChanged()
            }
            onUpdated: (pts) => {
                var g = win.gPts, moved = false, palm = false
                win.gLastEv = Date.now()
                for (var i = 0; i < pts.length; i++) {
                    var t = pts[i]
                    if (g[t.pointId] && win.palmCheck(t)) { palm = true; continue }
                    if (g[t.pointId]) { g[t.pointId] = {x: t.x, y: t.y}; moved = true }
                }
                if (palm) { win.fingersChanged(); return }
                if (moved) { win.gPts = g; win.fingersMoved() }
            }
            onReleased: (pts) => {
                var g = win.gPts, changed = false
                win.gLastEv = Date.now()
                for (var i = 0; i < pts.length; i++) {
                    var t = pts[i]
                    if (win.palms[t.pointId]) {
                        var pl = win.palms; delete pl[t.pointId]; win.palms = pl
                        if (Object.keys(g).length === 0) changed = true
                        continue
                    }
                    if (t.pointId in win.touchBtn) {
                        var tb = win.touchBtn
                        if (tb[t.pointId] >= 0) win.releaseBtn(tb[t.pointId], false)
                        delete tb[t.pointId]; win.touchBtn = tb
                        continue
                    }
                    if (g[t.pointId]) {
                        if (win.gMode === "three") win.fingersUp()
                        delete g[t.pointId]; changed = true
                    }
                }
                if (changed) { win.gPts = g; win.fingersChanged() }
            }
            onCanceled: (pts) => { win.endGesture(); win.gPts = ({}); win.palms = ({}); win.fingersChanged(); win.pressing = -1 }
        }

        Repeater {  // 9 calibration crosshairs, reading order, 10% inset (matches the PDF sheet)
            model: win.mode === "targets" ? 9 : 0
            Item {
                property real u: [0.1, 0.5, 0.9][index % 3]
                property real v: [0.1, 0.5, 0.9][Math.floor(index / 3)]
                property bool hot: win.nextTarget === index + 1
                x: u * view.width; y: v * view.height
                Rectangle { x: -40; y: -1.5; width: 80; height: 3; color: "black" }
                Rectangle { x: -1.5; y: -40; width: 3; height: 80; color: "black" }
                Rectangle { x: -14; y: -14; width: 28; height: 28; color: "transparent"; border.width: 2; border.color: "black" }
                Rectangle { visible: hot; x: -34; y: -34; width: 68; height: 68; radius: 34
                            color: "transparent"; border.width: 6; border.color: "black" }
                Text { x: 22; y: v > 0.6 ? -60 : 22; text: index + 1; font.pixelSize: hot ? 44 : 30; font.bold: hot }
            }
        }

        Text {
            visible: win.mode === "targets"
            anchors.centerIn: parent; anchors.verticalCenterOffset: view.height * 0.2
            horizontalAlignment: Text.AlignHCenter
            font.pixelSize: 34
            text: win.nextTarget > 0 ? "Tap the centre of crosshair " + win.nextTarget : "Calibration targets"
        }

        Text {
            visible: !win.panelOn
            x: 30; y: 20
            font.pixelSize: 26; color: "black"
            text: win.status + "    (power button: back to notebooks)"
        }
    }
}
)QML";
