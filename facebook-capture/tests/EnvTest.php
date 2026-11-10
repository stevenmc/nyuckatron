<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

final class EnvTest extends TestCase
{
    private string $tmpFile;

    protected function setUp(): void
    {
        $this->tmpFile = tempnam(sys_get_temp_dir(), 'envtest');
    }

    protected function tearDown(): void
    {
        @unlink($this->tmpFile);
    }

    public function testParsesKeyValuePairs(): void
    {
        file_put_contents($this->tmpFile, "FOO=bar\nBAZ=qux\n");
        $this->assertSame(['FOO' => 'bar', 'BAZ' => 'qux'], Env::load($this->tmpFile));
    }

    public function testIgnoresCommentsAndBlankLines(): void
    {
        file_put_contents($this->tmpFile, "# a comment\n\nFOO=bar\n   \n# another\n");
        $this->assertSame(['FOO' => 'bar'], Env::load($this->tmpFile));
    }

    public function testTrimsWhitespaceAroundKeyAndValue(): void
    {
        file_put_contents($this->tmpFile, "  FOO  =  bar  \n");
        $this->assertSame(['FOO' => 'bar'], Env::load($this->tmpFile));
    }

    public function testMissingFileReturnsEmptyArray(): void
    {
        $this->assertSame([], Env::load($this->tmpFile . '.does-not-exist'));
    }

    public function testLineWithoutEqualsIsIgnored(): void
    {
        file_put_contents($this->tmpFile, "not a valid line\nFOO=bar\n");
        $this->assertSame(['FOO' => 'bar'], Env::load($this->tmpFile));
    }
}
