# Session Workers for Executor Parallelism

**Status:** accepted
**Date:** 2026-05-15

The trace executor needs to log in multiple Actor Sessions concurrently, and later needs a path toward concurrent planned-step execution. Playwright's Python sync API is not thread-safe when one Playwright instance is shared across threads, while converting every browser tool to the async API would create broad churn before planned-step parallelism is implemented.

We run each Actor Session through its own worker thread. Each worker owns one Playwright instance, browser, browser context, and page. Login jobs for all Actor Sessions can be submitted at once, and existing sync browser tools keep their current shape.

## Consequences

- Initial Actor Session login is no longer limited by execution-wave concurrency.
- Browser tools continue to use the sync Playwright API.
- Future planned-step parallelism can submit work to the same Actor Session workers instead of sharing Playwright objects across threads.
- Each active Actor Session owns a browser process, so high concurrency uses more local resources than shared-context execution.
