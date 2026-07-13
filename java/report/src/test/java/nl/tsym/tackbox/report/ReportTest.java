package nl.tsym.tackbox.report;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.sentry.SentryEvent;
import io.sentry.SentryLevel;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
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
    void taskWrapperUsesPerNameFingerprint() {
        Report.init(recordingOptions());
        Report.safeRunnable("ingest", () -> {
            throw new IllegalStateException("boom");
        }).run();
        assertEquals(1, events.size());
        SentryEvent e = events.get(0);
        assertEquals(List.of("task:ingest"), e.getFingerprints());
        assertEquals(SentryLevel.ERROR, e.getLevel());
        assertEquals("ingest", e.getTag("task"));
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
