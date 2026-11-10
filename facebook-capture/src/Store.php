<?php

declare(strict_types=1);

/**
 * Flat-JSON-file storage for captured Facebook posts -- no database, this
 * project doesn't need one at this scale. Everything goes through
 * pending.json, held under an exclusive flock for the duration of each
 * operation so a capture landing mid-flush can never be silently dropped:
 * it simply blocks until the flush's lock is released, then appends to
 * what is by then the fresh, empty pending store.
 *
 * append() stores the extension's payload exactly as posted, nested
 * under "payload" rather than merged into the top level -- so a field the
 * client happens to send called "id" or "receivedAt" can never collide
 * with (or overwrite) the server-assigned ones. The rest of this pipeline
 * (the downstream ChatGPT task) was explicitly told to get the raw bundle
 * as-is and do its own semantic extraction; this class's job is just to
 * not lose or mutate that bundle.
 */
final class Store
{
    private string $pendingFile;
    private string $flushedDir;

    public function __construct(string $dataDir)
    {
        $this->pendingFile = rtrim($dataDir, '/') . '/pending.json';
        $this->flushedDir = rtrim($dataDir, '/') . '/flushed';
    }

    public function append(array $payload): array
    {
        return $this->withLock($this->pendingFile, function ($handle) use ($payload) {
            $pending = $this->readJsonArray($handle);
            $record = [
                'id' => bin2hex(random_bytes(8)),
                'receivedAt' => gmdate('c'),
                'payload' => $payload,
            ];
            $pending[] = $record;
            $this->writeJsonArray($handle, $pending);
            return $record;
        });
    }

    /** Read-only: the current pending list, untouched. */
    public function peekPending(): array
    {
        return $this->withLock($this->pendingFile, fn ($handle) => $this->readJsonArray($handle), LOCK_SH);
    }

    /**
     * Returns the current pending batch, appends it to today's dated
     * backup log, then clears the live pending store -- in that order, so
     * a failure writing the backup leaves the pending store untouched
     * (the data stays exactly where it already safely was) rather than
     * being cleared out from under a backup that was never actually
     * written.
     */
    public function flush(): array
    {
        return $this->withLock($this->pendingFile, function ($handle) {
            $batch = $this->readJsonArray($handle);
            if ($batch !== []) {
                $this->appendToBackupLog($batch);
            }
            $this->writeJsonArray($handle, []);
            return $batch;
        });
    }

    private function appendToBackupLog(array $batch): void
    {
        if (!is_dir($this->flushedDir) && !mkdir($this->flushedDir, 0775, true) && !is_dir($this->flushedDir)) {
            throw new RuntimeException("Could not create flushed-log directory: {$this->flushedDir}");
        }
        $logFile = $this->flushedDir . '/' . gmdate('Y-m-d') . '.json';
        $this->withLock($logFile, function ($handle) use ($batch) {
            $existing = $this->readJsonArray($handle);
            $this->writeJsonArray($handle, array_merge($existing, $batch));
            return null;
        });
    }

    /** Opens $path (creating it if needed), takes $lockType for the
     * duration of $work($handle), and always releases/closes afterward --
     * including when $work throws, so a failure never leaves a stale lock
     * behind. */
    private function withLock(string $path, callable $work, int $lockType = LOCK_EX)
    {
        $dir = dirname($path);
        if (!is_dir($dir) && !mkdir($dir, 0775, true) && !is_dir($dir)) {
            throw new RuntimeException("Could not create data directory: {$dir}");
        }

        $handle = fopen($path, 'c+');
        if ($handle === false) {
            throw new RuntimeException("Could not open {$path}");
        }
        try {
            if (!flock($handle, $lockType)) {
                throw new RuntimeException("Could not lock {$path}");
            }
            return $work($handle);
        } finally {
            flock($handle, LOCK_UN);
            fclose($handle);
        }
    }

    /** Reads the whole (locked) file handle as a JSON array; an empty or
     * missing file is treated as []  rather than an error -- the normal
     * state before anything has ever been captured/flushed. */
    private function readJsonArray($handle): array
    {
        rewind($handle);
        $contents = stream_get_contents($handle);
        if ($contents === false || trim($contents) === '') {
            return [];
        }
        $decoded = json_decode($contents, true);
        return is_array($decoded) ? $decoded : [];
    }

    private function writeJsonArray($handle, array $data): void
    {
        rewind($handle);
        ftruncate($handle, 0);
        fwrite($handle, json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE));
        fflush($handle);
    }
}
