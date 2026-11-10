<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

final class StoreTest extends TestCase
{
    private string $dataDir;
    private Store $store;

    protected function setUp(): void
    {
        $this->dataDir = sys_get_temp_dir() . '/store-test-' . bin2hex(random_bytes(6));
        mkdir($this->dataDir, 0775, true);
        $this->store = new Store($this->dataDir);
    }

    protected function tearDown(): void
    {
        $this->removeDir($this->dataDir);
    }

    private function removeDir(string $dir): void
    {
        if (!is_dir($dir)) {
            return;
        }
        foreach (scandir($dir) as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $path = "$dir/$entry";
            is_dir($path) ? $this->removeDir($path) : unlink($path);
        }
        rmdir($dir);
    }

    public function testPeekPendingIsEmptyInitially(): void
    {
        $this->assertSame([], $this->store->peekPending());
    }

    public function testAppendAddsServerAssignedIdAndTimestamp(): void
    {
        $record = $this->store->append(['permalink' => 'https://facebook.com/x']);

        $this->assertArrayHasKey('id', $record);
        $this->assertNotSame('', $record['id']);
        $this->assertArrayHasKey('receivedAt', $record);
        $this->assertSame(['permalink' => 'https://facebook.com/x'], $record['payload']);
    }

    public function testAppendedRecordAppearsInPending(): void
    {
        $this->store->append(['permalink' => 'a']);
        $this->store->append(['permalink' => 'b']);

        $pending = $this->store->peekPending();
        $this->assertCount(2, $pending);
        $this->assertSame('a', $pending[0]['payload']['permalink']);
        $this->assertSame('b', $pending[1]['payload']['permalink']);
    }

    public function testEachAppendGetsAUniqueId(): void
    {
        $first = $this->store->append(['permalink' => 'a']);
        $second = $this->store->append(['permalink' => 'b']);

        $this->assertNotSame($first['id'], $second['id']);
    }

    public function testClientPayloadCannotOverrideServerAssignedFields(): void
    {
        // The payload is nested under "payload" specifically so a client
        // field literally named "id" can never collide with the
        // server-assigned one.
        $record = $this->store->append(['id' => 'client-supplied', 'permalink' => 'a']);

        $this->assertNotSame('client-supplied', $record['id']);
        $this->assertSame('client-supplied', $record['payload']['id']);
    }

    public function testPeekPendingDoesNotMutateTheStore(): void
    {
        $this->store->append(['permalink' => 'a']);
        $this->store->peekPending();
        $this->store->peekPending();

        $this->assertCount(1, $this->store->peekPending());
    }

    public function testFlushReturnsTheBatchAndEmptiesPending(): void
    {
        $this->store->append(['permalink' => 'a']);
        $this->store->append(['permalink' => 'b']);

        $flushed = $this->store->flush();

        $this->assertCount(2, $flushed);
        $this->assertSame([], $this->store->peekPending());
    }

    public function testFlushWritesABackupLogFileForToday(): void
    {
        $this->store->append(['permalink' => 'a']);
        $this->store->flush();

        $logFile = $this->dataDir . '/flushed/' . gmdate('Y-m-d') . '.json';
        $this->assertFileExists($logFile);

        $logged = json_decode(file_get_contents($logFile), true);
        $this->assertCount(1, $logged);
        $this->assertSame('a', $logged[0]['payload']['permalink']);
    }

    public function testASecondFlushTheSameDayAppendsToTheSameBackupFileRatherThanOverwriting(): void
    {
        $this->store->append(['permalink' => 'a']);
        $this->store->flush();

        $this->store->append(['permalink' => 'b']);
        $this->store->flush();

        $logFile = $this->dataDir . '/flushed/' . gmdate('Y-m-d') . '.json';
        $logged = json_decode(file_get_contents($logFile), true);
        $this->assertCount(2, $logged);
    }

    public function testFlushingAnEmptyPendingStoreWritesNoBackupFile(): void
    {
        $this->store->flush();

        $this->assertFalse(is_dir($this->dataDir . '/flushed'));
    }

    public function testFlushReturnsEmptyArrayWhenNothingWasPending(): void
    {
        $this->assertSame([], $this->store->flush());
    }

    public function testACaptureAfterAFlushLandsInTheNowEmptyPendingStoreNotLost(): void
    {
        // This is the core "never silently lose a capture" guarantee: a
        // capture that arrives logically "during" a flush (simulated here
        // as simply happening right after it, since the real concurrency
        // protection is the shared flock around both operations) must
        // still end up safely in the pending store, not dropped on the
        // floor because a flush happened to be running.
        $this->store->append(['permalink' => 'a']);
        $this->store->flush();
        $this->store->append(['permalink' => 'b']);

        $pending = $this->store->peekPending();
        $this->assertCount(1, $pending);
        $this->assertSame('b', $pending[0]['payload']['permalink']);
    }
}
