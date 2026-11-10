<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

final class CaptureControllerTest extends TestCase
{
    private string $dataDir;
    private CaptureController $controller;

    protected function setUp(): void
    {
        $this->dataDir = sys_get_temp_dir() . '/capture-controller-test-' . bin2hex(random_bytes(6));
        mkdir($this->dataDir, 0775, true);
        $this->controller = new CaptureController(new Store($this->dataDir));
    }

    protected function tearDown(): void
    {
        foreach (glob($this->dataDir . '/*') as $f) {
            unlink($f);
        }
        rmdir($this->dataDir);
    }

    public function testValidJsonBodyIsStoredAndReturns200(): void
    {
        [$status, $body] = $this->controller->handle(json_encode(['permalink' => 'https://facebook.com/x']));

        $this->assertSame(200, $status);
        $decoded = json_decode($body, true);
        $this->assertSame('ok', $decoded['status']);
        $this->assertArrayHasKey('id', $decoded);
    }

    public function testNullBodyIsRejectedWithout400(): void
    {
        [$status, $body] = $this->controller->handle(null);

        $this->assertSame(400, $status);
        $this->assertArrayHasKey('error', json_decode($body, true));
    }

    public function testEmptyStringBodyIsRejected(): void
    {
        [$status] = $this->controller->handle('');
        $this->assertSame(400, $status);
    }

    public function testMalformedJsonIsRejected(): void
    {
        [$status] = $this->controller->handle('{not valid json');
        $this->assertSame(400, $status);
    }

    public function testJsonScalarIsRejectedSinceAPayloadMustBeAnObject(): void
    {
        [$status] = $this->controller->handle('"just a string"');
        $this->assertSame(400, $status);
    }

    public function testRejectedBodyIsNeverAppendedToTheStore(): void
    {
        $this->controller->handle('{not valid json');

        $store = new Store($this->dataDir);
        $this->assertSame([], $store->peekPending());
    }
}
