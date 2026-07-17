package nl.tsym.tackbox.report;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.sentry.SentryEvent;
import io.sentry.SentryLevel;
import java.lang.reflect.Constructor;
import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import java.util.stream.Collectors;
import java.util.stream.IntStream;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class ReportTest {

    // Well-formed but unroutable DSN. The recording beforeSend returns null, so
    // events are captured for assertion and never handed to transport.
    private static final String FAKE_DSN = "https://sample@example.invalid/1";

    private final List<SentryEvent> events = new CopyOnWriteArrayList<>();
    private final List<Notice> notices = new CopyOnWriteArrayList<>();

    private Options recordingOptions() {
        return new Options()
                .dsn(FAKE_DSN)
                .beforeSend((event, hint) -> {
                    events.add(event);
                    return null;
                });
    }

    @BeforeEach
    void setUp() {
        Report.resetForTest();
        events.clear();
        notices.clear();
    }

    @AfterEach
    void tearDown() {
        Report.resetForTest();
    }

    @Test
    void initNoOpOnEmptyDsn() {
        Report.init(new Options().dsn("").silentMissing(true));
        assertFalse(Report.ready(), "empty DSN must leave capture disabled");
        // Safe without a live endpoint: log-only, no throw, no shipped event.
        Report.error("boom", new RuntimeException("x"), Map.of("k", "v"), "area.x");
        assertTrue(events.isEmpty(), "log-only mode must not ship events");
    }

    @Test
    void rateLimitDropsRepeatInWindow() {
        Report.init(recordingOptions());
        assertTrue(Report.ready());
        Report.error("first", new RuntimeException("a"), null, "area.dup");
        Report.error("second", new RuntimeException("b"), null, "area.dup");
        assertEquals(1, events.size(), "second capture within the window must be dropped");
    }

    @Test
    void concurrentCapturesDoNotBleedScope() throws InterruptedException {
        Report.init(recordingOptions());
        int n = 32;
        ExecutorService pool = Executors.newFixedThreadPool(8);
        CountDownLatch start = new CountDownLatch(1);
        for (int i = 0; i < n; i++) {
            String key = "area.k" + i;
            pool.execute(() -> {
                awaitStart(start);
                Report.error("concurrent", new RuntimeException(key), Map.of("worker", key), key);
            });
        }
        start.countDown(); // release all workers at once to maximize contention
        pool.shutdown();
        assertTrue(pool.awaitTermination(10, TimeUnit.SECONDS), "workers must finish");

        assertEquals(n, events.size(), "every distinct key must produce its own event");
        Set<String> seen = events.stream()
                .map(ev -> {
                    assertEquals(1, ev.getFingerprints().size(), "one fingerprint per event");
                    assertEquals(SentryLevel.ERROR, ev.getLevel());
                    return ev.getFingerprints().get(0);
                })
                .collect(Collectors.toSet());
        Set<String> expected = IntStream.range(0, n)
                .mapToObj(i -> "area.k" + i)
                .collect(Collectors.toSet());
        assertEquals(expected, seen, "fingerprints must not bleed across threads");
    }

    @Test
    void errorDispatchesUserLaneEvenWhenNotReady() {
        Report.init(new Options().dsn("").silentMissing(true));
        Report.setNotifier(notices::add);
        assertFalse(Report.ready());
        Report.error("connection lost mid-stream", new RuntimeException("boom"), Map.of("area", "net"), "net.conn");
        assertEquals(1, notices.size(), "user lane delivers with capture disabled");
        assertEquals("error", notices.get(0).level());
        assertEquals("net.conn", notices.get(0).dedupKey());
        assertTrue(events.isEmpty(), "capture stays gated off when not ready");
    }

    @Test
    void errorDispatchesUserLaneWhenRateLimited() {
        Report.init(recordingOptions());
        Report.setNotifier(notices::add);
        Report.error("poll failed on stale token", new RuntimeException("e1"), null, "poll.stale");
        Report.error("poll failed on stale token", new RuntimeException("e2"), null, "poll.stale");
        assertEquals(1, events.size(), "duplicate capture dropped within the window");
        assertEquals(2, notices.size(), "every event reaches the user lane");
    }

    @Test
    void quietCapturesWarningNoUserLane() {
        Report.init(recordingOptions());
        Report.setNotifier(notices::add);
        Report.quiet("cache refresh degraded, using stale", new RuntimeException("timeout"), null, "cache.refresh");
        assertEquals(1, events.size());
        assertEquals(SentryLevel.WARNING, events.get(0).getLevel());
        assertEquals(List.of("cache.refresh"), events.get(0).getFingerprints());
        assertTrue(notices.isEmpty(), "quiet never touches the user lane");
    }

    @Test
    void notifyUserLaneOnlyDoesNotConsumeRateSlot() {
        Report.init(recordingOptions());
        Report.setNotifier(notices::add);
        Report.notify("you appear to be offline", new RuntimeException("net down"), null, "conn.offline");
        assertEquals(1, notices.size());
        assertEquals("notice", notices.get(0).level());
        assertTrue(events.isEmpty(), "notify captures nothing");
        // Same dedupKey still captures: notify consumed no rate slot.
        Report.error("still offline after retry", new RuntimeException("net down"), null, "conn.offline");
        assertEquals(1, events.size());
        assertEquals(2, notices.size());
    }

    @Test
    void panicFeedsUserLaneAndCaptures() {
        Report.init(recordingOptions());
        Report.setNotifier(notices::add);
        Report.panic("tray-loop", "boom");
        assertEquals(1, notices.size(), "panic feeds the user lane");
        assertEquals("fatal", notices.get(0).level());
        assertEquals("panic:tray-loop", notices.get(0).dedupKey());
        assertEquals(1, events.size(), "panic captures under its per-name fingerprint");
        assertEquals(List.of("panic:tray-loop"), events.get(0).getFingerprints());
        assertEquals(SentryLevel.FATAL, events.get(0).getLevel());
    }

    @Test
    void notifierExceptionDoesNotBreakCaller() {
        Report.init(recordingOptions());
        Report.setNotifier(n -> {
            throw new RuntimeException("notifier is broken");
        });
        // Returns normally: a propagating notifier error would fail this test.
        Report.error("upload failed mid-flight", new RuntimeException("hangup"), null, "upload.fail");
        assertEquals(2, events.size(), "original event + quiet capture of the notifier failure");
        List<List<String>> fps = events.stream().map(e -> e.getFingerprints()).toList();
        assertTrue(fps.contains(List.of("upload.fail")), "original event still captured");
        assertTrue(fps.contains(List.of("report.notifier")), "notifier failure on the quiet lane");
    }

    @Test
    void publicApiIsReportingOnly() throws NoSuchMethodException {
        int ps = Modifier.PUBLIC | Modifier.STATIC;
        Set<Sig> expected = Set.of(
                new Sig("init", void.class, List.<Class<?>>of(Options.class), ps),
                new Sig("ready", boolean.class, List.<Class<?>>of(), ps),
                new Sig("dsnFromEnv", String.class, List.<Class<?>>of(), ps),
                new Sig("verify", void.class, List.<Class<?>>of(long.class), ps),
                new Sig("flush", void.class, List.<Class<?>>of(), ps),
                new Sig("flush", void.class, List.<Class<?>>of(long.class), ps),
                new Sig("setNotifier", void.class, List.<Class<?>>of(Consumer.class), ps),
                new Sig("error", void.class,
                        List.<Class<?>>of(String.class, Throwable.class, Map.class, String.class), ps),
                new Sig("warn", void.class,
                        List.<Class<?>>of(String.class, Throwable.class, Map.class, String.class), ps),
                new Sig("quiet", void.class,
                        List.<Class<?>>of(String.class, Throwable.class, Map.class, String.class), ps),
                new Sig("notify", void.class,
                        List.<Class<?>>of(String.class, Throwable.class, Map.class, String.class), ps),
                new Sig("panic", void.class, List.<Class<?>>of(String.class, Object.class), ps),
                new Sig("crumb", void.class,
                        List.<Class<?>>of(String.class, String.class, Map.class), ps));
        Set<Sig> actual = Arrays.stream(Report.class.getDeclaredMethods())
                .filter(m -> !m.isSynthetic())
                .filter(m -> Modifier.isPublic(m.getModifiers()) && Modifier.isStatic(m.getModifiers()))
                .map(m -> new Sig(m.getName(), m.getReturnType(),
                        List.of(m.getParameterTypes()), m.getModifiers()))
                .collect(Collectors.toSet());
        assertEquals(expected, actual, "Report's public static methods must be reporting-only");

        assertEquals(Set.of(Report.ReportInitException.class),
                Set.of(Report.class.getDeclaredClasses()),
                "Report must declare no nested type beyond ReportInitException");

        int m = Report.ReportInitException.class.getModifiers();
        assertTrue(Modifier.isPublic(m) && Modifier.isStatic(m) && Modifier.isFinal(m),
                "ReportInitException must be public static final");
        assertEquals(RuntimeException.class, Report.ReportInitException.class.getSuperclass(),
                "ReportInitException must extend RuntimeException");
        Constructor<?> ctor = Report.ReportInitException.class.getConstructor(String.class);
        assertTrue(Modifier.isPublic(ctor.getModifiers()), "ReportInitException(String) must be public");
    }

    // Structured public-method signature for the reporting-only surface fixture:
    // name, return type, ordered parameter types, and modifiers.
    private record Sig(String name, Class<?> ret, List<Class<?>> params, int modifiers) {}

    // Blocks until the test releases the latch; rethrows on interrupt so the
    // catch propagates (never a silent swallow).
    private static void awaitStart(CountDownLatch latch) {
        try {
            latch.await();
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted waiting for start", ie);
        }
    }
}
