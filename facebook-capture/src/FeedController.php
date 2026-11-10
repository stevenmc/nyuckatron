<?php

declare(strict_types=1);

/**
 * Pure request-handling logic for feed.php -- see CaptureController's own
 * docblock for why this is split out from the superglobal-driven script.
 */
final class FeedController
{
    public function __construct(private Store $store)
    {
    }

    public function handle(bool $flush): array
    {
        $batch = $flush ? $this->store->flush() : $this->store->peekPending();
        $body = json_encode($batch, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        return [200, $body, 'application/json'];
    }
}
