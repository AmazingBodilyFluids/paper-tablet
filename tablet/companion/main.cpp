// rmpp-companion: optional tablet-side screen for the PC bridge (reMarkable Paper Pro, and the
// reMarkable 1 and 2 from the same source, built with their own SDKs).
//
// Runs only while the PC app keeps it open: xochitl is stopped by run.sh and restarted when
// this exits. Draws calibration crosshairs, a status line, the active-area outline and an
// ink preview that is never saved. Renders through reMarkable's documented Qt Quick
// "epaper" backend (developer.remarkable.com/documentation/qt_epaper).
//
// Control: one command per line on stdin (from the PC over SSH):
//   rot N            content rotation in 90-degree clockwise steps (3 = landscape, left side up)
//   mode targets|ink|blank
//   next N           highlight crosshair N (1..9, reading order); 0 = none
//   status TEXT      status line
//   area U0 V0 U1 V1 active-area outline in view fractions
//   clear            wipe the ink
//   snap W X Y X Y.. replace the current/last stroke with a clean shape (digitizer fractions)
//   panel left|right|off [FRAC]   side panel with shortcut buttons, FRAC of the view width
//   state pen 0|1 snap 0|1        button states owned by the PC
//   toast TEXT       short message in the panel
//   unlatch          show all hold keys released (the PC released them)
// Back to the PC, on stdout: "ACT:<action>" for panel buttons and finger gestures
// (undo, redo, smaller, bigger, brush, eraser, zoomin, zoomout, fit, down/up KEY, pen 0|1, snap 0|1).
//   quit
// Exits on stdin EOF or 5 s without "ping" (PC gone), the power button, SIGTERM/SIGHUP/SIGINT.

#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQuickWindow>
#include <QScreen>
#include <QMetaEnum>
#include <QQuickItem>
#include <QElapsedTimer>
#include <QSocketNotifier>
#include <QTimer>
#include <QVariant>

#include <csignal>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <linux/input.h>
#include <sys/ioctl.h>
#include <unistd.h>

// QML console output: "ACT:..." lines are actions for the PC and go to stdout on their own line;
// everything else goes to stderr as before ("LOG:" marks our own diagnostics).
static void on_message(QtMsgType, const QMessageLogContext &, const QString &msg) {
    QByteArray m = msg.toUtf8();
    int at = m.indexOf("ACT:");
    if (at >= 0) { fprintf(stdout, "%s\n", m.constData() + at); fflush(stdout); return; }
    int lg = m.indexOf("LOG:");
    if (lg >= 0) { fprintf(stderr, "companion: %s\n", m.constData() + lg + 4); return; }
    fprintf(stderr, "%s\n", m.constData());
}

static int sigpipe[2];
static void on_signal(int) { char c = 1; (void)!write(sigpipe[1], &c, 1); }

static int open_input(const char *needle, char *path_out, size_t n) {
    for (int i = 0; i < 16; i++) {
        char path[64], name[128] = "";
        snprintf(path, sizeof path, "/dev/input/event%d", i);
        int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        ioctl(fd, EVIOCGNAME(sizeof name), name);
        if (strstr(name, needle)) {
            snprintf(path_out, n, "%s", path);
            return fd;
        }
        close(fd);
    }
    return -1;
}

extern const char *COMPANION_QML;

// Fast pen refresh. reMarkable's epaper scene-graph plugin (libqsgepaper.so, shipped in the
// official SDK) contains EPScreenModeItem: a QQuickItem whose `mode` property (enum keys Pen,
// Mono, Animation, UI, Content, Sleep) picks the e-ink update mode for the area it covers;
// xochitl uses it for low-latency ink. It is not registered with QML and not documented, so it
// is created here through its exported constructor and checked through Qt's meta-object system
// before use. Anything unexpected and the app simply keeps the default (slower) mode.
// COMPANION_NO_PENMODE=1 turns it off.
static QQuickItem *make_pen_mode_item(QQuickItem *parent) {
    if (getenv("COMPANION_NO_PENMODE")) return nullptr;
    void *lib = dlopen("/usr/lib/plugins/scenegraph/libqsgepaper.so", RTLD_NOW | RTLD_NOLOAD);
    if (!lib) { fprintf(stderr, "companion: pen mode: epaper plugin not loaded\n"); return nullptr; }
    using Ctor = void (*)(void *, QQuickItem *);
    auto ctor = reinterpret_cast<Ctor>(dlsym(lib, "_ZN16EPScreenModeItemC1EP10QQuickItem"));
    if (!ctor) { fprintf(stderr, "companion: pen mode: EPScreenModeItem not found\n"); return nullptr; }
    // The class size is not public; this is far larger than a QQuickItem subclass needs.
    void *mem = ::operator new(4096);
    memset(mem, 0, 4096);
    ctor(mem, parent);
    auto *item = static_cast<QQuickItem *>(mem);
    const QMetaObject *mo = item->metaObject();
    int pi = mo->indexOfProperty("mode");
    if (strcmp(mo->className(), "EPScreenModeItem") != 0 || pi < 0 || !mo->property(pi).isEnumType()) {
        fprintf(stderr, "companion: pen mode: unexpected class %s, not used\n", mo->className());
        item->setVisible(false);
        return nullptr;
    }
    bool ok = false;
    int pen = mo->property(pi).enumerator().keyToValue("Pen", &ok);
    if (!ok) { fprintf(stderr, "companion: pen mode: no Pen mode\n"); item->setVisible(false); return nullptr; }
    item->setProperty("mode", pen);
    auto fit = [item, parent] { item->setPosition(QPointF(0, 0)); item->setSize(parent->size()); };
    fit();
    QObject::connect(parent, &QQuickItem::widthChanged, item, fit);
    QObject::connect(parent, &QQuickItem::heightChanged, item, fit);
    fprintf(stderr, "companion: pen mode on (mode %d = %s)\n", item->property("mode").toInt(),
            mo->property(pi).enumerator().valueToKey(item->property("mode").toInt()));
    return item;
}

int main(int argc, char **argv) {
    // stdout/stderr go through a pipe to the PC; keep the e-paper engine's own prints in order
    // with ours instead of arriving in a block at exit.
    setvbuf(stdout, nullptr, _IOLBF, 0);
    setvbuf(stderr, nullptr, _IONBF, 0);
    qInstallMessageHandler(on_message);
    QGuiApplication app(argc, argv);
    if (pipe(sigpipe) == 0) {
        for (int s : {SIGTERM, SIGHUP, SIGINT}) std::signal(s, on_signal);
        auto *sn = new QSocketNotifier(sigpipe[0], QSocketNotifier::Read, &app);
        QObject::connect(sn, &QSocketNotifier::activated, &app, [] { QGuiApplication::quit(); });
    }
    std::signal(SIGPIPE, SIG_IGN);

    QQmlApplicationEngine engine;
    engine.loadData(QByteArray(COMPANION_QML));
    if (engine.rootObjects().isEmpty()) {
        fprintf(stderr, "companion: QML failed to load\n");
        return 2;
    }
    QObject *root = engine.rootObjects().first();
    if (auto *w = qobject_cast<QQuickWindow *>(root)) {
        QScreen *scr = QGuiApplication::primaryScreen();
        QRect g = scr ? scr->geometry() : QRect();
        fprintf(stderr, "companion: screen %dx%d dpr %.2f, scenegraph %s\n", g.width(), g.height(),
                scr ? scr->devicePixelRatio() : 0.0, qPrintable(QQuickWindow::sceneGraphBackend()));
        if (w->width() <= 0 || w->height() <= 0) {
            // Paper Pro panel, portrait; used only if the platform reports no screen size (the rM1 / rM2
            // report theirs, 1404 x 1872).
            if (g.width() <= 0 || g.height() <= 0) g = QRect(0, 0, 1620, 2160);
            w->setGeometry(g);
        }
        w->show();
        fprintf(stderr, "companion: window %dx%d, visible %d\n", w->width(), w->height(), int(w->isVisible()));
        // Proof that the scene graph actually reached the panel (logged once).
        QObject::connect(w, &QQuickWindow::frameSwapped, w, [w] {
            static int n = 0;
            if (n++ != 0) return;
            fprintf(stderr, "companion: first frame drawn (%dx%d)\n", w->width(), w->height());
            // The epaper plugin is certainly loaded by now; add the pen-mode area (GUI thread).
            QMetaObject::invokeMethod(w, [w] {
                if (auto *ink = w->findChild<QQuickItem *>("ink")) {
                    // Hand the item and its mode numbers to QML, which switches to Content mode
                    // briefly when the paper, tracing image or mask change (Pen mode only
                    // flushes on pen activity).
                    if (QQuickItem *pm = make_pen_mode_item(ink)) {
                        QMetaEnum e = pm->metaObject()->property(pm->metaObject()->indexOfProperty("mode")).enumerator();
                        for (auto [key, prop] : {std::pair{"Pen", "modePen"}, {"UI", "modeUI"}, {"Animation", "modeAnim"}, {"Content", "modeContent"}}) {
                            bool ok = false;
                            int v = e.keyToValue(key, &ok);
                            w->setProperty(prop, ok ? v : -1);
                        }
                        w->setProperty("penModeItem", QVariant::fromValue<QObject *>(pm));
                    }
                } else fprintf(stderr, "companion: pen mode: ink item missing\n");
            }, Qt::QueuedConnection);
        }, Qt::DirectConnection);
    }

    // Pen: read in parallel with the PC stream (no grab). Digitizer -> panel pixels. The Paper
    // Pro's Elan digitizer runs with the panel; the rM1 / rM2 Wacom one is turned 270 degrees
    // (its X runs up the long side from the bottom, its Y to the right).
    char pen_path[64] = "";
    int pen_fd = open_input("marker", pen_path, sizeof pen_path);
    bool wacom = false;
    if (pen_fd < 0) wacom = (pen_fd = open_input("Wacom", pen_path, sizeof pen_path)) >= 0;
    input_absinfo ax{}, ay{}, ap{};
    if (pen_fd >= 0) {
        ioctl(pen_fd, EVIOCGABS(ABS_X), &ax);
        ioctl(pen_fd, EVIOCGABS(ABS_Y), &ay);
        ioctl(pen_fd, EVIOCGABS(ABS_PRESSURE), &ap);
        fprintf(stderr, "companion: pen %s X %d..%d Y %d..%d%s\n", pen_path, ax.minimum, ax.maximum,
                ay.minimum, ay.maximum, wacom ? " (turned 270)" : "");
    } else {
        fprintf(stderr, "companion: no pen device found\n");
    }
    const bool flip_x = getenv("COMPANION_FLIP_X"), flip_y = getenv("COMPANION_FLIP_Y");
    struct Pen { int x = 0, y = 0, p = 0; bool pen = false, rubber = false, touch = false; } pen;
    if (pen_fd >= 0) {
        auto *pn = new QSocketNotifier(pen_fd, QSocketNotifier::Read, &app);
        QObject::connect(pn, &QSocketNotifier::activated, &app, [&, root] {
            input_event evs[64];
            ssize_t r;
            while ((r = read(pen_fd, evs, sizeof evs)) > 0) {
                for (int i = 0; i < r / (ssize_t)sizeof(input_event); i++) {
                    const input_event &e = evs[i];
                    if (e.type == EV_ABS) {
                        if (e.code == ABS_X) pen.x = e.value;
                        else if (e.code == ABS_Y) pen.y = e.value;
                        else if (e.code == ABS_PRESSURE) pen.p = e.value;
                    } else if (e.type == EV_KEY) {
                        if (e.code == BTN_TOOL_PEN) pen.pen = e.value;
                        else if (e.code == BTN_TOOL_RUBBER) pen.rubber = e.value;
                        else if (e.code == BTN_TOUCH) pen.touch = e.value;
                    } else if (e.type == EV_SYN && e.code == SYN_REPORT) {
                        auto *win = qobject_cast<QQuickWindow *>(root);
                        double W = win ? win->width() : 1620, H = win ? win->height() : 2160;
                        double fx = double(pen.x - ax.minimum) / std::max(1, ax.maximum - ax.minimum);
                        double fy = double(pen.y - ay.minimum) / std::max(1, ay.maximum - ay.minimum);
                        if (wacom) { double t = fx; fx = fy; fy = 1 - t; }
                        if (flip_x) fx = 1 - fx;
                        if (flip_y) fy = 1 - fy;
                        double pr = double(pen.p) / std::max(1, ap.maximum);
                        QMetaObject::invokeMethod(root, "pen", Q_ARG(QVariant, fx * W), Q_ARG(QVariant, fy * H),
                                                  Q_ARG(QVariant, pr), Q_ARG(QVariant, pen.touch && pen.p > 0),
                                                  Q_ARG(QVariant, pen.rubber), Q_ARG(QVariant, pen.pen || pen.rubber));
                    }
                }
            }
        });
    }

    // Power button quits (hand the screen back).
    char pwr_path[64];
    int pwr_fd = open_input("powerkey", pwr_path, sizeof pwr_path);  // Paper Pro, rM2 ("snvs-powerkey")
    if (pwr_fd < 0) pwr_fd = open_input("gpio-keys", pwr_path, sizeof pwr_path);  // rM1: its buttons and power
    if (pwr_fd >= 0) {
        auto *wn = new QSocketNotifier(pwr_fd, QSocketNotifier::Read, &app);
        QObject::connect(wn, &QSocketNotifier::activated, &app, [pwr_fd] {
            input_event e;
            while (read(pwr_fd, &e, sizeof e) == sizeof e)
                if (e.type == EV_KEY && e.code == KEY_POWER && e.value == 1) QGuiApplication::quit();
        });
    }

    // Commands from the PC; EOF means the PC side is gone, so give the screen back.
    fcntl(0, F_SETFL, fcntl(0, F_GETFL) | O_NONBLOCK);
    auto *in = new QSocketNotifier(0, QSocketNotifier::Read, &app);
    QByteArray *buf = new QByteArray;
    // The PC sends "ping" every second. A pulled cable or sleeping PC never produces EOF here,
    // so silence for 5 s also gives the screen (and the pen) back to xochitl.
    auto *heard = new QElapsedTimer;
    heard->start();
    auto *watchdog = new QTimer(&app);
    QObject::connect(watchdog, &QTimer::timeout, &app, [heard] {
        if (heard->elapsed() > 5000) {
            fprintf(stderr, "companion: no keepalive from the PC for 5 s, exiting\n");
            QGuiApplication::quit();
        }
    });
    watchdog->start(1000);
    QObject::connect(in, &QSocketNotifier::activated, &app, [buf, root, in, heard] {
        heard->restart();
        char tmp[4096];
        ssize_t r;
        bool eof = false;
        while (true) {
            r = read(0, tmp, sizeof tmp);
            if (r > 0) { buf->append(tmp, r); continue; }
            if (r == 0) eof = true;
            break;
        }
        int nl;
        while ((nl = buf->indexOf('\n')) >= 0) {
            QString line = QString::fromUtf8(buf->left(nl)).trimmed();
            buf->remove(0, nl + 1);
            if (line == "quit") { QGuiApplication::quit(); return; }
            if (line == "ping") continue;
            if (!line.isEmpty()) QMetaObject::invokeMethod(root, "command", Q_ARG(QVariant, line));
        }
        if (eof) { in->setEnabled(false); QGuiApplication::quit(); }
    });

    fprintf(stderr, "companion: ready\n");
    int rc = app.exec();
    fprintf(stderr, "companion: exiting (%d)\n", rc);
    return rc;
}
