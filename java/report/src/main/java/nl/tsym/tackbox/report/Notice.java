package nl.tsym.tackbox.report;

import java.util.Map;

/** One user-lane event handed to the registered notifier ({@link
 *  Report#setNotifier}). The app owns rendering and any coalescing (keyed on
 *  {@code dedupKey}); the helper never suppresses the user lane (DECISIONS
 *  D005). {@code cause} is the caught error the notice is about. */
public record Notice(String msg, String level, Map<String, String> tags, String dedupKey, Throwable cause) {}
