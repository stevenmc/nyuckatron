<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

final class FeedControllerTest extends TestCase
{
    private string $dataDir;
    private Store $store;
    private FeedController $controller;

    protected function setUp(): void
    {
        $this->dataDir = sys_get_temp_dir() . '/feed-controller-test-' . bin2hex(random_bytes(6));
        mkdir($this->dataDir, 0775, true);
        $this->store = new Store($this->dataDir);
        $this->controller = new FeedController($this->store);
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

    public function testDefaultModeReturnsPendingListWithoutClearingIt(): void
    {
        $this->store->append(['permalink' => 'a']);

        [$status, $body] = $this->controller->handle(false);

        $this->assertSame(200, $status);
        $this->assertCount(1, json_decode($body, true));
        $this->assertCount(1, $this->store->peekPending());
    }

    public function testDefaultModeOnAnEmptyStoreReturnsAnEmptyJsonArray(): void
    {
        [$status, $body] = $this->controller->handle(false);

        $this->assertSame(200, $status);
        $this->assertSame([], json_decode($body, true));
    }

    public function testFlushModeReturnsPendingListAndClearsIt(): void
    {
        $this->store->append(['permalink' => 'a']);
        $this->store->append(['permalink' => 'b']);

        [$status, $body] = $this->controller->handle(true);

        $this->assertSame(200, $status);
        $this->assertCount(2, json_decode($body, true));
        $this->assertSame([], $this->store->peekPending());
    }

    public function testFlushModeWritesTheBackupLog(): void
    {
        $this->store->append(['permalink' => 'a']);
        $this->controller->handle(true);

        $this->assertFileExists($this->dataDir . '/flushed/' . gmdate('Y-m-d') . '.json');
    }
}
