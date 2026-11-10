<?php

declare(strict_types=1);

/**
 * Minimal ".env" file reader -- "KEY=value" lines, "#" comments, blank
 * lines ignored. No quoting/escaping support (not needed for the one
 * plain token this project stores here); matches the project's existing
 * Python-side .env convention rather than pulling in a Composer package
 * for something this small.
 */
final class Env
{
    public static function load(string $path): array
    {
        if (!is_readable($path)) {
            return [];
        }

        $values = [];
        foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
            $line = trim($line);
            if ($line === '' || $line[0] === '#' || !str_contains($line, '=')) {
                continue;
            }
            [$key, $value] = explode('=', $line, 2);
            $values[trim($key)] = trim($value);
        }
        return $values;
    }
}
