<?php

declare(strict_types=1);

/**
 * Shared-secret check for both endpoints. The token travels as either a
 * header (what the extension sends on its POST) or a query/POST param
 * (so feed.php's URL -- the one pasted into the ChatGPT scheduled task,
 * or opened directly in a browser for a manual look -- can carry it
 * directly in the link, no header-setting required).
 */
final class Auth
{
    public const HEADER_NAME = 'HTTP_X_CAPTURE_TOKEN';

    public static function extractToken(array $server, array $get, array $post): ?string
    {
        foreach ([$server[self::HEADER_NAME] ?? null, $get['token'] ?? null, $post['token'] ?? null] as $candidate) {
            if (is_string($candidate) && $candidate !== '') {
                return $candidate;
            }
        }
        return null;
    }

    public static function isValid(?string $provided, string $expected): bool
    {
        if ($provided === null || $expected === '') {
            return false;
        }
        return hash_equals($expected, $provided);
    }
}
