<?php

declare(strict_types=1);

/**
 * Pure request-handling logic for capture.php, separated from the
 * superglobals/php://input it's normally driven by so it's directly
 * unit-testable: pass it a raw body string, get back [status, body,
 * contentType] with no HTTP server involved.
 */
final class CaptureController
{
    public function __construct(private Store $store)
    {
    }

    public function handle(?string $rawBody): array
    {
        $data = ($rawBody === null || $rawBody === '') ? null : json_decode($rawBody, true);
        if (!is_array($data)) {
            return [400, json_encode(['error' => 'request body must be a JSON object']), 'application/json'];
        }

        $record = $this->store->append($data);
        return [200, json_encode(['status' => 'ok', 'id' => $record['id']]), 'application/json'];
    }
}
